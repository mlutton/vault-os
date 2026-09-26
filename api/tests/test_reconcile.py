import json
from datetime import datetime, timezone

import pytest

from vaultos.db.conn import connect
from vaultos.jobs import store
from vaultos.jobs.reconcile import reconcile_from_files
from vaultos.registry import load_registry
from vaultos.vault.runs import read_run_record


@pytest.fixture
def conn(tmp_path):
    return connect(tmp_path / "vaultos.db")


@pytest.fixture
def registry(tmp_vault):
    return load_registry(tmp_vault)


def _write_queue_file(
    tmp_vault, job_id, skill="metrics-pull", args=None, source="api", ts="2026-08-09T00:00:00Z"
):
    path = tmp_vault / "system" / "queue" / f"{job_id}.json"
    path.write_text(
        json.dumps({"id": job_id, "skill": skill, "args": args or {}, "ts": ts, "source": source})
    )
    return path


def _write_run_record(tmp_vault, job_id, **overrides):
    record = {
        "id": job_id,
        "skill": "metrics-pull",
        "args": {},
        "source": "api",
        "ts_queued": "2026-08-09T00:00:00Z",
        "ts_started": "2026-08-09T00:00:01Z",
        "ts_completed": "2026-08-09T00:00:05Z",
        "status": "ok",
        "exit_code": 0,
        "summary": "done",
        "md_path": f"system/runs/{job_id}.md",
        "log_path": f"system/runs/{job_id}.md",
        "deliverable_path": f"inbox/reports/{job_id}.md",
    }
    record.update(overrides)
    path = tmp_vault / "system" / "runs" / f"{job_id}.json"
    path.write_text(json.dumps(record))
    return path


def test_reconcile_creates_queued_job_from_queue_file(tmp_vault, conn, registry):
    _write_queue_file(tmp_vault, "j1", skill="ai-wire", source="obsidian")
    reconcile_from_files(tmp_vault, conn, registry)

    job = store.get_job(conn, "j1")
    assert job is not None
    assert job.status == "queued"
    assert job.skill == "ai-wire"
    assert job.source == "obsidian"


def test_reconcile_brings_run_record_forward_to_terminal_status(tmp_vault, conn, registry):
    _write_run_record(tmp_vault, "j2", status="ok", exit_code=0, summary="all good")
    reconcile_from_files(tmp_vault, conn, registry)

    job = store.get_job(conn, "j2")
    assert job is not None
    assert job.status == "ok"
    assert job.exit_code == 0
    assert job.summary == "all good"
    assert job.ts_queued == "2026-08-09T00:00:00Z"
    assert job.ts_started == "2026-08-09T00:00:01Z"
    assert job.ts_completed == "2026-08-09T00:00:05Z"
    assert job.deliverables == ["inbox/reports/j2.md"]


def test_reconcile_leaves_in_progress_run_as_running(tmp_vault, conn, registry):
    _write_run_record(
        tmp_vault, "j3", status="running", ts_completed=None, exit_code=None, summary=None
    )
    reconcile_from_files(tmp_vault, conn, registry)

    job = store.get_job(conn, "j3")
    assert job is not None
    assert job.status == "running"
    assert job.ts_started == "2026-08-09T00:00:01Z"
    assert job.ts_completed is None


def test_reconcile_does_not_regress_a_more_advanced_db_row(tmp_vault, conn, registry):
    store.create_job(
        conn,
        job_id="j4",
        skill="metrics-pull",
        args={},
        source="api",
        engine="claude",
        ts_queued="2026-08-09T00:00:00Z",
    )
    now = datetime.now(timezone.utc).isoformat()
    store.apply_event(
        conn, job_id="j4", status="running", ts="2026-08-09T00:00:01Z", received_at=now
    )
    store.apply_event(
        conn,
        job_id="j4",
        status="ok",
        ts="2026-08-09T00:00:05Z",
        received_at=now,
        exit_code=0,
        summary="finished before the spine went down",
    )

    # A stale run record on disk still shows "running" -- must not regress the DB.
    _write_run_record(
        tmp_vault, "j4", status="running", ts_completed=None, exit_code=None, summary=None
    )
    reconcile_from_files(tmp_vault, conn, registry)

    job = store.get_job(conn, "j4")
    assert job.status == "ok"
    assert job.summary == "finished before the spine went down"


def test_reconcile_skips_malformed_json_and_continues(tmp_vault, conn, registry):
    (tmp_vault / "system" / "queue" / "bad.json").write_text("{not valid json")
    _write_queue_file(tmp_vault, "j5", skill="ai-wire")

    result = reconcile_from_files(tmp_vault, conn, registry)

    assert store.get_job(conn, "j5") is not None
    assert result.skipped == 1
    assert result.queue_files_seen == 1


def test_reconcile_skips_run_record_missing_required_fields(tmp_vault, conn, registry):
    path = tmp_vault / "system" / "runs" / "bad.json"
    path.write_text(json.dumps({"id": "bad", "status": "ok"}))  # missing "skill"
    _write_run_record(tmp_vault, "j6")

    result = reconcile_from_files(tmp_vault, conn, registry)

    assert store.get_job(conn, "j6") is not None
    assert result.skipped == 1
    assert result.run_files_seen == 1


