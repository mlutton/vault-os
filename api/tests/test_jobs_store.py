import concurrent.futures

import pytest

from vaultos.db.conn import connect
from vaultos.jobs import store


@pytest.fixture
def conn(tmp_path):
    return connect(tmp_path / "vaultos.db")


def test_create_and_get_job(conn):
    job = store.create_job(
        conn, job_id="j1", skill="metrics-pull", args={}, source="api",
        engine="claude", ts_queued="2026-08-09T00:00:00Z",
    )
    assert job.status == "queued"
    assert job.deliverables == []
    fetched = store.get_job(conn, "j1")
    assert fetched.id == "j1"


def test_get_job_missing_returns_none(conn):
    assert store.get_job(conn, "nope") is None


def test_create_job_chain_source_is_idempotent(conn):
    first = store.create_job(
        conn, job_id="j1", skill="daily-topic-digest", args={}, source="chain:acquire:parent-1",
        engine="claude", ts_queued="2026-08-09T00:00:00Z",
    )
    assert first.id == "j1"

    second = store.create_job(
        conn, job_id="j2", skill="daily-topic-digest", args={}, source="chain:acquire:parent-1",
        engine="claude", ts_queued="2026-08-09T00:00:05Z",
    )
    # Same parent -> same winning job returned, no second row created.
    assert second.id == "j1"
    assert store.get_job(conn, "j2") is None


def test_create_job_non_chain_source_never_dedupes(conn):
    store.create_job(
        conn, job_id="j1", skill="metrics-pull", args={}, source="api",
        engine="claude", ts_queued="2026-08-09T00:00:00Z",
    )
    second = store.create_job(
        conn, job_id="j2", skill="metrics-pull", args={}, source="api",
        engine="claude", ts_queued="2026-08-09T00:00:05Z",
    )
    # Ordinary jobs share source values by design -- must never collide.
    assert second.id == "j2"
    assert store.get_job(conn, "j1") is not None


def test_duration_s_returns_none_on_mixed_tz_awareness_instead_of_raising(conn):
    # one naive, one aware timestamp -- fromisoformat() parses both fine, but
    # the subtraction itself raises TypeError; duration_s() must swallow that
    # too, not just ValueError from parsing.
    assert store.duration_s("2026-08-09T00:00:00", "2026-08-09T00:00:10Z") is None


def test_apply_event_advances_status(conn):
    store.create_job(
        conn, job_id="j1", skill="metrics-pull", args={}, source="api",
        engine="claude", ts_queued="t0",
    )
    job = store.apply_event(conn, job_id="j1", status="running", ts="t1", received_at="t1", pid=999)
    assert job.status == "running"
    assert job.runner_pid == 999

    job = store.apply_event(
        conn, job_id="j1", status="ok", ts="t2", received_at="t2",
        exit_code=0, summary="done", deliverable_path="inbox/x.md", md_path="system/runs/j1.md",
    )
    assert job.status == "ok"
    assert job.exit_code == 0
    assert job.deliverables == ["inbox/x.md"]


def test_apply_event_ignores_regression(conn):
    store.create_job(
        conn, job_id="j1", skill="metrics-pull", args={}, source="api",
        engine="claude", ts_queued="t0",
    )
    store.apply_event(conn, job_id="j1", status="ok", ts="t2", received_at="t2", exit_code=0, summary="done")
    job = store.apply_event(conn, job_id="j1", status="running", ts="t1", received_at="t1")
    assert job.status == "ok"


def test_apply_event_orphaned_is_superseded_by_ok(conn):
    store.create_job(
        conn, job_id="j1", skill="metrics-pull", args={}, source="api",
        engine="claude", ts_queued="t0",
    )
    store.apply_event(conn, job_id="j1", status="running", ts="t1", received_at="t1")
    store.apply_event(conn, job_id="j1", status="orphaned", ts="t2", received_at="t2")
    job = store.apply_event(conn, job_id="j1", status="ok", ts="t3", received_at="t3", exit_code=0, summary="done")
    assert job.status == "ok"


