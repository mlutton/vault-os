"""check+retry (2026-09-05 addendum to docs/specs/2026-09-04-runner-engine-
registry-design.md), proven end-to-end on the existing `script` engine per
ticket #23's Stage A: `check` is a top-level Skill field, the check runner
lives in vaultos.runner.core above the engine seam, and the script adapter
receives retry failure-context via the VAULTOS_CHECK_FEEDBACK env var.

Real subprocesses throughout, per the suite's established convention (see
test_runner_engine_script.py) -- no mocking of subprocess.run.
"""

import json
import stat

import pytest

from vaultos.config import Settings
from vaultos.db.conn import connect
from vaultos.jobs import store
from vaultos.registry import load_registry
from vaultos.runner.core import Runner
from vaultos.vault.intents import write_intent


def _write_script(path, body):
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


@pytest.fixture
def vault(tmp_path):
    vault_root = tmp_path / "vault"
    (vault_root / "system" / "queue").mkdir(parents=True)
    (vault_root / "system" / "runs").mkdir(parents=True)
    return vault_root


def _registry_with_check(vault_root, tmp_path, check_cmd, *, no_check=False):
    """A single script-engine skill ('checked-script') whose engine script
    records how many times it ran and what VAULTOS_CHECK_FEEDBACK it saw, so
    tests can assert on the retry mechanics rather than just the final
    status."""
    run_log = tmp_path / "run-log.txt"
    engine_script = _write_script(
        tmp_path / "engine.sh",
        f"#!/bin/sh\n"
        f'echo "ran" >> "{run_log}"\n'
        f'echo "feedback=${{VAULTOS_CHECK_FEEDBACK:-<none>}}" >> "{run_log}"\n'
        f'echo "ok"\n',
    )
    skill = {
        "id": "checked-script",
        "label": "Checked Script",
        "deck": True,
        "engine": "script",
        "args": [],
        "engine_config": {"argv": [str(engine_script)]},
    }
    if not no_check:
        skill["check"] = check_cmd
    registry = {"version": 1, "skills": [skill]}
    (vault_root / "system" / "skills.json").write_text(json.dumps(registry))
    return run_log


@pytest.fixture
def settings(monkeypatch, vault, tmp_path):
    monkeypatch.setenv("VAULT_ROOT", str(vault))
    monkeypatch.setenv("VAULTOS_DB", str(tmp_path / "vaultos.db"))
    monkeypatch.delenv("VAULTOS_STATE_ROOT", raising=False)
    return Settings()


@pytest.fixture
def conn(settings):
    return connect(settings.db_path)


def _enqueue(conn, vault, registry, ts="2026-09-05T00:00:00Z"):
    job = store.create_job(
        conn, job_id=f"checked-script-{ts}", skill="checked-script", args={},
        source="api", engine=registry.get("checked-script").engine, ts_queued=ts,
    )
    write_intent(vault, job_id=job.id, skill="checked-script", args={}, ts=ts, source="api")
    return job


def _run(conn, vault, registry, settings):
    events = []
    runner = Runner(conn, registry, settings, emit=events.append)
    claimed = runner.run_once()
    return claimed, events


def test_check_pass_first_try(conn, vault, tmp_path, settings):
    run_log = _registry_with_check(vault, tmp_path, "exit 0")
    registry = load_registry(vault)
    job = _enqueue(conn, vault, registry)

    claimed, events = _run(conn, vault, registry, settings)

    assert claimed is True
    final = store.get_job(conn, job.id)
    assert final.status == "ok"
    assert run_log.read_text().count("ran") == 1

    assert len(events) == 1
    assert events[0]["success"] is True
    assert events[0]["check"] == {"passed": True, "attempt": 1}


def test_check_fail_then_retry_then_pass(conn, vault, tmp_path, settings):
    # Fails exactly once (via a counter file), then passes -- and asserts the
    # check's own stdout+stderr was visible to the *retried* engine process
    # through VAULTOS_CHECK_FEEDBACK.
    counter = tmp_path / "check-counter"
    check_cmd = (
        f'if [ -f "{counter}" ]; then echo "second check ok"; exit 0; '
        f'else touch "{counter}"; echo "first check failure marker"; exit 1; fi'
    )
    run_log = _registry_with_check(vault, tmp_path, check_cmd)
    registry = load_registry(vault)
    job = _enqueue(conn, vault, registry)

    claimed, events = _run(conn, vault, registry, settings)

    assert claimed is True
    final = store.get_job(conn, job.id)
    assert final.status == "ok"

    log_text = run_log.read_text()
    assert log_text.count("ran") == 2, "engine should run once, then once more on retry"
    assert "feedback=<none>" in log_text, "first attempt has no retry context"
    assert "first check failure marker" in log_text, (
        "the retried process must see the failed check's stdout via "
        "VAULTOS_CHECK_FEEDBACK"
    )

    assert len(events) == 1
    assert events[0]["success"] is True
    assert events[0]["check"] == {"passed": True, "attempt": 2}


