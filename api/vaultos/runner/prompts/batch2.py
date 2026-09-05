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

import json
import re

from .base import (
    AUTONOMOUS_PREFIX,
    BuilderContext,
    BuiltPrompt,
    PromptBuilder,
    id8,
    now_time,
    slugify,
    today_date,
)


def acquire(args: dict, ctx: BuilderContext) -> BuiltPrompt | None:
    date = today_date(ctx.settings)
    deliverable = f"inbox/research/{date}-acquire.md"
    prompt = (
        f"""{AUTONOMOUS_PREFIX}\n\nTask: run today's consolidated cross-lane research pull and save it at exactly {deliverable}. This replaces six retired single-lane skills (leadership-brief, payments-brief, lean-brief, dev-trends-brief, chicago-brief, ai-wire) with one wide pass -- see projects/content-flow/2026-08-10-content-flow-design.md's Phase 3b for why. (A daily-topic-digest run against this report is queued automatically once you finish -- see Vault-Os-Api's jobs.py CHAIN_MAP -- so don't chain into it yourself here.)\n\nStep 1 -- fetch AND synthesize, per lane, through the acquisition-cache Cache/Resiliency Layer (see .claude/skills/acquisition-cache/SKILL.md). A fresh-enough prior result for the same target is served from cache instead of redone.\n\nRSS (1 Bash call, direct) -- fire this FIRST and wait for it to complete before doing anything else:\n"{ctx.settings.python_bin}" "{ctx.settings.rss_poll_script}"\nThis writes inbox/research/{date}-rss-feed.md on its own, and its live fetches already flow through the cache internally. Every lane subagent dispatched below reads this report's own section itself, so it must already exist on disk before you launch the Workflow call -- sequencing this first (not fired alongside the Workflow launch) closes a real race a lane subagent could otherwise hit reading a not-yet-written file.\n\nFetch + synthesize (5 lanes, isolated in per-lane subagents -- the one remaining step after RSS completes): launch the Workflow tool with scriptPath: "{ctx.settings.websearch_cached_fetch_workflow}", args: {{"todayDate": "{date}", "vaultRoot": "{ctx.vault_root}"}}. This dispatches 5 subagents in parallel. Each one, in a single turn: fetches its own WebSearch queries through the cache (leadership: 3, payments: 2, dev-trends: 3, chicago: 1, ai: 6 -- calling WebSearch for real only on a genuine miss); reads the RSS report's own matching section itself; for the ai lane, also calls yt_search_cached.py itself for its two queries (no separate yt-search step exists here anymore -- it's fully absorbed into that one lane's own subagent); synthesizes its own lane section (cross-source dedup, freshness/evergreen judgment -- see acquisition-cache/SKILL.md's Synthesizer section for the exact criteria); and persists that judged result through the same Cache/Resiliency Layer under a distinct acquisition type from the raw fetches. None of that raw or synthesized content ever lands in your own context -- only a minimal {{lane, candidates, kept}} per lane does, once all 5 finish. args.todayDate and args.vaultRoot are both required; the script throws if either is missing. Wait for the Workflow call's completion notification before proceeding to Step 2.\n\n(Known, documented limitation: cache persistence under concurrent dispatch isn't 100% reliable, for both raw fetch and lane synthesis entries -- see acquisition-cache/SKILL.md's "Concurrent persist reliability" section. A same-day rerun hits cache for most but not necessarily all of a lane's work; anything that misses just redoes that specific piece. This does not affect any single run's own report correctness.)\n\nStep 2 -- assemble the report. All five lanes' judgment work is already done and persisted -- this step is purely mechanical, a plain script, no reasoning involved:\n"{ctx.settings.python_bin}" "{ctx.settings.assemble_acquire_report_cli}" "{date}" "{deliverable}" leadership payments dev-trends chicago ai\nThis reads each lane's persisted synthesis result and writes the complete report -- frontmatter, title/date header, and all five lane sections in order -- directly to {deliverable}. Exit code 0 means the file is written; any non-zero exit means NOTHING was written (a missing or stale lane result is a hard failure, never silently rendered as an empty "0 kept" lane) -- the error message names which lane and why; do not proceed if this happens.\n\nStep 3 -- read the report you just wrote at {deliverable}. This is the ONLY time raw or synthesized lane content enters your own context, and it's the small, already-judged final report, never the raw pre-synthesis material any lane subagent gathered. Use it to make your final spoken-summary line name real specifics.\n\nEnd your reply with: SAVED {deliverable}"""
    )
    return BuiltPrompt(prompt=prompt, deliverable_path=deliverable)


