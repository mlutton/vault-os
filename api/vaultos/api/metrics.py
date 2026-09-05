from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from ..vault.metrics import (
    compute_delta,
    compute_delta_week,
    latest_metrics,
    latest_sample,
    parse_ts,
    project_trend,
    read_last_pull,
    read_metrics_csv,
)
from .deps import get_settings

router = APIRouter()

TOKEN_BURN_SOURCE = "claude_code"
PROJECTION_HORIZON = timedelta(hours=1)
TREND_WINDOW = timedelta(minutes=30)


def _require_vault(settings) -> None:
    if not settings.vault_readable():
        raise HTTPException(503, detail="vault root is missing or unreadable")


def _metric_to_dict(sample, pair_samples) -> dict:
    return {
        "source": sample.source,
        "metric": sample.metric,
        "value": sample.value,
        "delta": compute_delta(pair_samples, sample.source, sample.metric),
        "delta_week": compute_delta_week(pair_samples, sample.source, sample.metric),
        "timestamp": sample.timestamp,
        "status": sample.status,
        "error": sample.error,
    }


def _metrics_to_list(samples) -> list[dict]:
    latest = latest_metrics(samples)
    by_pair = defaultdict(list)
    for sample in samples:
        by_pair[(sample.source, sample.metric)].append(sample)
    return [_metric_to_dict(sample, by_pair[(sample.source, sample.metric)]) for sample in latest]


@router.get("/metrics")
def list_metrics(settings=Depends(get_settings)):
    _require_vault(settings)
    samples = read_metrics_csv(settings.vault_root)
    return _metrics_to_list(samples)


@router.get("/metrics/{source}/{metric}/history")
def metric_history(
    source: str,
    metric: str,
    days: int = Query(30, ge=1, le=365),
    settings=Depends(get_settings),
):
    _require_vault(settings)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    samples = read_metrics_csv(settings.vault_root)
    matching = [
        (parse_ts(s.timestamp), s) for s in samples if s.source == source and s.metric == metric
    ]
    matching = [(ts, s) for ts, s in matching if ts is not None and ts >= cutoff]
    matching.sort(key=lambda item: item[0])
    return [
        {"timestamp": s.timestamp, "value": s.value, "status": s.status, "error": s.error}
        for _, s in matching
    ]


@router.get("/metrics/token-burn")
def token_burn(settings=Depends(get_settings)):
    _require_vault(settings)
    samples = read_metrics_csv(settings.vault_root)
    tokens_sample = latest_sample(samples, source=TOKEN_BURN_SOURCE, metric="tokens_5h")
    cost_sample = latest_sample(samples, source=TOKEN_BURN_SOURCE, metric="cost_5h_usd")
    tokens_5h = tokens_sample.value if tokens_sample else None
    cost_5h_usd = cost_sample.value if cost_sample else None
    budget = settings.token_budget_5h_usd
    pct = (cost_5h_usd / budget) if cost_5h_usd is not None and budget else None

    now = datetime.now(timezone.utc)
    trend_cutoff = now - TREND_WINDOW
    recent_cost = [
        (parse_ts(s.timestamp), s.value)
        for s in samples
        if s.source == TOKEN_BURN_SOURCE and s.metric == "cost_5h_usd"
    ]
    recent_cost = [item for item in recent_cost if item[0] is not None and item[0] >= trend_cutoff]
    projection = project_trend(recent_cost, PROJECTION_HORIZON)

    last_pull = read_last_pull(settings.vault_root)
    pull_status = last_pull.get(TOKEN_BURN_SOURCE)
    freshness_s = None
    if pull_status is not None:
        pull_ts = parse_ts(pull_status.ts)
        if pull_ts is not None:
            freshness_s = (now - pull_ts).total_seconds()

    return {
        "tokens_5h": tokens_5h,
        "cost_5h_usd": cost_5h_usd,
        "budget": budget,
        "pct": pct,
        "projection": projection,
        "freshness_s": freshness_s,
    }
