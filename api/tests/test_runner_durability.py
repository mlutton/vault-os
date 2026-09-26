"""Runner recovery checks use only temporary job files and SQLite databases."""

import json
import logging
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

import vaultos.runner.core as core
import vaultos.timeutil as timeutil
import vaultos.vault.runner as vault_runner
from vaultos.api.runner import get_runner
from vaultos.config import Settings
from vaultos.db.conn import connect
from vaultos.jobs import store
from vaultos.jobs.reconcile import detect_orphans, reconcile_from_files
from vaultos.registry import load_registry
from vaultos.runner.core import Runner
from vaultos.runner.engines import EngineResult
from vaultos.runner.records import write_record
from vaultos.vault.intents import write_intent
from vaultos.vault.runner import read_heartbeat


class FakeEngine:
    def __init__(self, run=None):
        self.calls = 0
        self.run_callback = run

    def run(self, **kwargs):
        self.calls += 1
        if self.run_callback:
            self.run_callback()
        return EngineResult(success=True, exit_code=0, summary="complete")


@pytest.fixture
def setup(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    (vault / "system" / "queue").mkdir(parents=True)
    (vault / "system" / "runs").mkdir()
    (vault / "system" / "skills.json").write_text(
        json.dumps(
            {
                "version": 1,
                "skills": [{"id": "sample", "label": "Sample", "deck": True, "engine": "fake"}],
            }
        )
    )
    monkeypatch.setenv("VAULT_ROOT", str(vault))
    monkeypatch.setenv("VAULTOS_DB", str(tmp_path / "jobs.db"))
    monkeypatch.delenv("VAULTOS_STATE_ROOT", raising=False)
    settings = Settings()
    conn = connect(settings.db_path)
    registry = load_registry(vault)
    job = store.create_job(
        conn,
        job_id="sample-job",
        skill="sample",
        args={"key": "value"},
        source="api",
        engine="fake",
        ts_queued="2026-09-25T00:00:00Z",
    )
    write_intent(
        vault,
        job_id=job.id,
        skill=job.skill,
        args=job.args,
        ts=job.ts_queued,
        source=job.source,
    )
    yield vault, settings, conn, registry, job
    conn.close()


def test_long_job_survives_orphan_sweep(setup, monkeypatch):
    vault, settings, conn, registry, job = setup
    started = threading.Event()
    finish = threading.Event()

    def wait_for_completion():
        started.set()
        assert finish.wait(2)

    engine = FakeEngine(wait_for_completion)
    monkeypatch.setattr(core, "HEARTBEAT_INTERVAL_S", 0.025)
    clock_offset = [0]

    class TestClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(seconds=clock_offset[0])

    monkeypatch.setattr(timeutil, "datetime", TestClock)
    monkeypatch.setattr(vault_runner, "datetime", TestClock)
    runner = Runner(conn, registry, settings, engines={"fake": engine})
    worker = threading.Thread(target=runner.run_once)
    worker.start()
    try:
        assert started.wait(2)
        next_job = store.create_job(
            conn,
            job_id="next-job",
            skill="sample",
            args={},
            source="api",
            engine="fake",
            ts_queued="2026-09-25T00:00:01Z",
        )
        write_intent(
            vault,
            job_id=next_job.id,
            skill=next_job.skill,
            args=next_job.args,
            ts=next_job.ts_queued,
            source=next_job.source,
        )
        clock_offset[0] = 121  # Advance beyond the real 120-second sweep window.
        time.sleep(0.12)  # Allow several heartbeat writes at the advanced time.
        heartbeat = read_heartbeat(vault)
        assert heartbeat is not None and heartbeat.alive and heartbeat.busy
        assert heartbeat.pending == 1
        assert datetime.fromisoformat(heartbeat.ts.replace("Z", "+00:00")) > (
            datetime.now(timezone.utc) + timedelta(seconds=120)
        )
        assert detect_orphans(conn, heartbeat) == []
        assert store.get_job(conn, job.id).status == "running"
    finally:
        finish.set()
        worker.join(timeout=2)
    assert not worker.is_alive()
    assert store.get_job(conn, job.id).status == "ok"


@pytest.mark.parametrize("step", ["attempt", "terminal", "intent_removed"])
def test_crash_at_each_durable_step_rebuilds(setup, monkeypatch, tmp_path, step):
    vault, settings, conn, registry, job = setup
    engine = FakeEngine()
    runner = Runner(conn, registry, settings, engines={"fake": engine})

    if step in {"attempt", "terminal"}:
        original = core.write_record

        def crash_after_record(path, record):
            original(path, record)
            if (step == "attempt" and ".attempt-" in path.name) or (
                step == "terminal" and path.name == f"{job.id}.json"
            ):
                raise SystemExit("simulated crash")

        monkeypatch.setattr(core, "write_record", crash_after_record)
    else:
        original = core.remove_intent

        def crash_after_remove(path):
            original(path)
            raise SystemExit("simulated crash")

        monkeypatch.setattr(core, "remove_intent", crash_after_remove)

    with pytest.raises(SystemExit, match="simulated crash"):
        runner.run_once()
    monkeypatch.undo()

    attempt_path = vault / "system" / "runs" / f"{job.id}.attempt-1.json"
    terminal_path = vault / "system" / "runs" / f"{job.id}.json"
    intent_path = vault / "system" / "queue" / f"{job.id}.json"
    assert attempt_path.exists()
    assert terminal_path.exists() is (step != "attempt")
    assert intent_path.exists() is (step != "intent_removed")
    assert store.get_job(conn, job.id).status == ("queued" if step == "attempt" else "running")
    attempt_record = json.loads(attempt_path.read_text())
    assert attempt_record["args"] == job.args
    assert attempt_record["source"] == job.source
    assert attempt_record["runner_pid"] == runner.pid
    assert attempt_record["engine"] == "fake"
    assert attempt_record["ts_started"]
    if step != "attempt":
        terminal_record = json.loads(terminal_path.read_text())
        assert terminal_record["attempt_id"] == attempt_record["attempt_id"]
        assert terminal_record["ts_started"] == attempt_record["ts_started"]
        assert terminal_record["completion_evidence"]["engine"] == "fake"
        assert terminal_record["completion_evidence"]["exit_code"] == 0
        assert terminal_record["ts_completed"]

    fresh = connect(tmp_path / "fresh.db")
    try:
        reconcile_from_files(vault, fresh, registry)
        rebuilt = store.get_job(fresh, job.id)
        assert rebuilt is not None
        if step == "attempt":
            assert rebuilt.status == "queued"
            recovered = Runner(fresh, registry, settings, engines={"fake": engine})
            assert recovered.unresolved_attempts == [job.id]
            assert recovered.run_once() is False
            assert engine.calls == 0
        else:
            assert rebuilt.status == "ok"
            assert rebuilt.summary == "complete"
            assert engine.calls == 1
            assert Runner(fresh, registry, settings, engines={"fake": engine}).run_once() is False
            assert engine.calls == 1
    finally:
        fresh.close()


def test_unresolved_attempt_not_rerun(setup, caplog):
    vault, settings, conn, registry, job = setup
    write_record(
        vault / "system" / "runs" / f"{job.id}.attempt-1.json",
        {
            "id": job.id,
            "attempt_id": "prior-attempt",
            "skill": job.skill,
            "args": job.args,
            "source": job.source,
            "ts_started": "2026-09-25T00:00:01Z",
            "runner_pid": 123,
            "engine": "fake",
        },
    )
    engine = FakeEngine()
    with caplog.at_level(logging.WARNING):
        runner = Runner(conn, registry, settings, engines={"fake": engine})
    assert "unresolved attempt" in caplog.text
    assert runner.run_once() is False
    runner.write_heartbeat()
    assert read_heartbeat(vault).unresolved_attempts == (job.id,)
    assert get_runner(settings=settings)["unresolved_attempts"] == [job.id]
    assert engine.calls == 0
    assert store.get_job(conn, job.id).status == "queued"


def test_second_runner_refused(setup):
    _, settings, conn, registry, _ = setup
    first = Runner(conn, registry, settings, engines={"fake": FakeEngine()})
    second = Runner(conn, registry, settings, engines={"fake": FakeEngine()})
    with first._runner_lock():
        with pytest.raises(RuntimeError, match="another runner"):
            second.run_once()


def test_second_runner_process_exits_nonzero(setup):
    _, settings, conn, registry, _ = setup
    first = Runner(conn, registry, settings, engines={"fake": FakeEngine()})
    with first._runner_lock():
        result = subprocess.run(
            [sys.executable, "-m", "vaultos.runner"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    assert result.returncode != 0
    assert "another runner holds the state-root lock" in result.stderr


def test_missing_intent_is_not_claimed(setup):
    vault, settings, conn, registry, job = setup
    (vault / "system" / "queue" / f"{job.id}.json").unlink()
    engine = FakeEngine()
    assert Runner(conn, registry, settings, engines={"fake": engine}).run_once() is False
    assert store.get_job(conn, job.id).status == "queued"
    assert engine.calls == 0


def test_claim_uses_reread_intent_metadata(setup):
    vault, settings, conn, registry, job = setup
    conn.execute("UPDATE jobs SET engine = 'stale' WHERE id = ?", (job.id,))
    conn.commit()
    write_intent(
        vault,
        job_id=job.id,
        skill=job.skill,
        args={"key": "from-intent"},
        ts="2026-09-24T23:00:00Z",
        source="voice",
    )
    seen = []

    class InspectEngine:
        def run(self, *, job, **kwargs):
            seen.append((job.args, job.source, job.ts_queued, job.engine))
            return EngineResult(success=True, exit_code=0, summary="complete")

    runner = Runner(conn, registry, settings, engines={"fake": InspectEngine()})
    assert runner.run_once() is True
    assert seen == [({"key": "from-intent"}, "voice", "2026-09-24T23:00:00Z", "fake")]
    indexed = store.get_job(conn, job.id)
    assert indexed.args == {"key": "from-intent"}
    assert indexed.source == "voice"
    assert indexed.ts_queued == "2026-09-24T23:00:00Z"
    assert indexed.engine == "fake"
    terminal = json.loads((vault / "system" / "runs" / f"{job.id}.json").read_text())
    assert terminal["args"] == indexed.args
    assert terminal["source"] == indexed.source
    assert terminal["ts_queued"] == indexed.ts_queued


def test_terminal_file_blocks_stale_queued_row(setup):
    vault, settings, conn, registry, job = setup
    write_record(
        vault / "system" / "runs" / f"{job.id}.json",
        {
            "id": job.id,
            "skill": job.skill,
            "status": "ok",
            "ts_completed": "2026-09-25T00:00:03Z",
        },
    )
    engine = FakeEngine()
    assert Runner(conn, registry, settings, engines={"fake": engine}).run_once() is False
    assert engine.calls == 0
    assert store.get_job(conn, job.id).status == "queued"
    assert (vault / "system" / "queue" / f"{job.id}.json").exists()


@pytest.mark.parametrize("missing_attempt_id", [False, True])
def test_partial_terminal_is_unknown_and_attempt_stays_unresolved(
    setup, tmp_path, missing_attempt_id
):
    vault, settings, conn, registry, job = setup
    write_record(
        vault / "system" / "runs" / f"{job.id}.attempt-1.json",
        {
            "id": job.id,
            "attempt_id": "attempt-one",
            "skill": job.skill,
            "args": job.args,
            "source": job.source,
            "ts_started": "2026-09-25T00:00:01Z",
            "runner_pid": 123,
            "engine": "fake",
        },
    )
    partial = {
        "id": job.id,
        "skill": job.skill,
        "status": "ok",
        "ts_completed": "2026-09-25T00:00:03Z",
    }
    if not missing_attempt_id:
        partial["attempt_id"] = "attempt-one"
    (vault / "system" / "runs" / f"{job.id}.json").write_text(json.dumps(partial))
    fresh = connect(tmp_path / "partial-rebuild.db")
    try:
        result = reconcile_from_files(vault, fresh, registry)
        assert result.skipped == 1
        assert store.get_job(fresh, job.id).status == "queued"
        engine = FakeEngine()
        runner = Runner(fresh, registry, settings, engines={"fake": engine})
        assert runner.unresolved_attempts == [job.id]
        assert runner.run_once() is False
        assert engine.calls == 0
    finally:
        fresh.close()


def test_retry_has_distinct_attempt_and_nonrepeatable_skips_it(setup, monkeypatch):
    vault, settings, conn, registry, job = setup
    skill_path = vault / "system" / "skills.json"
    skill_data = json.loads(skill_path.read_text())
    skill_data["skills"][0]["repeatable"] = False
    skill_data["skills"][0]["check"] = "exit 1"
    skill_path.write_text(json.dumps(skill_data))
    registry = load_registry(vault)
    engine = FakeEngine()
    runner = Runner(conn, registry, settings, engines={"fake": engine})
    assert runner.run_once() is True
    assert engine.calls == 1
    assert store.get_job(conn, job.id).status == "error"
    assert (vault / "system" / "runs" / f"{job.id}.attempt-1.json").exists()
    assert not (vault / "system" / "runs" / f"{job.id}.attempt-2.json").exists()


def test_retry_writes_second_attempt_before_engine(setup, monkeypatch):
    vault, settings, conn, registry, job = setup
    skill_path = vault / "system" / "skills.json"
    skill_data = json.loads(skill_path.read_text())
    skill_data["skills"][0]["check"] = "exit 1"
    skill_path.write_text(json.dumps(skill_data))
    registry = load_registry(vault)
    observations = []

    def observe():
        observations.append(
            (
                (vault / "system" / "runs" / f"{job.id}.attempt-1.json").exists(),
                (vault / "system" / "runs" / f"{job.id}.attempt-2.json").exists(),
            )
        )

    engine = FakeEngine(observe)
    runner = Runner(conn, registry, settings, engines={"fake": engine})
    assert runner.run_once() is True
    assert observations == [(True, False), (True, True)]
    first = json.loads((vault / "system" / "runs" / f"{job.id}.attempt-1.json").read_text())
    second = json.loads((vault / "system" / "runs" / f"{job.id}.attempt-2.json").read_text())
    assert first["attempt_id"] != second["attempt_id"]
    terminal = json.loads((vault / "system" / "runs" / f"{job.id}.json").read_text())
    assert terminal["attempt_ids"] == [first["attempt_id"], second["attempt_id"]]


def test_retry_record_write_failure_does_not_claim_terminal_success(setup, monkeypatch):
    vault, settings, conn, registry, job = setup
    skill_path = vault / "system" / "skills.json"
    skill_data = json.loads(skill_path.read_text())
    skill_data["skills"][0]["check"] = "exit 1"
    skill_path.write_text(json.dumps(skill_data))
    registry = load_registry(vault)
    engine = FakeEngine()
    original = core.write_record

    def refuse_retry(path, record):
        if ".attempt-2.json" in path.name:
            raise OSError("synthetic write failure")
        return original(path, record)

    monkeypatch.setattr(core, "write_record", refuse_retry)
    runner = Runner(conn, registry, settings, engines={"fake": engine})
    with pytest.raises(core.RecordWriteError, match="attempt record"):
        runner.run_once()
    assert engine.calls == 1
    assert store.get_job(conn, job.id).status == "running"
    assert not (vault / "system" / "runs" / f"{job.id}.json").exists()
    assert (vault / "system" / "queue" / f"{job.id}.json").exists()
