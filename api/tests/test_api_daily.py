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

## Top 3 Priorities
1. [x] Ship Stage 4
2. [ ]
3. [ ]

## Schedule
- 09:00 — Standup

## Daily Drivers
- [ ] Review projects/ for status changes

## Activity Log

## Notes

## EOD Reflection
"""


def _write_note(vault_root, date, content):
    notes_dir = vault_root / "daily-notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    (notes_dir / f"{date}.md").write_text(content)


def test_get_daily_explicit_date(client, tmp_vault):
    _write_note(tmp_vault, "2026-08-08", REAL_NOTE)
    res = client.get("/daily", params={"date": "2026-08-08"})
    assert res.status_code == 200
    body = res.json()
    assert body["date"] == "2026-08-08"
    assert body["exists"] is True
    assert body["focus"] == "Ship Stage 3"
    assert body["schedule"] == [{"time": "09:00", "text": "Standup"}]
    assert body["top3"] == [
        {"text": "Ship Stage 4", "done": True},
        {"text": "", "done": False},
        {"text": "", "done": False},
    ]
    assert body["daily_drivers"] == [{"text": "Review projects/ for status changes", "done": False}]


def test_get_daily_no_note_yet_returns_200_not_404(client):
    res = client.get("/daily", params={"date": "2099-01-01"})
    assert res.status_code == 200
    body = res.json()
    assert body["exists"] is False
    assert body["focus"] is None
    assert body["schedule"] == []
    assert body["top3"] == []
    assert body["daily_drivers"] == []


def test_get_daily_today_resolves_a_concrete_date(client):
    res = client.get("/daily", params={"date": "today"})
    assert res.status_code == 200
    body = res.json()
    assert body["date"] != "today"
    assert len(body["date"]) == 10  # YYYY-MM-DD


def test_get_daily_response_shape(client, tmp_vault):
    _write_note(tmp_vault, "2026-08-08", REAL_NOTE)
    res = client.get("/daily", params={"date": "2026-08-08"})
    assert set(res.json().keys()) == {
        "date",
        "exists",
        "focus",
        "schedule",
        "top3",
        "daily_drivers",
    }
