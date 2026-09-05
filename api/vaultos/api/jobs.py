import logging
import re
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import seen
from ..jobs import store
from ..registry import Registry, SubmissionError, validate_submission
from ..timeutil import utcnow_z
from ..vault.intents import write_intent
from ..vault.runner import read_heartbeat
from .deps import get_conn, get_registry, get_settings

router = APIRouter()
logger = logging.getLogger(__name__)

# Skill completion -> follow-up skill to auto-dispatch, one hop only (no
# entry here is itself a key, so there's no risk of a chain loop). Added
# 2026-08-11 so daily-topic-digest gets a real job/history entry for its
# acquire-triggered runs instead of running inline inside acquire's own
# session -- see runner.js's "acquire" case, which no longer chains into it
# itself now that this exists. Design record:
# docs/adr/0016-jobs-can-auto-chain-a-followup-via-chain-map.md
#
# A retried/duplicate terminal event for the same parent job is safe: the
# dispatch below tags its source with the parent job's id, and
# store.create_job()'s partial unique index on chain: sources makes a
# second dispatch for that same parent a no-op rather than a second job.
# See ADR-0016's Consequences for what this closes.
#
# "deep-research": "research-into-draft" added 2026-08-11 for the content-flow
# design's auto-fired research path (Review Topics -> Create Draft -> auto
# deep-research). Deliberately does NOT extend this map's shape to pass args
# through -- the chained skill still dispatches with empty args, same as
# daily-topic-digest above. research-into-draft instead finds its input by a
# filename convention: deep-research, when called with a `draft_slug` arg,
# writes its report to inbox/deep-research/<draft_slug>-deep-research.md (see
# runner.js's "deep-research" case), so research-into-draft can join a draft
# to its report purely by slug, with no data needing to flow through the
# chain dispatch itself. inbox/deep-research/ was split out from
# inbox/research/ on 2026-08-11 to keep deep-research's per-topic reports a
# clean candidate list for the wiki-ingest skill.
CHAIN_MAP = {"acquire": "daily-topic-digest", "deep-research": "research-into-draft"}

# Same allowlisted-prefix + resolved-path-must-start-with-vault-root guard as
# /daily's path-traversal fix -- deliverable_path comes from runner-written
# job events, and runs process untrusted content (emails, web), so it must
# never be followed outside the dirs runs actually write to.
READABLE_PREFIXES = ("inbox/", "system/runs/", "daily-notes/", "writing/")
LINK_FRONTMATTER_RE = re.compile(r'^link:\s*["\']?(https?://\S+?)["\']?\s*$', re.MULTILINE)


def deliverable_link(vault_root: Path, deliverable_path: str | None) -> str | None:
    if not deliverable_path:
        return None
    clean = deliverable_path.replace("\\", "/")
    if not any(clean.startswith(prefix) for prefix in READABLE_PREFIXES):
        return None
    resolved_root = vault_root.resolve()
    candidate = (vault_root / clean).resolve()
    if candidate != resolved_root and resolved_root not in candidate.parents:
        return None
    try:
        # Generous enough to survive a long voice-ask `prompt:` field pushing
        # `link:` further down the frontmatter block than a short skill's would.
        raw = candidate.read_text()[:4096]
    except (OSError, UnicodeDecodeError):
        return None
    if not raw.startswith("---"):
        return None
    frontmatter = re.split(r"\r?\n---", raw, maxsplit=1)[0]
    match = LINK_FRONTMATTER_RE.search(frontmatter)
    return match.group(1) if match else None


class JobCreate(BaseModel):
    skill: str
    args: dict = Field(default_factory=dict)
    source: str = "api"


class JobEvent(BaseModel):
    status: Literal["queued", "running", "ok", "error", "orphaned"]
    ts: str
    skill: str | None = None
    args: dict | None = None
    source: str | None = None
    exit_code: int | None = None
    summary: str | None = None
    deliverable_path: str | None = None
    md_path: str | None = None
    pid: int | None = None


def _job_to_dict(job, vault_root: Path, conn) -> dict:
    return {
        "id": job.id,
        "skill": job.skill,
        "args": job.args,
        "source": job.source,
        "engine": job.engine,
        "status": job.status,
        "ts_queued": job.ts_queued,
        "ts_started": job.ts_started,
        "ts_completed": job.ts_completed,
        "exit_code": job.exit_code,
        "summary": job.summary,
        "md_path": job.md_path,
        "deliverables": job.deliverables,
        "runner_pid": job.runner_pid,
        "last_event_ts": job.last_event_ts,
        "label": None,
        "link": deliverable_link(vault_root, job.deliverable_path) if job.status == "ok" else None,
        "duration_s": store.duration_s(job.ts_started, job.ts_completed),
        # Additive (Phase 4 / ADR-0011) -- read/unread state for Active &
        # Recent / Results, computed via vaultos/seen.py rather than a
        # column on this table (every item type shares one mechanism).
        "seen": seen.is_seen(conn, item_type="job", item_id=job.id),
    }


def dispatch_skill(
    conn, registry: Registry, vault_root: Path, skill_id: str, args: dict, source: str
):
    """Validate + create_job + write_intent, shared by /jobs and the voice
    router (/route) -- both submission paths must go through the same
    registry validation and land in the same Jobs store. Raises
    SubmissionError on an invalid skill/args; callers decide how to surface
    that (a 400 for /jobs, a classification-fallback for /route).

    create_job() runs first: for a `chain:` source (ADR-0016) it's
    idempotent, so a duplicate dispatch for the same parent job returns the
    already-existing job instead of creating a new one. write_intent() --
    which is what actually causes the runner daemon to execute the skill --
    only fires when create_job() confirms this call was the one that created
    the row; a deduped call intentionally does nothing further. Ordinary
    (non-chain) sources always create a fresh job, same as before."""
    skill = validate_submission(registry, skill_id, args)
    job_id = str(uuid.uuid4())
    ts = utcnow_z()
    job = store.create_job(
        conn,
        job_id=job_id,
        skill=skill.id,
        args=args,
        source=source,
        engine=skill.engine,
        ts_queued=ts,
    )
    if job.id == job_id:
        write_intent(vault_root, job_id=job_id, skill=skill.id, args=args, ts=ts, source=source)
    return job.id, skill


