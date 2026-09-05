"""Unit tests for the prompt-builder registry's batch-2 builders (ticket
#26, the heavy research/writing pipeline set). Same "markers, not goldens"
discipline as `test_runner_prompts.py`'s batch-1 tests: per-skill assertions
on load-bearing phrases and interpolated job args/settings, plus the
deliverable path's exact shape -- never a full-string golden.

Built up one skill per commit, matching the ticket's per-skill commit plan;
this file now covers the full five-skill batch-2 set: `acquire`,
`daily-topic-digest`, `deep-research`, `article-refiner`, and
`research-persona-fanout`. The shared registry-boundary tests (batch-2 fully
registered, excluded set unchanged) live in `test_runner_prompts.py`.
"""

import json

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
)


@pytest.fixture
def settings(monkeypatch, tmp_path):
    monkeypatch.setenv("VAULT_ROOT", str(tmp_path))
    monkeypatch.delenv("HUD_TZ", raising=False)
    monkeypatch.delenv("PYTHON_BIN", raising=False)
    monkeypatch.delenv("RSS_POLL_SCRIPT", raising=False)
    monkeypatch.delenv("WEBSEARCH_CACHED_FETCH_WORKFLOW", raising=False)
    monkeypatch.delenv("ASSEMBLE_ACQUIRE_REPORT_CLI", raising=False)
    monkeypatch.delenv("YT_SEARCH_SCRIPT", raising=False)
    monkeypatch.delenv("ARTICLE_REFINER_SKILL_DOC_HINT", raising=False)
    monkeypatch.delenv("CACHE_CLI", raising=False)
    monkeypatch.delenv("ASSEMBLE_REVIEW_SCRIPT", raising=False)
    monkeypatch.delenv("RESEARCH_PERSONA_FANOUT_SKILL_DOC_HINT", raising=False)
    return Settings()


@pytest.fixture
def ctx(settings, tmp_path):
    return BuilderContext(vault_root=tmp_path, settings=settings, job_id="0123456789abcdef")


def test_get_builder_returns_a_callable_for_acquire():
    assert callable(get_builder("acquire"))


def test_acquire(ctx):
    built = PROMPT_BUILDER_REGISTRY["acquire"]({}, ctx)
    assert built is not None
    date = today_date(ctx.settings)
    assert built.deliverable_path == f"inbox/research/{date}-acquire.md"
    assert AUTONOMOUS_PREFIX in built.prompt
    assert "Step 1 -- fetch AND synthesize" in built.prompt
    assert "Step 2 -- assemble the report" in built.prompt
    assert "Step 3 -- read the report you just wrote" in built.prompt
    # Configured (path-free-by-default) settings values reach the prompt --
    # the scrub check itself (no personal absolute paths) lives in the
    # end-to-end test / the repo-root grep gate, not here.
    assert ctx.settings.python_bin in built.prompt
    assert ctx.settings.rss_poll_script in built.prompt
    assert ctx.settings.websearch_cached_fetch_workflow in built.prompt
    assert ctx.settings.assemble_acquire_report_cli in built.prompt
    assert str(ctx.vault_root) in built.prompt
    # The literal-brace JSON/object examples in this prompt (Workflow args,
    # and the "{lane, candidates, kept}" per-lane summary shape) -- confirms
    # the f-string's `{{`/`}}` escaping rendered single braces, not the
    # escape sequence itself leaking through.
    assert '{"todayDate": "' in built.prompt
    assert "{lane, candidates, kept}" in built.prompt
    assert "{{" not in built.prompt and "}}" not in built.prompt
    # Fix round 1 (operator decision): the legacy parenthetical named a
    # private-repo internal ("Vault-Os-Api's jobs.py CHAIN_MAP") -- replaced
    # with a generic equivalent, keeping the "don't chain into it yourself"
    # instruction. Assert the neutral wording landed and the private name
    # didn't survive.
    assert (
        "queued automatically once you finish -- see the platform's own job chaining"
        in built.prompt
    )
    assert "don't chain into it yourself here" in built.prompt
    assert "Vault-Os-Api" not in built.prompt
    assert "CHAIN_MAP" not in built.prompt
    assert f"SAVED {built.deliverable_path}" in built.prompt


def test_acquire_ignores_args_no_required_arg_gate(ctx):
    # Legacy source has no arg check for this skill -- any args dict (even
    # garbage) still produces a built prompt.
    built = PROMPT_BUILDER_REGISTRY["acquire"]({"unexpected": "value"}, ctx)
    assert built is not None


def test_env_var_override_of_a_batch2_setting_reaches_the_built_prompt(monkeypatch, tmp_path):
    # Fix round 1: nothing proved the env-var -> Settings -> prompt path
    # actually works end to end for a batch-2 field, only that the
    # PATH-FREE *default* shows up. Set a real-looking override and confirm
    # it's what lands in the built prompt, not the default.
    monkeypatch.setenv("VAULT_ROOT", str(tmp_path))
    monkeypatch.setenv("RSS_POLL_SCRIPT", "/opt/deploy/rss-feed-poll/poll.py")
    settings = Settings()
    ctx = BuilderContext(vault_root=tmp_path, settings=settings, job_id="0123456789abcdef")

    built = PROMPT_BUILDER_REGISTRY["acquire"]({}, ctx)

    assert built is not None
    assert "/opt/deploy/rss-feed-poll/poll.py" in built.prompt
    assert "the RSS poll script" not in built.prompt  # the default, not the override


