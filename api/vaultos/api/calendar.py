from fastapi import APIRouter, Depends, HTTPException

from ..vault.calendar import read_calendar_today
from .deps import get_settings

router = APIRouter()


@router.get("/calendar")
def get_calendar(settings=Depends(get_settings)):
    if not settings.vault_readable():
        raise HTTPException(503, detail="vault root is missing or unreadable")

    snapshot = read_calendar_today(settings.vault_root)
    if snapshot is None:
        # Never pulled yet -- distinguishable from "pulled, zero events today"
        # only by pulled_at being null, not by status code.
        return {"pulled_at": None, "events": []}
    return {
        "pulled_at": snapshot.pulled_at,
        "events": [
            {"summary": e.summary, "start": e.start, "end": e.end, "all_day": e.all_day}
            for e in snapshot.events
        ],
    }
