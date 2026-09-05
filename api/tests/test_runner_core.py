import json
import signal
import stat

import pytest

from vaultos.config import Settings
from vaultos.db.conn import connect
from vaultos.jobs import store
from vaultos.registry import load_registry
from vaultos.runner.core import Runner
from vaultos.state import resolve_state_root
from vaultos.vault.intents import write_intent
from vaultos.vault.runner import read_heartbeat


SCRIPT_REGISTRY = {
    "version": 1,
    "skills": [
        {
            "id": "hello-script",
            "label": "Hello Script",
            "deck": True,
            "engine": "script",
            "args": [{"name": "who", "required": False, "type": "string"}],
            "engine_config": {
                "argv": ["{script_path}", "{who}", "{vault_root}/inbox/reports/hello-script/{job_id}.md"],
                "deliverable": "inbox/reports/hello-script/{job_id}.md",
            },
        },
        {
            "id": "no-engine-skill",
            "label": "No Engine",
            "deck": True,
            "engine": "nonexistent-engine",
            "args": [],
        },
        {"id": "chained-target", "label": "Chained Target", "deck": True, "engine": "script", "args": []},
    ],
}


@pytest.fixture
def vault(tmp_path):
    vault_root = tmp_path / "vault"
    (vault_root / "system" / "queue").mkdir(parents=True)
    (vault_root / "system" / "runs").mkdir(parents=True)

    script_path = tmp_path / "hello.sh"
    script_path.write_text(
        '#!/bin/sh\nmkdir -p "$(dirname "$2")"\necho "hello ${1:-world}" > "$2"\n'
    )
    script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC)

    registry = json.loads(json.dumps(SCRIPT_REGISTRY))
    registry["skills"][0]["engine_config"]["argv"][0] = str(script_path)
    (vault_root / "system" / "skills.json").write_text(json.dumps(registry))
    return vault_root


@pytest.fixture
def settings(monkeypatch, vault, tmp_path):
    monkeypatch.setenv("VAULT_ROOT", str(vault))
    monkeypatch.setenv("VAULTOS_DB", str(tmp_path / "vaultos.db"))
    monkeypatch.delenv("VAULTOS_STATE_ROOT", raising=False)
    return Settings()


@pytest.fixture
def conn(settings):
    return connect(settings.db_path)


@pytest.fixture
def registry(vault):
    return load_registry(vault)


def _enqueue(conn, vault, registry, skill_id, args=None, ts="2026-09-04T00:00:00Z"):
    if args is None:
        # hello-script's argv template references {who}; default it here so
        # tests that don't care about the substitution value don't each have
        # to supply one.
        args = {"who": "world"} if skill_id == "hello-script" else {}
    job = store.create_job(
        conn, job_id=f"{skill_id}-{ts}", skill=skill_id, args=args, source="api",
        engine=registry.get(skill_id).engine, ts_queued=ts,
    )
    write_intent(vault, job_id=job.id, skill=skill_id, args=args, ts=ts, source="api")
    return job


def test_run_once_executes_script_job_end_to_end(conn, registry, settings, vault):
    job = _enqueue(conn, vault, registry, "hello-script", args={"who": "chicago"})

    runner = Runner(conn, registry, settings)
    claimed = runner.run_once()

    assert claimed is True
    final = store.get_job(conn, job.id)
    assert final.status == "ok"
    assert final.exit_code == 0
    assert final.deliverable_path == f"inbox/reports/hello-script/{job.id}.md"
    deliverable = vault / "inbox" / "reports" / "hello-script" / f"{job.id}.md"
    assert deliverable.read_text().strip() == "hello chicago"


def test_run_once_returns_false_when_queue_empty(conn, registry, settings):
    runner = Runner(conn, registry, settings)
    assert runner.run_once() is False


def test_run_once_unknown_engine_fails_fast(conn, registry, settings, vault):
    job = _enqueue(conn, vault, registry, "no-engine-skill")

    runner = Runner(conn, registry, settings)
    runner.run_once()

    final = store.get_job(conn, job.id)
    assert final.status == "error"
    assert "nonexistent-engine" in final.summary


def test_run_once_missing_engine_config_fails_job_not_runner(conn, registry, settings, vault):
    # "no-engine-skill" declares engine="nonexistent-engine" which has no
    # adapter in ENGINE_REGISTRY at all -- the job fails, the runner itself
    # must be able to keep going and claim the next job.
    _enqueue(conn, vault, registry, "no-engine-skill", ts="2026-09-04T00:00:00Z")
    second = _enqueue(conn, vault, registry, "hello-script", ts="2026-09-04T00:00:01Z")

    runner = Runner(conn, registry, settings)
    assert runner.run_once() is True
    assert runner.run_once() is True

    assert store.get_job(conn, second.id).status == "ok"


def test_chain_map_fires_through_runner_terminal_event(conn, registry, settings, vault, monkeypatch):
    import vaultos.api.jobs as jobs_module

    monkeypatch.setattr(jobs_module, "CHAIN_MAP", {"hello-script": "chained-target"})

    job = _enqueue(conn, vault, registry, "hello-script")
    runner = Runner(conn, registry, settings)
    runner.run_once()

    assert store.get_job(conn, job.id).status == "ok"
    chained = store.list_jobs(conn, statuses=["queued", "running"])
    assert any(j.skill == "chained-target" and j.source == f"chain:hello-script:{job.id}" for j in chained)


