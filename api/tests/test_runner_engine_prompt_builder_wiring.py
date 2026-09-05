"""Engine-level coverage of the prompt-builder registry wiring (ticket #25):
confirms `claude_cli.py`/`cursor_cli.py` actually prefer a registered
builder's prompt+deliverable over `engine_config`'s own `prompt` template or
the job's `prompt` arg, AND that a skill with NO builder keeps its exact
prior behavior unchanged -- the "no-builder passthrough" the ticket calls
out explicitly for testing. Drives the engines directly (unit-level, no
HTTP/Runner), mirroring test_runner_engine_claude_cli.py's own pattern.
End-to-end coverage through the HTTP API lives in
test_runner_prompts_batch1_end_to_end.py."""

from dataclasses import dataclass

import pytest

from vaultos.config import Settings
from vaultos.runner.engines.base import EngineContext
from vaultos.runner.engines.claude_cli import ClaudeCliEngine
from vaultos.runner.engines.cursor_cli import CursorCliEngine
from vaultos.runner.prompts import PROMPT_BUILDER_REGISTRY, BuiltPrompt


@dataclass
class _Job:
    id: str
    args: dict


@dataclass
class _Skill:
    id: str
    engine_config: dict


def _write_stub(path, body):
    path.write_text(body)
    import stat

    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def _echo_argv_stub(path):
    # Echoes the LAST argv element, not the first -- claude-cli passes the
    # prompt as argv[1], but cursor-cli inserts TRUST_FLAG before it, so the
    # prompt is only ever reliably the *last* element across both adapters
    # under test here (this file is parametrized over both).
    return _write_stub(
        path, '#!/bin/sh\nfor a in "$@"; do last="$a"; done\nprintf \'%s\' "$last"\n'
    )


def _ctx(vault_root):
    settings_stub = object.__new__(Settings)
    return EngineContext(
        vault_root=vault_root,
        state_root=vault_root / "system",
        settings=settings_stub,
        emit=lambda event: None,
    )


@pytest.fixture(params=[ClaudeCliEngine, CursorCliEngine], ids=["claude-cli", "cursor-cli"])
def engine_cls(request):
    return request.param


def test_no_builder_passthrough_uses_engine_config_prompt_template(tmp_path, engine_cls):
    """A skill id with no registered builder must behave EXACTLY as before
    this ticket: engine_config's own `prompt` template wins."""
    assert PROMPT_BUILDER_REGISTRY.get("summarize-freeform") is None
    stub = _echo_argv_stub(tmp_path / "cli")
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    skill = _Skill(
        id="summarize-freeform",
        engine_config={"binary": str(stub), "prompt": "Summarize {topic}."},
    )
    job = _Job(id="job-1", args={"topic": "chicago transit"})

    result = engine_cls().run(job=job, skill=skill, ctx=_ctx(vault_root))

    assert result.summary == "Summarize chicago transit."
    assert result.deliverable_path is None


def test_no_builder_passthrough_uses_job_prompt_arg(tmp_path, engine_cls):
    assert PROMPT_BUILDER_REGISTRY.get("ask-freeform") is None
    stub = _echo_argv_stub(tmp_path / "cli")
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    skill = _Skill(id="ask-freeform", engine_config={"binary": str(stub)})
    job = _Job(id="job-2", args={"prompt": "what time is it"})

    result = engine_cls().run(job=job, skill=skill, ctx=_ctx(vault_root))

    assert result.summary == "what time is it"


def test_registered_builder_wins_over_engine_config_prompt_template(
    tmp_path, engine_cls, monkeypatch
):
    """A skill WITH a registered builder must ignore engine_config's own
    `prompt` template (and any `prompt` job arg) entirely -- the builder is
    the sole prompt source once one is registered."""
    stub = _echo_argv_stub(tmp_path / "cli")
    vault_root = tmp_path / "vault"
    vault_root.mkdir()

    def fake_builder(args, ctx):
        return BuiltPrompt(prompt="BUILT PROMPT TEXT", deliverable_path="inbox/reports/x.md")

    monkeypatch.setitem(PROMPT_BUILDER_REGISTRY, "has-a-builder", fake_builder)
    skill = _Skill(
        id="has-a-builder",
        engine_config={"binary": str(stub), "prompt": "should never be used {topic}"},
    )
    job = _Job(id="job-3", args={"topic": "irrelevant", "prompt": "also never used"})

    result = engine_cls().run(job=job, skill=skill, ctx=_ctx(vault_root))

    assert result.summary == "BUILT PROMPT TEXT"


