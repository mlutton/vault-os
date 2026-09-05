"""Batch 1 of the prompt port (ticket #25): every skill from the legacy
Node daemon's `buildPrompt()` switch EXCEPT the heavy pipeline set (acquire,
daily-topic-digest, article-refiner, research-persona-fanout, deep-research
-- batch 2, ticket #26), voice-ask (excised feature, never ported -- see
module docstring below), and the script-engine skill (rss-feed-poll, which
has a `deliverablePathFor()` case but no `buildPrompt()` case -- no LLM
prompt to port). See this ticket's handback for the full enumeration.

Note on `daily-digest`, also named in ticket #25's batch-2/excluded list:
it has no `buildPrompt()`/`deliverablePathFor()` case in the legacy daemon
at all (confirmed by grep) -- it was never a runner-dispatched job there,
only an interactive Claude Code skill. Nothing to defer for it here; it's
named for completeness in the handback, not because anything was skipped.

Every prompt below is ported VERBATIM from `Fable-Os-Web/runner/runner.js`
(private repo, read-only source for this port) -- wording, step sequencing,
and guard rails unchanged, per the design addendum ("re-authoring prompts is
explicitly not this work"). The only textual change from the legacy source
is the one personal path it embedded (wiki-ingest's doc pointer), lifted to
`Settings.wiki_ingest_skill_doc_hint` -- see that builder's docstring.

voice-ask is NOT ported and never will be under this ticket: it was the
legacy daemon's push-to-talk voice-command handler, and voice command
routing was excised from this codebase entirely on 2026-09-04 (see
Fable-Os-Web/CLAUDE.md's "Load-bearing couplings" -- `POST /route` now
returns 404, spoken commands silently do nothing). There is no `voice-ask`
skill in this repo's registry and no plan to add one back.
"""

from posixpath import basename, dirname

from .base import (
    AUTONOMOUS_PREFIX,
    BuilderContext,
    BuiltPrompt,
    PromptBuilder,
    id8,
    today_date,
    tomorrow_date,
)


def plan_today(args: dict, ctx: BuilderContext) -> BuiltPrompt | None:
    date = today_date(ctx.settings)
    deliverable = f"daily-notes/{date}.md"
    prompt = (
        f"{AUTONOMOUS_PREFIX}\n\nTask: set up today's daily note at exactly {deliverable}."
        f"\n\nSteps:\n1. If today's note doesn't exist yet, create it from the template at "
        f"system/templates/daily.md (fill in today's date). If it already exists, edit it in "
        f"place — do not overwrite sections other skills may have already filled in (Leadership "
        f"And Payments News Brief, Activity Log, Notes, etc.).\n2. Read yesterday's daily note "
        f"(if present) for unfinished Top 3 Priorities and unchecked Daily Drivers — carry "
        f"forward any still-relevant ones into today's Top 3 Priorities / Daily Drivers.\n3. If "
        f"a Google Calendar MCP connector is available, pull today's events (timeZone="
        f"{ctx.settings.hud_tz}) and write them into the Schedule section, one per line, format "
        f'"- HH:MM — <event>" (24-hour time, matches the schema\'s parser regex exactly). If no '
        f"calendar connector is available, leave Schedule as-is and say so in your final reply."
        f"\n4. Follow the schema at system/schemas/daily-note.md for exact heading names and "
        f"field formats — heading text must match exactly or the HUD's parser will silently miss "
        f"the section.\n\nEnd your reply with: SAVED {deliverable}"
    )
    return BuiltPrompt(prompt=prompt, deliverable_path=deliverable)


def plan_tomorrow(args: dict, ctx: BuilderContext) -> BuiltPrompt | None:
    deliverable = f"daily-notes/{tomorrow_date(ctx.settings)}.md"
    prompt = (
        f"{AUTONOMOUS_PREFIX}\n\nTask: draft tomorrow's daily note at exactly {deliverable}."
        f"\n\nSteps:\n1. Read today's daily note for unfinished Top 3 priorities (carryover)."
        f"\n2. If a Google Calendar MCP connector is available, pull tomorrow's events "
        f"(timeZone={ctx.settings.hud_tz}).\n3. Suggest 3 priorities for tomorrow.\n4. Write the "
        f"note following the schema at system/schemas/daily-note.md.\n\nEnd your reply with: "
        f"SAVED {deliverable}"
    )
    return BuiltPrompt(prompt=prompt, deliverable_path=deliverable)


