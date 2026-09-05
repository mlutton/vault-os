"""One end-to-end smoke test per ported batch-1 skill (ticket #25), per the
runner spec's testing decision: "Plus one end-to-end smoke per ported skill
through the HTTP API with a stub engine." Mirrors
test_runner_claude_cli_end_to_end.py's pattern -- enqueue via the FastAPI
test client, run vaultos.runner against a stub `claude` binary (never a
real vendor CLI, no keys, no network), observe the job record via the API.

Each test additionally proves the prompt-builder registry's wiring actually
reached the subprocess call: the stub records its own invocation's argv to a
log file, and the deliverable path it's told to write (computed here with
the exact same helpers `vaultos.runner.prompts` uses internally, so the
expected path always matches what the builder itself would produce) is what
the runner reports back as the job's deliverable."""

import json
import stat

import pytest

from vaultos.runner.core import Runner
from vaultos.runner.prompts import id8, today_date, tomorrow_date


def _write_stub(path, body):
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


@pytest.fixture
def stub_claude(tmp_path):
    """A `claude` stand-in that (1) logs its full argv (so a test can
    inspect the exact built prompt) and (2) writes the file named by
    CLAUDE_STUB_DELIVERABLE (so the engine's own post-run existence check --
    see claude_cli.py's `run()` -- finds a real deliverable, exactly like a
    real CLI run that actually wrote its output)."""
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


BATCH1_SKILL_IDS = [
    "plan-today",
    "plan-tomorrow",
    "vault-cleanup",
    "inbox-brief",
    "metrics-pull",
    "research-into-draft",
    "wiki-ingest",
    "visual-asset-proposal",
    "draft-persona-fanout",
]


@pytest.fixture
def tmp_vault(tmp_path, stub_claude):
    """Overrides conftest's tmp_vault: registers every batch-1 skill against
    the same stub `claude` binary, all routed through the claude-cli engine
    -- exactly how they'll be registered in the real vault's
    system/skills.json once this ticket ships."""
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
            skill_entry("plan-today", []),
            skill_entry("plan-tomorrow", []),
            skill_entry("vault-cleanup", []),
            skill_entry("inbox-brief", []),
            skill_entry("metrics-pull", []),
            skill_entry("research-into-draft", []),
            skill_entry(
                "wiki-ingest",
                [{"name": "source_path", "required": True, "type": "string"}],
            ),
            skill_entry(
                "visual-asset-proposal",
                [{"name": "article_path", "required": True, "type": "string"}],
            ),
            skill_entry(
                "draft-persona-fanout",
                [
                    {"name": "article_path", "required": True, "type": "string"},
                    {"name": "personas", "required": True, "type": "string"},
                ],
            ),
        ],
    }
    (vault / "system" / "skills.json").write_text(json.dumps(registry))
    return vault


def _run_and_fetch(client, monkeypatch, log_path, deliverable_abs, skill, args):
    monkeypatch.setenv("CLAUDE_STUB_LOG", str(log_path))
    monkeypatch.setenv("CLAUDE_STUB_DELIVERABLE", str(deliverable_abs))

    res = client.post("/jobs", json={"skill": skill, "args": args})
    assert res.status_code == 201, res.text
    job_id = res.json()["id"]

    from vaultos.main import app

    runner = Runner(app.state.conn, app.state.registry, app.state.settings)
    assert runner.run_once() is True

    return job_id, client.get(f"/jobs/{job_id}").json()


def _invoked_prompt(log_path):
    """The prompt's own text spans many lines (it's a multi-paragraph
    string), so this returns the FULL block logged for the last invocation,
    not just its last line -- every fixture skill here is registered with
    no base `args` and no `model`, so the prompt is the invocation's only
    argv element (see claude_cli.py: `argv = [binary, *args]` then the
    prompt is appended last -- with `args` empty, the prompt is all that's
    logged)."""
    return log_path.read_text().split("=== invocation ===\n")[-1].strip("\n")


