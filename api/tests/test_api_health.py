import json
import shutil
from datetime import datetime, timezone


def test_health_reports_registry_and_runner(client, tmp_vault):
    ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    (tmp_vault / "system" / "runner-status.json").write_text(
        json.dumps({"ts": ts, "pid": 1, "active": 0, "pending": 0})
    )
    res = client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["registry_version"] == 1
    assert body["runner"]["alive"] is True


def test_health_without_runner_heartbeat(client):
    res = client.get("/health")
    body = res.json()
    assert body["ok"] is True
    assert body["runner"]["alive"] is False


def test_health_reports_not_ok_when_vault_removed(client, tmp_vault):
    shutil.rmtree(tmp_vault)
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["ok"] is False