def vault_cleanup(args: dict, ctx: BuilderContext) -> BuiltPrompt | None:
    date = today_date(ctx.settings)
    deliverable = f"inbox/reports/vault-cleanup/{date}-cleanup-{id8(ctx.job_id)}.md"
    prompt = (
        f"{AUTONOMOUS_PREFIX}\n\nTask: tidy the vault and report at exactly {deliverable}."
        f"\n\nScan the vault for markdown files whose YAML frontmatter contains `retention: "
        f"ephemeral` AND whose modification time is more than 30 days old. Do NOT touch any file "
        f"that lacks a `retention:` field entirely, or that carries `retention: durable` — both "
        f"are permanently out of scope for this skill regardless of age. This replaces the old "
        f"7-day untouched-file heuristic; age alone is never sufficient reason to move a file. "
        f"Move each match into an archive/ subfolder mirroring its original path (e.g. "
        f"inbox/research/2026-01-01-acquire.md → archive/inbox/research/2026-01-01-acquire.md). "
        f"Write a one-page report at {deliverable} — YAML frontmatter `date: {date}`, `skill: "
        f"vault-cleanup`, `tags: [cleanup, ops]`; body lists what moved, and a one-line count of "
        f"how many other files were scanned but skipped for lacking the ephemeral tag (no need to "
        f"list them individually).\n\nEnd your reply with: SAVED {deliverable}"
    )
    return BuiltPrompt(prompt=prompt, deliverable_path=deliverable)


def inbox_brief(args: dict, ctx: BuilderContext) -> BuiltPrompt | None:
    date = today_date(ctx.settings)
    deliverable = f"inbox/reports/inbox-briefs/{date}-inbox-brief-{id8(ctx.job_id)}.md"
    prompt = (
        f"{AUTONOMOUS_PREFIX}\n\nTask: triage inbox items from the last 24 hours and write a "
        f"report at exactly {deliverable}.\n\nSteps:\n1. Scan inbox/notes/, inbox/research/, and "
        f"inbox/personal/ (NOT inbox/reports/ — that's where skill outputs land, not raw items "
        f"to triage) for files created or modified in the last 24 hours.\n2. If a Gmail MCP "
        f"connector is available, also check for unread/recent messages worth surfacing, and for "
        f"each one note its real Gmail thread ID (from the connector's own data, never invented) "
        f"and classify it as `action` (genuinely needs a reply or decision from the user), `fyi` "
        f"(worth knowing, no action needed), or `skip` (noise). If the connector is unavailable, "
        f"skip that step and say so in your final reply — don't fail the whole brief over it."
        f"\n3. Summarize what's new: what each item is, whether it needs action, and any obvious "
        f"next step.\n4. Write the report — YAML frontmatter `date: {date}`, `skill: "
        f"inbox-brief`, `tags: [inbox]`; body groups items by source (vault notes vs. email) "
        f"with a one-line summary each. In the email section, render each message's sender (or "
        f"subject, whichever reads better in context) as a markdown link to "
        f"`https://mail.google.com/mail/u/0/#all/<real gmail thread id>` — same deep-link format "
        f"regardless of whether the section is a table or a list, so the link is clickable in "
        f"Obsidian. If any Gmail messages were triaged in step 2, ALSO add a frontmatter field "
        f"`action_items` listing every one of them (any priority, not just `action`) as a "
        f"single-line JSON array — this exact shape, no line breaks inside it: `action_items: "
        f'[{{"id": "<real gmail thread id>", "sender": "<sender name>", "subject": "<subject '
        f'line>", "priority": "action"}}]`. Omit `action_items` entirely if step 2 found no '
        f"Gmail connector or no messages.\n5. Check off \"- [ ] Inbox triage (Gmail)\" under "
        f"today's daily note's \"## Daily Drivers\" (daily-notes/{date}.md) if present — create "
        f"the day's Daily Drivers section from system/templates/daily.md first if today's note "
        f"doesn't exist yet.\n\nEnd your reply with: SAVED {deliverable}"
    )
    return BuiltPrompt(prompt=prompt, deliverable_path=deliverable)


