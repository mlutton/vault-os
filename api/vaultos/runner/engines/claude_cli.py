"""The `claude-cli` engine: a headless, one-shot invocation of a vendor CLI.
v2 adapter per the runner spec's Implementation Decisions and the
2026-09-05 "Decisions: check+retry + claude-cli adapter" addendum.

engine_config keys (all engine-specific, opaque to the platform -- see
vaultos/registry.py's Skill.engine_config docstring):
- `binary` (required): absolute path to the CLI executable. No login PATH is
  assumed in headless environments (spec story 10), so this is never
  resolved by name.
- `args` (optional): base argv appended after the binary, before the prompt
  -- e.g. flags the legacy daemon always passed (`--print`, permission
  flags, etc). A plain list of strings, not templated.
- `model` (optional): appended as a `--model <value>` flag after `args`,
  before the prompt.
- `timeout_s` (optional): defaults to DEFAULT_TIMEOUT_S, same convention as
  the script engine.
- `prompt` (optional): a str.format() template for the prompt, substituted
  against the job's own args plus the reserved `{job_id}` placeholder. When
  absent, the job's own `prompt` arg (a normal Skill arg, validated by the
  registry like any other) is used verbatim. Which shape a given skill uses
  is the registry author's call -- this ticket supports both rather than
  forcing every claude-cli skill to reshape its args around a template.

The prompt is passed as the invocation's final argv element (one-shot,
matching the legacy daemon's shape); stdout is the run's output.
"""

import subprocess

from .base import EngineContext, EngineResult

DEFAULT_TIMEOUT_S = 120
# Cap what lands in the job's `summary` column -- see script.py's identical
# constant and comment; the full output always goes to the per-run log file.
SUMMARY_MAX_CHARS = 500

# Retry context (runner core's check+retry loop -- see engines/base.py's
# Engine.run docstring) enters this adapter's input appended to the prompt
# under this marker line, per the 2026-09-05 addendum: "appended to the
# prompt under a clear marker line".
RETRY_CONTEXT_MARKER = "\n\n--- Previous attempt failed verification ---\n"


class ClaudeCliEngineError(RuntimeError):
    """A claude-cli skill is misconfigured (no binary, or no prompt source --
    neither an engine_config template nor a job `prompt` arg), or the binary
    itself couldn't be run. Raised, not swallowed -- vaultos.runner.core's
    generic engine-crash handling turns it into a clear `error` event, same
    as any other adapter exception (spec story 11's fast-fail semantics)."""


class ClaudeCliEngine:
    name = "claude-cli"

    def run(
        self, *, job, skill, ctx: EngineContext, retry_context: str | None = None
    ) -> EngineResult:
        config = skill.engine_config or {}
        binary = config.get("binary")
        if not binary:
            raise ClaudeCliEngineError(f"claude-cli skill '{skill.id}' has no binary configured")

        prompt = self._build_prompt(job, skill, config)
        if retry_context is not None:
            prompt = f"{prompt}{RETRY_CONTEXT_MARKER}{retry_context}"

        argv = [binary, *config.get("args", [])]
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
            raise ClaudeCliEngineError(f"claude-cli skill '{skill.id}' failed to run: {exc}") from exc

        self._write_run_log(ctx, job.id, argv, proc)

        summary = (proc.stdout.strip() or proc.stderr.strip() or f"claude-cli exited {proc.returncode}")
        return EngineResult(
            success=proc.returncode == 0,
            exit_code=proc.returncode,
            summary=summary[:SUMMARY_MAX_CHARS],
        )

    @staticmethod
    def _build_prompt(job, skill, config: dict) -> str:
        template = config.get("prompt")
        if template:
            subst = {**job.args, "job_id": job.id}
            try:
                return template.format(**subst)
            except KeyError as exc:
                raise ClaudeCliEngineError(
                    f"claude-cli skill '{skill.id}' prompt template references unknown placeholder {exc}"
                ) from exc

        prompt = job.args.get("prompt")
        if not prompt:
            raise ClaudeCliEngineError(
                f"claude-cli skill '{skill.id}' has no engine_config 'prompt' template "
                f"and the job carries no 'prompt' arg"
            )
        return prompt

    @staticmethod
    def _write_run_log(ctx: EngineContext, job_id: str, argv: list[str], proc: subprocess.CompletedProcess) -> None:
        log_dir = ctx.state_root / "runs"
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / f"{job_id}.log").write_text(
            f"$ {' '.join(argv)}\nexit={proc.returncode}\n\n"
            f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}\n"
        )
