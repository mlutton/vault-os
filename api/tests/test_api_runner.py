import json
from datetime import datetime, timedelta, timezone


def _write_status(vault_root, ts, **overrides):
    system = vault_root / "system"
    system.mkdir(parents=True, exist_ok=True)
    body = {"ts": ts, "pid": 123, "active": 1, "pending": 0, **overrides}
    (system / "runner-status.json").write_text(json.dumps(body))


def test_get_runner_alive(client, tmp_vault):
    ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    _write_status(tmp_vault, ts)

    res = client.get("/runner")
    assert res.status_code == 200
    body = res.json()
    assert body["alive"] is True
    assert body["pid"] == 123
    assert body["active"] == 1
    assert body["pending"] == 0
    assert body["ts"] == ts


def test_get_runner_stale_not_alive(client, tmp_vault):
    stale = (datetime.now(timezone.utc) - timedelta(seconds=200)).isoformat().replace("+00:00", "Z")
    _write_status(tmp_vault, stale)

    res = client.get("/runner")
    assert res.status_code == 200
    assert res.json()["alive"] is False


def test_get_runner_no_heartbeat_file_not_reporting(client):
    res = client.get("/runner")
    assert res.status_code == 200
    body = res.json()
    assert body["alive"] is False
    assert body["ts"] is None
    assert body["pid"] is None
    assert body["active"] is None
    assert body["pending"] is None
    assert body["version"] is None
    assert body["busy"] is False
    assert body["max_concurrent"] is None
    assert body["heartbeat_age_s"] is None


def test_get_runner_response_has_exactly_nine_fields(client, tmp_vault):
    ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    _write_status(tmp_vault, ts, busy=True, version="1.0.1", max_concurrent=3, in_flight=[])

    res = client.get("/runner")
    body = res.json()
    assert set(body.keys()) == {
        "ts",
        "pid",
        "active",
        "pending",
        "alive",
        "version",
        "busy",
        "max_concurrent",
        "heartbeat_age_s",
    }


def test_get_runner_reports_version_busy_max_concurrent_and_age(client, tmp_vault):
    ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    _write_status(tmp_vault, ts, busy=True, version="1.0.1", max_concurrent=3)

    res = client.get("/runner")
    body = res.json()
    assert body["version"] == "1.0.1"
    assert body["busy"] is True
    assert body["max_concurrent"] == 3
    assert body["heartbeat_age_s"] is not None
    assert body["heartbeat_age_s"] >= 0
