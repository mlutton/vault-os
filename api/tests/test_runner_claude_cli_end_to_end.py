"""End-to-end coverage of the `claude-cli` engine (ticket #23 stage B):
enqueue via the FastAPI test client, run vaultos.runner against a stub
`claude` binary (a fixture shell script -- never a real vendor CLI, no keys,
no network), observe the job record via the API. Mirrors
test_runner_end_to_end.py's pattern for the script engine."""

import json
import stat

import pytest

from vaultos.runner.core import Runner


def _write_stub(path, body):
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


@pytest.fixture
def stub_claude(tmp_path):
    """A scripted `claude` stand-in, driven by env vars so each test can
    choose its behavior without a different binary per test:
    - CLAUDE_STUB_MODE=fail -> nonzero exit
    - CLAUDE_STUB_MODE=sleep -> sleeps CLAUDE_STUB_SLEEP_S seconds
    - otherwise -> echoes CLAUDE_STUB_OUTPUT (default "stub ok")
    Every invocation's argv is appended to CLAUDE_STUB_LOG for inspection."""
    return _write_stub(
        tmp_path / "claude",
        "#!/bin/sh\n"
        'if [ -n "$CLAUDE_STUB_LOG" ]; then\n'
        '  echo "=== invocation ===" >> "$CLAUDE_STUB_LOG"\n'
        '  for a in "$@"; do printf \'%s\\n\' "$a" >> "$CLAUDE_STUB_LOG"; done\n'
        "fi\n"
        'if [ "$CLAUDE_STUB_MODE" = "fail" ]; then\n'
        '  echo "stub failure" >&2\n'
        "  exit 1\n"
        "fi\n"
        'if [ "$CLAUDE_STUB_MODE" = "sleep" ]; then\n'
        '  sleep "${CLAUDE_STUB_SLEEP_S:-5}"\n'
        "fi\n"
        'echo "${CLAUDE_STUB_OUTPUT:-stub ok}"\n',
    )


@pytest.fixture
def tmp_vault(tmp_path, stub_claude):
    """Overrides conftest's tmp_vault with a claude-cli skill pointed at the
    stub binary, alongside a check-bearing variant for the retry test."""
    vault = tmp_path / "vault"
    (vault / "system" / "queue").mkdir(parents=True)
    (vault / "system" / "runs").mkdir(parents=True)

    check_counter = tmp_path / "check-counter"
    registry = {
        "version": 1,
        "skills": [
            {
                "id": "ask-claude",
                "label": "Ask Claude",
                "deck": True,
                "engine": "claude-cli",
                "args": [{"name": "prompt", "required": True, "type": "string"}],
                "engine_config": {"binary": str(stub_claude)},
            },
            {
                "id": "unconfigured-claude",
                "label": "Unconfigured Claude",
                "deck": True,
                "engine": "claude-cli",
                "args": [{"name": "prompt", "required": True, "type": "string"}],
                "engine_config": {"binary": str(tmp_path / "no-such-claude-binary")},
            },
            {
                "id": "checked-claude",
                "label": "Checked Claude",
                "deck": True,
                "engine": "claude-cli",
                "args": [{"name": "prompt", "required": True, "type": "string"}],
                "engine_config": {"binary": str(stub_claude)},
                "check": (
                    f'if [ -f "{check_counter}" ]; then exit 0; '
                    f'else touch "{check_counter}"; echo "needs a retry"; exit 1; fi'
                ),
            },
        ],
    }
    (vault / "system" / "skills.json").write_text(json.dumps(registry))
    return vault


def test_claude_cli_job_enqueued_via_api_executes_end_to_end(client, tmp_vault, monkeypatch):
    monkeypatch.setenv("CLAUDE_STUB_OUTPUT", "here is your answer")
    res = client.post("/jobs", json={"skill": "ask-claude", "args": {"prompt": "what is chicago"}})
    assert res.status_code == 201
    job_id = res.json()["id"]

    from vaultos.main import app

    runner = Runner(app.state.conn, app.state.registry, app.state.settings)
    claimed = runner.run_once()
    assert claimed is True

    detail = client.get(f"/jobs/{job_id}").json()
    assert detail["status"] == "ok"
    assert detail["exit_code"] == 0
    assert detail["summary"] == "here is your answer"


def test_claude_cli_misconfigured_binary_fails_fast_via_runner(client, tmp_vault):
    res = client.post("/jobs", json={"skill": "unconfigured-claude", "args": {"prompt": "hi"}})
    job_id = res.json()["id"]

    from vaultos.main import app

    Runner(app.state.conn, app.state.registry, app.state.settings).run_once()

    detail = client.get(f"/jobs/{job_id}").json()
    assert detail["status"] == "error"
    assert "unconfigured-claude" in detail["summary"] or "failed to run" in detail["summary"]


def test_claude_cli_timeout_fails_job_via_runner(client, tmp_vault, monkeypatch):
    monkeypatch.setenv("CLAUDE_STUB_MODE", "sleep")
    monkeypatch.setenv("CLAUDE_STUB_SLEEP_S", "5")

    from vaultos.main import app

    # Patch the skill's timeout down for this one job rather than needing a
    # separate registry fixture -- engine_config is read fresh from the
    # in-memory registry object each run.
    skill = app.state.registry.get("ask-claude")
    object.__setattr__(skill, "engine_config", {**skill.engine_config, "timeout_s": 0.2})

    res = client.post("/jobs", json={"skill": "ask-claude", "args": {"prompt": "hi"}})
    job_id = res.json()["id"]

    Runner(app.state.conn, app.state.registry, app.state.settings).run_once()

    detail = client.get(f"/jobs/{job_id}").json()
    assert detail["status"] == "error"
    assert "failed to run" in detail["summary"] or "timed out" in detail["summary"].lower()


def test_claude_cli_retry_context_appears_in_retried_invocation(client, tmp_vault, monkeypatch, tmp_path):
    log_path = tmp_path / "argv.log"
    monkeypatch.setenv("CLAUDE_STUB_LOG", str(log_path))

    res = client.post("/jobs", json={"skill": "checked-claude", "args": {"prompt": "original prompt text"}})
    assert res.status_code == 201
    job_id = res.json()["id"]

    from vaultos.main import app

    events = []
    runner = Runner(app.state.conn, app.state.registry, app.state.settings, emit=events.append)
    claimed = runner.run_once()
    assert claimed is True

    detail = client.get(f"/jobs/{job_id}").json()
    assert detail["status"] == "ok"

    invocations = log_path.read_text().split("=== invocation ===\n")[1:]
    assert len(invocations) == 2, "claude-cli should be invoked once, then once more on retry"

    first_prompt = invocations[0].strip()
    retried_prompt = invocations[1].strip()
    assert first_prompt == "original prompt text"
    assert "original prompt text" in retried_prompt
    assert "needs a retry" in retried_prompt, (
        "the retried invocation's prompt argument must carry the failed "
        "check's stdout as failure context"
    )

    assert len(events) == 1
    assert events[0]["check"] == {"passed": True, "attempt": 2}
