from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def utcnow_z() -> str:
    """Current UTC time as ISO 8601 with a 'Z' suffix, matching the format used
    throughout the vault's own files and every job_events timestamp."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def today_in_tz(tz: str) -> str:
    """Today's date (YYYY-MM-DD) in the given IANA timezone -- the single
    place "what day is it" is computed, so HUD_TZ's day-boundary rule only
    needs fixing in one spot."""
    return datetime.now(ZoneInfo(tz)).date().isoformat()
