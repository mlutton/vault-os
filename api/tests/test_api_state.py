import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

DAILY_NOTE = """---
date: 2026-08-08
schema_version: 1
focus: "Ship Stage 4"
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
- [x] Inbox triage
"""


def _complete_job(client, skill, ts_ok, exit_code=0, summary="done"):
    job_id = client.post("/jobs", json={"skill": skill}).json()["id"]
    client.post(f"/jobs/{job_id}/events", json={"status": "running", "ts": "2026-08-01T00:00:00Z"})
    client.post(
        f"/jobs/{job_id}/events",
        json={
            "status": "ok" if exit_code == 0 else "error",
            "ts": ts_ok,
            "exit_code": exit_code,
            "summary": summary,
        },
    )
    return job_id


def test_get_state_response_shape(client):
    res = client.get("/state")
    assert res.status_code == 200
    body = res.json()
    assert set(body.keys()) == {
        "generated_at",
        "vault_root",
        "metrics",
        "runner",
        "daily",
        "runs",
        "queue",
        "morning",
        "skill_etas",
        "lane_briefs",
    }


def test_get_state_no_lane_highlights_key_at_all(client):
    res = client.get("/state")
    assert "lane_highlights" not in res.json()


def test_get_state_503_when_vault_unreadable(client, tmp_vault):
    import shutil

    shutil.rmtree(tmp_vault / "system")
    res = client.get("/state")
    assert res.status_code == 503


def test_get_state_daily_is_always_today(client, tmp_vault):
    today = datetime.now(ZoneInfo("America/Chicago")).date().isoformat()
    notes_dir = tmp_vault / "daily-notes"
    notes_dir.mkdir(parents=True)
    (notes_dir / f"{today}.md").write_text(DAILY_NOTE.replace("2026-08-08", today, 1))

    res = client.get("/state")
    body = res.json()
    assert body["daily"]["date"] == today
    assert body["daily"]["exists"] is True
    assert body["daily"]["top3"][0] == {"text": "Ship Stage 4", "done": True}
    assert body["daily"]["daily_drivers"] == [{"text": "Inbox triage", "done": True}]


def test_get_state_runs_capped_at_eight(client):
    for i in range(10):
        _complete_job(client, "metrics-pull", f"2026-08-01T00:{i:02d}:00Z")
    res = client.get("/state")
    assert len(res.json()["runs"]) == 8


def test_get_state_queue_only_includes_queued_not_running(client):
    queued = client.post("/jobs", json={"skill": "metrics-pull"}).json()["id"]
    running = client.post("/jobs", json={"skill": "acquire"}).json()["id"]
    client.post(f"/jobs/{running}/events", json={"status": "running", "ts": "2026-08-09T00:00:01Z"})

    res = client.get("/state")
    ids = {j["id"] for j in res.json()["queue"]}
    assert ids == {queued}


def test_get_state_runs_includes_currently_running_jobs(client):
    # a job mid-execution must be visible SOMEWHERE in /state -- it has
    # already left `queue` (queued-only) but hasn't reached ok/error yet
    running = client.post("/jobs", json={"skill": "acquire"}).json()["id"]
    client.post(f"/jobs/{running}/events", json={"status": "running", "ts": "2026-08-09T00:00:01Z"})

    res = client.get("/state")
    body = res.json()
    ids = {j["id"] for j in body["runs"]}
    assert running in ids
    assert not any(j["id"] == running for j in body["queue"])


def test_get_state_running_jobs_come_before_completed_in_runs(client):
    _complete_job(client, "metrics-pull", "2026-08-01T00:00:00Z")
    running = client.post("/jobs", json={"skill": "acquire"}).json()["id"]
    client.post(f"/jobs/{running}/events", json={"status": "running", "ts": "2026-08-09T00:00:01Z"})

    res = client.get("/state")
    assert res.json()["runs"][0]["id"] == running


def test_get_state_metrics_and_runner_match_individual_endpoints(client, tmp_vault):
    system = tmp_vault / "system"
    ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    (system / "runner-status.json").write_text(
        json.dumps({"ts": ts, "pid": 1, "active": 0, "pending": 0})
    )
    metrics_dir = system / "metrics"
    metrics_dir.mkdir(parents=True, exist_ok=True)
    (metrics_dir / "metrics.csv").write_text(
        "timestamp,source,metric,value,status,error\n2026-08-09T08:00:00Z,vault,new_files_24h,5.0,ok,\n"
    )

    state = client.get("/state").json()
    runner = client.get("/runner").json()
    metrics = client.get("/metrics").json()
    # heartbeat_age_s ticks between the two requests -- compare everything else exactly.
    state_runner, runner_body = dict(state["runner"]), dict(runner)
    del state_runner["heartbeat_age_s"], runner_body["heartbeat_age_s"]
    assert state_runner == runner_body
    assert state["metrics"] == metrics


def test_get_state_morning_null_when_not_run_yet(client):
    assert client.get("/state").json()["morning"] is None


def test_get_state_lane_briefs_empty_when_none_today(client):
    assert client.get("/state").json()["lane_briefs"] == []


def test_get_state_skill_etas_from_ok_runs(client):
    _complete_job(client, "metrics-pull", "2026-08-01T00:00:10Z")
    etas = client.get("/state").json()["skill_etas"]
    assert etas == {"metrics-pull": 10}
