import json
from pathlib import Path


def _complete_job(client, skill, ts_ok, status="ok", summary="done"):
    job_id = client.post("/jobs", json={"skill": skill}).json()["id"]
    client.post(f"/jobs/{job_id}/events", json={"status": "running", "ts": "2026-08-01T00:00:00Z"})
    client.post(
        f"/jobs/{job_id}/events",
        json={"status": status, "ts": ts_ok, "summary": summary},
    )
    return job_id


def _orphan_job(client, skill, ts):
    job_id = client.post("/jobs", json={"skill": skill}).json()["id"]
    client.post(f"/jobs/{job_id}/events", json={"status": "running", "ts": "2026-08-01T00:00:00Z"})
    client.post(f"/jobs/{job_id}/events", json={"status": "orphaned", "ts": ts})
    return job_id


def _write_inbox_brief(tmp_vault: Path, action_items):
    briefs_dir = tmp_vault / "inbox" / "reports" / "inbox-briefs"
    briefs_dir.mkdir(parents=True, exist_ok=True)
    path = briefs_dir / "2026-08-10-inbox-brief-test.md"
    path.write_text(
        "---\n"
        "date: 2026-08-10\n"
        "skill: inbox-brief\n"
        "tags: [inbox]\n"
        f"action_items: {json.dumps(action_items)}\n"
        "---\n\n# Inbox Brief\n"
    )
    return path


# --- /review-next -- job/document items only (2026-08-11: email and
# calendar-conflict moved out -- see /email-review below) ------------------


def test_review_next_orders_by_fixed_tier_then_newest_first(client, tmp_vault):
    research = _complete_job(client, "acquire", "2026-08-01T00:00:01Z")  # tier 4
    failed = _orphan_job(client, "metrics-pull", "2026-08-01T00:00:02Z")  # tier 1

    res = client.get("/review-next")
    assert res.status_code == 200
    items = res.json()
    tiers = [i["tier"] for i in items]
    assert tiers == sorted(tiers)  # tier ascending
    assert tiers[0] == 1
    assert all(i["item_type"] == "job" for i in items)
    job_ids = {i["item_id"] for i in items}
    assert failed in job_ids
    assert research in job_ids


def test_review_next_excludes_email_and_calendar_conflict_types(client, tmp_vault):
    _complete_job(client, "acquire", "2026-08-01T00:00:01Z")
    _write_inbox_brief(
        tmp_vault,
        [{"id": "thread-1", "sender": "Jane", "subject": "Budget", "priority": "action"}],
    )
    res = client.get("/review-next")
    types = {i["item_type"] for i in res.json()}
    assert types == {"job"}


def test_review_next_excludes_seen_items(client, tmp_vault):
    job_id = _orphan_job(client, "metrics-pull", "2026-08-01T00:00:01Z")
    res = client.get("/review-next")
    assert any(i["item_id"] == job_id for i in res.json())

    mark = client.post("/seen", json={"item_type": "job", "item_id": job_id})
    assert mark.status_code == 204

    res2 = client.get("/review-next")
    assert not any(i["item_id"] == job_id for i in res2.json())


def test_review_next_non_email_items_have_no_url(client, tmp_vault):
    _orphan_job(client, "metrics-pull", "2026-08-01T00:00:02Z")
    res = client.get("/review-next")
    job = next(i for i in res.json() if i["item_type"] == "job")
    assert job["url"] is None


def test_review_next_respects_limit(client, tmp_vault):
    for i in range(3):
        _orphan_job(client, "metrics-pull", f"2026-08-0{i + 1}T00:00:01Z")

    res = client.get("/review-next", params={"limit": 2})
    assert len(res.json()) == 2


def test_review_next_empty_when_nothing_to_show(client):
    res = client.get("/review-next")
    assert res.status_code == 200
    assert res.json() == []


# --- /email-review -- action-needed emails, newest first (2026-08-11) -----


def test_email_review_returns_all_action_items_from_latest_brief(client, tmp_vault):
    # read_latest_action_items() stamps every item in a brief with that
    # file's own frontmatter `date:` (not a per-item timestamp) -- so
    # "newest first" isn't a meaningful cross-item behavior to test here,
    # unlike /review-next's job items which each carry their own real ts.
    _write_inbox_brief(
        tmp_vault,
        [
            {"id": "a", "sender": "A", "subject": "First", "priority": "action"},
            {"id": "b", "sender": "B", "subject": "Second", "priority": "action"},
        ],
    )
    res = client.get("/email-review")
    assert res.status_code == 200
    ids = {i["item_id"] for i in res.json()}
    assert ids == {"a", "b"}


def test_email_review_filters_priority_to_action_only(client, tmp_vault):
    _write_inbox_brief(
        tmp_vault,
        [
            {"id": "a", "sender": "A", "subject": "Action item", "priority": "action"},
            {"id": "f", "sender": "B", "subject": "FYI item", "priority": "fyi"},
            {"id": "s", "sender": "C", "subject": "Skip item", "priority": "skip"},
        ],
    )
    res = client.get("/email-review")
    ids = {i["item_id"] for i in res.json()}
    assert ids == {"a"}


def test_email_review_items_carry_gmail_deep_link(client, tmp_vault):
    _write_inbox_brief(
        tmp_vault,
        [{"id": "18f2a3b1c9d4e5f6", "sender": "Jane", "subject": "Budget", "priority": "action"}],
    )
    res = client.get("/email-review")
    email = res.json()[0]
    assert email["item_type"] == "email"
    assert email["url"] == "https://mail.google.com/mail/u/0/#all/18f2a3b1c9d4e5f6"
    assert email["deliverable_path"] is None


def test_email_review_excludes_seen_items(client, tmp_vault):
    _write_inbox_brief(
        tmp_vault,
        [{"id": "thread-seen", "sender": "A", "subject": "S1", "priority": "action"}],
    )
    res = client.get("/email-review")
    assert any(i["item_id"] == "thread-seen" for i in res.json())

    mark = client.post("/seen", json={"item_type": "email", "item_id": "thread-seen"})
    assert mark.status_code == 204

    res2 = client.get("/email-review")
    assert not any(i["item_id"] == "thread-seen" for i in res2.json())


def test_email_review_respects_limit(client, tmp_vault):
    _write_inbox_brief(
        tmp_vault,
        [
            {"id": f"e{i}", "sender": "A", "subject": f"S{i}", "priority": "action"}
            for i in range(3)
        ],
    )
    res = client.get("/email-review", params={"limit": 2})
    assert len(res.json()) == 2


def test_email_review_empty_when_nothing_to_show(client):
    res = client.get("/email-review")
    assert res.status_code == 200
    assert res.json() == []


# --- /seen idempotency -------------------------------------------------


def test_mark_seen_is_idempotent(client):
    first = client.post("/seen", json={"item_type": "job", "item_id": "x"})
    second = client.post("/seen", json={"item_type": "job", "item_id": "x"})
    assert first.status_code == 204
    assert second.status_code == 204


# --- seen field on existing job endpoints ------------------------------


def test_runs_and_jobs_include_seen_field(client):
    job_id = _complete_job(client, "metrics-pull", "2026-08-01T00:00:01Z")

    run = next(r for r in client.get("/runs").json() if r["id"] == job_id)
    assert run["seen"] is False

    client.post("/seen", json={"item_type": "job", "item_id": job_id})

    run = next(r for r in client.get("/runs").json() if r["id"] == job_id)
    assert run["seen"] is True

    detail = client.get(f"/jobs/{job_id}").json()
    assert detail["seen"] is True