def daily_topic_digest(args: dict, ctx: BuilderContext) -> BuiltPrompt | None:
    date = today_date(ctx.settings)
    deliverable = f"inbox/reports/daily-topic-digest/{date}-daily-topic-digest.md"
    prompt = (
        f"""{AUTONOMOUS_PREFIX}\n\nTask: read unread evidence gathered so far and propose ranked article topics, writing a report at exactly {deliverable}.\n\nStep 1 -- gather everything not yet attached to a topic. Read every existing note in topics/ (the folder may not exist yet or may be empty -- a normal first-run state, not an error) and collect the full `evidence:` list from each, across every status including `rejected` -- a rejected topic's evidence should not cause its topic to be re-proposed. Then scan: (1) sources/ -- every file; (2) inbox/research/*.md -- acquire's consolidated per-lane reports, however many days are currently present. For each item found, check whether it's already referenced (by filename, wiki-link, or URL) in some existing topic's `evidence:` list -- anything not referenced is new evidence. If nothing is new, say so plainly in your final reply and stop -- don't force topic proposals out of stale material.\n\nStep 2 -- look for genuine signal, not just volume. Read every piece of new evidence and reason narratively: recurrence across independent sources (the same specific claim showing up from more than one outlet) is real signal; specificity beats vague trend mentions; check writing/product.md for the stated audience and writing/voice.md for tone -- a topic technically interesting but off-brand for a Group Engineering Manager's Substack is weaker than one that plays to lived experience; skim writing/published/_index.md if present so a topic that rehashes already-published ground needs a genuinely new angle.\n\nStep 3 -- dedup against existing topics by meaning, not filename. Compare each candidate against every existing topics/ note (any status). If a close match is `rejected`, do not re-propose it. If a close match is open (`proposed`/`queued`/etc.), don't create a duplicate -- add the new source(s) to that existing topic's `evidence:` list instead, leaving its `status:`/`first_seen:` untouched.\n\nStep 4 -- check persona fit, inline, no fan-out. For each candidate that survived Steps 2-3, read every file in writing/personas/ with `active: true` in its frontmatter (skip inactive ones; if the folder is missing or has no active entries, say so and skip this step -- every candidate proceeds to Step 5 unchecked). Reason narratively, in this same pass, against each active persona in turn -- one hit/miss line per persona with a one-sentence why, grounded in that persona's own "What they're looking for"/"What makes them bounce" sections; don't invent a numeric score, same as Step 2. Then: if the candidate hits at least one persona, proceed to Step 5 and name which persona(s) it lands with in the note. If it misses every persona but still clears Step 2's own signal bar (recurrence across independent sources + real specificity -- not a new bar), proceed to Step 5 anyway but say so explicitly in the note, e.g. "Off-persona: doesn't fit [persona names], flagged anyway because [reason]" -- and carry that flag through Step 6's ranked list and Step 7's pick prompt, don't let it blend in as an ordinary pick. If it misses every persona and doesn't clear that bar, drop it -- no topic note -- and record it in Step 6's report as "considered, dropped: off-persona, insufficient signal" so it isn't silently discarded.\n\nStep 5 -- write topic notes. For each genuinely new topic, create topics/<slug>.md using the template at system/templates/topic.md: `status: proposed`, `origin: proposed`, `first_seen: {date}`, `lane:` (single best-fit value from leadership/payments/lean-agile/dev-trends/chicago/ai/product -- if a topic spans lanes, pick the primary one and note the crossover in the body, don't invent a new lane), `evidence:` (wiki-links `[[filename]]`, no extension, or bare URLs for anything outside the vault), `retention: durable`. Body: a short title as H1, a "Why this might be worth writing about" section (2-4 specific sentences referencing the actual evidence, plus persona fit or off-persona flag from Step 4), an "Evidence" bulleted list mirroring the frontmatter, and an empty "Notes" section. Slugs: lowercase, hyphens, no date prefix.\n\nStep 6 -- write the report at exactly {deliverable}. YAML frontmatter: `date: {date}`, `skill: daily-topic-digest`, `tags: [digest, topics]`, `retention: ephemeral`. Body: how many evidence items were scanned, how many were already covered, how many new topics were proposed vs. folded into existing ones, how many candidates Step 4 dropped as off-persona/insufficient-signal (name them), and the ranked list itself (topic title, one-line why, persona-fit flag if off-persona, link to the note). Rank by: recurrence across sources > specificity > distinctiveness from published work -- narrative ranking only, don't fake a numeric score. Step 4's persona check doesn't change this ordering, it only filtered/flagged the candidate set.\n\nStep 7 -- your final reply must present the ranked list conversationally, not just "see the report", and explicitly ask which to pursue next, e.g.: "Ranked: 1) ... 2) ... 3) [off-persona, flagged anyway: ...] ... Want me to kick off deep-research on one of these, or should any of these move straight to queued?" Any off-persona-flagged entry must be named in this reply, not left to the report alone. This pick-prompt IS your final reply -- don't follow it with anything else.\n\nIf topics/, sources/, or inbox/research/ don't exist yet or are empty, say so and stop rather than fabricating topics from nothing. Never invent evidence -- every `evidence:` entry must trace to a real file actually read or a real URL that appeared in one."""
    )
    return BuiltPrompt(prompt=prompt, deliverable_path=deliverable)