def metrics_pull(args: dict, ctx: BuilderContext) -> BuiltPrompt | None:
    date = today_date(ctx.settings)
    deliverable = f"inbox/reports/metrics-pull/{date}-{id8(ctx.job_id)}.md"
    prompt = (
        f"{AUTONOMOUS_PREFIX}\n\nTask: append today's metrics to system/metrics/metrics.csv and "
        f"write a report at exactly {deliverable}.\n\nOnly pull metrics you can compute directly "
        f"from vault contents — do NOT invent, estimate, or carry-forward a number for anything "
        f"you can't derive from what's actually in the vault right now (no fabricated data)."
        f"\n\nFind today's consolidated acquire report — glob inbox/research/{date}-acquire*.md, "
        f"0 for every lane metric below if none exists yet today. Within it, for each of these "
        f"five `## <lane>` headings (lean-agile merged into leadership 2026-08-12 -- do not look "
        f"for a separate `## lean-agile` heading, it no longer exists), compute two numbers: "
        f'items_today = count of bullet lines (lines starting with "- ") under that heading, and '
        f'candidates_today = the first number in that heading\'s "*N candidates, M kept*" line (0 '
        f"if that line is missing):\n1. leadership_brief — heading `## leadership`\n2. "
        f"payments_brief — heading `## payments`\n3. dev_trends_brief — heading `## dev-trends`"
        f"\n4. chicago_brief — heading `## chicago`\n5. ai_wire — heading `## ai`\nAlso compute: "
        f"6. vault.new_files_24h — count of files anywhere in the vault (excluding the system/ "
        f"directory) with a modification time in the last 24 hours.\n\n(Note: substack and "
        f"claude_code metrics are intentionally NOT included here — this headless path doesn't "
        f"compute them; the vault's cron-scheduled scripts/run_all.sh does, every 5 minutes, and "
        f"covers all nine. lean_agile_brief is also intentionally gone as of this pass — it "
        f"stopped being a real metric once lean-agile merged into leadership 2026-08-12; "
        f"existing historical rows for it are left untouched, just nothing new gets appended.)"
        f"\n\nAppend one CSV row per metric to system/metrics/metrics.csv (eleven rows total: 5 "
        f"lanes × 2 metrics, plus vault.new_files_24h), matching its exact existing header "
        f"`timestamp,source,metric,value,status,error` — timestamp = current UTC time in ISO "
        f"8601 (e.g. 2026-08-08T20:00:00Z), status = \"ok\", error = blank. Do not rewrite "
        f"existing rows.\n\nWrite the report — YAML frontmatter `date: {date}`, `skill: "
        f"metrics-pull`, `tags: [metrics]`, `retention: ephemeral`; body is a small table of "
        f"what was pulled: | Source | Metric | Value | Status |.\n\nEnd your reply with: SAVED "
        f"{deliverable}"
    )
    return BuiltPrompt(prompt=prompt, deliverable_path=deliverable)


