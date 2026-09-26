from pathlib import Path

from ..state import resolve_state_root
from .durable import write_record


def write_intent(
    vault_root: Path,
    *,
    job_id: str,
    skill: str,
    args: dict,
    ts: str,
    source: str,
    exclusive: bool = False,
    chain: dict | None = None,
) -> Path:
    queue_dir = resolve_state_root(vault_root) / "queue"
    path = queue_dir / f"{job_id}.json"
    intent = {"id": job_id, "skill": skill, "args": args, "ts": ts, "source": source}
    if chain is not None:
        intent["chain"] = chain
    write_record(path, intent, exclusive=exclusive)
    return path
