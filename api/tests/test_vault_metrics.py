from datetime import datetime, timedelta, timezone

from vaultos.vault.metrics import (
    MetricSample,
    compute_delta,
    compute_delta_week,
    latest_metrics,
    parse_ts,
    read_last_pull,
    read_metrics_csv,
)

CSV_HEADER = "timestamp,source,metric,value,status,error\n"


def _write_csv(vault_root, rows):
    metrics_dir = vault_root / "system" / "metrics"
    metrics_dir.mkdir(parents=True, exist_ok=True)
    body = CSV_HEADER + "".join(rows)
    (metrics_dir / "metrics.csv").write_text(body)


def _iso(dt):
    return dt.isoformat().replace("+00:00", "Z")


def test_read_metrics_csv_missing_file_returns_empty(tmp_path):
    assert read_metrics_csv(tmp_path) == []


def test_read_metrics_csv_parses_rows(tmp_path):
    _write_csv(
        tmp_path,
        [
            "2026-08-09T08:40:01Z,vault,new_files_24h,52.0,ok,\n",
            "2026-08-09T08:40:01Z,claude_code,tokens_5h,5666403.09,ok,\n",
        ],
    )
    samples = read_metrics_csv(tmp_path)
    assert len(samples) == 2
    assert samples[0] == MetricSample(
        timestamp="2026-08-09T08:40:01Z",
        source="vault",
        metric="new_files_24h",
        value=52.0,
        status="ok",
        error="",
    )


def test_read_metrics_csv_skips_unparseable_rows(tmp_path):
    _write_csv(
        tmp_path,
        [
            "2026-08-09T08:40:01Z,vault,new_files_24h,52.0,ok,\n",
            "not-a-timestamp,vault,broken,not-a-number,ok,\n",
            "2026-08-09T08:41:01Z,vault,new_files_24h,53.0,ok,\n",
        ],
    )
    samples = read_metrics_csv(tmp_path)
    assert len(samples) == 2
    assert all(s.metric == "new_files_24h" for s in samples)


def test_read_metrics_csv_keeps_error_rows(tmp_path):
    _write_csv(
        tmp_path,
        ["2026-08-09T08:40:01Z,ai_wire,items_today,0.0,error,pull failed\n"],
    )
    samples = read_metrics_csv(tmp_path)
    assert len(samples) == 1
    assert samples[0].status == "error"
    assert samples[0].error == "pull failed"


def test_latest_metrics_picks_most_recent_per_pair(tmp_path):
    _write_csv(
        tmp_path,
        [
            "2026-08-09T08:00:00Z,vault,new_files_24h,50.0,ok,\n",
            "2026-08-09T08:05:00Z,vault,new_files_24h,52.0,ok,\n",
            "2026-08-09T08:00:00Z,substack,subscribers,10.0,ok,\n",
        ],
    )
    samples = read_metrics_csv(tmp_path)
    latest = latest_metrics(samples)
    by_pair = {(s.source, s.metric): s for s in latest}
    assert len(latest) == 2
    assert by_pair[("vault", "new_files_24h")].value == 52.0
    assert by_pair[("substack", "subscribers")].value == 10.0


def test_compute_delta_vs_immediately_preceding_sample():
    samples = [
        MetricSample("2026-08-09T08:00:00Z", "vault", "new_files_24h", 50.0, "ok", ""),
        MetricSample("2026-08-09T08:05:00Z", "vault", "new_files_24h", 52.0, "ok", ""),
        MetricSample("2026-08-09T08:10:00Z", "vault", "new_files_24h", 55.0, "ok", ""),
    ]
    assert compute_delta(samples, "vault", "new_files_24h") == 3.0


def test_compute_delta_none_with_single_sample():
    samples = [MetricSample("2026-08-09T08:00:00Z", "vault", "new_files_24h", 50.0, "ok", "")]
    assert compute_delta(samples, "vault", "new_files_24h") is None


def test_compute_delta_week_with_over_a_week_of_history():
    now = datetime.now(timezone.utc)
    samples = [
        MetricSample(_iso(now - timedelta(days=10)), "substack", "subscribers", 100.0, "ok", ""),
        MetricSample(_iso(now - timedelta(days=8)), "substack", "subscribers", 110.0, "ok", ""),
        MetricSample(_iso(now), "substack", "subscribers", 150.0, "ok", ""),
    ]
    # closest sample at/before now-7d is the day-8 sample (110.0)
    assert compute_delta_week(samples, "substack", "subscribers") == 40.0


def test_compute_delta_week_none_with_under_a_week_of_history():
    now = datetime.now(timezone.utc)
    samples = [
        MetricSample(_iso(now - timedelta(days=2)), "substack", "subscribers", 100.0, "ok", ""),
        MetricSample(_iso(now), "substack", "subscribers", 110.0, "ok", ""),
    ]
    assert compute_delta_week(samples, "substack", "subscribers") is None


def test_compute_delta_week_none_when_latest_sample_itself_is_stale():
    # The metric hasn't reported in 10 days -- its own latest sample must
    # never be compared against itself (that would produce a misleading
    # 0.0 "no change" instead of "no data").
    now = datetime.now(timezone.utc)
    samples = [MetricSample(_iso(now - timedelta(days=10)), "digest", "items_today", 5.0, "ok", "")]
    assert compute_delta_week(samples, "digest", "items_today") is None


def test_parse_ts_rejects_naive_timestamp():
    assert parse_ts("2026-08-09T08:00:00") is None


def test_read_metrics_csv_skips_naive_timestamp_row(tmp_path):
    _write_csv(
        tmp_path,
        [
            "2026-08-09T08:00:00,vault,new_files_24h,1.0,ok,\n",  # no Z/offset
            "2026-08-09T08:00:00Z,vault,new_files_24h,2.0,ok,\n",
        ],
    )
    samples = read_metrics_csv(tmp_path)
    assert len(samples) == 1
    assert samples[0].value == 2.0


def test_read_metrics_csv_skips_nan_and_infinity_values(tmp_path):
    _write_csv(
        tmp_path,
        [
            "2026-08-09T08:00:00Z,vault,a,nan,ok,\n",
            "2026-08-09T08:00:00Z,vault,b,inf,ok,\n",
            "2026-08-09T08:00:00Z,vault,c,-infinity,ok,\n",
            "2026-08-09T08:00:00Z,vault,d,5.0,ok,\n",
        ],
    )
    samples = read_metrics_csv(tmp_path)
    assert len(samples) == 1
    assert samples[0].metric == "d"


def test_read_last_pull_missing_file_returns_empty(tmp_path):
    assert read_last_pull(tmp_path) == {}


def test_read_last_pull_non_dict_json_returns_empty(tmp_path):
    metrics_dir = tmp_path / "system" / "metrics"
    metrics_dir.mkdir(parents=True)
    (metrics_dir / "last-pull.json").write_text("[]")
    assert read_last_pull(tmp_path) == {}

    (metrics_dir / "last-pull.json").write_text("null")
    assert read_last_pull(tmp_path) == {}


def test_read_last_pull_parses_per_source_status(tmp_path):
    metrics_dir = tmp_path / "system" / "metrics"
    metrics_dir.mkdir(parents=True)
    (metrics_dir / "last-pull.json").write_text(
        '{"vault": {"error": "", "status": "ok", "ts": "2026-08-09T08:40:01Z"}}'
    )
    result = read_last_pull(tmp_path)
    assert result["vault"].status == "ok"
    assert result["vault"].ts == "2026-08-09T08:40:01Z"
    assert result["vault"].error == ""
