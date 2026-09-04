import json
from datetime import datetime, timedelta, timezone

CSV_HEADER = "timestamp,source,metric,value,status,error\n"


def _iso(dt):
    return dt.isoformat().replace("+00:00", "Z")


def _write_csv(vault_root, rows):
    metrics_dir = vault_root / "system" / "metrics"
    metrics_dir.mkdir(parents=True, exist_ok=True)
    body = CSV_HEADER + "".join(rows)
    (metrics_dir / "metrics.csv").write_text(body)


def _write_last_pull(vault_root, data):
    metrics_dir = vault_root / "system" / "metrics"
    metrics_dir.mkdir(parents=True, exist_ok=True)
    (metrics_dir / "last-pull.json").write_text(json.dumps(data))


def test_integrations_empty_when_no_data(client):
    res = client.get("/integrations")
    assert res.status_code == 200
    assert res.json() == []


def test_integrations_includes_source_missing_from_last_pull_json(client, tmp_vault):
    # ai_wire has metrics.csv rows but (per the real vault) can be absent from
    # last-pull.json -- it must still show up, per ADR-0003.
    now = datetime.now(timezone.utc)
    _write_csv(tmp_vault, [f"{_iso(now)},ai_wire,items_today,3.0,ok,\n"])
    _write_last_pull(tmp_vault, {"vault": {"status": "ok", "ts": _iso(now), "error": ""}})

    res = client.get("/integrations")
    sources = {entry["source"] for entry in res.json()}
    assert "ai_wire" in sources
    assert "vault" in sources


def test_integrations_source_with_no_last_pull_entry_uses_csv_timestamp(client, tmp_vault):
    now = datetime.now(timezone.utc)
    _write_csv(tmp_vault, [f"{_iso(now)},ai_wire,items_today,3.0,ok,\n"])

    res = client.get("/integrations")
    entry = next(e for e in res.json() if e["source"] == "ai_wire")
    assert entry["last_pull_ts"] == _iso(now)
    assert entry["status"] is None
    assert entry["stale"] is False


def test_integrations_fresh_source_not_stale(client, tmp_vault):
    now = datetime.now(timezone.utc)
    _write_csv(tmp_vault, [f"{_iso(now)},vault,new_files_24h,5.0,ok,\n"])
    _write_last_pull(tmp_vault, {"vault": {"status": "ok", "ts": _iso(now), "error": ""}})

    res = client.get("/integrations")
    entry = next(e for e in res.json() if e["source"] == "vault")
    assert entry["stale"] is False
    assert entry["age_s"] < 5


def test_integrations_stale_source_flagged_after_15_minutes(client, tmp_vault):
    old = datetime.now(timezone.utc) - timedelta(minutes=20)
    _write_csv(tmp_vault, [f"{_iso(old)},vault,new_files_24h,5.0,ok,\n"])
    _write_last_pull(tmp_vault, {"vault": {"status": "ok", "ts": _iso(old), "error": ""}})

    res = client.get("/integrations")
    entry = next(e for e in res.json() if e["source"] == "vault")
    assert entry["stale"] is True


def test_integrations_response_shape(client, tmp_vault):
    now = datetime.now(timezone.utc)
    _write_csv(tmp_vault, [f"{_iso(now)},vault,new_files_24h,5.0,ok,\n"])
    _write_last_pull(tmp_vault, {"vault": {"status": "ok", "ts": _iso(now), "error": ""}})

    res = client.get("/integrations")
    entry = res.json()[0]
    assert set(entry.keys()) == {"source", "status", "last_pull_ts", "age_s", "stale"}