def test_heartbeat_written_and_served_fresh(conn, registry, settings, vault):
    runner = Runner(conn, registry, settings)
    runner.write_heartbeat()

    state_root = resolve_state_root(settings)
    assert (state_root / "runner-status.json").exists()

    heartbeat = read_heartbeat(settings.vault_root)
    assert heartbeat is not None
    assert heartbeat.alive is True
    assert heartbeat.pid == runner.pid


def test_heartbeat_reflects_pending_count(conn, registry, settings, vault):
    _enqueue(conn, vault, registry, "hello-script", ts="2026-09-04T00:00:00Z")
    _enqueue(conn, vault, registry, "chained-target", ts="2026-09-04T00:00:01Z")

    runner = Runner(conn, registry, settings)
    runner.write_heartbeat()

    heartbeat = read_heartbeat(settings.vault_root)
    assert heartbeat.pending == 2
    assert heartbeat.active == 0


def test_heartbeat_uses_state_root_override(monkeypatch, vault, tmp_path):
    monkeypatch.setenv("VAULT_ROOT", str(vault))
    monkeypatch.setenv("VAULTOS_DB", str(tmp_path / "vaultos.db"))
    override = tmp_path / "state-override"
    monkeypatch.setenv("VAULTOS_STATE_ROOT", str(override))
    settings = Settings()
    conn = connect(settings.db_path)
    registry = load_registry(vault)

    runner = Runner(conn, registry, settings)
    runner.write_heartbeat()

    assert (override / "runner-status.json").exists()
    # Legacy vault_root/system location is untouched by the override.
    assert not (vault / "system" / "runner-status.json").exists()


def test_shutdown_releases_in_flight_claim_not_yet_executing(conn, registry, settings, vault):
    job = _enqueue(conn, vault, registry, "hello-script")

    runner = Runner(conn, registry, settings)
    # Simulate the runner having claimed the job (as run_once() would) but
    # not yet dispatched it to the engine when the shutdown signal lands.
    claimed = store.claim_oldest_queued(conn, pid=runner.pid, ts="2026-09-04T00:00:02Z")
    runner._current_job_id = claimed.id

    runner.request_shutdown()

    released = store.get_job(conn, job.id)
    assert released.status == "queued"
    assert released.runner_pid is None
    assert runner._shutdown_event.is_set()


def test_shutdown_does_not_release_a_job_actively_executing(conn, registry, settings, vault):
    job = _enqueue(conn, vault, registry, "hello-script")
    runner = Runner(conn, registry, settings)
    claimed = store.claim_oldest_queued(conn, pid=runner.pid, ts="2026-09-04T00:00:02Z")
    runner._current_job_id = claimed.id
    runner._executing = True  # engine subprocess is "in flight"

    runner.request_shutdown()

    still_running = store.get_job(conn, job.id)
    assert still_running.status == "running"
    assert runner._shutdown_event.is_set()


def test_run_forever_installs_signal_handlers_that_trigger_shutdown(conn, registry, settings, monkeypatch):
    runner = Runner(conn, registry, settings)
    captured = {}
    monkeypatch.setattr(signal, "signal", lambda sig, handler: captured.__setitem__(sig, handler))

    runner._install_signal_handlers()

    # Bound methods aren't cached, so `is runner.request_shutdown` would
    # compare two distinct-but-equivalent bound-method objects; compare the
    # underlying function + instance instead.
    assert captured[signal.SIGTERM].__func__ is Runner.request_shutdown
    assert captured[signal.SIGTERM].__self__ is runner
    assert captured[signal.SIGINT].__func__ is Runner.request_shutdown

    assert not runner._shutdown_event.is_set()
    captured[signal.SIGTERM]()
    assert runner._shutdown_event.is_set()


def test_run_forever_exits_once_shutdown_requested(conn, registry, settings, vault):
    """The poll loop stops claiming new jobs once shutdown is requested, but
    finishes the job it's already executing (spec: "finishes or releases in-
    flight claims" -- run_once() here completes its one queued job before
    run_forever notices the flag on its next loop check)."""
    job = _enqueue(conn, vault, registry, "hello-script")
    runner = Runner(conn, registry, settings)
    runner.poll_interval_s = 0.01

    original_run_once = runner.run_once
    calls = []

    def run_once_then_stop():
        result = original_run_once()
        calls.append(result)
        runner._shutdown_event.set()
        return result

    runner.run_once = run_once_then_stop

    # run_forever() installs real SIGTERM/SIGINT handlers -- save and restore
    # the test process's own so this test doesn't leak a handler change into
    # the rest of the suite.
    prev_term = signal.getsignal(signal.SIGTERM)
    prev_int = signal.getsignal(signal.SIGINT)
    try:
        runner.run_forever()
    finally:
        signal.signal(signal.SIGTERM, prev_term)
        signal.signal(signal.SIGINT, prev_int)

    assert store.get_job(conn, job.id).status == "ok"
    assert calls == [True]