def test_plan_today_end_to_end(client, tmp_vault, monkeypatch, tmp_path):
    from vaultos.main import app

    date = today_date(app.state.settings)
    deliverable_rel = f"daily-notes/{date}.md"
    log_path = tmp_path / "argv.log"
    job_id, detail = _run_and_fetch(
        client, monkeypatch, log_path, tmp_vault / deliverable_rel, "plan-today", {},
    )

    assert detail["status"] == "ok"
    assert detail["deliverables"] == [deliverable_rel]
    prompt = _invoked_prompt(log_path)
    assert "set up today's daily note" in prompt
    assert deliverable_rel in prompt


def test_plan_tomorrow_end_to_end(client, tmp_vault, monkeypatch, tmp_path):
    from vaultos.main import app

    deliverable_rel = f"daily-notes/{tomorrow_date(app.state.settings)}.md"
    log_path = tmp_path / "argv.log"
    job_id, detail = _run_and_fetch(
        client, monkeypatch, log_path, tmp_vault / deliverable_rel, "plan-tomorrow", {},
    )

    assert detail["status"] == "ok"
    assert detail["deliverables"] == [deliverable_rel]
    assert "draft tomorrow's daily note" in _invoked_prompt(log_path)


def test_vault_cleanup_end_to_end(client, tmp_vault, monkeypatch, tmp_path):
    from vaultos.main import app

    job_id_placeholder = "unused"
    log_path = tmp_path / "argv.log"

    # deliverable path embeds the SERVER-assigned job id (id8), unknowable
    # before the POST -- so post first with a throwaway deliverable target,
    # then recompute the real one from the returned job id and re-run via a
    # second job. Simpler: post, read back id8(job_id) from the API's own
    # job id, THEN set the env var and run -- run_once() only claims after
    # the env var is set, so this ordering is safe (the job stays queued
    # until run_once() executes).
    monkeypatch.setenv("CLAUDE_STUB_LOG", str(log_path))
    res = client.post("/jobs", json={"skill": "vault-cleanup", "args": {}})
    assert res.status_code == 201
    job_id = res.json()["id"]
    date = today_date(app.state.settings)
    deliverable_rel = f"inbox/reports/vault-cleanup/{date}-cleanup-{id8(job_id)}.md"
    monkeypatch.setenv("CLAUDE_STUB_DELIVERABLE", str(tmp_vault / deliverable_rel))

    runner = Runner(app.state.conn, app.state.registry, app.state.settings)
    assert runner.run_once() is True
    detail = client.get(f"/jobs/{job_id}").json()

    assert detail["status"] == "ok"
    assert detail["deliverables"] == [deliverable_rel]
    prompt = _invoked_prompt(log_path)
    assert "retention: ephemeral" in prompt
    assert deliverable_rel in prompt


def test_inbox_brief_end_to_end(client, tmp_vault, monkeypatch, tmp_path):
    from vaultos.main import app

    log_path = tmp_path / "argv.log"
    monkeypatch.setenv("CLAUDE_STUB_LOG", str(log_path))
    res = client.post("/jobs", json={"skill": "inbox-brief", "args": {}})
    job_id = res.json()["id"]
    date = today_date(app.state.settings)
    deliverable_rel = f"inbox/reports/inbox-briefs/{date}-inbox-brief-{id8(job_id)}.md"
    monkeypatch.setenv("CLAUDE_STUB_DELIVERABLE", str(tmp_vault / deliverable_rel))

    Runner(app.state.conn, app.state.registry, app.state.settings).run_once()
    detail = client.get(f"/jobs/{job_id}").json()

    assert detail["status"] == "ok"
    assert detail["deliverables"] == [deliverable_rel]
    prompt = _invoked_prompt(log_path)
    assert "Gmail MCP connector" in prompt
    assert 'action_items: [{"id"' in prompt


def test_metrics_pull_end_to_end(client, tmp_vault, monkeypatch, tmp_path):
    from vaultos.main import app

    log_path = tmp_path / "argv.log"
    monkeypatch.setenv("CLAUDE_STUB_LOG", str(log_path))
    res = client.post("/jobs", json={"skill": "metrics-pull", "args": {}})
    job_id = res.json()["id"]
    date = today_date(app.state.settings)
    deliverable_rel = f"inbox/reports/metrics-pull/{date}-{id8(job_id)}.md"
    monkeypatch.setenv("CLAUDE_STUB_DELIVERABLE", str(tmp_vault / deliverable_rel))

    Runner(app.state.conn, app.state.registry, app.state.settings).run_once()
    detail = client.get(f"/jobs/{job_id}").json()

    assert detail["status"] == "ok"
    assert detail["deliverables"] == [deliverable_rel]
    assert "eleven rows total" in _invoked_prompt(log_path)


