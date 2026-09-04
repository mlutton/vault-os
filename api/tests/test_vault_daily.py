from vaultos.vault.daily import DailyNote, ScheduleEntry, Top3Item, read_daily_note

REAL_NOTE = """---
date: 2026-08-08
schema_version: 1
focus: "Ship Stage 3"
top3: ["", "", ""]
top3_done: [false, false, false]
effort: null
focus_blocks: null
posts_shipped: {}
videos_shipped_today: 0
---

# 2026-08-08

## Current Focus

This prose section should never be used as the source of truth for focus.

## Top 3 Priorities
1. [x] Ship Stage 4
2. [ ] Review PR
3. [ ]

## Leadership And Payments News Brief

## Schedule
- 09:00 — Standup
- 14:30 — Deep work block

## Daily Drivers
- [ ] Run leadership-payments-brief
- [x] Inbox triage (Gmail) — 31 thread(s), see [brief](inbox/reports/inbox-briefs/2026-08-08.md)

## Activity Log

## Notes

## EOD Reflection
"""

EMPTY_NOTE = """---
date: 2026-08-09
schema_version: 1
focus: ""
top3: ["", "", ""]
top3_done: [false, false, false]
effort: null
focus_blocks: null
posts_shipped: {}
videos_shipped_today: 0
---

# 2026-08-09

## Current Focus

## Top 3 Priorities
1. [ ]
2. [ ]
3. [ ]

## Schedule

## Daily Drivers

## Activity Log

## Notes

## EOD Reflection
"""


def _write_note(vault_root, date, content):
    notes_dir = vault_root / "daily-notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    (notes_dir / f"{date}.md").write_text(content)


def test_read_daily_note_missing_file_returns_empty_not_none(tmp_path):
    note = read_daily_note(tmp_path, "2026-08-09")
    assert note == DailyNote(
        date="2026-08-09", exists=False, focus=None, schedule=[], top3=[], daily_drivers=[]
    )


def test_read_daily_note_parses_focus_from_frontmatter(tmp_path):
    _write_note(tmp_path, "2026-08-08", REAL_NOTE)
    note = read_daily_note(tmp_path, "2026-08-08")
    assert note.exists is True
    assert note.focus == "Ship Stage 3"


def test_read_daily_note_parses_schedule_section(tmp_path):
    _write_note(tmp_path, "2026-08-08", REAL_NOTE)
    note = read_daily_note(tmp_path, "2026-08-08")
    assert note.schedule == [
        ScheduleEntry(time="09:00", text="Standup"),
        ScheduleEntry(time="14:30", text="Deep work block"),
    ]


def test_read_daily_note_empty_focus_is_none(tmp_path):
    _write_note(tmp_path, "2026-08-09", EMPTY_NOTE)
    note = read_daily_note(tmp_path, "2026-08-09")
    assert note.focus is None
    assert note.schedule == []


def test_read_daily_note_bare_yaml_null_focus_is_none_not_string(tmp_path):
    note_text = REAL_NOTE.replace('focus: "Ship Stage 3"', "focus: null")
    _write_note(tmp_path, "2026-08-08", note_text)
    note = read_daily_note(tmp_path, "2026-08-08")
    assert note.focus is None


def test_read_daily_note_tolerates_obsolete_leadership_payments_heading(tmp_path):
    # 2026-08-08 note above carries the now-retired heading — this asserts the
    # parser doesn't choke on it (schema doc: "expected, don't backfill it").
    _write_note(tmp_path, "2026-08-08", REAL_NOTE)
    note = read_daily_note(tmp_path, "2026-08-08")
    assert note.exists is True


def test_read_daily_note_parses_top3_from_body_checkboxes(tmp_path):
    _write_note(tmp_path, "2026-08-08", REAL_NOTE)
    note = read_daily_note(tmp_path, "2026-08-08")
    assert note.top3 == [
        Top3Item(text="Ship Stage 4", done=True),
        Top3Item(text="Review PR", done=False),
        Top3Item(text="", done=False),
    ]