def deep_research(args: dict, ctx: BuilderContext) -> BuiltPrompt | None:
    topic = (args.get("topic") or "").strip()
    if not topic:
        return None
    # Defense in depth, matching the legacy source's own comment: the API
    # layer already sanitizes args.topic before it reaches the queue, but
    # this prompt must stay self-contained and shouldn't blindly trust the
    # caller -- strip quote/backtick/control chars (incl. newlines, which
    # could inject fresh instructions into this headless prompt) and cap
    # length so a hostile topic can't break the shell quoting on the
    # yt-search command line below.
    safe_topic = re.sub(r'["`\x00-\x1f\x7f]', "", topic)[:200].strip()
    if not safe_topic:
        return None
    date = today_date(ctx.settings)
    draft_slug = (args.get("draft_slug") or "").strip()
    # Only accept a slug matching the vault's own lowercase-hyphen convention
    # (also closes a path-traversal vector) -- an unsafe/invalid draft_slug
    # falls back to the topic-based filename below, exactly like the legacy
    # deliverablePathFor()/buildPrompt() cases both do independently.
    safe_draft_slug = draft_slug if re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", draft_slug) else ""
    if safe_draft_slug:
        # Auto-fired path (content-flow design's Review Topics -> Create
        # Draft -> auto deep-research checkbox): deterministic, slug-keyed
        # filename, no date, overwritten on rerun -- so research-into-draft
        # (chained with zero args) can find this report purely by matching
        # the draft's own slug.
        deliverable = f"inbox/deep-research/{safe_draft_slug}-deep-research.md"
    else:
        # NOTE: the legacy deliverablePathFor() case slugifies the RAW
        # trimmed topic here, not the sanitized safe_topic used in the
        # prompt body below -- buildPrompt() computes its own safeTopic
        # independently and never feeds it back into the deliverable path,
        # so this mirrors that split exactly.
        deliverable = f"inbox/deep-research/{date}-{slugify(topic)}-deep-research.md"

    # topic_context is NEVER interpolated into search queries or the
    # filename (only safe_topic is) -- it's evidence text from a topic note,
    # not something safe to hand to yt-search's shell command line or treat
    # as a trusted search string. Strip control chars only (incl. newlines)
    # and cap length; used purely to enrich the synthesis instructions.
    topic_context = re.sub(r"[\x00-\x1f\x7f]", "", args.get("topic_context") or "")[:3000].strip()
    context_block = (
        f"\n\nContext already gathered before this research was requested (from the topic "
        f"note this run was fired against) — use it to inform the synthesis and avoid "
        f"re-discovering what's already known, but do not treat it as a citable source or "
        f"search target:\n{topic_context}"
        if topic_context
        else ""
    )
    draft_slug_frontmatter = (
        f", `draft_slug: {json.dumps(safe_draft_slug)}`" if safe_draft_slug else ""
    )
    prompt = (
        f"""{AUTONOMOUS_PREFIX}\n\nTask: produce a multi-source research brief on "{safe_topic}" and save it at exactly {deliverable}.\n\nFan out across four sources on this specific topic:\n\n1. YouTube — run this command and use its output for the YouTube section:\n"{ctx.settings.python_bin}" "{ctx.settings.yt_search_script}" "{safe_topic}" --count 10 --months 6\n2. Web — WebSearch "{safe_topic} 2026" (broad) and "{safe_topic} tutorial OR guide OR announcement" (targeted).\n3. X/Twitter — WebSearch "{safe_topic}" site:x.com and "{safe_topic}" site:twitter.com.\n4. GitHub — WebSearch "{safe_topic}" site:github.com and "{safe_topic} github repo OR library OR tool".\n\nDo NOT create a NotebookLM notebook or run any notebooklm CLI commands — this is a fast unattended scan, not the full interactive deep-research pipeline; keep it to WebSearch and the yt-search script above.\n\nSynthesize, don't just stack sections — look for: patterns across sources (the same point made independently on YouTube AND Twitter AND a blog is a strong signal), contradictions (one source says X is great, another says it's broken), gaps (an angle covered on one platform but not another), and velocity (is this rising or fading — compare dates and engagement).{context_block}\n\nStructure the note exactly, in this order:\n"# Deep Research: {safe_topic}" then "**Date:** {date}"\n"## Key Takeaways" — 3-5 bullets, the synthesized cross-source findings, not a per-source recap.\n"## YouTube Landscape" — markdown table "| Video | Creator | Views | Date |", plus key creators covering this topic and any content gaps.\n"## Web / Articles" — bullet per article with a 1-2 line summary and link.\n"## X / Twitter Pulse" — overall sentiment, notable voices, common questions, key threads with links.\n"## GitHub Activity" — bullet per notable repo (stars, what it does) plus overall community activity level.\n"## Content Opportunities" — gaps or angles worth pursuing, based on the cross-source synthesis above.\n\nYAML frontmatter: `date: {date}`, `skill: deep-research`, `topic: {json.dumps(topic)}`{draft_slug_frontmatter}, `tags: [research]`.\n\nEnd your reply with: SAVED {deliverable}"""
    )
    return BuiltPrompt(prompt=prompt, deliverable_path=deliverable)


