"""Writes the runner-status.json heartbeat that vaultos.vault.runner.read_heartbeat
(and, through it, GET /runner) reads -- same file name and field shape,
written under vaultos.state.resolve_state_root(settings) rather than a
hardcoded vault path, so a runner started with VAULTOS_STATE_ROOT set lands
its heartbeat there instead."""

import json
import os
from pathlib import Path

from ..timeutil import utcnow_z

RUNNER_VERSION = "0.1.0"


def write_heartbeat(
    state_root: Path,
    *,
    pid: int,
    active: int,
    pending: int,
    busy: bool,
    max_concurrent: int = 1,
    version: str = RUNNER_VERSION,
) -> Path:
    state_root.mkdir(parents=True, exist_ok=True)
    path = state_root / "runner-status.json"
    body = {
        "ts": utcnow_z(),
        "pid": pid,
        "active": active,
        "pending": pending,
        "version": version,
        "busy": busy,
        "max_concurrent": max_concurrent,
    }
    # Atomic write -- a crash mid-write must never leave a truncated/corrupt
    # heartbeat file for read_heartbeat to trip over (same convention as
    # cli.py's calendar_pull).
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(body))
    os.replace(tmp_path, path)
    return path
