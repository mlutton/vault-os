import os
from pathlib import Path


def pid_path(db_path: Path) -> Path:
    return db_path.parent / "vaultos.pid"


def write_pid(db_path: Path) -> Path:
    path = pid_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(os.getpid()))
    return path


def remove_pid(db_path: Path) -> None:
    try:
        pid_path(db_path).unlink()
    except FileNotFoundError:
        pass


def is_spine_alive(db_path: Path) -> bool:
    path = pid_path(db_path)
    if not path.exists():
        return False
    try:
        pid = int(path.read_text().strip())
    except (ValueError, OSError):
        return False
    try:
        os.kill(pid, 0)  # liveness check -- no signal sent, just existence
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # process exists, just owned by someone else
    return True
