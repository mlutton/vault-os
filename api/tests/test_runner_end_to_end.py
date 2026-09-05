"""End-to-end coverage of vaultos.runner driven through the same seam the
rest of the suite uses: enqueue via the FastAPI test client, then run the
runner against the app's own conn/registry/settings, then observe the job
record via the API. Mirrors test_api_jobs.py's pattern (see conftest.py's
`client`/`tmp_vault` fixtures), extended with a script-engine skill."""

import json
import stat

import pytest

from vaultos.jobs import store
from vaultos.runner.core import Runner


@pytest.fixture
def tmp_vault(tmp_path):
    """Overrides conftest's tmp_vault to add a script-engine skill (and its
    backing shell script) alongside the existing claude-engine fixtures, so
    this file's tests don't disturb test_api_jobs.py's own registry."""
    vault = tmp_path / "vault"
    (vault / "system" / "queue").mkdir(parents=True)
    (vault / "system" / "runs").mkdir(parents=True)

    script_path = tmp_path / "hello.sh"
    # `who`, when the caller supplies it, is passed straight through by the
    # test's own arg-substitution assertion (test_script_job_enqueued_via_api...);
    # everything else in this file just needs the job to succeed, so the
    # shell script defaults it rather than requiring every caller to pass one.
    script_path.write_text(
        '#!/bin/sh\nmkdir -p "$(dirname "$1")"\necho "hello ${2:-world}" > "$1"\n'
    )
    script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC)

    registry = {
        "version": 1,
        "skills": [
            {
                "id": "metrics-pull",
                "label": "Pull Metrics",
                "deck": True,
                "engine": "claude",
                "args": [],
            },
            {
                "id": "hello-script",
                "label": "Hello Script",
                "deck": True,
                "engine": "script",
                "args": [{"name": "who", "required": False, "type": "string"}],
                "engine_config": {
                    "argv": [
                        str(script_path),
                        "{vault_root}/inbox/reports/hello-script/{job_id}.md",
                        "{who}",
                    ],
                    "deliverable": "inbox/reports/hello-script/{job_id}.md",
                },
            },
            {
                "id": "unavailable-engine-skill",
                "label": "Unavailable Engine",
                "deck": True,
                "engine": "nonexistent-engine",
                "args": [],
            },
        ],
    }
    (vault / "system" / "skills.json").write_text(json.dumps(registry))
    return vault


def test_script_job_enqueued_via_api_executes_end_to_end(client, tmp_vault):
    res = client.post("/jobs", json={"skill": "hello-script", "args": {"who": "chicago"}})
    assert res.status_code == 201
    job_id = res.json()["id"]

    from vaultos.main import app

    runner = Runner(app.state.conn, app.state.registry, app.state.settings)
    claimed = runner.run_once()
    assert claimed is True

    detail = client.get(f"/jobs/{job_id}").json()
    assert detail["status"] == "ok"
    assert detail["exit_code"] == 0
    assert detail["deliverables"] == [f"inbox/reports/hello-script/{job_id}.md"]

    deliverable = tmp_vault / "inbox" / "reports" / "hello-script" / f"{job_id}.md"
    assert deliverable.read_text().strip() == "hello chicago"

    # Job disappears from the active list once terminal.
    assert job_id not in {j["id"] for j in client.get("/jobs").json()}


def test_script_job_events_recorded_and_visible_via_runs_style_query(client, tmp_vault):
    res = client.post("/jobs", json={"skill": "hello-script", "args": {"who": "world"}})
    job_id = res.json()["id"]

    from vaultos.main import app

    Runner(app.state.conn, app.state.registry, app.state.settings).run_once()

    job = store.get_job(app.state.conn, job_id)
    assert job.status == "ok"
    assert job.ts_started is not None
    assert job.ts_completed is not None
    assert job.runner_pid is not None


def test_unknown_engine_job_fails_fast_via_runner(client, tmp_vault):
    res = client.post("/jobs", json={"skill": "unavailable-engine-skill"})
    job_id = res.json()["id"]

    from vaultos.main import app

    Runner(app.state.conn, app.state.registry, app.state.settings).run_once()

    detail = client.get(f"/jobs/{job_id}").json()
    assert detail["status"] == "error"
    assert "nonexistent-engine" in detail["summary"]


def test_chaining_triggers_after_runner_posts_terminal_event(client, tmp_vault, monkeypatch):
    import vaultos.api.jobs as jobs_module

    monkeypatch.setattr(jobs_module, "CHAIN_MAP", {"hello-script": "metrics-pull"})

    res = client.post("/jobs", json={"skill": "hello-script", "args": {"who": "world"}})
    job_id = res.json()["id"]

    from vaultos.main import app

    Runner(app.state.conn, app.state.registry, app.state.settings).run_once()

    active = client.get("/jobs").json()
    chained = [j for j in active if j["skill"] == "metrics-pull"]
    assert len(chained) == 1
    assert chained[0]["source"] == f"chain:hello-script:{job_id}"


def test_two_runners_race_only_one_executes(client, tmp_vault):
    res = client.post("/jobs", json={"skill": "hello-script", "args": {"who": "world"}})
    job_id = res.json()["id"]

    from vaultos.main import app

    runner_a = Runner(app.state.conn, app.state.registry, app.state.settings, pid=111)
    runner_b = Runner(app.state.conn, app.state.registry, app.state.settings, pid=222)

    claimed_a = runner_a.run_once()
    claimed_b = runner_b.run_once()

    # Only one runner actually found (and ran) the job; the other found an
    # empty queue.
    assert {claimed_a, claimed_b} == {True, False}

    detail = client.get(f"/jobs/{job_id}").json()
    assert detail["status"] == "ok"
    assert detail["runner_pid"] in (111, 222)
