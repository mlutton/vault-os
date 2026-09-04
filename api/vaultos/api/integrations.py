from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from ..vault.metrics import latest_sample, parse_ts, read_last_pull, read_metrics_csv
from .deps import get_settings

router = APIRouter()

# 3x metrics-pull's 5-minute cron cadence -- see docs/adr/0003.
STALE_THRESHOLD_S = 900


@router.get("/integrations")
def list_integrations(settings=Depends(get_settings)):
    if not settings.vault_readable():
        raise HTTPException(503, detail="vault root is missing or unreadable")

    samples = read_metrics_csv(settings.vault_root)
    last_pull = read_last_pull(settings.vault_root)
    sources = {s.source for s in samples} | set(last_pull.keys())

    now = datetime.now(timezone.utc)
    result = []
    for source in sorted(sources):
        pull = last_pull.get(source)
        if pull is not None:
            last_pull_ts = pull.ts
        else:
            latest = latest_sample(samples, source=source)
            last_pull_ts = latest.timestamp if latest else None
        status = pull.status if pull is not None else None

        age_s = None
        parsed = parse_ts(last_pull_ts) if last_pull_ts else None
        if parsed is not None:
            age_s = (now - parsed).total_seconds()

        result.append(
            {
                "source": source,
                "status": status,
                "last_pull_ts": last_pull_ts,
                "age_s": age_s,
                # Unknown age is treated as stale -- never a free pass for a
                # source with no determinable last-pull time at all.
                "stale": age_s is None or age_s > STALE_THRESHOLD_S,
            }
        )
    return result
