"""vaultos.runner's claim-execute loop (ADR-0022: infrastructure -- this
package never imports from vaultos/modules/).

A Runner holds a state-root lock, selects queued candidates from the jobs
index, confirms each intent, and records an attempt before execution. It
posts terminal events through the same path (api.jobs.apply_event_and_chain)
as the HTTP API, preserving CHAIN_MAP auto-chaining in-process.
"""

import fcntl
import json
import logging
import os
import signal
import subprocess
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import replace

from ..api.jobs import apply_event_and_chain, chain_transitions
from ..config import Settings
from ..jobs import store
from ..registry import Registry
from ..state import resolve_state_root
from ..timeutil import utcnow_z
from ..vault.durable import ensure_durable_dir
from .engines import ENGINE_REGISTRY, EngineContext, EngineResult
from .heartbeat import RUNNER_VERSION, write_heartbeat
from .records import remove_intent, unresolved_attempts, write_record
from .recovery import recover_terminal_records

logger = logging.getLogger(__name__)


class RunnerLockHeldError(RuntimeError):
    """A runner already owns this state root."""


class RecordWriteError(RuntimeError):
    """An execution observation could not be made durable."""


# Cap what a check's own failure feedback contributes to a job's `summary`
# column on a second, terminal failure -- mirrors script.py's
# SUMMARY_MAX_CHARS; the full stdout/stderr is what actually gets replayed
# into the retried engine call, this is just the at-a-glance record.
CHECK_SUMMARY_MAX_CHARS = 500

# No timeout is specified by the runner spec's check+retry addendum for the
# check command itself; a default is applied here (same value as the script
# engine's DEFAULT_TIMEOUT_S) so a hung check command can't wedge the runner
# -- see this ticket's Deviations. Deliberately a plain module-level constant
# (not buried behind a settings object): `_run_check` reads it by bare name
# at call time, so a test can lower it with `monkeypatch.setattr(core,
# "CHECK_TIMEOUT_S", ...)` to exercise the timeout path in well under a
# second, exactly like patching any other module attribute.
CHECK_TIMEOUT_S = 120
HEARTBEAT_INTERVAL_S = 15


def default_emit(event: dict) -> None:
    """Default eval-event sink: structured logging (spec: "the runner
    context exposes an emit(event) hook; default sink is structured
    logging... so a future store can subscribe without engine changes")."""
    logger.info("runner.eval %s", event)


