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


def _write_last_pull(vault_root, ts):
    metrics_dir = vault_root / "system" / "metrics"
    metrics_dir.mkdir(parents=True, exist_ok=True)
    (metrics_dir / "last-pull.json").write_text(
        json.dumps({"claude_code": {"status": "ok", "ts": ts, "error": ""}})
    )


def test_token_burn_returns_latest_and_budget(client, tmp_vault):
    now = datetime.now(timezone.utc)
    _write_csv(
        tmp_vault,
        [
            f"{_iso(now - timedelta(minutes=10))},claude_code,tokens_5h,1000.0,ok,\n",
            f"{_iso(now - timedelta(minutes=10))},claude_code,cost_5h_usd,10.0,ok,\n",
            f"{_iso(now)},claude_code,tokens_5h,1200.0,ok,\n",
            f"{_iso(now)},claude_code,cost_5h_usd,12.0,ok,\n",
        ],
    )
    _write_last_pull(tmp_vault, _iso(now))

    res = client.get("/metrics/token-burn")
    assert res.status_code == 200
    body = res.json()
    assert body["tokens_5h"] == 1200.0
    assert body["cost_5h_usd"] == 12.0
    assert body["budget"] == 100.0  # default TOKEN_BUDGET_5H_USD
    assert body["pct"] == 0.12
    assert body["projection"] is not None
    assert body["freshness_s"] is not None
    assert body["freshness_s"] < 5


def test_token_burn_no_data_returns_nulls_but_keeps_budget(client):
    res = client.get("/metrics/token-burn")
    assert res.status_code == 200
    body = res.json()
    assert body["tokens_5h"] is None
    assert body["cost_5h_usd"] is None
    assert body["pct"] is None
    assert body["projection"] is None
    assert body["freshness_s"] is None
    assert body["budget"] == 100.0


def test_token_burn_response_shape(client):
    res = client.get("/metrics/token-burn")
    assert set(res.json().keys()) == {
        "tokens_5h", "cost_5h_usd", "budget", "pct", "projection", "freshness_s",
    }
