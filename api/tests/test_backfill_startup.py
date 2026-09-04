import json

from fastapi.testclient import TestClient


def test_spine_backfills_queue_file_present_before_startup(tmp_vault, tmp_path, monkeypatch):
    # Write the queue file BEFORE the spine ever starts -- proves backfill runs at
    # startup, not just live-event handling. Can't use the shared `client` fixture
    # here since it triggers lifespan startup as part of its own setup.
    job_id = "backfill-me"
    (tmp_vault / "system" / "queue" / f"{job_id}.json").write_text(
        json.dumps({"id": job_id, "skill": "ai-wire", "args": {}, "ts": "2026-08-09T00:00:00Z", "source": "obsidian"})
    )

    monkeypatch.setenv("VAULT_ROOT", str(tmp_vault))
    monkeypatch.setenv("VAULTOS_DB", str(tmp_path / "vaultos.db"))
    from vaultos.main import app

    with TestClient(app) as client:
        res = client.get(f"/jobs/{job_id}")
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "queued"
        assert body["skill"] == "ai-wire"
        assert body["source"] == "obsidian"
