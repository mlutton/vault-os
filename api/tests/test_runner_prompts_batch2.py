"""Unit tests for the prompt-builder registry's batch-2 builders (ticket
#26, the heavy research/writing pipeline set). Same "markers, not goldens"
discipline as `test_runner_prompts.py`'s batch-1 tests: per-skill assertions
on load-bearing phrases and interpolated job args/settings, plus the
deliverable path's exact shape -- never a full-string golden.

Built up one skill per commit, matching the ticket's per-skill commit plan;
this file currently covers `acquire` and `daily-topic-digest`. The shared
registry-boundary tests (batch-2 fully registered, excluded set unchanged)
live in `test_runner_prompts.py` and are updated once all five batch-2
skills land.
"""

import pytest

from vaultos.config import Settings
from vaultos.runner.prompts import AUTONOMOUS_PREFIX, PROMPT_BUILDER_REGISTRY, BuilderContext, get_builder, today_date


@pytest.fixture
def settings(monkeypatch, tmp_path):
    monkeypatch.setenv("VAULT_ROOT", str(tmp_path))
    monkeypatch.delenv("HUD_TZ", raising=False)
    monkeypatch.delenv("PYTHON_BIN", raising=False)
    monkeypatch.delenv("RSS_POLL_SCRIPT", raising=False)
    monkeypatch.delenv("WEBSEARCH_CACHED_FETCH_WORKFLOW", raising=False)
    monkeypatch.delenv("ASSEMBLE_ACQUIRE_REPORT_CLI", raising=False)
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
    assert f"SAVED {built.deliverable_path}" in built.prompt


def test_acquire_ignores_args_no_required_arg_gate(ctx):
    # Legacy source has no arg check for this skill -- any args dict (even
    # garbage) still produces a built prompt.
    built = PROMPT_BUILDER_REGISTRY["acquire"]({"unexpected": "value"}, ctx)
    assert built is not None


def test_get_builder_returns_a_callable_for_daily_topic_digest():
    assert callable(get_builder("daily-topic-digest"))


def test_daily_topic_digest(ctx):
    built = PROMPT_BUILDER_REGISTRY["daily-topic-digest"]({}, ctx)
    assert built is not None
    date = today_date(ctx.settings)
    assert built.deliverable_path == f"inbox/reports/daily-topic-digest/{date}-daily-topic-digest.md"
    assert AUTONOMOUS_PREFIX in built.prompt
    assert "Step 1 -- gather everything not yet attached to a topic" in built.prompt
    assert "Step 4 -- check persona fit, inline, no fan-out" in built.prompt
    assert "Step 7 -- your final reply must present the ranked list conversationally" in built.prompt
    assert f"first_seen: {date}" in built.prompt
    assert f"date: {date}" in built.prompt
    # Unlike every other batch-1/batch-2 skill, this prompt does NOT end
    # with "SAVED <deliverable>" -- Step 7's pick-prompt is the final reply
    # by design (ported verbatim, including that omission).
    assert "SAVED" not in built.prompt


def test_daily_topic_digest_ignores_args_no_required_arg_gate(ctx):
    built = PROMPT_BUILDER_REGISTRY["daily-topic-digest"]({"unexpected": "value"}, ctx)
    assert built is not None
