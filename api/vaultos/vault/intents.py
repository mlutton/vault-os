import json
from pathlib import Path

from ..state import resolve_state_root


def write_intent(
    vault_root: Path, *, job_id: str, skill: str, args: dict, ts: str, source: str
) -> Path:
    queue_dir = resolve_state_root(vault_root) / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    path = queue_dir / f"{job_id}.json"
    intent = {"id": job_id, "skill": skill, "args": args, "ts": ts, "source": source}
    path.write_text(json.dumps(intent, indent=2))
    return path
