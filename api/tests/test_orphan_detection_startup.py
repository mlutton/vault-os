import json
import time

from fastapi.testclient import TestClient


def test_stuck_job_gets_orphaned_shortly_after_startup(tmp_vault, tmp_path, monkeypatch):
    # A run record left mid-execution (spine crashed while this job was running) --
    # no runner-status.json on disk at all, so the runner is unambiguously gone.
    # Backfill (startup) will create this as a "running" job; the orphan-detection
    # background task's first iteration should then flip it to "orphaned" shortly
    # after startup, without waiting for the full 60s period.
    job_id = "stuck-job"
    record = {
        "id": job_id,
        "skill": "ai-wire",
        "args": {},
        "source": "api",
        "ts_queued": "2026-08-09T00:00:00Z",
        "ts_started": "2026-08-09T00:00:01Z",
        "ts_completed": None,
        "status": "running",
        "exit_code": None,
        "summary": None,
        "md_path": f"system/runs/{job_id}.md",
        "log_path": f"system/runs/{job_id}.md",
        "deliverable_path": None,
    }
    (tmp_vault / "system" / "runs" / f"{job_id}.json").write_text(json.dumps(record))

    monkeypatch.setenv("VAULT_ROOT", str(tmp_vault))
    monkeypatch.setenv("VAULTOS_DB", str(tmp_path / "vaultos.db"))
    from vaultos.main import app

    with TestClient(app) as client:
        res = client.get(f"/jobs/{job_id}")
        assert res.status_code == 200  # backfill ran, job exists at all
        assert res.json()["status"] in ("running", "orphaned")

        deadline = time.monotonic() + 2.0
        status = res.json()["status"]
        while status != "orphaned" and time.monotonic() < deadline:
            status = client.get(f"/jobs/{job_id}").json()["status"]
            time.sleep(0.05)

        assert status == "orphaned"
