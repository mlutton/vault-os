import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime

STATUS_RANK = {"queued": 0, "running": 1, "ok": 2, "error": 2, "orphaned": 2}

# All routes touching app.state.conn run as sync `def` handlers, so FastAPI/Starlette
# executes them concurrently across a threadpool, all sharing one sqlite3.Connection
# (opened with check_same_thread=False). The hazard is concurrent *use* of one
# sqlite3.Connection, not writes specifically -- a reader's SELECT can interleave with
# a writer's INSERT/UPDATE/commit on the same connection object and raise
# `sqlite3.InterfaceError` ("bad parameter or other API misuse") or return a
# half-written/garbled row. Serialize every public entry point that touches the
# connection (get_job, create_job, apply_event) at the Python level with a single lock
# so only one of them ever executes at a time against the connection.
#
# threading.Lock is NOT reentrant. create_job and apply_event call the lock-free
# `_get_job` helper below (not the public, lock-acquiring `get_job`) while they already
# hold `_lock`, to avoid deadlocking against themselves.
_lock = threading.Lock()


@dataclass
class Job:
    id: str
    skill: str
    args: dict
    source: str
    engine: str | None
    status: str
    ts_queued: str | None
    ts_started: str | None
    ts_completed: str | None
    exit_code: int | None
    summary: str | None
    md_path: str | None
    deliverable_path: str | None
    runner_pid: int | None
    last_event_ts: str

    @property
    def deliverables(self) -> list[str]:
        return [self.deliverable_path] if self.deliverable_path else []


def _row_to_job(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"],
        skill=row["skill"],
        args=json.loads(row["args"]),
        source=row["source"],
        engine=row["engine"],
        status=row["status"],
        ts_queued=row["ts_queued"],
        ts_started=row["ts_started"],
        ts_completed=row["ts_completed"],
        exit_code=row["exit_code"],
        summary=row["summary"],
        md_path=row["md_path"],
        deliverable_path=row["deliverable_path"],
        runner_pid=row["runner_pid"],
        last_event_ts=row["last_event_ts"],
    )


def _get_job(conn: sqlite3.Connection, job_id: str) -> Job | None:
    """Lock-free read. Only call this while already holding `_lock` (i.e. from inside
    create_job/apply_event). External callers must use the public `get_job` instead."""
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return _row_to_job(row) if row else None


def get_job(conn: sqlite3.Connection, job_id: str) -> Job | None:
    with _lock:
        return _get_job(conn, job_id)


_VALID_ORDER_COLUMNS = {"last_event_ts", "ts_completed"}


def list_jobs(
    conn: sqlite3.Connection, *, statuses: list[str], order_by: str | None = None
) -> list[Job]:
    if order_by is not None and order_by not in _VALID_ORDER_COLUMNS:
        raise ValueError(f"invalid order_by: {order_by}")
    with _lock:
        placeholders = ",".join("?" for _ in statuses)
        query = f"SELECT * FROM jobs WHERE status IN ({placeholders})"
        if order_by is not None:
            query += f" ORDER BY {order_by} DESC"
        rows = conn.execute(query, statuses).fetchall()
        return [_row_to_job(r) for r in rows]


def list_runs(
    conn: sqlite3.Connection, *, skill: str | None = None, since: str | None = None, limit: int = 50
) -> list[Job]:
    with _lock:
        query = "SELECT * FROM jobs WHERE status IN ('ok', 'error')"
        params: list = []
        if skill is not None:
            query += " AND skill = ?"
            params.append(skill)
        if since is not None:
            query += " AND ts_completed >= ?"
            params.append(since)
        query += " ORDER BY ts_completed DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [_row_to_job(r) for r in rows]


