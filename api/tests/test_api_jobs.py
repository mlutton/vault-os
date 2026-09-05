import concurrent.futures
import json
import threading


def test_submit_job_writes_intent_and_row(client, tmp_vault):
    res = client.post("/jobs", json={"skill": "metrics-pull"})
    assert res.status_code == 201
    body = res.json()
    assert body["skill"] == "metrics-pull"
    assert body["status"] == "queued"
    assert "runner_alive" in body

    queue_files = list((tmp_vault / "system" / "queue").glob("*.json"))
    assert len(queue_files) == 1
    intent = json.loads(queue_files[0].read_text())
    assert intent["id"] == body["id"]
    assert intent["skill"] == "metrics-pull"
    assert intent["source"] == "api"


def test_submit_job_defaults_source_to_api(client):
    res = client.post("/jobs", json={"skill": "metrics-pull"})
    job_id = res.json()["id"]
    fetched = client.get(f"/jobs/{job_id}")
    assert fetched.json()["source"] == "api"


def test_submit_job_unknown_skill_400(client):
    res = client.post("/jobs", json={"skill": "not-a-skill"})
    assert res.status_code == 400
    assert res.json()["detail"]["field"] == "skill"


def test_submit_job_unknown_skill_400_even_when_vault_unreadable(client, tmp_vault):
    # a bad request is a 400 regardless of vault health -- vault_readable()
    # must not be checked first and mask the real (client) error as a 503
    import shutil

    shutil.rmtree(tmp_vault / "system")
    res = client.post("/jobs", json={"skill": "not-a-skill"})
    assert res.status_code == 400
    assert res.json()["detail"]["field"] == "skill"


def test_submit_job_missing_required_arg_400(client):
    res = client.post("/jobs", json={"skill": "deep-research", "args": {}})
    assert res.status_code == 400
    assert res.json()["detail"]["field"] == "topic"


def test_submit_job_unknown_arg_key_400(client):
    res = client.post(
        "/jobs", json={"skill": "deep-research", "args": {"topic": "x", "bogus": "y"}}
    )
    assert res.status_code == 400
    assert res.json()["detail"]["field"] == "bogus"


def test_get_job_not_found_404(client):
    res = client.get("/jobs/does-not-exist")
    assert res.status_code == 404


def test_list_jobs_returns_only_queued_and_running(client):
    queued = client.post("/jobs", json={"skill": "metrics-pull"}).json()["id"]
    running = client.post("/jobs", json={"skill": "acquire"}).json()["id"]
    client.post(f"/jobs/{running}/events", json={"status": "running", "ts": "2026-08-09T00:00:01Z"})
    terminal = client.post("/jobs", json={"skill": "metrics-pull"}).json()["id"]
    client.post(
        f"/jobs/{terminal}/events", json={"status": "running", "ts": "2026-08-09T00:00:02Z"}
    )
    client.post(
        f"/jobs/{terminal}/events",
        json={"status": "ok", "ts": "2026-08-09T00:00:03Z", "exit_code": 0},
    )

    res = client.get("/jobs")
    assert res.status_code == 200
    ids = {job["id"] for job in res.json()}
    assert ids == {queued, running}


def test_list_jobs_sorted_by_last_event_ts_descending(client):
    first = client.post("/jobs", json={"skill": "metrics-pull"}).json()["id"]
    second = client.post("/jobs", json={"skill": "acquire"}).json()["id"]
    # Advance "first" after "second" was created, so its last_event_ts is now the newest.
    # A far-future timestamp guarantees this regardless of the real wall-clock time
    # "second" was created at (ts_queued uses real time, not test-supplied time).
    client.post(f"/jobs/{first}/events", json={"status": "running", "ts": "2099-01-01T00:00:00Z"})

    res = client.get("/jobs")
    ids = [job["id"] for job in res.json()]
    assert ids[0] == first
    assert ids[1] == second


def test_list_jobs_empty_when_none_queued_or_running(client):
    res = client.get("/jobs")
    assert res.status_code == 200
    assert res.json() == []


