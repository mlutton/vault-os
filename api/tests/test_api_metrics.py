from datetime import datetime, timedelta, timezone

import pytest

CSV_HEADER = "timestamp,source,metric,value,status,error\n"


def _write_csv(vault_root, rows):
    metrics_dir = vault_root / "system" / "metrics"
    metrics_dir.mkdir(parents=True, exist_ok=True)
    body = CSV_HEADER + "".join(rows)
    (metrics_dir / "metrics.csv").write_text(body)


def test_get_metrics_returns_flat_array_of_latest_per_pair(client, tmp_vault):
    _write_csv(
        tmp_vault,
        [
            "2026-08-09T08:00:00Z,vault,new_files_24h,50.0,ok,\n",
            "2026-08-09T08:05:00Z,vault,new_files_24h,52.0,ok,\n",
            "2026-08-09T08:00:00Z,substack,subscribers,10.0,ok,\n",
        ],
    )
    res = client.get("/metrics")
    assert res.status_code == 200
    body = res.json()
    assert len(body) == 2
    by_pair = {(entry["source"], entry["metric"]): entry for entry in body}
    assert by_pair[("vault", "new_files_24h")]["value"] == 52.0
    assert by_pair[("vault", "new_files_24h")]["delta"] == 2.0
    assert by_pair[("substack", "subscribers")]["delta"] is None


def test_get_metrics_surfaces_error_rows_as_is(client, tmp_vault):
    _write_csv(
        tmp_vault,
        ["2026-08-09T08:00:00Z,ai_wire,items_today,0.0,error,pull failed\n"],
    )
    res = client.get("/metrics")
    body = res.json()
    assert len(body) == 1
    assert body[0]["status"] == "error"
    assert body[0]["error"] == "pull failed"
    assert body[0]["value"] == 0.0


def test_get_metrics_empty_when_no_csv(client):
    res = client.get("/metrics")
    assert res.status_code == 200
    assert res.json() == []


def test_get_metrics_response_shape(client, tmp_vault):
    _write_csv(tmp_vault, ["2026-08-09T08:00:00Z,vault,new_files_24h,50.0,ok,\n"])
    res = client.get("/metrics")
    entry = res.json()[0]
    assert set(entry.keys()) == {
        "source",
        "metric",
        "value",
        "delta",
        "delta_week",
        "timestamp",
        "status",
        "error",
    }


def test_get_metrics_keeps_latest_blank_error(client, tmp_vault):
    _write_csv(
        tmp_vault,
        [
            "2026-08-09T08:00:00Z,ai_wire,items_today,7,ok,\n",
            "2026-08-09T09:00:00Z,ai_wire,items_today,,error,unreadable report\n",
        ],
    )
    res = client.get("/metrics")
    assert res.status_code == 200
    assert res.json() == [
        {
            "source": "ai_wire",
            "metric": "items_today",
            "value": None,
            "timestamp": "2026-08-09T09:00:00Z",
            "status": "error",
            "error": "unreadable report",
            "delta": None,
            "delta_week": None,
        }
    ]


@pytest.mark.parametrize(
    "tokens_value, tokens_status, expected_tokens", [("123", "ok", 123), ("", "error", None)]
)
def test_token_burn_skips_blank_error_in_trend(
    client, tmp_vault, tokens_value, tokens_status, expected_tokens
):
    now = datetime.now(timezone.utc)
    _write_csv(
        tmp_vault,
        [
            f"{(now - timedelta(minutes=20)).isoformat()},claude_code,cost_5h_usd,7,ok,\n",
            f"{(now - timedelta(minutes=10)).isoformat()},claude_code,cost_5h_usd,9,ok,\n",
            f"{now.isoformat()},claude_code,cost_5h_usd,,error,unreadable report\n",
            f"{now.isoformat()},claude_code,tokens_5h,{tokens_value},{tokens_status},\n",
        ],
    )
    res = client.get("/metrics/token-burn")
    assert res.status_code == 200
    body = res.json()
    assert body["cost_5h_usd"] is None
    assert body["pct"] is None
    assert body["tokens_5h"] == expected_tokens
    assert body["projection"] == 21
