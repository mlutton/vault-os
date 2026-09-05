"""One end-to-end smoke test per ported batch-2 skill (ticket #26), mirroring
`test_runner_prompts_batch1_end_to_end.py`'s pattern exactly: enqueue via the
FastAPI test client, run `vaultos.runner` against a stub `claude` binary
(never a real vendor CLI, no keys, no network), observe the job record via
the API, and confirm the stub's logged argv actually carries the
prompt-builder registry's built prompt.

Built up one skill per commit, matching the ticket's per-skill commit plan;
this file currently covers `acquire` and `daily-topic-digest`.
"""

import json
import stat

import pytest

from vaultos.runner.core import Runner
from vaultos.runner.prompts import today_date


def _write_stub(path, body):
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


@pytest.fixture
def stub_claude(tmp_path):
    return _write_stub(
        tmp_path / "claude",
        "#!/bin/sh\n"
        'echo "=== invocation ===" >> "$CLAUDE_STUB_LOG"\n'
        'for a in "$@"; do printf \'%s\\n\' "$a" >> "$CLAUDE_STUB_LOG"; done\n'
        'if [ -n "$CLAUDE_STUB_DELIVERABLE" ]; then\n'
        '  mkdir -p "$(dirname "$CLAUDE_STUB_DELIVERABLE")"\n'
        '  echo "stub deliverable content" > "$CLAUDE_STUB_DELIVERABLE"\n'
        "fi\n"
        'echo "stub ok"\n',
    )


@pytest.fixture
def tmp_vault(tmp_path, stub_claude):
    """Overrides conftest's tmp_vault: registers every batch-2 skill built so
    far against the same stub `claude` binary, all routed through the
    claude-cli engine -- exactly how they'll be registered in the real
    vault's system/skills.json once this ticket ships."""
    vault = tmp_path / "vault"
    (vault / "system" / "queue").mkdir(parents=True)
    (vault / "system" / "runs").mkdir(parents=True)

    def skill_entry(skill_id, args):
        return {
            "id": skill_id, "label": skill_id, "deck": True, "engine": "claude-cli",
            "args": args, "engine_config": {"binary": str(stub_claude)},
        }

    registry = {
        "version": 1,
        "skills": [
            skill_entry("acquire", []),
            skill_entry("daily-topic-digest", []),
        ],
    }
    (vault / "system" / "skills.json").write_text(json.dumps(registry))
    return vault


def _invoked_prompt(log_path):
    """The prompt's own text spans many lines, so this returns the FULL
    block logged for the last invocation (see the batch-1 e2e file's
    identically-named helper for the full rationale)."""
    return log_path.read_text().split("=== invocation ===\n")[-1].strip("\n")


def test_acquire_end_to_end(client, tmp_vault, monkeypatch, tmp_path):
    from vaultos.main import app

    date = today_date(app.state.settings)
    deliverable_rel = f"inbox/research/{date}-acquire.md"
    log_path = tmp_path / "argv.log"
    monkeypatch.setenv("CLAUDE_STUB_LOG", str(log_path))
    monkeypatch.setenv("CLAUDE_STUB_DELIVERABLE", str(tmp_vault / deliverable_rel))

    res = client.post("/jobs", json={"skill": "acquire", "args": {}})
    assert res.status_code == 201, res.text
    job_id = res.json()["id"]

    runner = Runner(app.state.conn, app.state.registry, app.state.settings)
    assert runner.run_once() is True
    detail = client.get(f"/jobs/{job_id}").json()

    assert detail["status"] == "ok"
    assert detail["deliverables"] == [deliverable_rel]
    prompt = _invoked_prompt(log_path)
    assert "Step 1 -- fetch AND synthesize" in prompt
    assert deliverable_rel in prompt


def test_daily_topic_digest_end_to_end(client, tmp_vault, monkeypatch, tmp_path):
    from vaultos.main import app

    date = today_date(app.state.settings)
    deliverable_rel = f"inbox/reports/daily-topic-digest/{date}-daily-topic-digest.md"
    log_path = tmp_path / "argv.log"
    monkeypatch.setenv("CLAUDE_STUB_LOG", str(log_path))
    monkeypatch.setenv("CLAUDE_STUB_DELIVERABLE", str(tmp_vault / deliverable_rel))

    res = client.post("/jobs", json={"skill": "daily-topic-digest", "args": {}})
    assert res.status_code == 201, res.text
    job_id = res.json()["id"]

    runner = Runner(app.state.conn, app.state.registry, app.state.settings)
    assert runner.run_once() is True
    detail = client.get(f"/jobs/{job_id}").json()

    assert detail["status"] == "ok"
    assert detail["deliverables"] == [deliverable_rel]
    prompt = _invoked_prompt(log_path)
    assert "propose ranked article topics" in prompt
    assert deliverable_rel in prompt
