import json
from dataclasses import dataclass
from pathlib import Path


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
    path = vault_root / "system" / "runs" / f"{job_id}.md"
    if not path.exists():
        return None
    return path.read_text()


def list_run_files(vault_root: Path) -> list[Path]:
    runs_dir = vault_root / "system" / "runs"
    if not runs_dir.is_dir():
        return []
    return sorted(runs_dir.glob("*.json"))


def read_run_record(path: Path) -> RunRecord:
    data = json.loads(path.read_text())
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