def test_research_into_draft_end_to_end(client, tmp_vault, monkeypatch, tmp_path):
    from vaultos.main import app

    log_path = tmp_path / "argv.log"
    monkeypatch.setenv("CLAUDE_STUB_LOG", str(log_path))
    res = client.post("/jobs", json={"skill": "research-into-draft", "args": {}})
    job_id = res.json()["id"]
    date = today_date(app.state.settings)
    deliverable_rel = f"inbox/reports/research-into-draft/{date}-research-into-draft-{id8(job_id)}.md"
    monkeypatch.setenv("CLAUDE_STUB_DELIVERABLE", str(tmp_vault / deliverable_rel))

    Runner(app.state.conn, app.state.registry, app.state.settings).run_once()
    detail = client.get(f"/jobs/{job_id}").json()

    assert detail["status"] == "ok"
    assert detail["deliverables"] == [deliverable_rel]
    assert "research_report:" in _invoked_prompt(log_path)


def test_wiki_ingest_end_to_end(client, tmp_vault, monkeypatch, tmp_path):
    from vaultos.main import app

    source_path = "inbox/deep-research/2026-09-05-some-topic-deep-research.md"
    log_path = tmp_path / "argv.log"
    monkeypatch.setenv("CLAUDE_STUB_LOG", str(log_path))
    res = client.post("/jobs", json={"skill": "wiki-ingest", "args": {"source_path": source_path}})
    job_id = res.json()["id"]
    date = today_date(app.state.settings)
    deliverable_rel = f"inbox/reports/wiki-ingest/{date}-wiki-ingest-{id8(job_id)}.md"
    monkeypatch.setenv("CLAUDE_STUB_DELIVERABLE", str(tmp_vault / deliverable_rel))

    Runner(app.state.conn, app.state.registry, app.state.settings).run_once()
    detail = client.get(f"/jobs/{job_id}").json()

    assert detail["status"] == "ok"
    assert detail["deliverables"] == [deliverable_rel]
    prompt = _invoked_prompt(log_path)
    assert source_path in prompt
    assert "~/" not in prompt and "/home/" not in prompt


def test_wiki_ingest_missing_source_path_fails_fast(client, tmp_vault):
    res = client.post("/jobs", json={"skill": "wiki-ingest", "args": {"source_path": ""}})
    assert res.status_code == 400


def test_visual_asset_proposal_end_to_end(client, tmp_vault, monkeypatch, tmp_path):
    article_path = "writing/articles/my-piece/my-piece.md"
    deliverable_rel = "writing/articles/my-piece/visual-assets-proposal.md"
    log_path = tmp_path / "argv.log"
    job_id, detail = _run_and_fetch(
        client, monkeypatch, log_path, tmp_vault / deliverable_rel,
        "visual-asset-proposal", {"article_path": article_path},
    )

    assert detail["status"] == "ok"
    assert detail["deliverables"] == [deliverable_rel]
    prompt = _invoked_prompt(log_path)
    assert article_path in prompt
    assert "exactly one hero/cover image concept" in prompt


def test_draft_persona_fanout_end_to_end(client, tmp_vault, monkeypatch, tmp_path):
    article_path = "writing/articles/my-piece/my-piece.md"
    deliverable_rel = "writing/articles/my-piece/reviews/round-1/_summary.md"
    log_path = tmp_path / "argv.log"
    job_id, detail = _run_and_fetch(
        client, monkeypatch, log_path, tmp_vault / deliverable_rel,
        "draft-persona-fanout", {"article_path": article_path, "personas": "gem, cto"},
    )

    assert detail["status"] == "ok"
    assert detail["deliverables"] == [deliverable_rel]
    prompt = _invoked_prompt(log_path)
    assert "Personas for this run: gem, cto" in prompt
