def test_run_log_returns_raw_markdown_body(client, tmp_vault):
    job_id = client.post("/jobs", json={"skill": "metrics-pull"}).json()["id"]
    (tmp_vault / "system" / "runs" / f"{job_id}.md").write_text(
        "# run transcript\n\nline one\nline two\n"
    )

    res = client.get(f"/runs/{job_id}/log")
    assert res.status_code == 200
    assert res.text == "# run transcript\n\nline one\nline two\n"
    assert "text/markdown" in res.headers["content-type"]


def test_run_log_404_when_job_does_not_exist(client):
    res = client.get("/runs/does-not-exist/log")
    assert res.status_code == 404


def test_run_log_404_when_log_file_missing_but_job_exists(client):
    job_id = client.post("/jobs", json={"skill": "metrics-pull"}).json()["id"]
    # No .md written for this job -- matches the real malformed-record class
    # (e.g. vault fixtures 12c0bd9b, 44b06262: JSON present, .md missing).
    res = client.get(f"/runs/{job_id}/log")
    assert res.status_code == 404


def test_run_log_works_for_an_in_progress_job(client, tmp_vault):
    job_id = client.post("/jobs", json={"skill": "metrics-pull"}).json()["id"]
    client.post(f"/jobs/{job_id}/events", json={"status": "running", "ts": "2026-08-09T00:00:00Z"})
    (tmp_vault / "system" / "runs" / f"{job_id}.md").write_text("still running...\n")

    res = client.get(f"/runs/{job_id}/log")
    assert res.status_code == 200
    assert res.text == "still running...\n"
