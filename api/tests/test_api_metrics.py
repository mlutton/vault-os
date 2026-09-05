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
