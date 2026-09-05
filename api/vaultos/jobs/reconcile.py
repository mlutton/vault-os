import json
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from ..registry import Registry
from ..timeutil import utcnow_z
from ..vault.runner import RunnerHeartbeat
from ..vault.runs import list_run_files, read_run_record
from .store import apply_event, list_jobs

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReconcileResult:
    queue_files_seen: int
    run_files_seen: int
    skipped: int


def _engine_for(registry: Registry, skill: str) -> str | None:
    skill_def = registry.get(skill)
    return skill_def.engine if skill_def else None


def reconcile_from_files(
    vault_root: Path, conn: sqlite3.Connection, registry: Registry
) -> ReconcileResult:
    """Walk system/queue/ + system/runs/ and apply each file's state via apply_event() --
    the same monotonic transition logic live events use (ADR-0001). Unparseable files are
    skipped and logged, never fatal to the rest of the pass."""
    received_at = utcnow_z()
    queue_files_seen = 0
    run_files_seen = 0
    skipped = 0

    queue_dir = vault_root / "system" / "queue"
    if queue_dir.is_dir():
        for path in sorted(queue_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text())
                job_id = data.get("id") or path.stem
                skill = data["skill"]
                ts = data["ts"]
            except (OSError, json.JSONDecodeError, KeyError) as e:
                logger.warning("reconcile: skipping unparseable queue file %s: %s", path, e)
                skipped += 1
                continue
            queue_files_seen += 1
            apply_event(
                conn,
                job_id=job_id,
                status="queued",
                ts=ts,
                received_at=received_at,
                skill=skill,
                args=data.get("args", {}),
                source=data.get("source", "api"),
                engine=_engine_for(registry, skill),
            )

    for path in list_run_files(vault_root):
        try:
            record = read_run_record(path)
        except (OSError, json.JSONDecodeError, KeyError) as e:
            logger.warning("reconcile: skipping unparseable run file %s: %s", path, e)
            skipped += 1
            continue
        run_files_seen += 1
        engine = _engine_for(registry, record.skill)

        if record.ts_queued:
            apply_event(
                conn,
                job_id=record.id,
                status="queued",
                ts=record.ts_queued,
                received_at=received_at,
                skill=record.skill,
                args=record.args,
                source=record.source,
                engine=engine,
            )

        if record.ts_started:
            apply_event(
                conn,
                job_id=record.id,
                status="running",
                ts=record.ts_started,
                received_at=received_at,
                skill=record.skill,
                args=record.args,
                source=record.source,
                engine=engine,
                md_path=record.md_path,
                deliverable_path=record.deliverable_path,
            )

        if record.status in ("ok", "error"):
            apply_event(
                conn,
                job_id=record.id,
                status=record.status,
                ts=record.ts_completed or record.ts_started or received_at,
                received_at=received_at,
                exit_code=record.exit_code,
                summary=record.summary,
            )

    return ReconcileResult(
        queue_files_seen=queue_files_seen, run_files_seen=run_files_seen, skipped=skipped
    )


def detect_orphans(conn: sqlite3.Connection, heartbeat: RunnerHeartbeat | None) -> list[str]:
    """Mark stuck 'running' jobs as orphaned: a runner_pid mismatch against the live
    heartbeat, or (absent a mismatch signal) a stale/missing heartbeat. Goes through
    apply_event() like every other transition, not a direct UPDATE, so job_events
    stays a complete audit trail (ADR-0001). Never auto-retries -- only marks status."""
    received_at = utcnow_z()
    orphaned_ids = []
    for job in list_jobs(conn, statuses=["running"]):
        pid_mismatch = (
            job.runner_pid is not None and heartbeat is not None and job.runner_pid != heartbeat.pid
        )
        stale_or_missing = heartbeat is None or not heartbeat.alive

        if pid_mismatch or stale_or_missing:
            # source="orphan-detector" never touches jobs.source (apply_event only
            # writes that column on the creation path, never reached for an
            # already-existing running job) -- it lands only in job_events.detail,
            # identifying this transition as detector-originated in the audit trail.
            apply_event(
                conn,
                job_id=job.id,
                status="orphaned",
                ts=received_at,
                received_at=received_at,
                source="orphan-detector",
            )
            orphaned_ids.append(job.id)

    return orphaned_ids