def research_into_draft(args: dict, ctx: BuilderContext) -> BuiltPrompt | None:
    date = today_date(ctx.settings)
    deliverable = f"inbox/reports/research-into-draft/{date}-research-into-draft-{id8(ctx.job_id)}.md"
    prompt = (
        f"{AUTONOMOUS_PREFIX}\n\nTask: merge any waiting deep-research reports into their "
        f"matching draft articles, and save a summary of what happened at exactly {deliverable}."
        f"\n\nStep 1 -- find candidates. List every writing/articles/<slug>/ directory. For each, "
        f"read <slug>/<slug>.md's frontmatter. Skip any article whose frontmatter already has a "
        f"research_report: field -- it's already been merged, this run has nothing new to do for "
        f"it. For every remaining article, check whether inbox/deep-research/<slug>-deep-"
        f"research.md exists (exact filename match on the article's own slug -- this is the only "
        f"join key, there is no other id to correlate on). If it doesn't exist, skip this "
        f"article too -- no waiting report.\n\nStep 2 -- for each article with a waiting report, "
        f"read the report and merge it in. Content-flow design Q48-49: articles have a general "
        f'"## Notes" section (not specific to research) for context gathered before/during '
        f'writing. If the article doesn\'t already have a "## Notes" heading, add one at the end '
        f'of the body. Under "## Notes" (after anything already there -- never touch, reorder, '
        f'or delete existing Notes content, or anything else in the article), append a new "### '
        f'Research (auto-merged {date})" sub-section containing 2-4 bullet points of the '
        f"report's Key Takeaways most relevant to this specific article's subject (skip "
        f"takeaways that are off-topic for this piece even if the report covered other ground), "
        f"plus a \"Sources\" sub-list of the 2-4 strongest supporting links from the report "
        f"(prefer Web/Articles entries; skip YouTube/X links unless nothing else supports a "
        f'given point). IMPORTANT: only ever match the exact heading text "## Notes" as the '
        f'section marker -- articles routinely use "##" for their own internal subheadings as '
        f"ordinary prose structure, and those must never be touched, merged into, or mistaken "
        f"for the Notes section. Do NOT change the article's status field -- it stays whatever "
        f'it already was.\n\nStep 3 -- stamp research_report: "[[<slug>-deep-research.md]]" into '
        f"the article's frontmatter (added, not replacing any existing field) so this article is "
        f"skipped on future runs.\n\nStep 4 -- write the summary report at exactly {deliverable}. "
        f"YAML frontmatter: `date: {date}`, `skill: research-into-draft`, `tags: [writing, "
        f"research]`, `retention: ephemeral`. Body: how many draft articles were scanned, how "
        f"many had a waiting report, and for each one merged -- its slug, article title, and how "
        f"many Research bullets/sources were added under Notes. If none had a waiting report, "
        f"say so plainly rather than fabricating activity.\n\nIf writing/articles/ doesn't exist "
        f"or is empty, say so and stop.\n\nEnd your reply with: SAVED {deliverable}"
    )
    return BuiltPrompt(prompt=prompt, deliverable_path=deliverable)


def wiki_ingest(args: dict, ctx: BuilderContext) -> BuiltPrompt | None:
    source_path = (args.get("source_path") or "").strip()
    if not source_path:
        return None
    date = today_date(ctx.settings)
    deliverable = f"inbox/reports/wiki-ingest/{date}-wiki-ingest-{id8(ctx.job_id)}.md"
    # The one wording change from the legacy source in this whole batch:
    # runner.js hardcoded "~/.claude/skills/wiki-ingest/SKILL.md" here -- a
    # real, personal, home-relative path that a public repo's committed
    # strings may not contain (design addendum: "Configuration does the
    # scrubbing"). Lifted to Settings.wiki_ingest_skill_doc_hint, which
    # defaults to a path-free description -- see config.py.
    doc_hint = ctx.settings.wiki_ingest_skill_doc_hint
    prompt = (
        f"{AUTONOMOUS_PREFIX}\n\nTask: distill the deep-research report at {source_path} into a "
        f"new wiki/ page, then remove the source report -- and save a short summary of what "
        f"happened at exactly {deliverable}. Full editorial rules: {doc_hint} -- this inlines "
        f"the operational steps.\n\nStep 1 -- read {source_path} in full. If it doesn't exist, "
        f"stop immediately: write {deliverable} saying plainly that the file wasn't found "
        f"(already ingested, or a bad path) and do nothing else.\n\nStep 2 -- distill, don't "
        f"copy. Write genuinely evergreen prose (the takeaways, the mechanism, why it matters), "
        f"not the report restructured with different headings. Carry forward the report's real "
        f"external citations (actual article/tweet URLs it cited) into a Sources section -- "
        f"never link back to {source_path} itself, since it's about to be deleted and would be a "
        f"dead link immediately. Never invent a citation, stat, or claim the source report "
        f"didn't actually make.\n\nStep 3 -- determine the slug. Check topics/ for a topic note "
        f"whose evidence: list references this exact filename (same filename-join discipline "
        f"research-into-draft uses). If found, derive the slug from that topic's own slug. If "
        f"not traceable, derive a slug from the report's own subject -- lowercase, hyphens, no "
        f"date prefix. If wiki/<slug>.md already exists, auto-suffix (-2, -3, ...) rather than "
        f"overwriting -- never silently clobber an existing page.\n\nStep 4 -- write "
        f"wiki/<slug>.md. Frontmatter exactly `retention: durable` and `first_seen: {date}` -- "
        f"match the existing hand-written pages in wiki/ (see wiki/matt-pocock-skills.md), "
        f"nothing extra, no origin/topic/lane fields. Body: an H1 title, the distilled prose "
        f'from Step 2, a "## Sources" section with the carried-forward citations, and if Step 3 '
        f'found a topic, one line in prose (not frontmatter) reading "Source topic: '
        f'[[<topic-slug>]]".\n\nStep 5 -- verify before touching anything else. Read the new '
        f"wiki page back. Confirm it's non-empty, has valid frontmatter, and actually contains "
        f"real distilled content. Do not proceed to Steps 6-7 until this passes -- if "
        f"verification fails, write {deliverable} explaining the failure and leave {source_path} "
        f"untouched.\n\nStep 6 -- if Step 3 traced this to a topic, update that topic's "
        f"evidence: list: replace the now-dead inbox/deep-research/<file> entry with a "
        f"[[<wiki-slug>]] link to the new page. Leave everything else on the topic note "
        f"untouched. Skip this step entirely if no topic was traceable.\n\nStep 7 -- only after "
        f"Step 5 passed, delete {source_path}. This is the one irreversible action in this task, "
        f"which is why it's strictly last.\n\nStep 8 -- write the summary at exactly "
        f"{deliverable}. YAML frontmatter: `date: {date}`, `skill: wiki-ingest`, `tags: [wiki]`, "
        f"`retention: ephemeral`. Body: which report was ingested, the new wiki page's path, "
        f"whether a topic's evidence list was updated, and confirmation the source report was "
        f"removed -- or, if Step 1/5 stopped early, exactly what and why.\n\nEnd your reply "
        f"with: SAVED {deliverable}"
    )
    return BuiltPrompt(prompt=prompt, deliverable_path=deliverable)


