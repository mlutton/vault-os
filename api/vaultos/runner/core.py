"""vaultos.runner's claim-execute loop (ADR-0022: infrastructure -- this
package never imports from vaultos/modules/).

A Runner claims the oldest queued job from the job store with an atomic
claim, routes it to an engine adapter by the skill's `engine` field, and
posts the terminal event through the same path (api.jobs.apply_event_and_chain)
the HTTP API uses, so CHAIN_MAP auto-chaining fires unchanged -- all
in-process, no HTTP calls. See docs/specs/2026-09-04-runner-engine-registry-design.md.
"""

import logging
import os
import signal
import threading
import time

from ..api.jobs import apply_event_and_chain
from ..config import Settings
from ..jobs import store
from ..registry import Registry
from ..state import resolve_state_root
from ..timeutil import utcnow_z
from .engines import ENGINE_REGISTRY, EngineContext
from .heartbeat import RUNNER_VERSION, write_heartbeat

logger = logging.getLogger(__name__)


def default_emit(event: dict) -> None:
    """Default eval-event sink: structured logging (spec: "the runner
    context exposes an emit(event) hook; default sink is structured
    logging... so a future store can subscribe without engine changes")."""
    logger.info("runner.eval %s", event)


class Runner:
    def __init__(
        self, conn, registry: Registry, settings: Settings, *,
        engines: dict | None = None, poll_interval_s: float | None = None,
        emit=None, pid: int | None = None,
    ):
        self.conn = conn
        self.registry = registry
        self.settings = settings
        self.engines = engines if engines is not None else ENGINE_REGISTRY
        self.poll_interval_s = (
            poll_interval_s if poll_interval_s is not None else settings.runner_poll_interval_s
        )
        self.emit = emit or default_emit
        self.pid = pid if pid is not None else os.getpid()
        self.state_root = resolve_state_root(settings)

        # In-flight bookkeeping for clean shutdown (spec story 14): a job id
        # is set the moment it's claimed and cleared once its terminal event
        # is posted. `_executing` distinguishes "claimed, engine not yet
        # dispatched" (safe to release back to queued on shutdown) from
        # "engine actively running" (not safe to release -- the subprocess
        # is still writing; shutdown() lets it finish instead).
        self._current_job_id: str | None = None
        self._executing = False
        self._shutdown_event = threading.Event()

    # -- single-job claim/execute, the testable synchronous entrypoint -----

    def run_once(self) -> bool:
        """Claim and fully execute at most one queued job. Returns True if a
        job was claimed (regardless of its outcome), False if the queue was
        empty or shutdown was already requested."""
        if self._shutdown_event.is_set():
            return False

        ts = utcnow_z()
        job = store.claim_oldest_queued(self.conn, pid=self.pid, ts=ts)
        if job is None:
            return False

        self._current_job_id = job.id
        try:
            if self._shutdown_event.is_set():
                # Claimed the row an instant before a shutdown request landed,
                # and hasn't started real work on it yet -- release it rather
                # than start a fresh run this process is about to exit.
                store.release_job(self.conn, job_id=job.id, ts=utcnow_z())
                return True
            self._execute(job)
        finally:
            self._current_job_id = None
            self._executing = False
        return True

    def _execute(self, job) -> None:
        skill = self.registry.get(job.skill)
        engine = self.engines.get(job.engine) if job.engine else None
        if engine is None:
            self._post_terminal(
                job, status="error", exit_code=None,
                summary=f"unknown or unconfigured engine {job.engine!r} for skill {job.skill!r}",
            )
            return

        ctx = EngineContext(
            vault_root=self.settings.vault_root, state_root=self.state_root,
            settings=self.settings, emit=self.emit,
        )
        self._executing = True
        start = time.monotonic()
        try:
            result = engine.run(job=job, skill=skill, ctx=ctx)
        except Exception as exc:  # noqa: BLE001 - any engine crash must not take the runner down
            duration_s = time.monotonic() - start
            self.emit({
                "run_id": job.id, "skill": job.skill, "engine": job.engine,
                "duration_s": duration_s, "success": False, "check": None,
            })
            logger.exception("runner: engine %r crashed on job %s", job.engine, job.id)
            self._post_terminal(job, status="error", exit_code=None, summary=f"engine crashed: {exc}")
            return

        duration_s = time.monotonic() - start
        self.emit({
            "run_id": job.id, "skill": job.skill, "engine": job.engine,
            "duration_s": duration_s, "success": result.success, "check": None,
        })
        self._post_terminal(
            job, status=("ok" if result.success else "error"), exit_code=result.exit_code,
            summary=result.summary, deliverable_path=result.deliverable_path,
        )

    def _post_terminal(self, job, *, status, exit_code, summary, deliverable_path=None) -> None:
        apply_event_and_chain(
            self.conn, self.registry, self.settings.vault_root,
            job_id=job.id, status=status, ts=utcnow_z(),
            exit_code=exit_code, summary=summary, deliverable_path=deliverable_path,
            pid=self.pid,
        )

    # -- heartbeat -----------------------------------------------------

    def write_heartbeat(self) -> None:
        pending = len(store.list_jobs(self.conn, statuses=["queued"]))
        write_heartbeat(
            self.state_root, pid=self.pid,
            active=1 if self._current_job_id else 0,
            pending=pending, busy=bool(self._current_job_id),
            max_concurrent=1, version=RUNNER_VERSION,
        )

    # -- clean shutdown --------------------------------------------------

    def request_shutdown(self, signum=None, frame=None) -> None:
        """Signal handler (and directly callable). Stops the poll loop from
        claiming any further job. If a job is currently claimed but its
        engine hasn't started running yet, releases it back to `queued`
        immediately rather than starting fresh work this process is about to
        exit -- if the engine IS already running, this only sets the flag;
        the in-flight run is left to finish (killing a live subprocess
        mid-write is not attempted in v1), and run_once()/run_forever() exit
        cleanly once it completes."""
        self._shutdown_event.set()
        if self._current_job_id is not None and not self._executing:
            store.release_job(self.conn, job_id=self._current_job_id, ts=utcnow_z())
            self._current_job_id = None

    def _install_signal_handlers(self) -> None:
        signal.signal(signal.SIGTERM, self.request_shutdown)
        signal.signal(signal.SIGINT, self.request_shutdown)

    # -- main loop ---------------------------------------------------------

    def run_forever(self) -> None:
        self._install_signal_handlers()
        while not self._shutdown_event.is_set():
            claimed = self.run_once()
            self.write_heartbeat()
            if not claimed:
                self._shutdown_event.wait(self.poll_interval_s)
        self.write_heartbeat()
