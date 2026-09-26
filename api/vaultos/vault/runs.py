import json
from dataclasses import dataclass
from pathlib import Path

from ..state import resolve_state_root


@dataclass(frozen=True)
class RunRecord:
    id: str
    skill: str
    args: dict
    source: str | None
    ts_queued: str | None
    ts_started: str | None
    ts_completed: str | None
    status: str
    exit_code: int | None
    summary: str | None
    md_path: str | None
    deliverable_path: str | None


def read_run_log(vault_root: Path, job_id: str) -> str | None:
    path = resolve_state_root(vault_root) / "runs" / f"{job_id}.md"
    if not path.exists():
        return None
    return path.read_text()


def list_run_files(vault_root: Path) -> list[Path]:
    runs_dir = resolve_state_root(vault_root) / "runs"
    if not runs_dir.is_dir():
        return []
    return sorted(path for path in runs_dir.glob("*.json") if ".attempt-" not in path.name)


def read_run_record(path: Path) -> RunRecord:
    data = json.loads(path.read_text())
    if "attempt_id" not in data and any(path.parent.glob(f"{path.stem}.attempt-*.json")):
        raise KeyError("terminal run lacks attempt identity")
    if "attempt_id" in data:
        if data["status"] not in {"ok", "error"} or not data.get("ts_completed"):
            raise KeyError("attempt terminal lacks completed outcome")
        required = {
            "id",
            "attempt_id",
            "attempt_ids",
            "skill",
            "args",
            "source",
            "ts_queued",
            "ts_started",
            "ts_completed",
            "status",
            "exit_code",
            "summary",
            "deliverable_path",
            "completion_evidence",
        }
        if not required.issubset(data):
            raise KeyError("terminal run lacks required fields")
        evidence = data.get("completion_evidence")
        if (
            data["id"] != path.stem
            or not data["attempt_id"]
            or not isinstance(data["attempt_ids"], list)
            or data["attempt_id"] not in data["attempt_ids"]
            or not isinstance(data["args"], dict)
            or not isinstance(data["source"], str)
            or not data["ts_queued"]
            or not data["ts_started"]
            or data["status"] not in {"ok", "error"}
            or not isinstance(evidence, dict)
            or evidence.get("engine") is None
            or evidence.get("summary") != data.get("summary")
            or evidence.get("exit_code") != data.get("exit_code")
            or evidence.get("deliverable_path") != data.get("deliverable_path")
        ):
            raise KeyError("terminal run lacks matching completion evidence")
    return RunRecord(
        id=data.get("id") or path.stem,
        skill=data["skill"],
        args=data.get("args", {}),
        source=data.get("source"),
        ts_queued=data.get("ts_queued"),
        ts_started=data.get("ts_started"),
        ts_completed=data.get("ts_completed"),
        status=data["status"],
        exit_code=data.get("exit_code"),
        summary=data.get("summary"),
        md_path=data.get("md_path"),
        deliverable_path=data.get("deliverable_path"),
    )