def article_refiner(args: dict, ctx: BuilderContext) -> BuiltPrompt | None:
    article_path = (args.get("article_path") or "").strip()
    if not article_path:
        return None
    date = today_date(ctx.settings)
    deliverable = f"inbox/reports/article-refiner/{date}-article-refiner-{id8(ctx.job_id)}.md"
    # date AND local time, not just the date -- a second run later the same
    # day needs distinguishable headings across every dated sub-section it
    # writes (Proposed Revision, Hook Options, Changelog), not just
    # Changelog, or a same-day re-run edits the prior entry in place instead
    # of creating its own.
    run_stamp = f"{date} {now_time(ctx.settings)}"
    doc_hint = ctx.settings.article_refiner_skill_doc_hint
    prompt = (
        f"""{AUTONOMOUS_PREFIX}\n\nTask: propose a polish pass on the article at {article_path}, and save a short summary of what you proposed at exactly {deliverable}.\n\nThis is content-flow design Q52's queueable form of the article-refiner skill (see {doc_hint} for the full editorial rules this inlines) -- it is NOT a direct rewrite. You never touch the article's Body directly; everything you produce gets appended into the article's own Notes/Changelog/Flagged for Input sections for the author to review and manually promote.\n\nStep 1 -- read the article. Its Body is everything in the file before the first exact occurrence of "## Notes", "## Changelog", or "## Flagged for Input" (if none exist, the whole thing is Body). IMPORTANT: only ever match those three headings by their exact text -- articles routinely use "##" for their own internal subheadings as ordinary prose structure (confirmed: 11 of 22 published articles do this), and those must never be mistaken for a section boundary.\n\nIf Body is empty or near-empty, there's no existing draft to refine -- treat whatever's in Notes (evidence, research, the author's own thinking) as raw material and draft a full first piece from it.\n\nIf Body already has real prose, that's the actual draft to work from -- and Notes now serves two different purposes you need to tell apart:\n- Background context (evidence lists, research bullets, prior "### Proposed Revision" or "### Research" entries from earlier runs) -- scratch space from other tools and prior passes. Don't re-read this as new instructions, and don't repeat it verbatim into your output.\n- Revision requests -- freeform notes the author wrote about THIS draft specifically: things to cover, cut, expand, or change. These read like directives, not evidence ("expand the X section," "cut the Y paragraph," "extend the closing"). Treat these as live instructions for this pass. If a request already looks satisfied by the current Body, don't force a change for its own sake -- just say so in the Changelog, the same way you'd note a Flagged item is now resolved.\n\nStep 2 -- refine, don't reinvent. Preserve the author's argument and voice exactly -- same claims, same support, same characteristic word choices and rhythm, just clearer and more persuasive. Never invent statistics, dates, names, quotes, or sources that aren't in the draft; when a passage would clearly benefit from a specific fact that isn't there, leave it honestly vague or mark it inline with [NEEDS INPUT: ...] rather than fabricating one. Work the dimensions that actually need it: hook/opening, structure, clarity/flow, closing, voice consistency -- don't rewrite what already works. When Body already had prose and Step 1 identified revision requests in Notes, weave those specific changes in as part of this pass -- don't just do a generic polish and skip what was actually asked for. Produce the full refined draft, not notes about what should change.\n\nStep 2b -- link sources. When the draft references a specific person's quote, a named source, or a statistic, and that source's URL is unambiguous (the only candidate source provided for that specific claim -- check the article's own Evidence/Sources entries and any linked deep-research report), hyperlink it inline using standard markdown: `[Aaron Levie](https://x.com/levie/status/...)`. If two or more possible sources could back the same claim and it isn't clear which one actually does, don't guess and link one -- flag the attribution with [NEEDS INPUT: ...] instead, same as a missing fact. A confidently-wrong link is worse than an honest gap.\n\nStep 2c -- hook and subtitle options. Every draft needs a deliberate opening, not a default one. Generate 2-3 distinct hook options for the piece's first paragraph, each paired with a matching subtitle -- draw from patterns already validated in this author's own published work: a direct-address conditional ("If you're experiencing X, this is for you"), a sharp claim/thesis-first opening, a concrete scene or anecdote, or a real open question. Each option should be a genuinely different approach, not a reworded variant of the same one. Pick whichever fits this piece best and use it as the actual opening of your refined draft in Step 2 -- don't leave the draft with a placeholder opening. Never write into the `subtitle:` frontmatter field directly -- subtitle gets the exact same propose-then-promote treatment as Body. Put every option (including the one you used) in the Hook Options entry described in Step 3, and let the author choose which one, if any, to promote themselves.\n\nStep 3 -- append your output into the article's own sections, never touching Body or frontmatter:\n- If the article doesn't already have a "## Notes" heading, add one at the end of the body. Under it, append a "### Proposed Revision ({run_stamp})" sub-section containing the complete refined draft in full, and a "### Hook Options ({run_stamp})" sub-section listing each option from Step 2c -- label, the opening paragraph text, and its paired subtitle -- marking which one you used in the Proposed Revision as "(used above)". Use the exact same {run_stamp} value (date and time) for both headings on this pass, so they're visibly paired.\n- If the article doesn't already have a "## Changelog" heading, add one at the end of the body (after Notes if you just added it). Under it, append a "### {run_stamp}" sub-section with a short paragraph or a few bullets on the headline edits and why -- tight context for the author, not a second draft. If Step 1 identified any revision requests in Notes, explicitly call out which ones you applied (and how) and which ones you judged already satisfied by the existing Body -- the author needs to be able to tell what happened to each request without re-reading the whole diff.\n- If the article doesn't already have a "## Flagged for Input" heading, add one at the end of the body. Under it, append the specific things needing the author's input (or skip this step entirely if there's nothing to flag -- don't write an empty section).\n\nStep 4 -- write the summary at exactly {deliverable}. YAML frontmatter: `date: {date}`, `skill: article-refiner`, `tags: [writing]`, `retention: ephemeral`. Body: the article's title, a one-line description of the headline edits proposed, and how many items (if any) got flagged.\n\nEnd your reply with: SAVED {deliverable}"""
    )
    return BuiltPrompt(prompt=prompt, deliverable_path=deliverable)


# skill id -> builder, merged into vaultos.runner.prompts.PROMPT_BUILDER_REGISTRY
# (see prompts/__init__.py) -- mirrors batch1.py's own BATCH1_BUILDERS.
BATCH2_BUILDERS: dict[str, PromptBuilder] = {
    "acquire": acquire,
    "daily-topic-digest": daily_topic_digest,
    "deep-research": deep_research,
    "article-refiner": article_refiner,
}
