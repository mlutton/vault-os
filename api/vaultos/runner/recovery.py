"""Replay durable terminal observations without rerunning their parents."""

import logging

from ..api.jobs import child_has_record, dispatch_skill
from ..jobs import store
from ..registry import SubmissionError
from ..state import resolve_state_root
from ..timeutil import utcnow_z
from ..vault.runs import list_run_files, read_run_record
from .records import remove_intent

logger = logging.getLogger(__name__)


def recover_terminal_records(conn, registry, vault_root) -> None:
    """Caller must hold the runner lock. Legacy or incomplete files are inert."""
    root = resolve_state_root(vault_root)
    for path in list_run_files(vault_root):
        try:
            record = read_run_record(path)
            if record.attempt_id is None:
                continue
        except (OSError, ValueError, KeyError) as exc:
            logger.warning("recovery: skipping invalid terminal file %s: %s", path.name, exc)
            continue

        intent = root / "queue" / f"{record.id}.json"
        if intent.exists():
            remove_intent(intent)

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
                        registry.get(record.skill).engine if registry.get(record.skill) else None
                    ),
                    exit_code=record.exit_code if status == record.status else None,
                    summary=record.summary if status == record.status else None,
                    deliverable_path=record.deliverable_path,
                    md_path=record.md_path,
                )

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
