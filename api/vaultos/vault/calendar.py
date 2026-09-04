import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import icalendar
import recurring_ical_events


@dataclass(frozen=True)
class CalendarEvent:
    summary: str
    start: str  # ISO 8601 -- date-only (YYYY-MM-DD) for all-day events, offset datetime otherwise
    end: str
    all_day: bool


@dataclass(frozen=True)
class CalendarSnapshot:
    pulled_at: str
    events: list[CalendarEvent]


def parse_ical_events(raw: str, tz: str) -> list[CalendarEvent]:
    """Parses raw .ics text and returns events (including expanded recurring
    occurrences) overlapping "today" in the given timezone, sorted by start.
    Never raises -- malformed feeds or individual bad recurrence series
    degrade to an empty list rather than taking down the whole pull."""
    try:
        cal = icalendar.Calendar.from_ical(raw)
    except Exception:
        return []

    zone = ZoneInfo(tz)
    today_start = datetime.now(zone).replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow_start = today_start + timedelta(days=1)

    # tz-aware datetime boundaries, not plain date() objects -- passing
    # naive dates here silently gets interpreted in UTC, which misses any
    # event that's genuinely "today" in `tz` but would fall on a different
    # UTC calendar date near midnight.
    try:
        occurrences = recurring_ical_events.of(cal, skip_bad_series=True).between(
            today_start, tomorrow_start
        )
    except Exception:
        return []

    events: list[CalendarEvent] = []
    for occ in occurrences:
        dtstart = occ.get("DTSTART")
        if dtstart is None:
            continue
        start_val = dtstart.dt
        all_day = not isinstance(start_val, datetime)
        dtend = occ.get("DTEND")
        end_val = dtend.dt if dtend is not None else start_val
        events.append(
            CalendarEvent(
                summary=str(occ.get("SUMMARY", "")),
                start=start_val.isoformat(),
                end=end_val.isoformat(),
                all_day=all_day,
            )
        )
    events.sort(key=lambda e: e.start)
    return events


def read_calendar_today(vault_root: Path) -> CalendarSnapshot | None:
    path = vault_root / "system" / "metrics" / "calendar-today.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    if "pulled_at" not in data:
        return None
    raw_events = data.get("events", [])
    if not isinstance(raw_events, list):
        return None

    # skip individual malformed events rather than discarding the whole
    # day -- one bad entry shouldn't hide every other real event
    events: list[CalendarEvent] = []
    for e in raw_events:
        try:
            events.append(
                CalendarEvent(
                    summary=e["summary"], start=e["start"], end=e["end"], all_day=e["all_day"]
                )
            )
        except (KeyError, TypeError):
            continue
    return CalendarSnapshot(pulled_at=data["pulled_at"], events=events)
