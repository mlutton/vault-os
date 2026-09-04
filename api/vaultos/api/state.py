from fastapi import APIRouter, Depends, HTTPException

from ..jobs import store
from ..timeutil import utcnow_z
from ..vault.daily import read_daily_note
from ..vault.metrics import read_metrics_csv
from ..vault.reports import read_lane_briefs, read_morning_report
from ..vault.runner import read_heartbeat
from .daily import _daily_to_dict, _resolve_date
from .deps import get_conn, get_settings
from .jobs import _job_to_dict
from .metrics import _metrics_to_list
from .runner import _runner_to_dict

router = APIRouter()

# HUD's historical window, not /runs's own default of 50 -- the composite
# matches what the cockpit actually renders without over-fetching.
RUNS_CAP = 8


def _morning_to_dict(report) -> dict | None:
    if report is None:
        return None
    return {
        "rel": report.rel,
        "headlines": [{"text": h.text, "link": h.link} for h in report.headlines],
    }


def _lane_briefs_to_list(items) -> list[dict]:
    return [
        {"source": item.source, "skill": item.skill, "title": item.title, "rel": item.rel, "headline": item.headline}
        for item in items
    ]


def build_state_snapshot(conn, settings) -> dict:
    """The full /state payload, as a plain function -- shared with the voice
    router (route.py), which needs the exact same vault-state snapshot the
    HUD reads, not a second copy of this assembly."""
    vault_root = settings.vault_root
    tz = settings.hud_tz

    today = _resolve_date("today", tz)
    daily_note = read_daily_note(vault_root, today)
    # list_runs() only ever returns ok/error (ts_completed is its sort key,
    # which a still-running job doesn't have) -- a job sits invisible in
    # neither `runs` nor `queue` (queued-only) for its entire execution
    # window unless we explicitly fold "running" in here too. Most-recent
    # activity first: in-progress jobs ahead of the completed history.
    running = store.list_jobs(conn, statuses=["running"], order_by="last_event_ts")
    runs = (running + store.list_runs(conn, limit=RUNS_CAP))[:RUNS_CAP]
    queue = store.list_jobs(conn, statuses=["queued"], order_by="last_event_ts")

    return {
        "generated_at": utcnow_z(),
        "vault_root": str(vault_root),
        "metrics": _metrics_to_list(read_metrics_csv(vault_root)),
        "runner": _runner_to_dict(read_heartbeat(vault_root)),
        "daily": _daily_to_dict(daily_note),
        "runs": [_job_to_dict(job, vault_root, conn) for job in runs],
        "queue": [_job_to_dict(job, vault_root, conn) for job in queue],
        "morning": _morning_to_dict(read_morning_report(vault_root, tz)),
        "skill_etas": store.compute_skill_etas(conn),
        "lane_briefs": _lane_briefs_to_list(read_lane_briefs(vault_root, tz)),
    }


@router.get("/state")
def get_state(conn=Depends(get_conn), settings=Depends(get_settings)):
    if not settings.vault_readable():
        raise HTTPException(503, detail="vault root is missing or unreadable")
    return build_state_snapshot(conn, settings)
