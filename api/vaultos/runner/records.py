"""Durable skill-job observations written before updating the jobs index."""

import json
import os
from pathlib import Path

from ..vault.durable import write_record  # noqa: F401 - runner compatibility export
from ..vault.runs import read_run_record


def remove_intent(path: Path) -> None:
    path.unlink(missing_ok=True)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def unresolved_attempts(state_root: Path) -> list[str]:
    runs = state_root / "runs"
    if not runs.is_dir():
        return []
    unresolved = set()
    for path in runs.glob("*.attempt-*.json"):
        job_id = path.name.split(".attempt-", 1)[0]
        terminal_path = runs / f"{job_id}.json"
        try:
            terminal = read_run_record(terminal_path)
            terminal_data = json.loads(terminal_path.read_text())
            attempt_data = json.loads(path.read_text())
            if not isinstance(terminal_data, dict):
                raise KeyError("terminal record must be an object")
            if not isinstance(attempt_data, dict) or not isinstance(
                attempt_data.get("attempt_id"), str
            ):
                raise KeyError("attempt record lacks string attempt identity")
            resolved = (
                terminal.id == job_id
                and terminal.status in {"ok", "error"}
                and attempt_data["attempt_id"]
                in terminal_data.get("attempt_ids", [terminal_data.get("attempt_id")])
            )
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            resolved = False
        if not resolved:
            unresolved.add(job_id)
    return sorted(unresolved)