def test_check_fail_then_retry_then_fail_is_error(conn, vault, tmp_path, settings):
    run_log = _registry_with_check(vault, tmp_path, 'echo "always fails" >&2; exit 1')
    registry = load_registry(vault)
    job = _enqueue(conn, vault, registry)

    claimed, events = _run(conn, vault, registry, settings)

    assert claimed is True
    final = store.get_job(conn, job.id)
    assert final.status == "error"
    assert "always fails" in final.summary

    assert run_log.read_text().count("ran") == 2, "exactly one retry, no more"

    assert len(events) == 1
    assert events[0]["success"] is False
    assert events[0]["check"] == {"passed": False, "attempt": 2}


def test_engine_failure_on_retry_is_error(conn, vault, tmp_path, settings):
    """If the retried engine invocation itself fails (nonzero exit), the job
    errors without a second check -- per the addendum, "Engine failure on the
    retry also -> error.\""""
    run_log = tmp_path / "run-log.txt"
    engine_script = _write_script(
        tmp_path / "engine.sh",
        f"#!/bin/sh\n"
        f'echo "ran" >> "{run_log}"\n'
        f'if [ -n "$VAULTOS_CHECK_FEEDBACK" ]; then echo "retry engine boom" >&2; exit 7; fi\n'
        f'echo "ok"\n',
    )
    skill = {
        "id": "checked-script", "label": "Checked Script", "deck": True, "engine": "script",
        "args": [], "engine_config": {"argv": [str(engine_script)]}, "check": "exit 1",
    }
    (vault / "system" / "skills.json").write_text(json.dumps({"version": 1, "skills": [skill]}))
    registry = load_registry(vault)
    job = _enqueue(conn, vault, registry)

    claimed, events = _run(conn, vault, registry, settings)

    assert claimed is True
    final = store.get_job(conn, job.id)
    assert final.status == "error"
    assert final.exit_code == 7
    assert run_log.read_text().count("ran") == 2

    assert len(events) == 1
    assert events[0]["check"] == {"passed": False, "attempt": 2}


def test_no_check_declared_engine_success_suffices(conn, vault, tmp_path, settings):
    run_log = _registry_with_check(vault, tmp_path, "exit 0", no_check=True)
    registry = load_registry(vault)
    assert registry.get("checked-script").check is None
    job = _enqueue(conn, vault, registry)

    claimed, events = _run(conn, vault, registry, settings)

    assert claimed is True
    final = store.get_job(conn, job.id)
    assert final.status == "ok"
    assert run_log.read_text().count("ran") == 1

    assert len(events) == 1
    assert events[0]["check"] is None


def test_check_not_run_when_engine_itself_fails(conn, vault, tmp_path, settings):
    """A check declared but the engine fails outright (before any check):
    no check should run at all, and the job errors on the engine's own
    failure -- check+retry only ever engages after an engine success."""
    run_log = tmp_path / "run-log.txt"
    engine_script = _write_script(
        tmp_path / "engine.sh", f'#!/bin/sh\necho "ran" >> "{run_log}"\nexit 9\n',
    )
    check_marker = tmp_path / "check-ran"
    skill = {
        "id": "checked-script", "label": "Checked Script", "deck": True, "engine": "script",
        "args": [], "engine_config": {"argv": [str(engine_script)]},
        "check": f'touch "{check_marker}"; exit 0',
    }
    (vault / "system" / "skills.json").write_text(json.dumps({"version": 1, "skills": [skill]}))
    registry = load_registry(vault)
    job = _enqueue(conn, vault, registry)

    claimed, events = _run(conn, vault, registry, settings)

    assert claimed is True
    final = store.get_job(conn, job.id)
    assert final.status == "error"
    assert final.exit_code == 9
    assert not check_marker.exists()
    assert run_log.read_text().count("ran") == 1

    assert len(events) == 1
    assert events[0]["check"] is None
