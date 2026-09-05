import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from vaultos.vault.calendar import (
    CalendarEvent,
    CalendarSnapshot,
    parse_ical_events,
    read_calendar_today,
)

TZ = "America/Chicago"


def _ics(*vevents: str) -> str:
    body = "\n".join(vevents)
    return f"BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//test//test//EN\n{body}\nEND:VCALENDAR\n"


def _vevent(uid: str, dtstart: str, dtend: str, summary: str, rrule: str | None = None) -> str:
    lines = [
        "BEGIN:VEVENT",
        f"UID:{uid}@example.com",
        f"DTSTART:{dtstart}",
        f"DTEND:{dtend}",
    ]
    if rrule:
        lines.append(f"RRULE:{rrule}")
    lines.append(f"SUMMARY:{summary}")
    lines.append("END:VEVENT")
    return "\n".join(lines)


def _today_utc_hhmm(hour: int, minute: int = 0) -> str:
    """Today's date (in TZ) at the given local hour, expressed as a UTC
    DTSTART string -- avoids hardcoding a stale date in fixtures."""
    now_local = datetime.now(ZoneInfo(TZ))
    local_dt = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    utc_dt = local_dt.astimezone(ZoneInfo("UTC"))
    return utc_dt.strftime("%Y%m%dT%H%M%SZ")


def test_parse_ical_events_one_off_event_today():
    ics = _ics(_vevent("1", _today_utc_hhmm(14), _today_utc_hhmm(15), "One-off meeting"))
    events = parse_ical_events(ics, TZ)
    assert len(events) == 1
    assert events[0].summary == "One-off meeting"
    assert events[0].all_day is False


def test_parse_ical_events_recurring_weekly_event_matching_today():
    weekday = datetime.now(ZoneInfo(TZ)).strftime("%A").upper()[:2]
    # a weekly recurrence anchored on today's weekday, starting a week ago
    anchor = (datetime.now(ZoneInfo(TZ)) - timedelta(days=7)).replace(
        hour=9, minute=0, second=0, microsecond=0
    )
    anchor_utc = anchor.astimezone(ZoneInfo("UTC")).strftime("%Y%m%dT%H%M%SZ")
    ics = _ics(
        _vevent("2", anchor_utc, anchor_utc, "Weekly standup", rrule=f"FREQ=WEEKLY;BYDAY={weekday}")
    )
    events = parse_ical_events(ics, TZ)
    assert any(e.summary == "Weekly standup" for e in events)


def test_parse_ical_events_all_day_event_today():
    today = datetime.now(ZoneInfo(TZ)).date()
    tomorrow = today + timedelta(days=1)
    ics = _ics(
        "\n".join(
            [
                "BEGIN:VEVENT",
                "UID:3@example.com",
                f"DTSTART;VALUE=DATE:{today.strftime('%Y%m%d')}",
                f"DTEND;VALUE=DATE:{tomorrow.strftime('%Y%m%d')}",
                "SUMMARY:All day event",
                "END:VEVENT",
            ]
        )
    )
    events = parse_ical_events(ics, TZ)
    assert len(events) == 1
    assert events[0].all_day is True


def test_parse_ical_events_excludes_events_not_today():
    next_week = datetime.now(ZoneInfo(TZ)) + timedelta(days=7)
    start = next_week.astimezone(ZoneInfo("UTC")).strftime("%Y%m%dT%H%M%SZ")
    ics = _ics(_vevent("4", start, start, "Next week's meeting"))
    assert parse_ical_events(ics, TZ) == []


def test_parse_ical_events_late_evening_event_uses_tz_not_utc():
    # Regression: a naive date() boundary silently interprets "today" in
    # UTC, missing an event that's genuinely today in `tz` but a different
    # UTC calendar date near midnight. 23:30 local, near the UTC day flip.
    ics = _ics(_vevent("5", _today_utc_hhmm(23, 30), _today_utc_hhmm(23, 45), "Late evening event"))
    events = parse_ical_events(ics, TZ)
    assert any(e.summary == "Late evening event" for e in events)


def test_parse_ical_events_malformed_ics_returns_empty_not_raise():
    assert parse_ical_events("not a valid ics file", TZ) == []


def test_parse_ical_events_empty_string_returns_empty():
    assert parse_ical_events("", TZ) == []


def test_read_calendar_today_missing_file_returns_none(tmp_path):
    assert read_calendar_today(tmp_path) is None


def test_read_calendar_today_parses_real_shape(tmp_path):
    metrics_dir = tmp_path / "system" / "metrics"
    metrics_dir.mkdir(parents=True)
    (metrics_dir / "calendar-today.json").write_text(
        json.dumps(
            {
                "pulled_at": "2026-08-09T12:00:00Z",
                "events": [
                    {
                        "summary": "Standup",
                        "start": "2026-08-09T09:00:00-05:00",
                        "end": "2026-08-09T09:30:00-05:00",
                        "all_day": False,
                    }
                ],
            }
        )
    )
    snapshot = read_calendar_today(tmp_path)
    assert snapshot == CalendarSnapshot(
        pulled_at="2026-08-09T12:00:00Z",
        events=[
            CalendarEvent(
                summary="Standup",
                start="2026-08-09T09:00:00-05:00",
                end="2026-08-09T09:30:00-05:00",
                all_day=False,
            )
        ],
    )


def test_read_calendar_today_malformed_json_returns_none(tmp_path):
    metrics_dir = tmp_path / "system" / "metrics"
    metrics_dir.mkdir(parents=True)
    (metrics_dir / "calendar-today.json").write_text("{not json")
    assert read_calendar_today(tmp_path) is None


def test_read_calendar_today_missing_keys_returns_none(tmp_path):
    metrics_dir = tmp_path / "system" / "metrics"
    metrics_dir.mkdir(parents=True)
    (metrics_dir / "calendar-today.json").write_text(json.dumps({"events": []}))
    assert read_calendar_today(tmp_path) is None
