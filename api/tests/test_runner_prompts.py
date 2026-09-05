"""Unit tests for the prompt-builder registry (ticket #25) and its batch-1
builders. Per the runner spec's testing decision ("Tests are markers, not
goldens"): every per-skill test asserts load-bearing phrases and
interpolated job args appear in the built prompt, plus the deliverable
path's shape -- never a full-string golden, since prompts are expected to
evolve. Registry-level coverage (lookup, no-builder passthrough, unknown
skill) lives at the top; engine-level wiring (a builder's prompt/deliverable
actually reaching claude-cli's subprocess call) is covered separately by
test_runner_prompts_batch1_end_to_end.py."""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from vaultos.config import Settings
from vaultos.runner.prompts import (
    AUTONOMOUS_PREFIX,
    PROMPT_BUILDER_REGISTRY,
    BuilderContext,
    get_builder,
    id8,
    now_time,
    slugify,
    today_date,
    tomorrow_date,
)

BATCH1_SKILL_IDS = [
    "plan-today",
    "plan-tomorrow",
    "vault-cleanup",
    "inbox-brief",
    "metrics-pull",
    "research-into-draft",
    "wiki-ingest",
    "visual-asset-proposal",
    "draft-persona-fanout",
]

# Batch 2 (ticket #26), now fully registered -- its own per-skill marker
# tests live in test_runner_prompts_batch2.py; this list exists here only
# for the registry-boundary checks below (full-enumeration + excluded-set).
BATCH2_SKILL_IDS = [
    "acquire",
    "daily-topic-digest",
    "deep-research",
    "article-refiner",
    "research-persona-fanout",
]


@pytest.fixture
def settings(monkeypatch, tmp_path):
    monkeypatch.setenv("VAULT_ROOT", str(tmp_path))
    monkeypatch.delenv("HUD_TZ", raising=False)
    monkeypatch.delenv("WIKI_INGEST_SKILL_DOC_HINT", raising=False)
    return Settings()


@pytest.fixture
def ctx(settings, tmp_path):
    return BuilderContext(vault_root=tmp_path, settings=settings, job_id="0123456789abcdef")


# -- registry-level: lookup, no-builder passthrough, unknown skill ---------


def test_get_builder_returns_a_callable_for_every_batch1_skill():
    for skill_id in BATCH1_SKILL_IDS:
        assert callable(get_builder(skill_id)), skill_id


def test_get_builder_returns_none_for_a_skill_with_no_builder():
    # "ask-claude"-style freeform skills (prompt from the job's own `prompt`
    # arg, or an engine_config template) never get a batch-1 case -- the
    # registry must say so plainly rather than raising, so callers can fall
    # back to their current behavior (the no-builder passthrough).
    assert get_builder("ask-claude") is None
    assert get_builder("some-future-skill-not-yet-ported") is None


def test_registry_keys_match_the_batch1_plus_batch2_enumeration():
    # Batch 2 (ticket #26) is now fully registered alongside batch 1 --
    # updated from the batch-1-only exact-match this test started as, now
    # that both batches ship in this repo.
    assert set(PROMPT_BUILDER_REGISTRY.keys()) == set(BATCH1_SKILL_IDS) | set(BATCH2_SKILL_IDS)


def test_get_builder_returns_a_callable_for_every_batch2_skill():
    for skill_id in BATCH2_SKILL_IDS:
        assert callable(get_builder(skill_id)), skill_id


def test_excluded_skills_have_no_builder():
    # Explicit negative coverage for the enumeration boundary: the
    # never-ported/no-prompt skills must NOT show up here just because
    # someone adds a case to the wrong batch file. Batch 2's own five skills
    # moved OUT of this negative list once they were registered above --
    # see test_registry_keys_match_the_batch1_plus_batch2_enumeration and
    # test_get_builder_returns_a_callable_for_every_batch2_skill instead.
    for skill_id in ["daily-digest", "voice-ask", "rss-feed-poll"]:
        assert get_builder(skill_id) is None, skill_id


