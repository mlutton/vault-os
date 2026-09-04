import re

from fastapi import APIRouter, Depends, HTTPException, Query

from ..timeutil import today_in_tz
from ..vault.daily import read_daily_note
from .deps import get_settings

router = APIRouter()

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _resolve_date(date: str, tz: str) -> str:
    if date == "today":
        return today_in_tz(tz)
    return date


def _daily_to_dict(note) -> dict:
    return {
        "date": note.date,
        "exists": note.exists,
        "focus": note.focus,
        "schedule": [{"time": e.time, "text": e.text} for e in note.schedule],
        "top3": [{"text": item.text, "done": item.done} for item in note.top3],
        "daily_drivers": [{"text": item.text, "done": item.done} for item in note.daily_drivers],
    }


@router.get("/daily")
def get_daily(date: str = Query("today"), settings=Depends(get_settings)):
    if not settings.vault_readable():
        raise HTTPException(503, detail="vault root is missing or unreadable")

    resolved = _resolve_date(date, settings.hud_tz)
    # Strict format check before it ever reaches a filesystem path -- date
    # is caller-controlled, and an unvalidated "../" would let this read
    # any .md file in the vault, not just daily-notes/*.md.
    if not DATE_RE.match(resolved):
        raise HTTPException(400, detail="date must be 'today' or YYYY-MM-DD")

    note = read_daily_note(settings.vault_root, resolved)
    return _daily_to_dict(note)
