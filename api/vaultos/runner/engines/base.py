"""The engine adapter interface (ADR: runner + engine registry spec).

An engine is anything that can take a claimed job and its skill definition
and run it to completion. v1 ships exactly one adapter (`script`, in
`script.py`); `claude-cli` and `cursor-cli` are named by the spec but not
built in this ticket -- adding either later is "one adapter plus one
registry row" against this same interface, per the spec's contributor story.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from ...config import Settings


@dataclass(frozen=True)
class EngineContext:
    """Everything an adapter needs beyond the job/skill themselves. `emit` is
    the eval-event hook (spec: "the runner context exposes an emit(event)
    hook; default sink is structured logging") -- adapters may call it for
    engine-specific intermediate events; the runner core always emits the
    per-run summary event itself after `run()` returns, regardless of
    whether the adapter emitted anything."""

    vault_root: Path
    state_root: Path
    settings: Settings
    emit: Callable[[dict], None]


@dataclass(frozen=True)
class EngineResult:
    success: bool
    exit_code: int | None
    summary: str
    deliverable_path: str | None = None


class Engine(Protocol):
    name: str

    def run(self, *, job, skill, ctx: EngineContext) -> EngineResult:
        """Run `job` (a vaultos.jobs.store.Job, status already flipped to
        `running` by the claim) to completion and report the outcome.
        Never called for a job whose engine key isn't this adapter's --
        routing happens in vaultos.runner.core before this is invoked.
        Implementations may raise; the runner core treats any exception as
        an engine crash and fails the job to `error` rather than propagating
        (spec story 12: "survive an engine crash and move on")."""
        ...
