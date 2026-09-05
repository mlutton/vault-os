"""Batch 2 of the prompt port (ticket #26): the heavy research/writing
pipeline skills from the legacy Node daemon's `buildPrompt()` switch --
acquire, daily-topic-digest, article-refiner, research-persona-fanout, and
deep-research. See `batch1.py`'s module docstring for the full enumeration
of what's ported where, and what's excluded (voice-ask, rss-feed-poll,
daily-digest) and why.

Every prompt below is ported VERBATIM from `Fable-Os-Web/runner/runner.js`
(private repo, read-only source for this port) -- wording, step sequencing,
and guard rails unchanged, per the design addendum ("re-authoring prompts is
explicitly not this work"). Textual changes from the legacy source are only
the absolute personal paths the legacy config-constant block embedded --
each one lifted to its own `Settings` field (see `config.py`'s batch-2
block): `Settings.python_bin`, `.rss_poll_script`, `.yt_search_script`,
`.websearch_cached_fetch_workflow`, `.assemble_acquire_report_cli`,
`.cache_cli`, `.assemble_review_script`, plus two more doc-pointer hints in
`wiki_ingest_skill_doc_hint`'s own style: `.article_refiner_skill_doc_hint`
and `.research_persona_fanout_skill_doc_hint`.

Built incrementally, one skill (and its own test file additions) per commit
-- this first slice is `acquire` only; the module grows to the full
five-skill set across the ticket's remaining commits.
"""

from .base import (
    AUTONOMOUS_PREFIX,
    BuilderContext,
    BuiltPrompt,
    PromptBuilder,
    today_date,
)


def acquire(args: dict, ctx: BuilderContext) -> BuiltPrompt | None:
    date = today_date(ctx.settings)
    deliverable = f"inbox/research/{date}-acquire.md"
    prompt = (
        f"""{AUTONOMOUS_PREFIX}\n\nTask: run today's consolidated cross-lane research pull and save it at exactly {deliverable}. This replaces six retired single-lane skills (leadership-brief, payments-brief, lean-brief, dev-trends-brief, chicago-brief, ai-wire) with one wide pass -- see projects/content-flow/2026-08-10-content-flow-design.md's Phase 3b for why. (A daily-topic-digest run against this report is queued automatically once you finish -- see Vault-Os-Api's jobs.py CHAIN_MAP -- so don't chain into it yourself here.)\n\nStep 1 -- fetch AND synthesize, per lane, through the acquisition-cache Cache/Resiliency Layer (see .claude/skills/acquisition-cache/SKILL.md). A fresh-enough prior result for the same target is served from cache instead of redone.\n\nRSS (1 Bash call, direct) -- fire this FIRST and wait for it to complete before doing anything else:\n"{ctx.settings.python_bin}" "{ctx.settings.rss_poll_script}"\nThis writes inbox/research/{date}-rss-feed.md on its own, and its live fetches already flow through the cache internally. Every lane subagent dispatched below reads this report's own section itself, so it must already exist on disk before you launch the Workflow call -- sequencing this first (not fired alongside the Workflow launch) closes a real race a lane subagent could otherwise hit reading a not-yet-written file.\n\nFetch + synthesize (5 lanes, isolated in per-lane subagents -- the one remaining step after RSS completes): launch the Workflow tool with scriptPath: "{ctx.settings.websearch_cached_fetch_workflow}", args: {{"todayDate": "{date}", "vaultRoot": "{ctx.vault_root}"}}. This dispatches 5 subagents in parallel. Each one, in a single turn: fetches its own WebSearch queries through the cache (leadership: 3, payments: 2, dev-trends: 3, chicago: 1, ai: 6 -- calling WebSearch for real only on a genuine miss); reads the RSS report's own matching section itself; for the ai lane, also calls yt_search_cached.py itself for its two queries (no separate yt-search step exists here anymore -- it's fully absorbed into that one lane's own subagent); synthesizes its own lane section (cross-source dedup, freshness/evergreen judgment -- see acquisition-cache/SKILL.md's Synthesizer section for the exact criteria); and persists that judged result through the same Cache/Resiliency Layer under a distinct acquisition type from the raw fetches. None of that raw or synthesized content ever lands in your own context -- only a minimal {{lane, candidates, kept}} per lane does, once all 5 finish. args.todayDate and args.vaultRoot are both required; the script throws if either is missing. Wait for the Workflow call's completion notification before proceeding to Step 2.\n\n(Known, documented limitation: cache persistence under concurrent dispatch isn't 100% reliable, for both raw fetch and lane synthesis entries -- see acquisition-cache/SKILL.md's "Concurrent persist reliability" section. A same-day rerun hits cache for most but not necessarily all of a lane's work; anything that misses just redoes that specific piece. This does not affect any single run's own report correctness.)\n\nStep 2 -- assemble the report. All five lanes' judgment work is already done and persisted -- this step is purely mechanical, a plain script, no reasoning involved:\n"{ctx.settings.python_bin}" "{ctx.settings.assemble_acquire_report_cli}" "{date}" "{deliverable}" leadership payments dev-trends chicago ai\nThis reads each lane's persisted synthesis result and writes the complete report -- frontmatter, title/date header, and all five lane sections in order -- directly to {deliverable}. Exit code 0 means the file is written; any non-zero exit means NOTHING was written (a missing or stale lane result is a hard failure, never silently rendered as an empty "0 kept" lane) -- the error message names which lane and why; do not proceed if this happens.\n\nStep 3 -- read the report you just wrote at {deliverable}. This is the ONLY time raw or synthesized lane content enters your own context, and it's the small, already-judged final report, never the raw pre-synthesis material any lane subagent gathered. Use it to make your final spoken-summary line name real specifics.\n\nEnd your reply with: SAVED {deliverable}"""
    )
    return BuiltPrompt(prompt=prompt, deliverable_path=deliverable)


# skill id -> builder, merged into vaultos.runner.prompts.PROMPT_BUILDER_REGISTRY
# (see prompts/__init__.py) -- mirrors batch1.py's own BATCH1_BUILDERS.
BATCH2_BUILDERS: dict[str, PromptBuilder] = {
    "acquire": acquire,
}
