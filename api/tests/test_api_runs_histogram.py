from datetime import datetime, timedelta, timezone


def _today():
    return datetime.now(timezone.utc).date()


def _complete_job_on(client, skill, days_ago, exit_code=0):
    ts_completed = (_today() - timedelta(days=days_ago)).isoformat() + "T12:00:00Z"
    job_id = client.post("/jobs", json={"skill": skill}).json()["id"]
    client.post(f"/jobs/{job_id}/events", json={"status": "running", "ts": "2020-01-01T00:00:00Z"})
    client.post(
        f"/jobs/{job_id}/events",
        json={"status": "ok" if exit_code == 0 else "error", "ts": ts_completed, "exit_code": exit_code, "summary": "x"},
    )
    return job_id


def test_histogram_zero_fills_days_with_no_runs(client):
    res = client.get("/runs/histogram", params={"days": 5})
    assert res.status_code == 200
    body = res.json()
    assert body["days"] == 5
    assert len(body["buckets"]) == 5
    assert all(b["count"] == 0 for b in body["buckets"])

    expected_dates = [(_today() - timedelta(days=i)).isoformat() for i in reversed(range(5))]
    assert [b["date"] for b in body["buckets"]] == expected_dates


def test_histogram_counts_completed_runs_by_day(client):
    _complete_job_on(client, "metrics-pull", days_ago=0)
    _complete_job_on(client, "acquire", days_ago=0)
    _complete_job_on(client, "metrics-pull", days_ago=2, exit_code=1)  # error still counts

    res = client.get("/runs/histogram", params={"days": 5})
    buckets = {b["date"]: b["count"] for b in res.json()["buckets"]}
    assert buckets[_today().isoformat()] == 2
    assert buckets[(_today() - timedelta(days=2)).isoformat()] == 1
    assert buckets[(_today() - timedelta(days=1)).isoformat()] == 0


def test_histogram_excludes_non_terminal_jobs(client):
    client.post("/jobs", json={"skill": "metrics-pull"})  # stays queued
    res = client.get("/runs/histogram", params={"days": 5})
    assert all(b["count"] == 0 for b in res.json()["buckets"])


def test_histogram_excludes_orphaned_jobs(client):
    job_id = client.post("/jobs", json={"skill": "metrics-pull"}).json()["id"]
    client.post(f"/jobs/{job_id}/events", json={"status": "running", "ts": "2020-01-01T00:00:00Z"})
    client.post(f"/jobs/{job_id}/events", json={"status": "orphaned", "ts": "2020-01-01T00:00:01Z"})

    res = client.get("/runs/histogram", params={"days": 5})
    assert all(b["count"] == 0 for b in res.json()["buckets"])


def test_histogram_default_days_is_30(client):
    res = client.get("/runs/histogram")
    assert res.status_code == 200
    body = res.json()
    assert body["days"] == 30
    assert len(body["buckets"]) == 30