# -- date/time helpers -------------------------------------------------


def test_today_date_is_hud_tz_local_yyyy_mm_dd(settings):
    result = today_date(settings)
    assert len(result) == 10 and result.count("-") == 2


def test_tomorrow_date_is_one_day_after_today(settings):
    from datetime import date, timedelta

    today = date.fromisoformat(today_date(settings))
    assert tomorrow_date(settings) == (today + timedelta(days=1)).isoformat()


def test_now_time_is_hh_mm_24h(settings):
    result = now_time(settings)
    assert len(result) == 5 and result[2] == ":"


def test_id8_truncates_to_eight_chars():
    assert id8("0123456789abcdef") == "01234567"
    assert id8("") == "x"
    assert id8(None) == "x"


# -- slugify (fix round 1, ticket #26 -- added alongside deep-research, ----
# -- gets its own direct coverage here since it lives in base.py) ---------


def test_slugify_empty_falls_back_to_untitled():
    assert slugify("") == "untitled"
    assert slugify(None) == "untitled"
    assert slugify("   ") == "untitled"


def test_slugify_punctuation_only_falls_back_to_untitled():
    # Nothing left after stripping [^a-z0-9\s-] -- same "untitled" fallback
    # as an empty input.
    assert slugify("!!!???...") == "untitled"


def test_slugify_lowercases_and_collapses_whitespace_to_hyphens():
    assert slugify("AI Agent   Orchestration") == "ai-agent-orchestration"


def test_slugify_truncates_to_max_len():
    long_title = "a" * 60
    result = slugify(long_title)
    assert len(result) == 48
    assert result == "a" * 48

    result_custom = slugify(long_title, max_len=10)
    assert result_custom == "a" * 10


# -- timezone correctness (fix round 1, 2026-09-05) -----------------------
# The tests above only check *shape* (10 chars, two dashes) -- a helper that
# silently dropped ZoneInfo(settings.hud_tz) and used naive UTC instead
# would still pass every one of them. These fix a real UTC instant and
# assert the actual HUD_TZ-local calendar day/time it resolves to, via the
# injectable `now` param `_resolve_instant` added for exactly this.


def _settings_with_tz(monkeypatch, tmp_path, hud_tz):
    monkeypatch.setenv("VAULT_ROOT", str(tmp_path))
    monkeypatch.setenv("HUD_TZ", hud_tz)
    monkeypatch.delenv("WIKI_INGEST_SKILL_DOC_HINT", raising=False)
    return Settings()


def test_today_date_is_genuinely_hud_tz_local_not_naive_utc(monkeypatch, tmp_path):
    # 2026-03-08T05:30:00Z is 2026-03-07 23:30 in America/Chicago (still
    # CST, UTC-6, a few hours before that day's own spring-forward
    # transition) but already 2026-03-08 in UTC -- a genuine cross-midnight
    # split a naive-UTC implementation would collapse to one date for both.
    instant = datetime(2026, 3, 8, 5, 30, tzinfo=ZoneInfo("UTC"))

    chicago_settings = _settings_with_tz(monkeypatch, tmp_path, "America/Chicago")
    assert today_date(chicago_settings, now=instant) == "2026-03-07"

    utc_settings = _settings_with_tz(monkeypatch, tmp_path, "UTC")
    assert today_date(utc_settings, now=instant) == "2026-03-08"

    # The whole point: the two hud_tz values must disagree on "today" for
    # this instant. A ZoneInfo-dropping implementation would make both
    # resolve to the UTC calendar date and this would fail.
    assert today_date(chicago_settings, now=instant) != today_date(utc_settings, now=instant)


