import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..state import resolve_state_root


@dataclass(frozen=True)
class RunnerHeartbeat:
    ts: str
    pid: int
    active: int
    pending: int
    alive: bool
    version: str = "?"
    busy: bool = False
    max_concurrent: int = 0
    heartbeat_age_s: float | None = None
    unresolved_attempts: tuple[str, ...] = ()


def read_heartbeat(vault_root: Path, *, stale_after_s: float = 120.0) -> RunnerHeartbeat | None:
    path = resolve_state_root(vault_root) / "runner-status.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None

    ts = data.get("ts")
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None

    age = (datetime.now(timezone.utc) - parsed).total_seconds()
    return RunnerHeartbeat(
        ts=ts,
        pid=data.get("pid", 0),
        active=data.get("active", 0),
        pending=data.get("pending", 0),
        alive=age < stale_after_s,
        version=data.get("version", "?"),
        busy=bool(data.get("busy", False)),
        max_concurrent=data.get("max_concurrent", 0),
        heartbeat_age_s=age,
        unresolved_attempts=tuple(data.get("unresolved_attempts", [])),
    )
