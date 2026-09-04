import json
from pathlib import Path

from vaultos.vault.inbox_brief import read_latest_action_items


def _write(dir_path: Path, name: str, body: str) -> Path:
    dir_path.mkdir(parents=True, exist_ok=True)
    path = dir_path / name
    path.write_text(body)
    return path


def test_missing_directory_returns_empty(tmp_path):
    assert read_latest_action_items(tmp_path) == []


def test_missing_action_items_field_returns_empty(tmp_path):
    briefs = tmp_path / "inbox" / "reports" / "inbox-briefs"
    _write(briefs, "2026-08-10.md", "---\ndate: 2026-08-10\nskill: inbox-brief\n---\nbody\n")
    assert read_latest_action_items(tmp_path) == []


def test_malformed_json_in_action_items_returns_empty(tmp_path):
    briefs = tmp_path / "inbox" / "reports" / "inbox-briefs"
    _write(briefs, "2026-08-10.md", "---\ndate: 2026-08-10\naction_items: [{not valid json}]\n---\n")
    assert read_latest_action_items(tmp_path) == []


def test_picks_most_recently_modified_file(tmp_path):
    briefs = tmp_path / "inbox" / "reports" / "inbox-briefs"
    older = _write(
        briefs, "old.md",
        '---\ndate: 2026-08-09\naction_items: [{"id": "old", "sender": "A", "subject": "old", "priority": "action"}]\n---\n',
    )
    newer = _write(
        briefs, "new.md",
        '---\ndate: 2026-08-10\naction_items: [{"id": "new", "sender": "B", "subject": "new", "priority": "action"}]\n---\n',
    )
    import os
    import time

    old_time = time.time() - 100
    os.utime(older, (old_time, old_time))

    items = read_latest_action_items(tmp_path)
    assert [i.id for i in items] == ["new"]


def test_filters_out_non_action_priority(tmp_path):
    briefs = tmp_path / "inbox" / "reports" / "inbox-briefs"
    items = [
        {"id": "a", "sender": "A", "subject": "s1", "priority": "action"},
        {"id": "b", "sender": "B", "subject": "s2", "priority": "fyi"},
        {"id": "c", "sender": "C", "subject": "s3", "priority": "skip"},
    ]
    _write(briefs, "2026-08-10.md", f"---\ndate: 2026-08-10\naction_items: {json.dumps(items)}\n---\n")

    result = read_latest_action_items(tmp_path)
    assert [i.id for i in result] == ["a"]


def test_entry_missing_required_field_is_skipped_not_fatal(tmp_path):
    briefs = tmp_path / "inbox" / "reports" / "inbox-briefs"
    items = [
        {"id": "good", "sender": "A", "subject": "s1", "priority": "action"},
        {"id": "bad", "priority": "action"},  # missing sender/subject
    ]
    _write(briefs, "2026-08-10.md", f"---\ndate: 2026-08-10\naction_items: {json.dumps(items)}\n---\n")

    result = read_latest_action_items(tmp_path)
    assert [i.id for i in result] == ["good"]