def visual_asset_proposal(args: dict, ctx: BuilderContext) -> BuiltPrompt | None:
    article_path = (args.get("article_path") or "").strip()
    if not article_path:
        return None
    deliverable = f"{dirname(article_path)}/visual-assets-proposal.md"
    prompt = (
        f"{AUTONOMOUS_PREFIX}\n\nTask: read the article at {article_path} and propose the "
        f"visuals it should have, saving the brief at exactly {deliverable}.\n\nRead the whole "
        f"article first. Look for: the core tension/hook (usually the strongest hero-image "
        f"candidate), places where prose is doing a diagram's job (a process, a before/after, a "
        f"system with multiple parts, a comparison), and any actual numbers or comparisons in "
        f"the text (chart candidates -- never invent data the article doesn't contain)."
        f"\n\nEvery brief gets exactly one hero/cover image concept. Add 1-3 supporting visuals "
        f"only where the article actually earned them -- a short single-argument piece may need "
        f"only the hero; don't pad to hit a round number. For each visual (hero included) write "
        f"three parts: Placement (anchored to the article's actual structure, specific enough "
        f"that whoever lays out the post wouldn't have to guess), Concept (subject/composition, "
        f"mood/style -- prefer diagrams/schematics/abstract illustration over literal stock-"
        f"photo tropes for this software-engineering/fintech audience -- and, for a chart, the "
        f"exact chart type/axes/data it plots using only numbers present in the article; if that "
        f"data is credited in the article to a specific source -- a named study, report, "
        f"organization, or person -- carry that attribution into the concept as a caption line "
        f'or axis footnote, e.g. "Source: DORA 2024 Report", so credit doesn\'t get lost between '
        f"the article's prose and the finished visual), and Rationale (1-2 sentences on what it "
        f"clarifies for a skimming reader).\n\nWrite the brief at exactly {deliverable} in this "
        f'exact structure:\n"# Visual Brief: [article title]" then a 1-2 sentence read on the '
        f'article\'s hook, then "## 1. Hero / Cover Image" with Placement/Concept/Rationale, '
        f'then one "## N. [short descriptive name]" section per supporting visual in the same '
        f"three-part format. Close with one line only if something's worth flagging (e.g. no "
        f"hard data so no chart is proposed, or several visuals share a visual motif) -- omit it "
        f"otherwise.\n\nEnd your reply with: SAVED {deliverable}"
    )
    return BuiltPrompt(prompt=prompt, deliverable_path=deliverable)


