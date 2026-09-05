import json
from datetime import datetime, timezone

import pytest

from vaultos.db.conn import connect
from vaultos.jobs import store
from vaultos.jobs.reconcile import detect_orphans
from vaultos.vault.runner import RunnerHeartbeat


@pytest.fixture
def conn(tmp_path):
    return connect(tmp_path / "vaultos.db")


def _make_running_job(conn, job_id, runner_pid):
    store.create_job(
        conn,
        job_id=job_id,
        skill="metrics-pull",
        args={},
        source="api",
        engine="claude",
        ts_queued="t0",
    )
    now = datetime.now(timezone.utc).isoformat()
    store.apply_event(
        conn, job_id=job_id, status="running", ts="t1", received_at=now, pid=runner_pid
    )


def test_detect_orphans_marks_missing_heartbeat_as_orphaned(conn):
    _make_running_job(conn, "j1", runner_pid=100)
    orphaned = detect_orphans(conn, heartbeat=None)

    assert orphaned == ["j1"]
    assert store.get_job(conn, "j1").status == "orphaned"


def test_detect_orphans_marks_stale_heartbeat_as_orphaned(conn):
    _make_running_job(conn, "j1", runner_pid=100)
    stale_heartbeat = RunnerHeartbeat(ts="t0", pid=100, active=1, pending=0, alive=False)

    orphaned = detect_orphans(conn, heartbeat=stale_heartbeat)

    assert orphaned == ["j1"]
    assert store.get_job(conn, "j1").status == "orphaned"


def test_detect_orphans_marks_pid_mismatch_as_orphaned_even_if_fresh(conn):
    _make_running_job(conn, "j1", runner_pid=100)
    # A fresh heartbeat under a DIFFERENT pid means the runner that had this job died
    # and a new one started -- orphaned regardless of the new heartbeat being alive.
    fresh_but_different_pid = RunnerHeartbeat(ts="t9", pid=999, active=0, pending=0, alive=True)

    orphaned = detect_orphans(conn, heartbeat=fresh_but_different_pid)

    assert orphaned == ["j1"]
    assert store.get_job(conn, "j1").status == "orphaned"


def test_detect_orphans_leaves_healthy_running_job_alone(conn):
    _make_running_job(conn, "j1", runner_pid=100)
    healthy_heartbeat = RunnerHeartbeat(ts="t9", pid=100, active=1, pending=0, alive=True)

    orphaned = detect_orphans(conn, heartbeat=healthy_heartbeat)

    assert orphaned == []
    assert store.get_job(conn, "j1").status == "running"


def test_detect_orphans_ignores_non_running_jobs(conn):
    store.create_job(
        conn,
        job_id="queued-job",
        skill="ai-wire",
        args={},
        source="api",
        engine="claude",
        ts_queued="t0",
    )
    _make_running_job(conn, "running-job", runner_pid=100)
    now = datetime.now(timezone.utc).isoformat()
    store.apply_event(
        conn,
        job_id="running-job",
        status="ok",
        ts="t2",
        received_at=now,
        exit_code=0,
        summary="done",
    )
    # "running-job" is actually terminal now -- re-fetch to be explicit about scope.

    _make_running_job(conn, "still-running", runner_pid=100)
    orphaned = detect_orphans(conn, heartbeat=None)

    assert orphaned == ["still-running"]
    assert store.get_job(conn, "queued-job").status == "queued"


def test_detect_orphans_goes_through_apply_event_leaving_an_audit_trail(conn):
    _make_running_job(conn, "j1", runner_pid=100)
    detect_orphans(conn, heartbeat=None)

    rows = conn.execute(
        "SELECT status, detail FROM job_events WHERE job_id = 'j1' ORDER BY id"
    ).fetchall()
    statuses = [r["status"] for r in rows]
    assert statuses == ["queued", "running", "orphaned"]

    # The orphaned event's detail must identify it as detector-originated --
    # otherwise it's indistinguishable from any other apply_event() call
    # when someone inspects job_events to understand why a job flipped.
    orphaned_detail = json.loads(rows[-1]["detail"])
    assert orphaned_detail.get("source") == "orphan-detector"


def test_a_later_ok_event_still_supersedes_an_orphaned_job(conn):
    _make_running_job(conn, "j1", runner_pid=100)
    detect_orphans(conn, heartbeat=None)
    assert store.get_job(conn, "j1").status == "orphaned"

    now = datetime.now(timezone.utc).isoformat()
    store.apply_event(
        conn,
        job_id="j1",
        status="ok",
        ts="t2",
        received_at=now,
        exit_code=0,
        summary="actually finished",
    )

    assert store.get_job(conn, "j1").status == "ok"
