from datetime import datetime, timedelta, timezone

CSV_HEADER = "timestamp,source,metric,value,status,error\n"


def _write_csv(vault_root, rows):
    metrics_dir = vault_root / "system" / "metrics"
    metrics_dir.mkdir(parents=True, exist_ok=True)
    body = CSV_HEADER + "".join(rows)
    (metrics_dir / "metrics.csv").write_text(body)


def _iso(dt):
    return dt.isoformat().replace("+00:00", "Z")


def test_history_returns_ascending_series_for_pair(client, tmp_vault):
    now = datetime.now(timezone.utc)
    _write_csv(
        tmp_vault,
        [
            f"{_iso(now - timedelta(days=2))},vault,new_files_24h,50.0,ok,\n",
            f"{_iso(now - timedelta(days=1))},vault,new_files_24h,52.0,ok,\n",
            f"{_iso(now)},vault,new_files_24h,55.0,ok,\n",
            f"{_iso(now)},substack,subscribers,10.0,ok,\n",
        ],
    )
    res = client.get("/metrics/vault/new_files_24h/history")
    assert res.status_code == 200
    body = res.json()
    assert [entry["value"] for entry in body] == [50.0, 52.0, 55.0]


def test_history_respects_days_window(client, tmp_vault):
    now = datetime.now(timezone.utc)
    _write_csv(
        tmp_vault,
        [
            f"{_iso(now - timedelta(days=40))},vault,new_files_24h,1.0,ok,\n",
            f"{_iso(now - timedelta(days=1))},vault,new_files_24h,2.0,ok,\n",
        ],
    )
    res = client.get("/metrics/vault/new_files_24h/history", params={"days": 7})
    assert res.status_code == 200
    values = [entry["value"] for entry in res.json()]
    assert values == [2.0]


def test_history_unknown_pair_returns_empty_array_not_404(client, tmp_vault):
    _write_csv(tmp_vault, ["2026-08-09T08:00:00Z,vault,new_files_24h,50.0,ok,\n"])
    res = client.get("/metrics/nonsense/nonsense/history")
    assert res.status_code == 200
    assert res.json() == []


def test_history_no_csv_returns_empty_array(client):
    res = client.get("/metrics/vault/new_files_24h/history")
    assert res.status_code == 200
    assert res.json() == []


def test_history_response_shape(client, tmp_vault):
    _write_csv(tmp_vault, ["2026-08-09T08:00:00Z,vault,new_files_24h,50.0,ok,\n"])
    res = client.get("/metrics/vault/new_files_24h/history", params={"days": 365})
    entry = res.json()[0]
    assert set(entry.keys()) == {"timestamp", "value", "status", "error"}