def test_apply_event_creates_job_when_unseen(conn):
    job = store.apply_event(
        conn, job_id="j-unseen", status="running", ts="t1", received_at="t1",
        skill="ai-wire", args={}, source="obsidian", engine="claude", pid=42,
    )
    assert job is not None
    assert job.skill == "ai-wire"
    assert job.source == "obsidian"
    assert job.status == "running"
    assert job.ts_started == "t1"


def test_apply_event_cannot_create_job_without_skill(conn):
    job = store.apply_event(conn, job_id="j-unseen", status="ok", ts="t1", received_at="t1", exit_code=0)
    assert job is None


def test_apply_event_is_idempotent_and_order_independent(tmp_path):
    events = [
        dict(status="queued", ts="t0", received_at="t0", skill="metrics-pull", args={}, source="api", engine="claude"),
        dict(status="running", ts="t1", received_at="t1", pid=1),
        dict(status="ok", ts="t2", received_at="t2", exit_code=0, summary="done"),
    ]

    conn_forward = connect(tmp_path / "forward.db")
    for e in events:
        store.apply_event(conn_forward, job_id="j1", **e)
    forward = store.get_job(conn_forward, "j1")

    conn_reverse = connect(tmp_path / "reverse.db")
    for e in list(reversed(events)) + events:
        store.apply_event(conn_reverse, job_id="j1", **e)
    reverse = store.get_job(conn_reverse, "j1")

    assert forward.status == reverse.status == "ok"
    assert forward.exit_code == reverse.exit_code == 0
    assert forward.ts_started == reverse.ts_started == "t1"
    assert forward.ts_completed == reverse.ts_completed == "t2"


def test_apply_event_backfills_running_metadata_after_terminal_event(conn):
    """Regression test: a late "running" event must not lose its payload.

    runner.js posts "running" and its terminal report close together, unawaited, over
    separate HTTP requests with no ordering guarantee. If the terminal event's HTTP
    request is processed first, the running event's ts_started/pid/md_path/deliverable_path
    must still be captured (first-write-wins) rather than silently discarded because the
    status-rank gate says "running" doesn't outrank "error".
    """
    store.create_job(
        conn, job_id="j1", skill="metrics-pull", args={}, source="api",
        engine="claude", ts_queued="t0",
    )
    job = store.apply_event(conn, job_id="j1", status="error", ts="t2", received_at="t2", exit_code=1)
    assert job.status == "error"

    job = store.apply_event(
        conn, job_id="j1", status="running", ts="t1", received_at="t1",
        pid=4242, md_path="system/runs/j1.md", deliverable_path="inbox/x.md",
    )
    # Status must NOT regress back to "running" -- it stays at the terminal value.
    assert job.status == "error"
    assert job.exit_code == 1
    # But the metadata carried by the late "running" event must still be captured.
    assert job.ts_started == "t1"
    assert job.runner_pid == 4242
    assert job.md_path == "system/runs/j1.md"
    assert job.deliverables == ["inbox/x.md"]


def test_apply_event_concurrent_calls_converge_to_consistent_state(tmp_path):
    """Regression test for Finding 3: apply_event's read-modify-write must be atomic
    across threads sharing the connection, not just correct when called sequentially.

    All FastAPI job routes are sync `def` handlers, so Starlette runs them concurrently
    across a threadpool, all sharing the single app.state.conn. apply_event does a SELECT
    then an UPDATE with no transaction boundary tying them together, so two concurrent
    calls for the same job could both read the same "before" state, both decide to apply,
    and interleave their writes -- leaving the row on a stale/torn intermediate status.
    This fires many overlapping apply_event calls for the *same* job_id from multiple
    threads against one shared connection (the exact pattern the real app has) and
    asserts the row converges to the fully-applied terminal state no matter the
    interleaving. Without the store.py lock, this is capable of catching a torn row.
    """
    conn = connect(tmp_path / "concurrent.db")
    store.create_job(
        conn, job_id="j1", skill="metrics-pull", args={}, source="api",
        engine="claude", ts_queued="t0",
    )

    events = [
        dict(status="queued", ts="t0", received_at="t0"),
        dict(status="running", ts="t1", received_at="t1", pid=1),
        dict(status="ok", ts="t2", received_at="t2", exit_code=0, summary="done"),
    ]
    # Repeat the same set of events many times so they race against each other
    # repeatedly across worker threads, regardless of the order the pool schedules them.
    tasks = events * 20

    def submit(event):
        return store.apply_event(conn, job_id="j1", **event)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(submit, tasks))

    assert all(result is not None for result in results)

    job = store.get_job(conn, "j1")
    assert job.status == "ok"
    assert job.exit_code == 0
    assert job.summary == "done"
    assert job.ts_queued == "t0"
    assert job.ts_started == "t1"
    assert job.ts_completed == "t2"
    assert job.runner_pid == 1