def test_get_builder_returns_a_callable_for_daily_topic_digest():
    assert callable(get_builder("daily-topic-digest"))


def test_daily_topic_digest(ctx):
    built = PROMPT_BUILDER_REGISTRY["daily-topic-digest"]({}, ctx)
    assert built is not None
    date = today_date(ctx.settings)
    assert (
        built.deliverable_path == f"inbox/reports/daily-topic-digest/{date}-daily-topic-digest.md"
    )
    assert AUTONOMOUS_PREFIX in built.prompt
    assert "Step 1 -- gather everything not yet attached to a topic" in built.prompt
    # Fix round 1: Steps 2/3/5/6 were unasserted -- a validator proved this
    # hollow by deleting them from the built prompt with every existing
    # assertion still green. One load-bearing marker per step now.
    assert "Step 2 -- look for genuine signal, not just volume" in built.prompt
    assert "Step 3 -- dedup against existing topics by meaning, not filename" in built.prompt
    assert "Step 4 -- check persona fit, inline, no fan-out" in built.prompt
    assert "Step 5 -- write topic notes" in built.prompt
    assert "Step 6 -- write the report at exactly" in built.prompt
    assert (
        "Step 7 -- your final reply must present the ranked list conversationally" in built.prompt
    )
    assert f"first_seen: {date}" in built.prompt
    assert f"date: {date}" in built.prompt
    # Unlike every other batch-1/batch-2 skill, this prompt does NOT end
    # with "SAVED <deliverable>" -- Step 7's pick-prompt is the final reply
    # by design (ported verbatim, including that omission).
    assert "SAVED" not in built.prompt


def test_daily_topic_digest_ignores_args_no_required_arg_gate(ctx):
    built = PROMPT_BUILDER_REGISTRY["daily-topic-digest"]({"unexpected": "value"}, ctx)
    assert built is not None


def test_get_builder_returns_a_callable_for_deep_research():
    assert callable(get_builder("deep-research"))


def test_deep_research_requires_topic(ctx):
    assert PROMPT_BUILDER_REGISTRY["deep-research"]({}, ctx) is None
    assert PROMPT_BUILDER_REGISTRY["deep-research"]({"topic": "   "}, ctx) is None
    # A topic that's nothing BUT quotes/backticks/control chars strips down
    # to empty after sanitization -- same reject as a blank topic.
    assert PROMPT_BUILDER_REGISTRY["deep-research"]({"topic": '"`"`'}, ctx) is None


def test_deep_research_no_draft_slug(ctx):
    topic = "AI agent orchestration"
    built = PROMPT_BUILDER_REGISTRY["deep-research"]({"topic": topic}, ctx)
    assert built is not None
    date = today_date(ctx.settings)
    assert built.deliverable_path == f"inbox/deep-research/{date}-{slugify(topic)}-deep-research.md"
    assert AUTONOMOUS_PREFIX in built.prompt
    assert f'"{topic}"' in built.prompt  # safe_topic == topic here (no unsafe chars)
    assert ctx.settings.python_bin in built.prompt
    assert ctx.settings.yt_search_script in built.prompt
    # Fix round 1: the WebSearch fan-out sources (web/X/GitHub) were
    # unasserted -- only the YouTube leg (via yt_search_script) and section
    # headings were covered. One marker per fan-out source now.
    assert "2. Web — WebSearch" in built.prompt
    assert "3. X/Twitter — WebSearch" in built.prompt
    assert "4. GitHub — WebSearch" in built.prompt
    assert "site:x.com" in built.prompt
    assert "site:github.com" in built.prompt
    assert "## Key Takeaways" in built.prompt
    assert "## GitHub Activity" in built.prompt
    assert f"topic: {json.dumps(topic)}" in built.prompt
    assert "draft_slug:" not in built.prompt
    assert f"SAVED {built.deliverable_path}" in built.prompt


def test_deep_research_with_valid_draft_slug(ctx):
    built = PROMPT_BUILDER_REGISTRY["deep-research"](
        {"topic": "payments infra", "draft_slug": "my-great-article"}, ctx
    )
    assert built is not None
    assert built.deliverable_path == "inbox/deep-research/my-great-article-deep-research.md"
    assert 'draft_slug: "my-great-article"' in built.prompt


def test_deep_research_invalid_draft_slug_falls_back_to_topic_slug(ctx):
    # Uppercase / invalid-shape draft_slug is rejected, same as the legacy
    # regex validation -- falls back to the topic-based filename, and the
    # frontmatter omits draft_slug entirely (never claims the unsafe value).
    topic = "payments infra"
    built = PROMPT_BUILDER_REGISTRY["deep-research"](
        {"topic": topic, "draft_slug": "Not Valid!"}, ctx
    )
    assert built is not None
    date = today_date(ctx.settings)
    assert built.deliverable_path == f"inbox/deep-research/{date}-{slugify(topic)}-deep-research.md"
    assert "draft_slug:" not in built.prompt


