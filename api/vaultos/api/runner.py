from fastapi import APIRouter, Depends, HTTPException

from ..vault.runner import read_heartbeat
from .deps import get_settings

router = APIRouter()


def _runner_to_dict(heartbeat) -> dict:
    if heartbeat is None:
        return {
            "ts": None, "pid": None, "active": None, "pending": None, "alive": False,
            "version": None, "busy": False, "max_concurrent": None, "heartbeat_age_s": None,
        }
    return {
        "ts": heartbeat.ts,
        "pid": heartbeat.pid,
        "active": heartbeat.active,
        "pending": heartbeat.pending,
        "alive": heartbeat.alive,
        "version": heartbeat.version,
        "busy": heartbeat.busy,
        "max_concurrent": heartbeat.max_concurrent,
        "heartbeat_age_s": heartbeat.heartbeat_age_s,
    }


@router.get("/runner")
def get_runner(settings=Depends(get_settings)):
    if not settings.vault_readable():
        raise HTTPException(503, detail="vault root is missing or unreadable")

    return _runner_to_dict(read_heartbeat(settings.vault_root))