@router.post("/jobs", status_code=201)
def submit_job(
    body: JobCreate,
    conn=Depends(get_conn),
    registry: Registry = Depends(get_registry),
    settings=Depends(get_settings),
):
    # Validate before the vault-readable check (restores the precedence from
    # before dispatch_skill() was extracted): a bad request is a 400
    # regardless of vault health, not a 503 that masks the real problem.
    try:
        validate_submission(registry, body.skill, body.args)
    except SubmissionError as exc:
        raise HTTPException(400, detail={"field": exc.field, "message": str(exc)})

    if not settings.vault_readable():
        raise HTTPException(503, detail="vault root is missing or unreadable")

    try:
        job_id, skill = dispatch_skill(
            conn, registry, settings.vault_root, body.skill, body.args, body.source
        )
    except SubmissionError as exc:
        raise HTTPException(400, detail={"field": exc.field, "message": str(exc)})

    heartbeat = read_heartbeat(settings.vault_root)
    return {
        "id": job_id,
        "skill": skill.id,
        "status": "queued",
        "runner_alive": bool(heartbeat and heartbeat.alive),
    }


@router.get("/jobs")
def list_active_jobs(conn=Depends(get_conn), settings=Depends(get_settings)):
    jobs = store.list_jobs(conn, statuses=["queued", "running"], order_by="last_event_ts")
    return [_job_to_dict(job, settings.vault_root, conn) for job in jobs]


@router.get("/jobs/{job_id}")
def get_job_detail(job_id: str, conn=Depends(get_conn), settings=Depends(get_settings)):
    job = store.get_job(conn, job_id)
    if job is None:
        raise HTTPException(404, detail="job not found")
    return _job_to_dict(job, settings.vault_root, conn)


def apply_event_and_chain(
    conn,
    registry: Registry,
    vault_root: Path,
    *,
    job_id: str,
    status: str,
    ts: str,
    skill: str | None = None,
    args: dict | None = None,
    source: str | None = None,
    exit_code: int | None = None,
    summary: str | None = None,
    deliverable_path: str | None = None,
    md_path: str | None = None,
    pid: int | None = None,
):
    """Post one job event through store.apply_event and, on a terminal `ok`
    that CHAIN_MAP maps, auto-dispatch the follow-up skill -- the same two
    steps POST /jobs/{id}/events performs. Factored out so vaultos.runner can
    drive the identical path in-process (no HTTP round-trip) when it posts a
    job's terminal status, per the runner spec ("the same event-posting path
    jobs.py uses today, so CHAIN_MAP auto-chaining fires unchanged").

    Returns the updated Job, or None if the event couldn't create/find a job
    (unknown job_id with no skill to create it from -- the same condition
    the HTTP endpoint turns into a 404)."""
    engine = None
    if skill is not None:
        skill_def = registry.get(skill)
        engine = skill_def.engine if skill_def else None

    received_at = utcnow_z()
    job = store.apply_event(
        conn,
        job_id=job_id,
        status=status,
        ts=ts,
        received_at=received_at,
        skill=skill,
        args=args,
        source=source,
        engine=engine,
        exit_code=exit_code,
        summary=summary,
        deliverable_path=deliverable_path,
        md_path=md_path,
        pid=pid,
    )
    if job is None:
        return None

    chained_skill = CHAIN_MAP.get(job.skill)
    if chained_skill and job.status == "ok":
        # source embeds the parent job's id so a retried/duplicate terminal
        # event for the same parent can't double-dispatch -- create_job()'s
        # partial unique index on chain: sources (migration 0003) makes this
        # call idempotent; a duplicate call here is a safe no-op, not
        # something this call site needs to guard against itself.
        chain_source = f"chain:{job.skill}:{job.id}"
        try:
            dispatch_skill(conn, registry, vault_root, chained_skill, {}, source=chain_source)
        except SubmissionError as exc:
            # Don't fail the triggering job's own event just because its
            # chained follow-up couldn't be dispatched (e.g. the follow-up
            # skill isn't registered yet) -- that's a real gap worth seeing
            # in the logs, not a reason to 500 an otherwise-successful event.
            logger.warning("chain dispatch %s -> %s failed: %s", job.skill, chained_skill, exc)

    return job


@router.post("/jobs/{job_id}/events")
def post_job_event(
    job_id: str,
    body: JobEvent,
    conn=Depends(get_conn),
    registry: Registry = Depends(get_registry),
    settings=Depends(get_settings),
):
    job = apply_event_and_chain(
        conn,
        registry,
        settings.vault_root,
        job_id=job_id,
        status=body.status,
        ts=body.ts,
        skill=body.skill,
        args=body.args,
        source=body.source,
        exit_code=body.exit_code,
        summary=body.summary,
        deliverable_path=body.deliverable_path,
        md_path=body.md_path,
        pid=body.pid,
    )
    if job is None:
        raise HTTPException(
            404, detail="job not found and event did not carry enough detail to create it"
        )

    return _job_to_dict(job, settings.vault_root, conn)