def test_now_time_is_genuinely_hud_tz_local(monkeypatch, tmp_path):
    instant = datetime(2026, 3, 8, 5, 30, tzinfo=ZoneInfo("UTC"))

    chicago_settings = _settings_with_tz(monkeypatch, tmp_path, "America/Chicago")
    assert now_time(chicago_settings, now=instant) == "23:30"

    utc_settings = _settings_with_tz(monkeypatch, tmp_path, "UTC")
    assert now_time(utc_settings, now=instant) == "05:30"


def test_tomorrow_date_across_a_dst_spring_forward_transition(monkeypatch, tmp_path):
    # America/Chicago springs forward (2am -> 3am local) on 2026-03-08.
    # Fixing "now" the evening before, local time, means today_date() is
    # 2026-03-07 and tomorrow_date() must land on 2026-03-08 -- the actual
    # transition day -- via plain Y/M/D + 1 arithmetic, never by adding 24
    # wall-clock hours to the instant (which a DST transition can skew by an
    # hour and, in principle, onto the wrong calendar day for an instant
    # close enough to a boundary).
    instant = datetime(2026, 3, 8, 5, 30, tzinfo=ZoneInfo("UTC"))
    chicago_settings = _settings_with_tz(monkeypatch, tmp_path, "America/Chicago")

    assert today_date(chicago_settings, now=instant) == "2026-03-07"
    assert tomorrow_date(chicago_settings, now=instant) == "2026-03-08"


def test_injectable_now_defaults_to_real_time_when_omitted(settings):
    # Every production call site (`today_date(ctx.settings)`, no `now`) must
    # keep working unchanged -- the param is additive and keyword-only.
    result = today_date(settings)
    assert len(result) == 10 and result.count("-") == 2


# -- per-skill markers: load-bearing phrases + interpolated args + -------
# -- deliverable path shape (never full-string goldens) ------------------


def test_plan_today(ctx):
    built = PROMPT_BUILDER_REGISTRY["plan-today"]({}, ctx)
    assert built is not None
    assert built.deliverable_path == f"daily-notes/{today_date(ctx.settings)}.md"
    assert AUTONOMOUS_PREFIX in built.prompt
    assert "set up today's daily note" in built.prompt
    assert "system/templates/daily.md" in built.prompt
    assert "system/schemas/daily-note.md" in built.prompt
    assert f"timeZone={ctx.settings.hud_tz}" in built.prompt
    assert f"SAVED {built.deliverable_path}" in built.prompt


def test_plan_tomorrow(ctx):
    built = PROMPT_BUILDER_REGISTRY["plan-tomorrow"]({}, ctx)
    assert built is not None
    assert built.deliverable_path == f"daily-notes/{tomorrow_date(ctx.settings)}.md"
    assert AUTONOMOUS_PREFIX in built.prompt
    assert "draft tomorrow's daily note" in built.prompt
    assert f"timeZone={ctx.settings.hud_tz}" in built.prompt
    assert f"SAVED {built.deliverable_path}" in built.prompt


def test_vault_cleanup(ctx):
    built = PROMPT_BUILDER_REGISTRY["vault-cleanup"]({}, ctx)
    assert built is not None
    date = today_date(ctx.settings)
    assert built.deliverable_path == f"inbox/reports/vault-cleanup/{date}-cleanup-{id8(ctx.job_id)}.md"
    assert AUTONOMOUS_PREFIX in built.prompt
    assert "retention: ephemeral" in built.prompt
    assert "retention: durable" in built.prompt
    assert "30 days old" in built.prompt
    assert "skill: vault-cleanup" in built.prompt
    assert f"date: {date}" in built.prompt
    assert f"SAVED {built.deliverable_path}" in built.prompt


