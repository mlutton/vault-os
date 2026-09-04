from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse

from ..jobs import store
from ..vault.runs import read_run_log
from .deps import get_conn, get_settings
from .jobs import _job_to_dict

router = APIRouter()


@router.get("/runs")
def list_runs(
    limit: int = Query(50, ge=1, le=200),
    skill: str | None = None,
    since: str | None = None,
    conn=Depends(get_conn),
    settings=Depends(get_settings),
):
    jobs = store.list_runs(conn, skill=skill, since=since, limit=limit)
    return [_job_to_dict(job, settings.vault_root, conn) for job in jobs]


@router.get("/runs/histogram")
def runs_histogram(days: int = Query(30, ge=1, le=365), conn=Depends(get_conn)):
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=days - 1)
    counts = store.count_runs_by_day(conn, since_date=start.isoformat())
    dates = [(start + timedelta(days=i)).isoformat() for i in range(days)]
    buckets = [{"date": d, "count": counts.get(d, 0)} for d in dates]
    return {"days": days, "buckets": buckets}


@router.get("/runs/{job_id}/log")
def run_log(job_id: str, conn=Depends(get_conn), settings=Depends(get_settings)):
    job = store.get_job(conn, job_id)
    if job is None:
        raise HTTPException(404, detail="job not found")
    content = read_run_log(settings.vault_root, job_id)
    if content is None:
        raise HTTPException(404, detail="run log file is missing")
    return PlainTextResponse(content, media_type="text/markdown")
