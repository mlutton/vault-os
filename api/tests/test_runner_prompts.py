"""Unit tests for the prompt-builder registry (ticket #25) and its batch-1
builders. Per the runner spec's testing decision ("Tests are markers, not
goldens"): every per-skill test asserts load-bearing phrases and
interpolated job args appear in the built prompt, plus the deliverable
path's shape -- never a full-string golden, since prompts are expected to
evolve. Registry-level coverage (lookup, no-builder passthrough, unknown
skill) lives at the top; engine-level wiring (a builder's prompt/deliverable
actually reaching claude-cli's subprocess call) is covered separately by
test_runner_prompts_batch1_end_to_end.py."""

from pathlib import Path

import pytest

from vaultos.config import Settings
from vaultos.runner.prompts import (
    AUTONOMOUS_PREFIX,
    PROMPT_BUILDER_REGISTRY,
    BuilderContext,
    get_builder,
    id8,
    now_time,
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


def test_registry_keys_match_the_batch1_enumeration():
    assert set(PROMPT_BUILDER_REGISTRY.keys()) == set(BATCH1_SKILL_IDS)


def test_batch2_and_excluded_skills_have_no_builder():
    # Explicit negative coverage for the enumeration boundary: batch 2 (a
    # separate ticket, #26) and the never-ported/no-prompt skills must NOT
    # show up here just because someone adds a case to the wrong batch file.
    for skill_id in [
        "acquire", "daily-topic-digest", "article-refiner",
        "research-persona-fanout", "deep-research", "daily-digest",
        "voice-ask", "rss-feed-poll",
    ]:
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
    assert "## leadership" in built.prompt and "## payments" in built.prompt
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
