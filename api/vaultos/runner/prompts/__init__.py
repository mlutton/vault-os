"""The prompt-builder registry (ticket #25): skill id -> builder(job args,
BuilderContext) -> BuiltPrompt | None. The one new seam the runner spec's
2026-09-05 "prompt-builder registry + port batches" addendum calls for --
the legacy Node daemon's per-skill `buildPrompt()`/`deliverablePathFor()`
cases land behind this single lookup, keyed exactly as vaultos.registry
keys skills.

Engines stay the integration point: `vaultos.runner.engines.claude_cli` and
`.cursor_cli` check `get_builder(skill.id)` first and use its prompt (and
deliverable path) as their input when a skill has one; a skill with no
builder keeps today's behavior unchanged (prompt from job args / engine
config's own `prompt` template -- the no-builder passthrough, tested in
test_runner_prompts.py). This package never imports `vaultos.modules` --
same infrastructure-only rule as the rest of `vaultos.runner`.

Batch 1 (ticket #25) shipped the operational/simple skills. Batch 2 (this
ticket, #26) adds the heavy pipeline set -- acquire, daily-topic-digest,
article-refiner, research-persona-fanout, deep-research -- the same way, in
its own `batch2.py`, built up one skill per commit.
"""

from .base import AUTONOMOUS_PREFIX, BuilderContext, BuiltPrompt, PromptBuilder, id8, now_time, today_date, tomorrow_date
from .batch1 import BATCH1_BUILDERS
from .batch2 import BATCH2_BUILDERS

PROMPT_BUILDER_REGISTRY: dict[str, PromptBuilder] = {
    **BATCH1_BUILDERS,
    **BATCH2_BUILDERS,
}


def get_builder(skill_id: str) -> PromptBuilder | None:
    """The lookup engines call. None means "no builder for this skill" --
    callers fall back to their own current prompt source (engine_config's
    `prompt` template, or the job's own `prompt` arg), never an error on its
    own; only a registered builder that itself returns None (missing/blank
    required job args) is the "this job is invalid" case."""
    return PROMPT_BUILDER_REGISTRY.get(skill_id)


__all__ = [
    "AUTONOMOUS_PREFIX",
    "BuilderContext",
    "BuiltPrompt",
    "PROMPT_BUILDER_REGISTRY",
    "PromptBuilder",
    "get_builder",
    "id8",
    "now_time",
    "today_date",
    "tomorrow_date",
]