def test_reconcile_is_idempotent(tmp_vault, conn, registry):
    _write_queue_file(tmp_vault, "j7")
    _write_run_record(tmp_vault, "j8", status="error", exit_code=1, summary="boom")

    reconcile_from_files(tmp_vault, conn, registry)
    reconcile_from_files(tmp_vault, conn, registry)

    j7 = store.get_job(conn, "j7")
    j8 = store.get_job(conn, "j8")
    assert j7.status == "queued"
    assert j8.status == "error"
    assert j8.exit_code == 1


def test_reconcile_handles_missing_queue_and_runs_dirs(tmp_path, conn, registry):
    empty_vault = tmp_path / "empty-vault"
    empty_vault.mkdir()
    result = reconcile_from_files(empty_vault, conn, registry)
    assert result.queue_files_seen == 0
    assert result.run_files_seen == 0
    assert result.skipped == 0


def test_reconcile_ignores_attempt_files(tmp_vault, conn, registry):
    attempt = tmp_vault / "system" / "runs" / "job-a.attempt-1.json"
    attempt.write_text(
        json.dumps(
            {
                "id": "job-a",
                "attempt_id": "attempt-a",
                "skill": "metrics-pull",
                "args": {},
                "source": "api",
                "ts_started": "2026-08-09T00:00:01Z",
                "runner_pid": 123,
                "engine": "claude",
            }
        )
    )

    result = reconcile_from_files(tmp_vault, conn, registry)

    assert result.run_files_seen == 0
    assert result.skipped == 0
    assert store.get_job(conn, "job-a") is None


def test_reconcile_partial_terminal_does_not_report_success(tmp_vault, conn, registry):
    partial = tmp_vault / "system" / "runs" / "partial.json"
    (tmp_vault / "system" / "runs" / "partial.attempt-1.json").write_text(
        json.dumps({"id": "partial", "attempt_id": "attempt-1"})
    )
    partial.write_text(
        json.dumps(
            {
                "id": "partial",
                "skill": "metrics-pull",
                "status": "ok",
                "ts_completed": "2026-08-09T00:00:05Z",
            }
        )
    )

    result = reconcile_from_files(tmp_vault, conn, registry)

    assert result.skipped == 1
    assert store.get_job(conn, "partial") is None


def test_legacy_terminal_without_source_reads_and_rebuilds(tmp_vault, tmp_path, registry):
    path = tmp_vault / "system" / "runs" / "legacy.json"
    path.write_text(
        json.dumps(
            {
                "id": "legacy",
                "skill": "metrics-pull",
                "args": {"sample": "value"},
                "ts_queued": "2026-08-09T00:00:00Z",
                "ts_started": "2026-08-09T00:00:01Z",
                "ts_completed": "2026-08-09T00:00:05Z",
                "status": "ok",
                "exit_code": 0,
                "summary": "complete",
            }
        )
    )

    record = read_run_record(path)
    assert record.id == "legacy"
    assert record.source is None
    assert record.status == "ok"

    fresh = connect(tmp_path / "fresh-legacy.db")
    try:
        result = reconcile_from_files(tmp_vault, fresh, registry)
        job = store.get_job(fresh, "legacy")
        assert result.run_files_seen == 1
        assert job is not None
        assert job.status == "ok"
        assert job.source == "api"
        assert job.args == {"sample": "value"}
        assert job.ts_completed == "2026-08-09T00:00:05Z"
    finally:
        fresh.close()


def test_legacy_running_record_reads_with_optional_fields_missing(tmp_vault):
    path = tmp_vault / "system" / "runs" / "legacy-running.json"
    path.write_text(json.dumps({"skill": "metrics-pull", "status": "running"}))

    record = read_run_record(path)

    assert record.id == "legacy-running"
    assert record.status == "running"
    assert record.args == {}
    assert record.source is None
    assert record.ts_started is None
    assert record.ts_completed is None


def test_legacy_terminal_without_completion_time_reads_and_rebuilds(tmp_vault, tmp_path, registry):
    path = tmp_vault / "system" / "runs" / "legacy-no-completion.json"
    path.write_text(
        json.dumps(
            {
                "skill": "metrics-pull",
                "status": "ok",
                "ts_queued": "2026-08-09T00:00:00Z",
                "ts_started": "2026-08-09T00:00:01Z",
            }
        )
    )

    record = read_run_record(path)
    assert record.ts_completed is None
    assert record.status == "ok"

    fresh = connect(tmp_path / "fresh-no-completion.db")
    try:
        result = reconcile_from_files(tmp_vault, fresh, registry)
        job = store.get_job(fresh, "legacy-no-completion")
        assert result.run_files_seen == 1
        assert job is not None
        assert job.status == "ok"
        assert job.ts_completed == record.ts_started
    finally:
        fresh.close()
