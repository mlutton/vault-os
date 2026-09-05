import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import modules
from .api import (
    calendar,
    daily,
    health,
    integrations,
    jobs,
    metrics,
    review_next,
    runner,
    runs,
    skills,
    state,
)
from .config import Settings
from .db.conn import connect
from .jobs.reconcile import detect_orphans, reconcile_from_files
from .pidfile import remove_pid, write_pid
from .registry import load_registry
from .vault.runner import read_heartbeat

logger = logging.getLogger(__name__)

ORPHAN_CHECK_INTERVAL_S = 60


def _run_orphan_sweep(app: FastAPI) -> None:
    heartbeat = read_heartbeat(app.state.settings.vault_root)
    detect_orphans(app.state.conn, heartbeat)


async def _orphan_detection_loop(app: FastAPI, stop_event: asyncio.Event) -> None:
    # Cooperative shutdown ONLY -- never `task.cancel()` this loop from outside.
    # `_run_orphan_sweep` runs on a real OS thread via `asyncio.to_thread`, and
    # cancelling the awaiting task does not, and cannot, stop that thread: the
    # `to_thread` executor has no way to interrupt a thread that's already
    # running, so cancellation just abandons the await early while the thread
    # keeps executing queries against `app.state.conn` in the background. If
    # shutdown then closes that connection (as lifespan below always does),
    # the still-running sweep thread can be mid-`sqlite3` call on a connection
    # that's being torn out from under it -- reliably a segfault, not just an
    # exception (see ticket #28: this loop's own `list_jobs` call is the exact
    # crash site CI's faulthandler dump named). So shutdown here always lets
    # the CURRENT sweep (if any) finish naturally -- it only ever stops
    # between sweeps, at the `stop_event.wait()` below.
    while not stop_event.is_set():
        try:
            # Sync DB work -- offload to a thread so it never blocks the event
            # loop (which is otherwise free to keep serving requests).
            await asyncio.to_thread(_run_orphan_sweep, app)
        except Exception:
            logger.exception("orphan detection loop failed")
        if stop_event.is_set():
            break
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=ORPHAN_CHECK_INTERVAL_S)
        except asyncio.TimeoutError:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings()
    app.state.settings = settings
    app.state.conn = connect(settings.db_path)
    app.state.registry = load_registry(settings.vault_root)
    # PID file must exist for the FULL duration the spine touches the DB --
    # including while backfill below is still running -- so reindex's
    # "refuse if the spine is live" guarantee actually holds throughout, not
    # only after startup finishes.
    write_pid(settings.db_path)
    reconcile_from_files(settings.vault_root, app.state.conn, app.state.registry)

    orphan_stop = asyncio.Event()
    orphan_task = asyncio.create_task(_orphan_detection_loop(app, orphan_stop))

    yield

    # Signal + await, never cancel(): see _orphan_detection_loop's docstring
    # comment -- awaiting it here (with no prior cancel()) blocks until any
    # sweep already in flight genuinely finishes on its own thread, so the
    # connection below is never closed while that thread is still using it.
    orphan_stop.set()
    await orphan_task
    remove_pid(settings.db_path)
    app.state.conn.close()


app = FastAPI(lifespan=lifespan)
app.include_router(health.router)
app.include_router(jobs.router)
app.include_router(runs.router)
app.include_router(skills.router)
app.include_router(runner.router)
app.include_router(metrics.router)
app.include_router(daily.router)
app.include_router(integrations.router)
app.include_router(state.router)
app.include_router(calendar.router)
app.include_router(review_next.router)
# The routers above are the platform; domain modules register themselves
# through the ADR-0022 contract instead of being named here.
modules.register_all(app, modules.ModuleContext())