def test_inbox_brief(ctx):
    built = PROMPT_BUILDER_REGISTRY["inbox-brief"]({}, ctx)
    assert built is not None
    date = today_date(ctx.settings)
    assert built.deliverable_path == f"inbox/reports/inbox-briefs/{date}-inbox-brief-{id8(ctx.job_id)}.md"
    assert AUTONOMOUS_PREFIX in built.prompt
    assert "inbox/notes/" in built.prompt
    assert "Gmail MCP connector" in built.prompt
    # The one literal-brace JSON example in this batch -- confirms the
    # f-string's `{{`/`}}` escaping actually rendered single braces, not the
    # escape sequence itself leaking through.
    assert 'action_items: [{"id": "<real gmail thread id>"' in built.prompt
    assert "{{" not in built.prompt and "}}" not in built.prompt
    assert f"daily-notes/{date}.md" in built.prompt
    assert f"SAVED {built.deliverable_path}" in built.prompt


def test_metrics_pull(ctx):
    built = PROMPT_BUILDER_REGISTRY["metrics-pull"]({}, ctx)
    assert built is not None
    date = today_date(ctx.settings)
    assert built.deliverable_path == f"inbox/reports/metrics-pull/{date}-{id8(ctx.job_id)}.md"
    assert AUTONOMOUS_PREFIX in built.prompt
    assert f"inbox/research/{date}-acquire*.md" in built.prompt
    # All five lane headings, not just the first two -- the legacy prompt
    # names each of the five `## <lane>` headings by hand (payments and
    # dev-trends/chicago/ai are easy to drop silently in a future edit since
    # they're mid-paragraph, not their own bullet).
    for lane_heading in ("## leadership", "## payments", "## dev-trends", "## chicago", "## ai"):
        assert lane_heading in built.prompt, lane_heading
    assert "lean-agile merged into leadership" in built.prompt
    assert "eleven rows total" in built.prompt
    assert f"SAVED {built.deliverable_path}" in built.prompt


def test_research_into_draft(ctx):
    built = PROMPT_BUILDER_REGISTRY["research-into-draft"]({}, ctx)
    assert built is not None
    date = today_date(ctx.settings)
    assert built.deliverable_path == (
        f"inbox/reports/research-into-draft/{date}-research-into-draft-{id8(ctx.job_id)}.md"
    )
    assert AUTONOMOUS_PREFIX in built.prompt
    assert "writing/articles/<slug>/" in built.prompt
    assert "research_report:" in built.prompt
    assert f"Research (auto-merged {date})" in built.prompt
    assert f"SAVED {built.deliverable_path}" in built.prompt


def test_wiki_ingest_requires_source_path(ctx):
    assert PROMPT_BUILDER_REGISTRY["wiki-ingest"]({}, ctx) is None
    assert PROMPT_BUILDER_REGISTRY["wiki-ingest"]({"source_path": "   "}, ctx) is None


def test_wiki_ingest(ctx):
    source_path = "inbox/deep-research/2026-09-05-some-topic-deep-research.md"
    built = PROMPT_BUILDER_REGISTRY["wiki-ingest"]({"source_path": source_path}, ctx)
    assert built is not None
    date = today_date(ctx.settings)
    assert built.deliverable_path == f"inbox/reports/wiki-ingest/{date}-wiki-ingest-{id8(ctx.job_id)}.md"
    assert AUTONOMOUS_PREFIX in built.prompt
    assert source_path in built.prompt
    assert "distill, don't copy" in built.prompt
    assert "never silently clobber an existing page" in built.prompt
    # Scrub check: the legacy prompt hardcoded
    # "~/.claude/skills/wiki-ingest/SKILL.md" -- confirm the port carries no
    # home-relative or absolute personal path, only the configured hint.
    assert "~/" not in built.prompt
    assert "/home/" not in built.prompt
    assert ctx.settings.wiki_ingest_skill_doc_hint in built.prompt
    assert f"SAVED {built.deliverable_path}" in built.prompt


def test_visual_asset_proposal_requires_article_path(ctx):
    assert PROMPT_BUILDER_REGISTRY["visual-asset-proposal"]({}, ctx) is None
    assert PROMPT_BUILDER_REGISTRY["visual-asset-proposal"]({"article_path": " "}, ctx) is None