def count_runs_by_day(conn: sqlite3.Connection, *, since_date: str) -> dict[str, int]:
    """{date: count} for completed runs (ok/error) whose ts_completed's date is
    >= since_date (a 'YYYY-MM-DD' string). Dates with zero runs are simply absent --
    callers zero-fill gaps themselves."""
    with _lock:
        rows = conn.execute(
            "SELECT substr(ts_completed, 1, 10) AS day, COUNT(*) AS n "
            "FROM jobs WHERE status IN ('ok', 'error') AND ts_completed >= ? "
            "GROUP BY day",
            (since_date,),
        ).fetchall()
        return {row["day"]: row["n"] for row in rows}


def duration_s(ts_started: str | None, ts_completed: str | None) -> int | None:
    """Whole-second wall-clock duration between two ISO timestamps, or None if
    either is missing/unparseable. The single canonical implementation --
    `_job_to_dict` (vaultos/api/jobs.py) and compute_skill_etas below both use
    this, so a rounding/timezone fix only needs to happen in one place."""
    if not ts_started or not ts_completed:
        return None
    try:
        started = datetime.fromisoformat(ts_started.replace("Z", "+00:00"))
        completed = datetime.fromisoformat(ts_completed.replace("Z", "+00:00"))
        return max(0, round((completed - started).total_seconds()))
    except (ValueError, TypeError):
        # TypeError covers one naive + one aware timestamp -- subtraction
        # raises rather than parsing, so it can't be caught alongside the
        # fromisoformat() calls above without wrapping the subtraction too.
        return None


