"""Replay terminal observations and project intents before ordinary claims."""

import json
import logging
import sqlite3

from ..api.jobs import child_has_record, dispatch_skill
from ..jobs import store
from ..jobs.reconcile import log_projection_collision
from ..registry import SubmissionError
from ..state import resolve_state_root
from ..timeutil import utcnow_z
from ..vault.runs import list_run_files, read_run_record
from .records import remove_intent

logger = logging.getLogger(__name__)


def recover_terminal_records(conn, registry, vault_root, *, reported_skips=None) -> None:
    """Caller holds the runner lock; legacy or incomplete outcomes are inert."""
    if reported_skips is None:
        reported_skips = set()

    def warn_once(path, message, exc):
        if path not in reported_skips:
            logger.warning(message, path.name, exc)
            reported_skips.add(path)

    def collision_once(path, source, exc):
        if path not in reported_skips:
            log_projection_collision(conn, path, source, exc)
            reported_skips.add(path)

    root = resolve_state_root(vault_root)
    for path in list_run_files(vault_root):
        try:
            record = read_run_record(path)
            if record.attempt_id is None:
                continue
        except (OSError, ValueError, KeyError) as exc:
            warn_once(path, "recovery: skipping invalid terminal file %s: %s", exc)
            continue

        intent = root / "queue" / f"{record.id}.json"
        if intent.exists():
            remove_intent(intent)

        try:
            job = store.get_job(conn, record.id)
            if job is None or job.status not in {"ok", "error"}:
                # Project submission/start metadata too when the index was lost.
                for status, ts in (
                    ("queued", record.ts_queued),
                    ("running", record.ts_started),
                    (record.status, record.ts_completed),
                ):
                    store.apply_event(
                        conn,
                        job_id=record.id,
                        status=status,
                        ts=ts,
                        received_at=utcnow_z(),
                        skill=record.skill,
                        args=record.args,
                        source=record.source,
                        engine=(
                            registry.get(record.skill).engine
                            if registry.get(record.skill)
                            else None
                        ),
                        exit_code=record.exit_code if status == record.status else None,
                        summary=record.summary if status == record.status else None,
                        deliverable_path=record.deliverable_path,
                        md_path=record.md_path,
                    )
        except sqlite3.IntegrityError as exc:
            collision_once(path, record.source, exc)
            continue
        except sqlite3.Error as exc:
            warn_once(path, "recovery: skipping terminal projection %s: %s", exc)
            continue

        for transition in record.transitions:
            if child_has_record(vault_root, transition["child_id"]):
                continue
            try:
                dispatch_skill(
                    conn,
                    registry,
                    vault_root,
                    transition["child_skill"],
                    {},
                    source=f"chain:{record.skill}:{record.id}",
                    job_id=transition["child_id"],
                    chain={
                        "parent_job_id": record.id,
                        "parent_attempt_id": record.attempt_id,
                        "rule_id": transition["rule_id"],
                        "rule_version": transition["rule_version"],
                    },
                )
            except (SubmissionError, OSError) as exc:
                logger.warning(
                    "recovery: transition %s remains pending: %s", transition["rule_id"], exc
                )

    # Project durable submissions, including children published during this pass.
    # Execution still goes through the ordinary claim and its intent re-read.
    for path in sorted((root / "queue").glob("*.json")):
        try:
            data = json.loads(path.read_text())
            for field in ("id", "skill", "ts"):
                if not isinstance(data.get(field), str) or not data[field]:
                    raise ValueError(f"intent {field} must be a non-empty string")
            if not isinstance(data.get("args", {}), dict):
                raise ValueError("intent args must be an object")
            if not isinstance(data.get("source", "api"), str):
                raise ValueError("intent source must be a string")
            job_id = data["id"]
            skill = data["skill"]
            ts = data["ts"]
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            warn_once(path, "recovery: skipping invalid intent file %s: %s", exc)
            continue
        try:
            if store.get_job(conn, job_id) is not None:
                continue
            skill_def = registry.get(skill)
            store.apply_event(
                conn,
                job_id=job_id,
                status="queued",
                ts=ts,
                received_at=utcnow_z(),
                skill=skill,
                args=data.get("args", {}),
                source=data.get("source", "api"),
                engine=skill_def.engine if skill_def else None,
            )
        except sqlite3.IntegrityError as exc:
            collision_once(path, data.get("source", "api"), exc)
        except sqlite3.Error as exc:
            warn_once(path, "recovery: skipping intent projection %s: %s", exc)