def _ok_job(conn, job_id, skill, ts_started, ts_completed):
    store.create_job(conn, job_id=job_id, skill=skill, args={}, source="api", engine="claude", ts_queued=ts_started)
    store.apply_event(conn, job_id=job_id, status="running", ts=ts_started, received_at=ts_started)
    store.apply_event(
        conn, job_id=job_id, status="ok", ts=ts_completed, received_at=ts_completed, exit_code=0, summary="done",
    )


def test_compute_skill_etas_only_counts_ok_runs(conn):
    _ok_job(conn, "j1", "metrics-pull", "2026-08-01T00:00:00Z", "2026-08-01T00:00:10Z")
    store.create_job(conn, job_id="j2", skill="metrics-pull", args={}, source="api", engine="claude", ts_queued="t0")
    store.apply_event(conn, job_id="j2", status="running", ts="t0", received_at="t0")
    store.apply_event(conn, job_id="j2", status="error", ts="t1", received_at="t1", exit_code=1)

    etas = store.compute_skill_etas(conn)
    assert etas == {"metrics-pull": 10}


def test_compute_skill_etas_ignores_rows_missing_timestamps(conn):
    store.apply_event(
        conn, job_id="j-no-times", status="ok", ts="t0", received_at="t0",
        skill="metrics-pull", args={}, source="api", exit_code=0,
    )
    assert store.compute_skill_etas(conn) == {}


def test_compute_skill_etas_returns_median_per_skill(conn):
    _ok_job(conn, "j1", "ai-wire", "2026-08-01T00:00:00Z", "2026-08-01T00:00:10Z")
    _ok_job(conn, "j2", "ai-wire", "2026-08-01T00:01:00Z", "2026-08-01T00:01:20Z")
    _ok_job(conn, "j3", "ai-wire", "2026-08-01T00:02:00Z", "2026-08-01T00:02:30Z")

    etas = store.compute_skill_etas(conn)
    assert etas == {"ai-wire": 20}


def test_compute_skill_etas_respects_limit(conn):
    for i in range(3):
        _ok_job(conn, f"j{i}", "metrics-pull", f"2026-08-0{i + 1}T00:00:00Z", f"2026-08-0{i + 1}T00:05:00Z")
    etas = store.compute_skill_etas(conn, limit=1)
    assert etas == {"metrics-pull": 300}


def test_apply_event_backfills_ts_queued_regardless_of_order(tmp_path):
    """Regression test for ts_queued backfill when queued event arrives after job creation.

    When a running event with skill creates a job first (ts_queued stays None),
    a late queued event must still backfill ts_queued correctly.
    """
    conn = connect(tmp_path / "backfill.db")

    # Running event arrives first and creates the job (with skill)
    job = store.apply_event(
        conn, job_id="j1", status="running", ts="t1", received_at="t1",
        skill="metrics-pull", args={}, source="api", engine="claude", pid=999,
    )
    assert job.status == "running"
    assert job.ts_queued is None  # Not set because job was created by running event
    assert job.ts_started == "t1"

    # Queued event arrives late for the same job
    job = store.apply_event(
        conn, job_id="j1", status="queued", ts="t0", received_at="t0",
    )
    # ts_queued should be backfilled even though status won't advance
    assert job.ts_queued == "t0"
    assert job.status == "running"  # Status unchanged (0 is not > 1)
    assert job.ts_started == "t1"