def test_deep_research_sanitizes_unsafe_topic_chars_in_body(ctx):
    # Quotes/backticks/control chars are stripped from the prompt BODY
    # (search queries, filename-adjacent text), but the frontmatter's own
    # `topic:` field still carries the ORIGINAL topic via json.dumps --
    # matches the legacy split between safeTopic (body) and topic (record).
    topic = 'weird "topic" with `backticks`'
    built = PROMPT_BUILDER_REGISTRY["deep-research"]({"topic": topic}, ctx)
    assert built is not None
    assert '"`' not in built.prompt.split("YAML frontmatter")[0]
    assert f"topic: {json.dumps(topic)}" in built.prompt


def test_deep_research_topic_context_appended_when_present(ctx):
    built_without = PROMPT_BUILDER_REGISTRY["deep-research"]({"topic": "x"}, ctx)
    built_with = PROMPT_BUILDER_REGISTRY["deep-research"](
        {"topic": "x", "topic_context": "some prior evidence"}, ctx
    )
    assert "Context already gathered" not in built_without.prompt
    assert "Context already gathered" in built_with.prompt
    assert "some prior evidence" in built_with.prompt


def test_article_refiner_requires_article_path(ctx):
    assert PROMPT_BUILDER_REGISTRY["article-refiner"]({}, ctx) is None
    assert PROMPT_BUILDER_REGISTRY["article-refiner"]({"article_path": "  "}, ctx) is None


def test_article_refiner(ctx):
    article_path = "writing/articles/my-piece/my-piece.md"
    built = PROMPT_BUILDER_REGISTRY["article-refiner"]({"article_path": article_path}, ctx)
    assert built is not None
    date = today_date(ctx.settings)
    assert (
        built.deliverable_path
        == f"inbox/reports/article-refiner/{date}-article-refiner-{id8(ctx.job_id)}.md"
    )
    assert AUTONOMOUS_PREFIX in built.prompt
    assert article_path in built.prompt
    assert ctx.settings.article_refiner_skill_doc_hint in built.prompt
    assert "~/" not in built.prompt and "/home/" not in built.prompt
    assert '"## Notes", "## Changelog", or "## Flagged for Input"' in built.prompt
    run_stamp = f"{date} {now_time(ctx.settings)}"
    assert f"### Proposed Revision ({run_stamp})" in built.prompt
    assert f"### Hook Options ({run_stamp})" in built.prompt
    assert f"### {run_stamp}" in built.prompt
    assert "skill: article-refiner" in built.prompt
    assert f"SAVED {built.deliverable_path}" in built.prompt


def test_research_persona_fanout_requires_article_path(ctx):
    assert PROMPT_BUILDER_REGISTRY["research-persona-fanout"]({}, ctx) is None
    assert PROMPT_BUILDER_REGISTRY["research-persona-fanout"]({"article_path": " "}, ctx) is None


def test_research_persona_fanout(ctx):
    article_path = "writing/articles/my-piece/my-piece.md"
    built = PROMPT_BUILDER_REGISTRY["research-persona-fanout"]({"article_path": article_path}, ctx)
    assert built is not None
    date = today_date(ctx.settings)
    assert built.deliverable_path == (
        f"inbox/reports/research-persona-fanout/{date}-research-persona-fanout-{id8(ctx.job_id)}.md"
    )
    assert AUTONOMOUS_PREFIX in built.prompt
    assert article_path in built.prompt
    assert ctx.settings.research_persona_fanout_skill_doc_hint in built.prompt
    assert "~/" not in built.prompt and "/home/" not in built.prompt
    # slug is derived from the article's own parent directory name, and used
    # to build the deep-research report join path -- same filename-join
    # discipline as batch 1's research-into-draft.
    assert "inbox/deep-research/my-piece-deep-research.md" in built.prompt
    assert (
        'test -f "inbox/deep-research/my-piece-deep-research.md" && echo EXISTS || echo MISSING'
        in built.prompt
    )
    assert ctx.settings.cache_cli in built.prompt
    assert ctx.settings.assemble_review_script in built.prompt
    # Fix round 1: Step 3 (load active personas) was never asserted -- this
    # step has real incident history (see Step 1's own 2026-08-15 note in
    # the prompt), so it needs a marker like every other step.
    assert "Step 3 -- load active personas" in built.prompt
    assert "writing/personas/" in built.prompt
    assert "`active: true`" in built.prompt
    assert "don't invent personas" in built.prompt
    run_stamp = f"{date} {now_time(ctx.settings)}"
    assert f"### Research Persona Review ({run_stamp})" in built.prompt
    # Literal-brace JSON example (Part 2's persist payload) -- confirms the
    # f-string's `{{`/`}}` escaping rendered single braces.
    assert '{"persona": "<this persona' in built.prompt
    assert "{{" not in built.prompt and "}}" not in built.prompt
    assert f"SAVED {built.deliverable_path}" in built.prompt