def draft_persona_fanout(args: dict, ctx: BuilderContext) -> BuiltPrompt | None:
    article_path = (args.get("article_path") or "").strip()
    personas_arg = (args.get("personas") or "").strip()
    if not article_path or not personas_arg:
        return None
    persona_slugs = [s.strip() for s in personas_arg.split(",") if s.strip()]
    if not persona_slugs:
        return None

    # Round number picked here, synchronously, rather than left to the model
    # -- scan the article's reviews/ dir for existing round-N folders and use
    # the next integer (matches the legacy daemon's deliverablePathFor()
    # case exactly, including scanning ALL entries -- files or directories
    # -- under reviews/, not just subdirectories, since that's what
    # readdirSync().filter() there does too).
    reviews_dir = f"{dirname(article_path)}/reviews"
    abs_reviews_dir = ctx.vault_root / reviews_dir
    next_round = 1
    if abs_reviews_dir.is_dir():
        rounds = [
            int(entry.name[len("round-"):])
            for entry in abs_reviews_dir.iterdir()
            if entry.name.startswith("round-") and entry.name[len("round-"):].isdigit()
        ]
        if rounds:
            next_round = max(rounds) + 1
    deliverable = f"{reviews_dir}/round-{next_round}/_summary.md"

    round_dir = dirname(deliverable)  # .../reviews/round-N
    round_label = basename(round_dir)  # "round-N"
    personas_joined = ", ".join(persona_slugs)
    personas_frontmatter_list = ", ".join(f'"{slug}"' for slug in persona_slugs)
    prompt = (
        f"{AUTONOMOUS_PREFIX}\n\nTask: review the article at {article_path} against these reader "
        f"personas and write the results into {round_dir}/.\n\nPersonas for this run: "
        f"{personas_joined}. Read each one's file at writing/personas/<slug>.md (frontmatter: "
        f"name, role, active, plus a body covering what they're looking for, what they care "
        f"about, what makes them bounce). If any listed persona file doesn't exist, skip it and "
        f"note the omission in the summary -- don't invent a persona to fill the gap."
        f"\n\nRead the article once per persona, actually inhabiting that reader's point of view "
        f"rather than writing generic feedback with a name stapled on. For each persona, write "
        f'{round_dir}/<persona-slug>.md with: a direct "Does this land?" verdict (1-2 sentences, '
        f'not hedged into meaninglessness), "What resonates" (specific parts that work for this '
        f'reader and why), "What\'s missing or unclear from their POV", and 1-3 concrete '
        f"suggested adjustments (specific and actionable, capped at 3).\n\nAfter all persona "
        f"files are written, run `git rev-parse HEAD` in the vault directory to get the current "
        f"commit sha, then write {deliverable} (the round summary) with YAML frontmatter: "
        f'`round: <the integer in "{round_label}">`, `personas: [{personas_frontmatter_list}]`, '
        f"`reviewed_sha: <the sha you just got>`, `reviewed_at: <current UTC ISO timestamp>`, "
        f"`status: complete`, `retention: durable`. Body: an \"Overall synthesis\" section -- "
        f"note where personas agree (shared strengths/gaps are highest-confidence, worth fixing "
        f"regardless of audience), where they genuinely conflict (name the tradeoff plainly, "
        f"don't paper over it with a mushy middle), and a recommendation on what to prioritize."
        f"\n\nEnd your reply with: SAVED {deliverable}"
    )
    return BuiltPrompt(prompt=prompt, deliverable_path=deliverable)


# skill id -> builder, merged into vaultos.runner.prompts.PROMPT_BUILDER_REGISTRY
# (see prompts/__init__.py) -- mirrors engines/__init__.py's ENGINE_REGISTRY
# assembly pattern.
BATCH1_BUILDERS: dict[str, PromptBuilder] = {
    "plan-today": plan_today,
    "plan-tomorrow": plan_tomorrow,
    "vault-cleanup": vault_cleanup,
    "inbox-brief": inbox_brief,
    "metrics-pull": metrics_pull,
    "research-into-draft": research_into_draft,
    "wiki-ingest": wiki_ingest,
    "visual-asset-proposal": visual_asset_proposal,
    "draft-persona-fanout": draft_persona_fanout,
}
