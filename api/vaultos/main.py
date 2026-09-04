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


async def _orphan_detection_loop(app: FastAPI) -> None:
    while True:
        try:
            # Sync DB work -- offload to a thread so it never blocks the event
            # loop (which is otherwise free to keep serving requests).
            await asyncio.to_thread(_run_orphan_sweep, app)
        except Exception:
            logger.exception("orphan detection loop failed")
        await asyncio.sleep(ORPHAN_CHECK_INTERVAL_S)


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

    orphan_task = asyncio.create_task(_orphan_detection_loop(app))

    yield

    orphan_task.cancel()
    try:
        await orphan_task
    except asyncio.CancelledError:
        pass
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
