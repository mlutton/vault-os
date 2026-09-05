"""The `cursor-cli` engine: a headless, one-shot invocation of the Cursor
CLI (`cursor-agent`). A deliberate MIRROR of `claude_cli.py` (ticket #24) --
same engine_config keys, same prompt/argv/retry shape -- with one adapter-
specific addition documented below.

engine_config keys (all engine-specific, opaque to the platform -- see
vaultos/registry.py's Skill.engine_config docstring):
- `binary` (required): absolute path to the CLI executable. No login PATH is
  assumed in headless environments (spec story 10), so this is never
  resolved by name.
- `args` (optional): base argv appended after the binary, before the trust
  flag/model/prompt. A plain list of strings, not templated.
- `model` (optional): appended as a `--model <value>` flag after the trust
  flag, before the prompt.
- `timeout_s` (optional): defaults to DEFAULT_TIMEOUT_S, same convention as
  claude_cli/script.
- `prompt` (optional): a str.format() template for the prompt, substituted
  against the job's own args plus the reserved `{job_id}` placeholder. When
  absent, the job's own `prompt` arg (a normal Skill arg, validated by the
  registry like any other) is used verbatim.

**Prompt-builder registry (ticket #25)**: mirrors claude_cli.py exactly --
before either of the above, this adapter checks
`vaultos.runner.prompts.get_builder(skill.id)`; a registered builder's
prompt (and deliverable path) wins over `engine_config`'s `prompt` template
and the job's own `prompt` arg. No builder = unchanged passthrough behavior.
See claude_cli.py's module docstring for the full rationale (deliberately
not repeated here -- same mirror relationship as the rest of this file).

The prompt is passed as the invocation's final argv element (one-shot,
matching claude_cli's shape); stdout is the run's output.

**Trust flag**: this adapter always appends `--trust` (TRUST_FLAG), right
after the configured base `args` and before `--model`/the prompt --
unconditionally, with no engine_config switch to omit it. The real
`cursor-agent` binary refuses to run headlessly in a directory it hasn't
been told to trust, which would otherwise turn every run into an
interactive-prompt hang in an environment with no one to answer it (spec's
v1 adapter list: "cursor-cli ... requires its trust flag"). Since every job
this adapter runs is already headless by construction, there is no case
where trust should be withheld, so the flag is not configurable.
"""

import subprocess

from ..prompts import BuilderContext, get_builder
from .base import EngineContext, EngineResult
from .claude_cli import RETRY_CONTEXT_MARKER  # noqa: F401 -- shared marker contract, see below

DEFAULT_TIMEOUT_S = 120
# Cap what lands in the job's `summary` column -- see script.py's identical
# constant and comment; the full output always goes to the per-run log file.
SUMMARY_MAX_CHARS = 500

# Trust flag: see module docstring. Unconditional -- never gated by
# engine_config.
TRUST_FLAG = "--trust"

# Retry-context marker: reused verbatim from claude_cli.py rather than
# duplicated. Both adapters are one-shot CLI engines living in the same
# `engines` package with no independent versioning story, so importing the
# constant keeps the marker text (and the "same marker contract" the ticket
# calls for) as one literal source of truth instead of two strings that could
# drift; re-exported here (see the `noqa` import above) so tests can import
# it from either module without caring which one owns it.


class CursorCliEngineError(RuntimeError):
    """A cursor-cli skill is misconfigured (no binary, or no prompt source --
    neither an engine_config template nor a job `prompt` arg), or the binary
    itself couldn't be run. Raised, not swallowed -- vaultos.runner.core's
    generic engine-crash handling turns it into a clear `error` event, same
    as any other adapter exception (spec story 11's fast-fail semantics)."""


class CursorCliEngine:
    name = "cursor-cli"

    def run(
        self, *, job, skill, ctx: EngineContext, retry_context: str | None = None
    ) -> EngineResult:
        config = skill.engine_config or {}
        binary = config.get("binary")
        if not binary:
            raise CursorCliEngineError(f"cursor-cli skill '{skill.id}' has no binary configured")

        prompt, deliverable_path = self._build_prompt(job, skill, config, ctx)
        if retry_context is not None:
            prompt = f"{prompt}{RETRY_CONTEXT_MARKER}{retry_context}"

        argv = [binary, *config.get("args", []), TRUST_FLAG]
        model = config.get("model")
        if model:
            argv += ["--model", model]
        argv.append(prompt)

        timeout_s = config.get("timeout_s", DEFAULT_TIMEOUT_S)
        try:
            proc = subprocess.run(
                argv, cwd=ctx.vault_root, capture_output=True, text=True, timeout=timeout_s,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CursorCliEngineError(f"cursor-cli skill '{skill.id}' failed to run: {exc}") from exc

        self._write_run_log(ctx, job.id, argv, proc)

        resolved_deliverable = None
        if proc.returncode == 0 and deliverable_path and (ctx.vault_root / deliverable_path).exists():
            resolved_deliverable = deliverable_path

        summary = (proc.stdout.strip() or proc.stderr.strip() or f"cursor-cli exited {proc.returncode}")
        return EngineResult(
            success=proc.returncode == 0,
            exit_code=proc.returncode,
            summary=summary[:SUMMARY_MAX_CHARS],
            deliverable_path=resolved_deliverable,
        )

    @staticmethod
    def _build_prompt(job, skill, config: dict, ctx: EngineContext) -> tuple[str, str | None]:
        """Returns (prompt, deliverable_path) -- see claude_cli.py's
        identical method for the full rationale (prompt-builder registry
        checked first; deliverable_path is None unless a builder supplied
        one)."""
        builder = get_builder(skill.id)
        if builder is not None:
            built = builder(
                job.args, BuilderContext(vault_root=ctx.vault_root, settings=ctx.settings, job_id=job.id)
            )
            if built is None:
                raise CursorCliEngineError(
                    f"cursor-cli skill '{skill.id}' prompt builder rejected this job's args "
                    f"(missing or blank required field)"
                )
            return built.prompt, built.deliverable_path

        template = config.get("prompt")
        if template:
            subst = {**job.args, "job_id": job.id}
            try:
                return template.format(**subst), None
            except KeyError as exc:
                raise CursorCliEngineError(
                    f"cursor-cli skill '{skill.id}' prompt template references unknown placeholder {exc}"
                ) from exc

        prompt = job.args.get("prompt")
        if not prompt:
            raise CursorCliEngineError(
                f"cursor-cli skill '{skill.id}' has no engine_config 'prompt' template "
                f"and the job carries no 'prompt' arg"
            )
        return prompt, None

    @staticmethod
    def _write_run_log(ctx: EngineContext, job_id: str, argv: list[str], proc: subprocess.CompletedProcess) -> None:
        log_dir = ctx.state_root / "runs"
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / f"{job_id}.log").write_text(
            f"$ {' '.join(argv)}\nexit={proc.returncode}\n\n"
            f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}\n"
        )
