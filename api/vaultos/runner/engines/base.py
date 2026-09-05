"""The engine adapter interface (ADR: runner + engine registry spec).

An engine is anything that can take a claimed job and its skill definition
and run it to completion. v1 shipped `script` (`script.py`), ticket #23 added
`claude-cli` (`claude_cli.py`), and ticket #24 added `cursor-cli`
(`cursor_cli.py`) -- the spec's full v1 engine set, each "one adapter plus
one registry row" against this same interface, per the spec's contributor
story.
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

    def run(
        self, *, job, skill, ctx: EngineContext, retry_context: str | None = None
    ) -> EngineResult:
        """Run `job` (a vaultos.jobs.store.Job, status already flipped to
        `running` by the claim) to completion and report the outcome.
        Never called for a job whose engine key isn't this adapter's --
        routing happens in vaultos.runner.core before this is invoked.
        Implementations may raise; the runner core treats any exception as
        an engine crash and fails the job to `error` rather than propagating
        (spec story 12: "survive an engine crash and move on").

        `retry_context`: runner core's check+retry loop (2026-09-05 addendum
        to the runner spec) calls `run()` a second time, with this set to the
        failed check's combined stdout+stderr, when the skill declares a
        `check` that failed after the first attempt. Core decides *that*
        exactly one retry happens; each adapter decides *how* the context
        enters its input -- e.g. an environment variable for a subprocess
        engine (see `script.py`'s `VAULTOS_CHECK_FEEDBACK`), or appended to
        the prompt under a marker line for a CLI/LLM engine (see
        `claude_cli.py`). None on a first attempt or when no check is
        declared; adapters that ignore it simply behave identically on
        retry."""
        ...
