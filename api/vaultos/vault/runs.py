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
    if not isinstance(data, dict):
        raise KeyError("run record must be an object")
    if not isinstance(data.get("skill"), str) or not isinstance(data.get("status"), str):
        raise KeyError("run record lacks string skill or status")
    if "id" in data and not isinstance(data["id"], str):
        raise KeyError("run id must be a string")
    if "args" in data and not isinstance(data["args"], dict):
        raise KeyError("run args must be an object")
    for field in (
        "source",
        "ts_queued",
        "ts_started",
        "ts_completed",
        "summary",
        "md_path",
        "deliverable_path",
    ):
        if field in data and data[field] is not None and not isinstance(data[field], str):
            raise KeyError(f"run {field} must be a string")
    if (
        "exit_code" in data
        and data["exit_code"] is not None
        and not isinstance(data["exit_code"], int)
    ):
        raise KeyError("run exit_code must be an integer")
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
            or not isinstance(data["attempt_id"], str)
            or not data["attempt_id"]
            or not isinstance(data["attempt_ids"], list)
            or not all(isinstance(attempt_id, str) for attempt_id in data["attempt_ids"])
            or data["attempt_id"] not in data["attempt_ids"]
            or not isinstance(data["args"], dict)
            or not isinstance(data["source"], str)
            or not data["ts_queued"]
            or not data["ts_started"]
            or not isinstance(evidence, dict)
            or not isinstance(evidence.get("engine"), str)
            or not evidence["engine"]
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
