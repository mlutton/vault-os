def _complete_job(client, skill, ts_ok, exit_code=0, summary="done"):
    job_id = client.post("/jobs", json={"skill": skill}).json()["id"]
    client.post(f"/jobs/{job_id}/events", json={"status": "running", "ts": "2026-08-01T00:00:00Z"})
    client.post(
        f"/jobs/{job_id}/events",
        json={"status": "ok" if exit_code == 0 else "error", "ts": ts_ok, "exit_code": exit_code, "summary": summary},
    )
    return job_id


def test_list_runs_returns_only_ok_and_error(client):
    done = _complete_job(client, "metrics-pull", "2026-08-01T00:00:01Z")
    queued = client.post("/jobs", json={"skill": "acquire"}).json()["id"]

    res = client.get("/runs")
    assert res.status_code == 200
    ids = {job["id"] for job in res.json()}
    assert done in ids
    assert queued not in ids


def test_list_runs_excludes_orphaned(client):
    job_id = client.post("/jobs", json={"skill": "metrics-pull"}).json()["id"]
    client.post(f"/jobs/{job_id}/events", json={"status": "running", "ts": "2026-08-01T00:00:00Z"})
    client.post(f"/jobs/{job_id}/events", json={"status": "orphaned", "ts": "2026-08-01T00:00:01Z"})

    res = client.get("/runs")
    ids = {job["id"] for job in res.json()}
    assert job_id not in ids


def test_list_runs_ordered_newest_completed_first(client):
    older = _complete_job(client, "metrics-pull", "2026-08-01T00:00:01Z")
    newer = _complete_job(client, "metrics-pull", "2026-08-05T00:00:01Z")

    res = client.get("/runs")
    ids = [job["id"] for job in res.json()]
    assert ids.index(newer) < ids.index(older)


def test_list_runs_filters_by_skill(client):
    metrics_run = _complete_job(client, "metrics-pull", "2026-08-01T00:00:01Z")
    acquire_run = _complete_job(client, "acquire", "2026-08-01T00:00:02Z")

    res = client.get("/runs", params={"skill": "metrics-pull"})
    ids = {job["id"] for job in res.json()}
    assert ids == {metrics_run}
    assert acquire_run not in ids


def test_list_runs_filters_by_since(client):
    older = _complete_job(client, "metrics-pull", "2026-08-01T00:00:01Z")
    newer = _complete_job(client, "metrics-pull", "2026-08-05T00:00:01Z")

    res = client.get("/runs", params={"since": "2026-08-03T00:00:00Z"})
    ids = {job["id"] for job in res.json()}
    assert ids == {newer}
    assert older not in ids


def test_list_runs_respects_limit(client):
    for i in range(5):
        _complete_job(client, "metrics-pull", f"2026-08-0{i + 1}T00:00:01Z")

    res = client.get("/runs", params={"limit": 2})
    assert len(res.json()) == 2


def test_list_runs_limit_default_and_max(client):
    res = client.get("/runs", params={"limit": 500})
    assert res.status_code == 422  # exceeds max of 200

    res = client.get("/runs")
    assert res.status_code == 200  # default limit (50) applies with no param


def test_list_runs_includes_label_link_duration_s(client):
    job_id = _complete_job(client, "metrics-pull", "2026-08-01T00:00:11Z")
    res = client.get("/runs")
    run = next(r for r in res.json() if r["id"] == job_id)
    assert run["label"] is None
    assert run["link"] is None
    assert run["duration_s"] == 11