def test_events_endpoint_advances_status(client):
    submitted = client.post("/jobs", json={"skill": "metrics-pull"}).json()
    job_id = submitted["id"]

    res = client.post(
        f"/jobs/{job_id}/events",
        json={"status": "running", "ts": "2026-08-09T00:00:01Z", "pid": 555},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "running"
    assert res.json()["runner_pid"] == 555

    res = client.post(
        f"/jobs/{job_id}/events",
        json={
            "status": "ok",
            "ts": "2026-08-09T00:00:05Z",
            "exit_code": 0,
            "summary": "done",
            "deliverable_path": "inbox/reports/metrics-pull/2026-08-09-abc.md",
            "md_path": "system/runs/abc.md",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["deliverables"] == ["inbox/reports/metrics-pull/2026-08-09-abc.md"]


def test_job_response_includes_label_link_duration_s(client, tmp_vault):
    (tmp_vault / "inbox").mkdir()
    (tmp_vault / "inbox" / "x.md").write_text('---\nlink: "https://example.com/out"\n---\n')

    job_id = client.post(
        "/jobs",
        json={"skill": "metrics-pull"},
    ).json()["id"]
    client.post(f"/jobs/{job_id}/events", json={"status": "running", "ts": "2026-08-09T00:00:01Z"})
    res = client.post(
        f"/jobs/{job_id}/events",
        json={
            "status": "ok",
            "ts": "2026-08-09T00:00:11Z",
            "exit_code": 0,
            "summary": "done",
            "deliverable_path": "inbox/x.md",
        },
    )
    body = res.json()
    assert body["label"] is None  # not a voice-ask
    assert body["link"] == "https://example.com/out"
    assert body["duration_s"] == 10


def test_job_response_duration_s_null_when_still_running(client):
    job_id = client.post("/jobs", json={"skill": "metrics-pull"}).json()["id"]
    client.post(f"/jobs/{job_id}/events", json={"status": "running", "ts": "2026-08-09T00:00:01Z"})
    res = client.get(f"/jobs/{job_id}")
    assert res.json()["duration_s"] is None


def test_job_response_link_null_when_not_ok(client, tmp_vault):
    (tmp_vault / "inbox").mkdir()
    (tmp_vault / "inbox" / "x.md").write_text('---\nlink: "https://example.com/out"\n---\n')
    job_id = client.post("/jobs", json={"skill": "metrics-pull"}).json()["id"]
    res = client.post(
        f"/jobs/{job_id}/events",
        json={
            "status": "error",
            "ts": "2026-08-09T00:00:01Z",
            "exit_code": 1,
            "deliverable_path": "inbox/x.md",
        },
    )
    assert res.json()["link"] is None


def test_events_endpoint_creates_job_never_submitted(client):
    res = client.post(
        "/jobs/never-seen/events",
        json={
            "status": "running",
            "ts": "2026-08-09T00:00:01Z",
            "skill": "acquire",
            "args": {},
            "source": "obsidian",
            "pid": 42,
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["skill"] == "acquire"
    assert body["source"] == "obsidian"
    assert body["engine"] == "claude"


def test_events_endpoint_returns_404_when_it_cannot_create(client):
    res = client.post(
        "/jobs/never-seen/events",
        json={"status": "ok", "ts": "2026-08-09T00:00:01Z", "exit_code": 0},
    )
    assert res.status_code == 404


def test_events_endpoint_invalid_status_422_and_no_job_created(client):
    res = client.post(
        "/jobs/never-seen-bogus/events",
        json={
            "status": "bogus",
            "ts": "2026-08-09T00:00:01Z",
            "skill": "acquire",
            "args": {},
            "source": "obsidian",
        },
    )
    assert res.status_code == 422

    # No job row should have been created from the rejected event.
    fetched = client.get("/jobs/never-seen-bogus")
    assert fetched.status_code == 404


def test_events_endpoint_invalid_status_422_on_existing_job(client):
    submitted = client.post("/jobs", json={"skill": "metrics-pull"}).json()
    job_id = submitted["id"]

    res = client.post(
        f"/jobs/{job_id}/events",
        json={"status": "bogus", "ts": "2026-08-09T00:00:01Z"},
    )
    assert res.status_code == 422

    # The existing job's status must be unaffected by the rejected event.
    fetched = client.get(f"/jobs/{job_id}")
    assert fetched.json()["status"] == "queued"


def test_concurrent_gets_and_events_via_real_app_no_torn_state(client):
    """Reproduces the re-reviewer's methodology for the unlocked-get_job gap: many
    threads hitting GET /jobs/{id} and POST /jobs/{id}/events concurrently through the
    real FastAPI app (TestClient), for a batch of jobs, all sharing one app.state.conn.

    Before store.get_job acquired the module lock, this reliably produced
    sqlite3.InterfaceError ("bad parameter or other API misuse") surfacing as 500s from
    POST, and false 404s from GET for jobs that definitely exist (readers racing a
    writer's uncommitted/interleaved transaction on the same connection object). Asserts
    zero non-2xx responses and zero false 404s across many concurrent calls.
    """
    job_ids = []
    for _ in range(30):
        res = client.post("/jobs", json={"skill": "metrics-pull"})
        assert res.status_code == 201
        job_ids.append(res.json()["id"])

    failures = []
    lock = threading.Lock()

    def hammer(job_id, i):
        try:
            if i % 2 == 0:
                res = client.get(f"/jobs/{job_id}")
                if res.status_code != 200:
                    with lock:
                        failures.append(("GET", job_id, i, res.status_code, res.text))
            else:
                res = client.post(
                    f"/jobs/{job_id}/events",
                    json={"status": "running", "ts": f"2026-08-09T00:00:{i % 60:02d}Z", "pid": i},
                )
                if res.status_code != 200:
                    with lock:
                        failures.append(("POST", job_id, i, res.status_code, res.text))
        except Exception as exc:  # noqa: BLE001 - we want to catch and report every failure mode
            with lock:
                failures.append(("EXCEPTION", job_id, i, repr(exc), ""))

    tasks = [(job_id, i) for job_id in job_ids for i in range(20)]

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
        list(executor.map(lambda args: hammer(*args), tasks))

    assert failures == []


# --- CHAIN_MAP auto-dispatch (2026-08-11) ---------------------------------


def _complete(client, job_id, status, ts="2026-08-01T00:00:01Z"):
    client.post(f"/jobs/{job_id}/events", json={"status": "running", "ts": "2026-08-01T00:00:00Z"})
    client.post(
        f"/jobs/{job_id}/events",
        json={
            "status": status,
            "ts": ts,
            "exit_code": 0 if status == "ok" else 1,
            "summary": "done",
        },
    )


def test_acquire_ok_auto_dispatches_daily_topic_digest(client, tmp_vault):
    acquire_id = client.post("/jobs", json={"skill": "acquire"}).json()["id"]
    _complete(client, acquire_id, "ok")

    # acquire's own job is terminal (ok), so /jobs (queued+running only)
    # should show exactly the chained follow-up, not acquire itself.
    active = client.get("/jobs").json()
    chained = [j for j in active if j["skill"] == "daily-topic-digest"]
    assert len(chained) == 1
    assert chained[0]["source"] == f"chain:acquire:{acquire_id}"
    assert chained[0]["status"] == "queued"

    queue_files = list((tmp_vault / "system" / "queue").glob("*.json"))
    assert any(json.loads(f.read_text())["skill"] == "daily-topic-digest" for f in queue_files)


def test_duplicate_ok_event_does_not_double_dispatch(client, tmp_vault):
    # A retried/duplicate terminal event (e.g. the runner re-POSTing after a
    # dropped response) must not fire the chained follow-up twice.
    acquire_id = client.post("/jobs", json={"skill": "acquire"}).json()["id"]
    _complete(client, acquire_id, "ok")
    client.post(
        f"/jobs/{acquire_id}/events",
        json={"status": "ok", "ts": "2026-08-01T00:00:01Z", "exit_code": 0, "summary": "done"},
    )

    active = client.get("/jobs").json()
    chained = [j for j in active if j["skill"] == "daily-topic-digest"]
    assert len(chained) == 1

    queue_files = [
        f
        for f in (tmp_vault / "system" / "queue").glob("*.json")
        if json.loads(f.read_text())["skill"] == "daily-topic-digest"
    ]
    assert len(queue_files) == 1


def test_acquire_error_does_not_dispatch_daily_topic_digest(client):
    acquire_id = client.post("/jobs", json={"skill": "acquire"}).json()["id"]
    _complete(client, acquire_id, "error")

    active = client.get("/jobs").json()
    assert not [j for j in active if j["skill"] == "daily-topic-digest"]


def test_non_chained_skill_completion_dispatches_nothing(client):
    job_id = client.post("/jobs", json={"skill": "metrics-pull"}).json()["id"]
    _complete(client, job_id, "ok")

    active = client.get("/jobs").json()
    assert active == []