def test_read_daily_note_empty_top3_still_has_three_placeholder_slots(tmp_path):
    _write_note(tmp_path, "2026-08-09", EMPTY_NOTE)
    note = read_daily_note(tmp_path, "2026-08-09")
    assert note.top3 == [
        Top3Item(text="", done=False),
        Top3Item(text="", done=False),
        Top3Item(text="", done=False),
    ]


def test_read_daily_note_top3_not_from_frontmatter_arrays(tmp_path):
    # Frontmatter's top3/top3_done carry different values than the body --
    # the body checkboxes must win, not the frontmatter write-side cache.
    note_text = REAL_NOTE.replace(
        'top3: ["", "", ""]\ntop3_done: [false, false, false]',
        'top3: ["stale a", "stale b", "stale c"]\ntop3_done: [true, true, true]',
    )
    _write_note(tmp_path, "2026-08-08", note_text)
    note = read_daily_note(tmp_path, "2026-08-08")
    assert note.top3[0] == Top3Item(text="Ship Stage 4", done=True)
    assert note.top3[1] == Top3Item(text="Review PR", done=False)


def test_read_daily_note_parses_daily_drivers_as_dash_bullets(tmp_path):
    # Real production daily notes use dash-bullet checkboxes for Daily
    # Drivers ("- [ ] text"), NOT the numbered checkboxes the frozen schema
    # doc's "same regex as Top 3" line claims -- verified against real
    # 2026-08-08/2026-08-09 vault notes. This is the format that must parse.
    _write_note(tmp_path, "2026-08-08", REAL_NOTE)
    note = read_daily_note(tmp_path, "2026-08-08")
    assert note.daily_drivers == [
        Top3Item(text="Run leadership-payments-brief", done=False),
        Top3Item(
            text="Inbox triage (Gmail) — 31 thread(s), see [brief](inbox/reports/inbox-briefs/2026-08-08.md)",
            done=True,
        ),
    ]


def test_read_daily_note_daily_drivers_numbered_checkboxes_do_not_match(tmp_path):
    # Explicit regression guard against silently reverting to the schema
    # doc's stated (but factually wrong) numbered-checkbox format.
    note_text = REAL_NOTE.replace(
        "- [ ] Run leadership-payments-brief\n"
        "- [x] Inbox triage (Gmail) — 31 thread(s), see [brief](inbox/reports/inbox-briefs/2026-08-08.md)",
        "1. [ ] Run leadership-payments-brief",
    )
    _write_note(tmp_path, "2026-08-08", note_text)
    note = read_daily_note(tmp_path, "2026-08-08")
    assert note.daily_drivers == []


def test_read_daily_note_empty_daily_drivers_section_returns_empty_list(tmp_path):
    _write_note(tmp_path, "2026-08-09", EMPTY_NOTE)
    note = read_daily_note(tmp_path, "2026-08-09")
    assert note.daily_drivers == []


def test_read_daily_note_blank_daily_driver_with_no_trailing_space_still_matches(tmp_path):
    # DAILY_DRIVERS_LINE_RE previously required a trailing \s+ after the
    # checkbox, so a bare "- [ ]" placeholder (no trailing space) silently
    # failed to match while the equivalent Top3 line parsed fine -- both now
    # use the same optional-whitespace shape.
    note_text = REAL_NOTE.replace(
        "- [ ] Run leadership-payments-brief\n"
        "- [x] Inbox triage (Gmail) — 31 thread(s), see [brief](inbox/reports/inbox-briefs/2026-08-08.md)",
        "- [ ]",
    )
    _write_note(tmp_path, "2026-08-08", note_text)
    note = read_daily_note(tmp_path, "2026-08-08")
    assert note.daily_drivers == [Top3Item(text="", done=False)]