class Runner:
    def __init__(
        self,
        conn,
        registry: Registry,
        settings: Settings,
        *,
        engines: dict | None = None,
        poll_interval_s: float | None = None,
        emit=None,
        pid: int | None = None,
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
        if self.state_root.resolve() != resolve_state_root(settings.vault_root).resolve():
            raise ValueError("runner state root differs from the job-file reader root")
        self.unresolved_attempts = unresolved_attempts(self.state_root)
        for job_id in self.unresolved_attempts:
            logger.warning(
                "runner: unresolved attempt for job %s; manual recovery required", job_id
            )
        self._lock_fd = None
        self._lock_owner: int | None = None
        self._reported_skips: set[str] = set()
        self._attempt_id: str | None = None
        self._attempt_ids: list[str] = []
        self._chain_origin: dict | None = None

        # An attempt is durable before the DB claim. Once claimed, shutdown
        # lets the engine finish; an unfinished attempt needs manual recovery.
        self._current_job_id: str | None = None
        self._executing = False
        self._shutdown_event = threading.Event()

    # -- single-job claim/execute, the testable synchronous entrypoint -----

    def run_once(self) -> bool:
        """Claim and fully execute at most one queued job. Returns True if a
        job was claimed (regardless of its outcome), False if the queue was
        empty or shutdown was already requested."""
        with self._runner_lock():
            if self._shutdown_event.is_set():
                return False
            self.recover()
            observed = unresolved_attempts(self.state_root)
            for job_id in set(observed) - set(self.unresolved_attempts):
                logger.warning(
                    "runner: unresolved attempt for job %s; manual recovery required", job_id
                )
            self.unresolved_attempts = observed
            job = self._claim_candidate()
            if job is None:
                return False
            if self._shutdown_event.is_set():
                self.unresolved_attempts.append(job.id)
                logger.warning(
                    "runner: shutdown after recording attempt for job %s; manual recovery required",
                    job.id,
                )
                self._executing = False
                self._attempt_id = None
                self._attempt_ids = []
                return True
            self._current_job_id = job.id
            stop_heartbeat = threading.Event()
            beat = threading.Thread(
                target=self._heartbeat_loop, args=(stop_heartbeat,), daemon=False
            )
            beat.start()
            try:
                self._execute(job)
            finally:
                stop_heartbeat.set()
                beat.join()
                self._current_job_id = None
                self._executing = False
                self._attempt_id = None
                self._attempt_ids = []
            return True

    @contextmanager
    def _runner_lock(self):
        if self._lock_fd is not None:
            if self._lock_owner != threading.get_ident():
                raise RunnerLockHeldError("another call holds the runner lock")
            yield
            return
        ensure_durable_dir(self.state_root)
        fd = os.open(self.state_root / "runner.lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RunnerLockHeldError("another runner holds the state-root lock") from exc
            self._lock_fd = fd
            self._lock_owner = threading.get_ident()
            yield
        finally:
            self._lock_fd = None
            self._lock_owner = None
            os.close(fd)

    def _claim_candidate(self):
        for candidate in store.queued_candidates(self.conn):
            if candidate.id in self.unresolved_attempts:
                continue
            if (self.state_root / "runs" / f"{candidate.id}.json").exists():
                if candidate.id not in self._reported_skips:
                    logger.warning(
                        "runner: job %s already has a terminal file; left queued", candidate.id
                    )
                    self._reported_skips.add(candidate.id)
                continue
            skill = self.registry.get(candidate.skill)
            engine_name = skill.engine if skill else None
            if engine_name not in self.engines:
                if candidate.id not in self._reported_skips:
                    logger.warning(
                        "runner: job %s has unknown engine %r; left queued",
                        candidate.id,
                        engine_name,
                    )
                    self._reported_skips.add(candidate.id)
                continue
            intent_path = self.state_root / "queue" / f"{candidate.id}.json"
            try:
                intent = json.loads(intent_path.read_text())
                if intent.get("id") != candidate.id or intent["skill"] != candidate.skill:
                    raise ValueError("intent identity differs from queued job")
                if not isinstance(intent.get("args", {}), dict):
                    raise ValueError("intent args must be an object")
                if not isinstance(intent.get("source", "api"), str):
                    raise ValueError("intent source must be a string")
                if not isinstance(intent.get("ts"), str) or not intent["ts"]:
                    raise ValueError("intent queue time must be a string")
                job = replace(
                    candidate,
                    args=intent.get("args", {}),
                    source=intent.get("source", "api"),
                    ts_queued=intent["ts"],
                    engine=engine_name,
                )
                self._chain_origin = intent.get("chain")
            except (OSError, ValueError, KeyError) as exc:
                if candidate.id not in self._reported_skips:
                    logger.warning(
                        "runner: job %s has no valid intent; left queued: %s", candidate.id, exc
                    )
                    self._reported_skips.add(candidate.id)
                continue
            if self._shutdown_event.is_set():
                return None
            self._attempt_ids = []
            started = self._write_attempt(job, attempt_number=1)
            self.write_heartbeat()
            claimed = store.claim_oldest_queued(
                self.conn,
                pid=self.pid,
                ts=started,
                job_id=job.id,
                args=job.args,
                source=job.source,
                engine=job.engine,
                queued_ts=job.ts_queued,
            )
            if claimed is None:
                # An external DB writer raced this runner. The durable attempt
                # remains unresolved; it must not be silently executed later.
                self.unresolved_attempts.append(job.id)
                return None
            self._executing = True
            return replace(claimed, args=job.args, source=job.source, engine=job.engine)
        return None

    def _write_attempt(self, job, *, attempt_number: int) -> str:
        attempt_id = uuid.uuid4().hex
        started = utcnow_z()
        record = {
            "id": job.id,
            "attempt_id": attempt_id,
            "attempt_number": attempt_number,
            "skill": job.skill,
            "args": job.args,
            "source": job.source,
            "ts_queued": job.ts_queued,
            "ts_started": started,
            "runner_pid": self.pid,
            "engine": job.engine,
        }
        try:
            write_record(
                self.state_root / "runs" / f"{job.id}.attempt-{attempt_number}.json", record
            )
        except OSError as exc:
            raise RecordWriteError("attempt record could not be written") from exc
        self._attempt_id = attempt_id
        self._attempt_ids.append(attempt_id)
        return started

    def _heartbeat_loop(self, stop: threading.Event) -> None:
        # The engine and check can block for minutes. The thread joins before
        # the runner's connection closes; store access uses its shared lock.
        while not stop.is_set():
            try:
                self.write_heartbeat()
            except Exception:  # noqa: BLE001 - keep beating after a transient failure
                logger.exception("runner: heartbeat write failed")
            stop.wait(HEARTBEAT_INTERVAL_S)

    def _execute(self, job) -> None:
        skill = self.registry.get(job.skill)
        engine = self.engines.get(job.engine) if job.engine else None
        if engine is None:
            self._post_terminal(
                job,
                status="error",
                exit_code=None,
                summary=f"unknown or unconfigured engine {job.engine!r} for skill {job.skill!r}",
            )
            return

        ctx = EngineContext(
            vault_root=self.settings.vault_root,
            state_root=self.state_root,
            settings=self.settings,
            emit=self.emit,
        )
        self._executing = True
        start = time.monotonic()
        try:
            result, check_outcome = self._run_with_check(job, skill, engine, ctx)
        except RecordWriteError:
            raise
        except Exception as exc:  # noqa: BLE001 - any engine crash must not take the runner down
            duration_s = time.monotonic() - start
            self.emit(
                {
                    "run_id": job.id,
                    "skill": job.skill,
                    "engine": job.engine,
                    "duration_s": duration_s,
                    "success": False,
                    "check": None,
                }
            )
            logger.exception("runner: engine %r crashed on job %s", job.engine, job.id)
            self._post_terminal(
                job, status="error", exit_code=None, summary=f"engine crashed: {exc}"
            )
            return

        duration_s = time.monotonic() - start
        self.emit(
            {
                "run_id": job.id,
                "skill": job.skill,
                "engine": job.engine,
                "duration_s": duration_s,
                "success": result.success,
                "check": check_outcome,
            }
        )
        self._post_terminal(
            job,
            status=("ok" if result.success else "error"),
            exit_code=result.exit_code,
            summary=result.summary,
            deliverable_path=result.deliverable_path,
            check_outcome=check_outcome,
        )

    def _run_with_check(self, job, skill, engine, ctx: EngineContext):
        """Run `engine` once, then -- if it succeeded and `skill` declares a
        `check` -- verify with exactly one retry (2026-09-05 check+retry
        addendum to the runner spec). Returns (EngineResult, check_outcome)
        where check_outcome is None when no check was declared (engine
        success alone is job success, unchanged from #22) or
        {"passed": bool, "attempt": 1 | 2} recording which attempt the check
        was decided on. `check_outcome` only ever describes a check that
        actually ran (operator decision, fix round 1): if the retried ENGINE
        itself fails (no second check runs), the outcome reported is the
        last check that did run -- the first one, which is why the retry
        happened at all -- not a synthetic "attempt: 2" implying a second
        check that never executed; the engine failure itself is already
        carried by the returned EngineResult's exit code/summary. Any
        exception from `engine.run` (first call or the retry) propagates to
        the caller unchanged -- `_execute`'s existing engine-crash handling
        covers both ("engine failure on the retry also -> error", per the
        addendum)."""
        result = engine.run(job=job, skill=skill, ctx=ctx)
        if not result.success or not skill.check:
            return result, None

        passed, feedback = self._run_check(skill, ctx)
        if passed:
            return result, {"passed": True, "attempt": 1}

        if not skill.repeatable:
            return EngineResult(
                success=False,
                exit_code=result.exit_code,
                summary=f"check failed; automatic retry disabled: {feedback[:CHECK_SUMMARY_MAX_CHARS]}",
                deliverable_path=result.deliverable_path,
            ), {"passed": False, "attempt": 1}

        self._write_attempt(job, attempt_number=2)
        retry_result = engine.run(job=job, skill=skill, ctx=ctx, retry_context=feedback)
        if not retry_result.success:
            # The retry's engine call itself failed -- no second check ran,
            # so the outcome we report is the last check that actually did:
            # the first one (which is exactly why this retry happened).
            return retry_result, {"passed": False, "attempt": 1}

        passed_retry, feedback_retry = self._run_check(skill, ctx)
        if passed_retry:
            return retry_result, {"passed": True, "attempt": 2}

        failed_result = EngineResult(
            success=False,
            exit_code=retry_result.exit_code,
            summary=f"check failed after retry: {feedback_retry[:CHECK_SUMMARY_MAX_CHARS]}",
            deliverable_path=retry_result.deliverable_path,
        )
        return failed_result, {"passed": False, "attempt": 2}

    @staticmethod
    def _run_check(skill, ctx: EngineContext) -> tuple[bool, str]:
        """Run `skill.check` as a shell command with the job's working
        context -- VAULT_ROOT in env, cwd set to it -- per the addendum: "a
        check is a shell command run with the job's working context". Exit 0
        passes. Returns (passed, combined stdout+stderr) -- the latter is
        exactly what's carried as failure context into the one retry, and
        into the terminal error summary on a second failure."""
        env = {**os.environ, "VAULT_ROOT": str(ctx.vault_root)}
        try:
            proc = subprocess.run(
                skill.check,
                shell=True,
                cwd=ctx.vault_root,
                capture_output=True,
                text=True,
                env=env,
                timeout=CHECK_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired as exc:
            feedback = f"check command timed out after {CHECK_TIMEOUT_S}s: {exc}"
            return False, feedback
        feedback = f"--- check stdout ---\n{proc.stdout}--- check stderr ---\n{proc.stderr}"
        return proc.returncode == 0, feedback

    def _post_terminal(
        self, job, *, status, exit_code, summary, deliverable_path=None, check_outcome=None
    ) -> None:
        ts = utcnow_z()
        transitions = (
            chain_transitions(job.skill, job.id, self._attempt_id) if status == "ok" else []
        )
        write_record(
            self.state_root / "runs" / f"{job.id}.json",
            {
                "id": job.id,
                "attempt_id": self._attempt_id,
                "attempt_ids": self._attempt_ids,
                "skill": job.skill,
                "args": job.args,
                "source": job.source,
                "ts_queued": job.ts_queued,
                "ts_started": job.ts_started,
                "ts_completed": ts,
                "status": status,
                "exit_code": exit_code,
                "summary": summary,
                "deliverable_path": deliverable_path,
                "transitions": transitions,
                **({"chain": self._chain_origin} if self._chain_origin is not None else {}),
                "completion_evidence": {
                    "engine": job.engine,
                    "exit_code": exit_code,
                    "summary": summary,
                    "deliverable_path": deliverable_path,
                    "check": check_outcome,
                },
            },
        )
        remove_intent(self.state_root / "queue" / f"{job.id}.json")
        apply_event_and_chain(
            self.conn,
            self.registry,
            self.settings.vault_root,
            job_id=job.id,
            status=status,
            ts=ts,
            exit_code=exit_code,
            summary=summary,
            deliverable_path=deliverable_path,
            pid=self.pid,
            attempt_id=self._attempt_id,
            transitions=transitions,
        )

    def recover(self) -> None:
        """Repair durable terminal observations under the same lock as claims."""
        with self._runner_lock():
            recover_terminal_records(self.conn, self.registry, self.settings.vault_root)

    # -- heartbeat -----------------------------------------------------

    def write_heartbeat(self) -> None:
        pending = len(store.list_jobs(self.conn, statuses=["queued"]))
        write_heartbeat(
            self.state_root,
            pid=self.pid,
            active=1 if self._current_job_id else 0,
            pending=pending,
            busy=bool(self._current_job_id),
            max_concurrent=1,
            version=RUNNER_VERSION,
            unresolved_attempts=self.unresolved_attempts,
        )

    # -- clean shutdown --------------------------------------------------

    def request_shutdown(self, signum=None, frame=None) -> None:
        """Signal handler (and directly callable). Stops the poll loop from
        claiming further jobs. A job claimed at the shutdown boundary is held
        as an unresolved attempt for manual recovery. A live engine finishes
        before the runner exits."""
        self._shutdown_event.set()

    def _install_signal_handlers(self) -> None:
        signal.signal(signal.SIGTERM, self.request_shutdown)
        signal.signal(signal.SIGINT, self.request_shutdown)

    # -- main loop ---------------------------------------------------------

    def run_forever(self) -> None:
        with self._runner_lock():
            self._install_signal_handlers()
            self.recover()
            while not self._shutdown_event.is_set():
                claimed = self.run_once()
                self.write_heartbeat()
                if not claimed:
                    self._shutdown_event.wait(self.poll_interval_s)
            self.write_heartbeat()
