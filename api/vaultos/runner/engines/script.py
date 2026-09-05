"""The `script` engine: runs a per-skill argv template as a subprocess, no
LLM involved. v1 adapter per the runner spec's Implementation Decisions.

The argv template (and an optional deliverable-path template) live on the
skill's `engine_config` in the registry -- data-driven configuration, not
hardcoded in this file, per the ticket's "binary + args from runner
configuration, not hardcoded". Each argv element is a str.format() template
substituted against the job's own args plus two reserved placeholders,
`{job_id}` and `{vault_root}`.
"""

import os
import subprocess

from .base import EngineContext, EngineResult

DEFAULT_TIMEOUT_S = 120
# Cap what lands in the job's `summary` column -- stdout/stderr can be
# arbitrarily large; the full output always goes to the per-run log file
# under the state root (spec story 17), this is just the at-a-glance record.
SUMMARY_MAX_CHARS = 500


class ScriptEngineError(RuntimeError):
    """A script skill is misconfigured (no argv, or an argv placeholder the
    job's args/reserved substitutions can't resolve). Raised, not swallowed
    here -- vaultos.runner.core's generic engine-crash handling turns it into
    a clear `error` event, same as any other adapter exception."""


class ScriptEngine:
    name = "script"

    def run(
        self, *, job, skill, ctx: EngineContext, retry_context: str | None = None
    ) -> EngineResult:
        config = skill.engine_config or {}
        argv_template = config.get("argv")
        if not argv_template:
            raise ScriptEngineError(f"script skill '{skill.id}' has no argv configured")

        subst = {**job.args, "job_id": job.id, "vault_root": str(ctx.vault_root)}
        try:
            argv = [str(item).format(**subst) for item in argv_template]
        except KeyError as exc:
            raise ScriptEngineError(
                f"script skill '{skill.id}' argv references unknown placeholder {exc}"
            ) from exc

        # This adapter's choice of how retry_context (core's check+retry loop,
        # see engines/base.py's Engine.run docstring) enters its input: an
        # environment variable, visible to the subprocess like any other env
        # var. Only set on a retry -- a first attempt runs with a plain
        # inherited environment, same as before this feature existed.
        env = None
        if retry_context is not None:
            env = {**os.environ, "VAULTOS_CHECK_FEEDBACK": retry_context}

        timeout_s = config.get("timeout_s", DEFAULT_TIMEOUT_S)
        try:
            proc = subprocess.run(
                argv,
                cwd=ctx.vault_root,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                env=env,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ScriptEngineError(f"script skill '{skill.id}' failed to run: {exc}") from exc

        self._write_run_log(ctx, job.id, argv, proc)

        deliverable_path = None
        deliverable_template = config.get("deliverable")
        if proc.returncode == 0 and deliverable_template:
            rel = deliverable_template.format(**subst)
            if (ctx.vault_root / rel).exists():
                deliverable_path = rel

        summary = proc.stdout.strip() or proc.stderr.strip() or f"script exited {proc.returncode}"
        return EngineResult(
            success=proc.returncode == 0,
            exit_code=proc.returncode,
            summary=summary[:SUMMARY_MAX_CHARS],
            deliverable_path=deliverable_path,
        )

    @staticmethod
    def _write_run_log(
        ctx: EngineContext, job_id: str, argv: list[str], proc: subprocess.CompletedProcess
    ) -> None:
        log_dir = ctx.state_root / "runs"
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / f"{job_id}.log").write_text(
            f"$ {' '.join(argv)}\nexit={proc.returncode}\n\n"
            f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}\n"
        )
