import json
from datetime import datetime, timedelta, timezone

from vaultos.vault.runner import read_heartbeat


def _write_status(vault_root, ts, **overrides):
    system = vault_root / "system"
    system.mkdir(parents=True, exist_ok=True)
    body = {"ts": ts, "pid": 123, "active": 1, "pending": 0, **overrides}
    (system / "runner-status.json").write_text(json.dumps(body))


def test_read_heartbeat_missing_file(tmp_path):
    assert read_heartbeat(tmp_path) is None


def test_read_heartbeat_fresh_is_alive(tmp_path):
    ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    _write_status(tmp_path, ts)
    hb = read_heartbeat(tmp_path)
    assert hb.alive is True
    assert hb.pid == 123


def test_read_heartbeat_parses_version_busy_max_concurrent(tmp_path):
    ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    _write_status(tmp_path, ts, version="1.0.1", busy=True, max_concurrent=3)
    hb = read_heartbeat(tmp_path)
    assert hb.version == "1.0.1"
    assert hb.busy is True
    assert hb.max_concurrent == 3
    assert hb.heartbeat_age_s is not None
    assert hb.heartbeat_age_s >= 0


def test_read_heartbeat_defaults_when_fields_absent(tmp_path):
    ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    _write_status(tmp_path, ts)
    hb = read_heartbeat(tmp_path)
    assert hb.version == "?"
    assert hb.busy is False
    assert hb.max_concurrent == 0


def test_read_heartbeat_stale_is_not_alive(tmp_path):
    stale = (datetime.now(timezone.utc) - timedelta(seconds=200)).isoformat().replace("+00:00", "Z")
    _write_status(tmp_path, stale)
    hb = read_heartbeat(tmp_path)
    assert hb.alive is False


def test_read_heartbeat_malformed_json(tmp_path):
    system = tmp_path / "system"
    system.mkdir(parents=True)
    (system / "runner-status.json").write_text("{not json")
    assert read_heartbeat(tmp_path) is None