def test_builder_deliverable_path_set_when_file_exists_after_success(
    tmp_path, engine_cls, monkeypatch
):
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    deliverable_abs = vault_root / "inbox" / "reports" / "built.md"
    deliverable_abs.parent.mkdir(parents=True)

    def fake_builder(args, ctx):
        # Simulate the engine's own subprocess having written the
        # deliverable -- in this unit test we just pre-create it, since
        # what's under test is the *existence check + wiring*, not a real
        # CLI's file-writing behavior (covered end-to-end elsewhere).
        deliverable_abs.write_text("done")
        return BuiltPrompt(prompt="write it", deliverable_path="inbox/reports/built.md")

    monkeypatch.setitem(PROMPT_BUILDER_REGISTRY, "writes-deliverable", fake_builder)
    stub = _echo_argv_stub(tmp_path / "cli")
    skill = _Skill(id="writes-deliverable", engine_config={"binary": str(stub)})
    job = _Job(id="job-4", args={})

    result = engine_cls().run(job=job, skill=skill, ctx=_ctx(vault_root))

    assert result.success is True
    assert result.deliverable_path == "inbox/reports/built.md"


def test_builder_deliverable_path_none_when_file_missing_after_success(
    tmp_path, engine_cls, monkeypatch
):
    vault_root = tmp_path / "vault"
    vault_root.mkdir()

    def fake_builder(args, ctx):
        # Never actually written -- the stub CLI below doesn't write it,
        # standing in for a run that claimed success but produced nothing.
        return BuiltPrompt(prompt="write it", deliverable_path="inbox/reports/never-written.md")

    monkeypatch.setitem(PROMPT_BUILDER_REGISTRY, "missing-deliverable", fake_builder)
    stub = _echo_argv_stub(tmp_path / "cli")
    skill = _Skill(id="missing-deliverable", engine_config={"binary": str(stub)})
    job = _Job(id="job-5", args={})

    result = engine_cls().run(job=job, skill=skill, ctx=_ctx(vault_root))

    assert result.success is True
    assert result.deliverable_path is None


def test_builder_rejecting_job_args_raises_engine_error(tmp_path, engine_cls, monkeypatch):
    from vaultos.runner.engines.claude_cli import ClaudeCliEngineError
    from vaultos.runner.engines.cursor_cli import CursorCliEngineError

    vault_root = tmp_path / "vault"
    vault_root.mkdir()

    def rejecting_builder(args, ctx):
        return None  # missing/blank required job arg, per convention

    monkeypatch.setitem(PROMPT_BUILDER_REGISTRY, "picky-builder", rejecting_builder)
    stub = _echo_argv_stub(tmp_path / "cli")
    skill = _Skill(id="picky-builder", engine_config={"binary": str(stub)})
    job = _Job(id="job-6", args={})

    expected_error = ClaudeCliEngineError if engine_cls is ClaudeCliEngine else CursorCliEngineError
    with pytest.raises(expected_error, match="picky-builder"):
        engine_cls().run(job=job, skill=skill, ctx=_ctx(vault_root))


def test_retry_context_still_appended_after_builder_prompt(tmp_path, engine_cls, monkeypatch):
    stub = _echo_argv_stub(tmp_path / "cli")
    vault_root = tmp_path / "vault"
    vault_root.mkdir()

    def fake_builder(args, ctx):
        return BuiltPrompt(prompt="original builder prompt", deliverable_path="inbox/x.md")

    monkeypatch.setitem(PROMPT_BUILDER_REGISTRY, "retryable-builder", fake_builder)
    skill = _Skill(id="retryable-builder", engine_config={"binary": str(stub)})
    job = _Job(id="job-7", args={})

    result = engine_cls().run(
        job=job,
        skill=skill,
        ctx=_ctx(vault_root),
        retry_context="check said: nope",
    )

    assert "original builder prompt" in result.summary
    assert "check said: nope" in result.summary