def test_visual_asset_proposal(ctx):
    article_path = "writing/articles/my-piece/my-piece.md"
    built = PROMPT_BUILDER_REGISTRY["visual-asset-proposal"]({"article_path": article_path}, ctx)
    assert built is not None
    assert built.deliverable_path == "writing/articles/my-piece/visual-assets-proposal.md"
    assert AUTONOMOUS_PREFIX in built.prompt
    assert article_path in built.prompt
    assert "exactly one hero/cover image concept" in built.prompt
    assert "never invent data the article doesn't contain" in built.prompt
    assert f"SAVED {built.deliverable_path}" in built.prompt


def test_draft_persona_fanout_requires_article_path_and_personas(ctx):
    assert PROMPT_BUILDER_REGISTRY["draft-persona-fanout"]({}, ctx) is None
    assert PROMPT_BUILDER_REGISTRY["draft-persona-fanout"](
        {"article_path": "writing/articles/x/x.md"}, ctx
    ) is None
    assert PROMPT_BUILDER_REGISTRY["draft-persona-fanout"](
        {"personas": "gem, cto"}, ctx
    ) is None
    assert PROMPT_BUILDER_REGISTRY["draft-persona-fanout"](
        {"article_path": "writing/articles/x/x.md", "personas": "  ,  "}, ctx
    ) is None


def test_draft_persona_fanout_first_round(ctx):
    article_path = "writing/articles/my-piece/my-piece.md"
    built = PROMPT_BUILDER_REGISTRY["draft-persona-fanout"](
        {"article_path": article_path, "personas": "gem, cto"}, ctx
    )
    assert built is not None
    assert built.deliverable_path == "writing/articles/my-piece/reviews/round-1/_summary.md"
    assert AUTONOMOUS_PREFIX in built.prompt
    assert article_path in built.prompt
    assert "Personas for this run: gem, cto" in built.prompt
    assert 'personas: ["gem", "cto"]' in built.prompt
    assert 'round: <the integer in "round-1">' in built.prompt
    assert "git rev-parse HEAD" in built.prompt
    assert f"SAVED {built.deliverable_path}" in built.prompt


def test_draft_persona_fanout_picks_next_round_number(ctx, tmp_path):
    article_path = "writing/articles/my-piece/my-piece.md"
    reviews_dir = tmp_path / "writing/articles/my-piece/reviews"
    (reviews_dir / "round-1").mkdir(parents=True)
    (reviews_dir / "round-3").mkdir(parents=True)
    (reviews_dir / "not-a-round").mkdir(parents=True)  # must not confuse the max()

    built = PROMPT_BUILDER_REGISTRY["draft-persona-fanout"](
        {"article_path": article_path, "personas": "gem"}, ctx
    )

    assert built.deliverable_path == "writing/articles/my-piece/reviews/round-4/_summary.md"


def test_draft_persona_fanout_round_scan_counts_files_not_just_dirs(ctx, tmp_path):
    # The legacy daemon's own scan is `readdirSync(absReviewsDir).filter(...)`
    # -- it never checks isDirectory(), so a stray round-N *file* (e.g. a
    # leftover from a manual edit, or a future format change) counts toward
    # the max the same as a round-N directory would. The port mirrors that
    # exactly (see draft_persona_fanout()'s comment in batch1.py) -- this is
    # the entry-type-agnostic case the earlier round-scan test didn't cover.
    article_path = "writing/articles/my-piece/my-piece.md"
    reviews_dir = tmp_path / "writing/articles/my-piece/reviews"
    reviews_dir.mkdir(parents=True)
    (reviews_dir / "round-1").mkdir()
    (reviews_dir / "round-5").write_text("not a directory")  # a FILE, not a dir

    built = PROMPT_BUILDER_REGISTRY["draft-persona-fanout"](
        {"article_path": article_path, "personas": "gem"}, ctx
    )

    assert built.deliverable_path == "writing/articles/my-piece/reviews/round-6/_summary.md"