def compute_skill_etas(conn: sqlite3.Connection, *, limit: int = 200) -> dict[str, int]:
    """Median completed duration per skill, from the most recent `limit` ok runs.
    DB-sourced (the jobs table is the reconciled source of truth since Stage 2),
    not a re-parse of system/runs/*.json files."""
    with _lock:
        rows = conn.execute(
            "SELECT skill, ts_started, ts_completed FROM jobs "
            "WHERE status = 'ok' AND ts_started IS NOT NULL AND ts_completed IS NOT NULL "
            "ORDER BY ts_completed DESC LIMIT ?",
            (limit,),
        ).fetchall()

    durations_by_skill: dict[str, list[int]] = {}
    for row in rows:
        duration = duration_s(row["ts_started"], row["ts_completed"])
        if duration is None:
            continue
        durations_by_skill.setdefault(row["skill"], []).append(duration)

    etas: dict[str, int] = {}
    for skill, durations in durations_by_skill.items():
        durations.sort()
        etas[skill] = durations[len(durations) // 2]
    return etas


def create_job(conn, *, job_id, skill, args, source, engine, ts_queued) -> Job:
    """Create a new job. For a `chain:{parent_skill}:{parent_job_id}` source
    (see ADR-0016), this is idempotent: the `jobs_chain_source` partial unique
    index (migration 0003) rejects a second insert for the same parent, and
    the existing owner is returned instead -- callers can tell "mine was the
    one actually created" by checking `returned_job.id == job_id`. Ordinary
    sources (api/voice/vault-hud/obsidian) are never constrained by that
    index -- they intentionally share source values across many jobs."""
    with _lock:
        if source.startswith("chain:"):
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO jobs (id, skill, args, source, engine, status, ts_queued, last_event_ts)
                VALUES (?, ?, ?, ?, ?, 'queued', ?, ?)
                """,
                (job_id, skill, json.dumps(args), source, engine, ts_queued, ts_queued),
            )
            if cur.rowcount == 0:
                existing = conn.execute("SELECT * FROM jobs WHERE source = ?", (source,)).fetchone()
                conn.commit()
                return _row_to_job(existing)
        else:
            conn.execute(
                """
                INSERT INTO jobs (id, skill, args, source, engine, status, ts_queued, last_event_ts)
                VALUES (?, ?, ?, ?, ?, 'queued', ?, ?)
                """,
                (job_id, skill, json.dumps(args), source, engine, ts_queued, ts_queued),
            )
        conn.execute(
            "INSERT OR IGNORE INTO job_events (job_id, status, ts, detail, received_at) "
            "VALUES (?, 'queued', ?, ?, ?)",
            (job_id, ts_queued, json.dumps({"skill": skill, "args": args, "source": source}), ts_queued),
        )
        conn.commit()
        return _get_job(conn, job_id)


def apply_event(
    conn, *, job_id, status, ts, received_at, skill=None, args=None, source=None,
    engine=None, exit_code=None, summary=None, deliverable_path=None, md_path=None, pid=None,
) -> Job | None:
    with _lock:
        detail = {
            k: v for k, v in dict(
                skill=skill, args=args, source=source, exit_code=exit_code, summary=summary,
                deliverable_path=deliverable_path, md_path=md_path, pid=pid,
            ).items() if v is not None
        }
        # Note: job_events insertion happens before jobs row exists for on-the-fly creation.
        # This is safe because foreign_keys PRAGMA is not enabled; if FK enforcement is added
        # in future, on-the-fly paths must create jobs before recording events.
        conn.execute(
            "INSERT OR IGNORE INTO job_events (job_id, status, ts, detail, received_at) VALUES (?, ?, ?, ?, ?)",
            (job_id, status, ts, json.dumps(detail), received_at),
        )

        job = _get_job(conn, job_id)
        created = job is None
        if created:
            if skill is None:
                conn.commit()
                return None
            conn.execute(
                "INSERT INTO jobs (id, skill, args, source, engine, status, last_event_ts) "
                "VALUES (?, ?, ?, ?, ?, 'queued', ?)",
                (job_id, skill, json.dumps(args or {}), source or "api", engine, ts),
            )
            job = _get_job(conn, job_id)

        # Unconditionally backfill ts_queued for queued events, independent of status-rank gate.
        # This ensures that regardless of event arrival order, the first queued event to arrive
        # sets ts_queued, even if the job was created by a higher-rank event.
        if status == "queued":
            conn.execute(
                "UPDATE jobs SET ts_queued = COALESCE(ts_queued, ?) WHERE id = ?",
                (ts, job_id),
            )

        # Unconditionally backfill metadata fields captured whenever we first learn them,
        # independent of the status-rank gate. These represent facts observed at a point in
        # time (e.g. "the runner reported this pid"), not "the current authoritative state",
        # so a late-arriving event must not lose them just because a terminal event already
        # advanced the status. First-write-wins via COALESCE: never overwritten once set.
        if status == "running":
            conn.execute(
                "UPDATE jobs SET ts_started = COALESCE(ts_started, ?) WHERE id = ?",
                (ts, job_id),
            )
        if pid is not None:
            conn.execute(
                "UPDATE jobs SET runner_pid = COALESCE(runner_pid, ?) WHERE id = ?",
                (pid, job_id),
            )
        if md_path is not None:
            conn.execute(
                "UPDATE jobs SET md_path = COALESCE(md_path, ?) WHERE id = ?",
                (md_path, job_id),
            )
        if deliverable_path is not None:
            conn.execute(
                "UPDATE jobs SET deliverable_path = COALESCE(deliverable_path, ?) WHERE id = ?",
                (deliverable_path, job_id),
            )

        should_apply = created or STATUS_RANK[status] > STATUS_RANK[job.status] or (
            status in ("ok", "error") and job.status == "orphaned"
        )
        if should_apply:
            fields = ["status = ?", "last_event_ts = ?"]
            values: list = [status, ts]
            # Note: ts_queued, ts_started, runner_pid, md_path, deliverable_path are no longer
            # set here; they're unconditionally backfilled above regardless of arrival order.
            if status in ("ok", "error"):
                fields.append("ts_completed = ?")
                values.append(ts)
            if exit_code is not None:
                fields.append("exit_code = ?")
                values.append(exit_code)
            if summary is not None:
                fields.append("summary = ?")
                values.append(summary)
            values.append(job_id)
            conn.execute(f"UPDATE jobs SET {', '.join(fields)} WHERE id = ?", values)

        conn.commit()
        return _get_job(conn, job_id)
