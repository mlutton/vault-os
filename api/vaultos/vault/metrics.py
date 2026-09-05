import csv
import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

DELTA_WEEK = timedelta(days=7)


@dataclass(frozen=True)
class MetricSample:
    timestamp: str
    source: str
    metric: str
    value: float
    status: str
    error: str


@dataclass(frozen=True)
class LastPullStatus:
    status: str
    ts: str
    error: str


def parse_ts(raw: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    # Every timestamp this system writes carries a "Z"/offset; a naive
    # value is malformed input, not something safe to assume is UTC.
    if parsed.tzinfo is None:
        return None
    return parsed


def read_metrics_csv(vault_root: Path) -> list[MetricSample]:
    path = vault_root / "system" / "metrics" / "metrics.csv"
    if not path.exists():
        return []

    samples = []
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                if parse_ts(row["timestamp"]) is None:
                    continue
                value = float(row["value"])
                if not math.isfinite(value):
                    continue
                samples.append(
                    MetricSample(
                        timestamp=row["timestamp"],
                        source=row["source"],
                        metric=row["metric"],
                        value=value,
                        status=row["status"],
                        error=row.get("error") or "",
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
    return samples


def _timestamped(samples: list[MetricSample]) -> list[tuple[datetime, MetricSample]]:
    """Pairs each sample with its parsed timestamp, dropping unparseable ones."""
    result = []
    for sample in samples:
        ts = parse_ts(sample.timestamp)
        if ts is not None:
            result.append((ts, sample))
    return result


def latest_metrics(samples: list[MetricSample]) -> list[MetricSample]:
    latest: dict[tuple[str, str], tuple[datetime, MetricSample]] = {}
    for ts, sample in _timestamped(samples):
        key = (sample.source, sample.metric)
        current = latest.get(key)
        if current is None or ts > current[0]:
            latest[key] = (ts, sample)
    return [sample for _, sample in latest.values()]


def latest_sample(
    samples: list[MetricSample], *, source: str, metric: str | None = None
) -> MetricSample | None:
    """The most recent sample for a Source (all Metrics) or one (Source, Metric) pair."""
    matches = [
        (ts, sample)
        for ts, sample in _timestamped(samples)
        if sample.source == source and (metric is None or sample.metric == metric)
    ]
    if not matches:
        return None
    return max(matches, key=lambda item: item[0])[1]


def _pair_samples_sorted(
    samples: list[MetricSample], source: str, metric: str
) -> list[tuple[datetime, MetricSample]]:
    pairs = [
        (ts, sample)
        for ts, sample in _timestamped(samples)
        if sample.source == source and sample.metric == metric
    ]
    pairs.sort(key=lambda item: item[0])
    return pairs


def compute_delta(samples: list[MetricSample], source: str, metric: str) -> float | None:
    pairs = _pair_samples_sorted(samples, source, metric)
    if len(pairs) < 2:
        return None
    return pairs[-1][1].value - pairs[-2][1].value


def compute_delta_week(samples: list[MetricSample], source: str, metric: str) -> float | None:
    pairs = _pair_samples_sorted(samples, source, metric)
    if not pairs:
        return None
    latest_ts, latest_pair_sample = pairs[-1]
    cutoff = datetime.now(timezone.utc) - DELTA_WEEK
    # Exclude the latest sample itself -- a metric that stopped reporting
    # over a week ago must never be compared against itself (which would
    # produce a misleading 0.0 "no change" instead of "no data").
    candidates = [pair for pair in pairs[:-1] if pair[0] <= cutoff]
    if not candidates:
        return None
    _, closest = max(candidates, key=lambda item: item[0])
    return latest_pair_sample.value - closest.value


def read_last_pull(vault_root: Path) -> dict[str, LastPullStatus]:
    path = vault_root / "system" / "metrics" / "last-pull.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}

    result = {}
    for source, entry in data.items():
        try:
            result[source] = LastPullStatus(
                status=entry["status"], ts=entry["ts"], error=entry.get("error", "")
            )
        except (KeyError, TypeError):
            continue
    return result


def project_trend(samples: list[tuple[datetime, float]], horizon: timedelta) -> float | None:
    """Least-squares linear extrapolation of (timestamp, value) samples to
    `horizon` past the last sample. Real usage data, not an authoritative
    reading -- see docs/adr/0002-token-burn-is-a-local-approximation.md."""
    if len(samples) < 2:
        return None
    ordered = sorted(samples, key=lambda item: item[0])
    t0 = ordered[0][0]
    xs = [(ts - t0).total_seconds() for ts, _ in ordered]
    ys = [value for _, value in ordered]
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    denominator = sum((x - mean_x) ** 2 for x in xs)
    if denominator == 0:
        return ys[-1]
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denominator
    intercept = mean_y - slope * mean_x
    projected_x = xs[-1] + horizon.total_seconds()
    return intercept + slope * projected_x
