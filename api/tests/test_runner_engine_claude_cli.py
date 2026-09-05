"""Unit-level tests for the `claude-cli` engine adapter (ticket #23 stage B),
mirroring test_runner_engine_script.py's pattern: a stub shell script stands
in for the vendor CLI (never a real one, no keys, no network), and every
test drives ClaudeCliEngine().run() directly against it."""

import os
import stat
from dataclasses import dataclass

import pytest

from vaultos.config import Settings
from vaultos.runner.engines.base import EngineContext
from vaultos.runner.engines.claude_cli import (
    RETRY_CONTEXT_MARKER,
    ClaudeCliEngine,
    ClaudeCliEngineError,
)


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
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def _echo_argv_stub(path, log_path):
    """A stub `claude` binary that appends each argv element (one per line,
    with a delimiter between invocations) to `log_path`, then prints a fixed
    line to stdout -- lets tests assert on exactly what the adapter invoked
    it with."""
    return _write_stub(
        path,
        "#!/bin/sh\n"
        f'echo "=== invocation ===" >> "{log_path}"\n'
        f'for a in "$@"; do printf \'%s\\n\' "$a" >> "{log_path}"; done\n'
        'echo "stub output"\n',
    )


def _ctx(vault_root, state_root=None):
    settings_stub = object.__new__(Settings)  # unused by the engine itself
    return EngineContext(
        vault_root=vault_root, state_root=state_root or (vault_root / "system"),
        settings=settings_stub, emit=lambda event: None,
    )


def test_claude_cli_success_prompt_from_job_arg(tmp_path):
    stub = _write_stub(tmp_path / "claude", "#!/bin/sh\necho \"reply to: $1\"\n")
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    skill = _Skill(id="ask-claude", engine_config={"binary": str(stub)})
    job = _Job(id="job-1", args={"prompt": "what time is it"})

    result = ClaudeCliEngine().run(job=job, skill=skill, ctx=_ctx(vault_root))

    assert result.success is True
    assert result.exit_code == 0
    assert result.summary == "reply to: what time is it"


def test_claude_cli_prompt_template_interpolation(tmp_path):
    stub = _write_stub(tmp_path / "claude", "#!/bin/sh\necho \"$1\"\n")
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    skill = _Skill(
        id="summarize",
        engine_config={"binary": str(stub), "prompt": "Summarize {topic} for job {job_id}."},
    )
    job = _Job(id="job-2", args={"topic": "chicago transit"})

    result = ClaudeCliEngine().run(job=job, skill=skill, ctx=_ctx(vault_root))

    assert result.success is True
    assert result.summary == "Summarize chicago transit for job job-2."


def test_claude_cli_prompt_template_unknown_placeholder_raises(tmp_path):
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    skill = _Skill(id="s", engine_config={"binary": "/bin/true", "prompt": "{nonexistent}"})
    job = _Job(id="job-3", args={})

    with pytest.raises(ClaudeCliEngineError, match="nonexistent"):
        ClaudeCliEngine().run(job=job, skill=skill, ctx=_ctx(vault_root))


def test_claude_cli_no_prompt_source_raises(tmp_path):
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    skill = _Skill(id="no-prompt-skill", engine_config={"binary": "/bin/true"})
    job = _Job(id="job-4", args={})

    with pytest.raises(ClaudeCliEngineError, match="no-prompt-skill"):
        ClaudeCliEngine().run(job=job, skill=skill, ctx=_ctx(vault_root))


def test_claude_cli_no_binary_configured_raises(tmp_path):
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    skill = _Skill(id="unconfigured-skill", engine_config={})
    job = _Job(id="job-5", args={"prompt": "hi"})

    with pytest.raises(ClaudeCliEngineError, match="unconfigured-skill"):
        ClaudeCliEngine().run(job=job, skill=skill, ctx=_ctx(vault_root))


def test_claude_cli_misconfigured_binary_path_fails_fast(tmp_path):
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    skill = _Skill(
        id="s", engine_config={"binary": str(tmp_path / "does-not-exist-claude")},
    )
    job = _Job(id="job-6", args={"prompt": "hi"})

    with pytest.raises(ClaudeCliEngineError, match="failed to run"):
        ClaudeCliEngine().run(job=job, skill=skill, ctx=_ctx(vault_root))


def test_claude_cli_nonzero_exit_is_failure(tmp_path):
    stub = _write_stub(tmp_path / "claude", "#!/bin/sh\necho boom >&2\nexit 2\n")
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    skill = _Skill(id="s", engine_config={"binary": str(stub)})
    job = _Job(id="job-7", args={"prompt": "hi"})

    result = ClaudeCliEngine().run(job=job, skill=skill, ctx=_ctx(vault_root))

    assert result.success is False
    assert result.exit_code == 2
    assert "boom" in result.summary


def test_claude_cli_model_flag_appended_when_configured(tmp_path):
    log = tmp_path / "argv.log"
    stub = _echo_argv_stub(tmp_path / "claude", log)
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    skill = _Skill(id="s", engine_config={"binary": str(stub), "model": "opus"})
    job = _Job(id="job-8", args={"prompt": "hi"})

    ClaudeCliEngine().run(job=job, skill=skill, ctx=_ctx(vault_root))

    lines = log.read_text().splitlines()
    assert "--model" in lines
    assert lines[lines.index("--model") + 1] == "opus"


def test_claude_cli_no_model_flag_when_not_configured(tmp_path):
    log = tmp_path / "argv.log"
    stub = _echo_argv_stub(tmp_path / "claude", log)
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    skill = _Skill(id="s", engine_config={"binary": str(stub)})
    job = _Job(id="job-9", args={"prompt": "hi"})

    ClaudeCliEngine().run(job=job, skill=skill, ctx=_ctx(vault_root))

    assert "--model" not in log.read_text().splitlines()


def test_claude_cli_base_args_precede_model_and_prompt(tmp_path):
    log = tmp_path / "argv.log"
    stub = _echo_argv_stub(tmp_path / "claude", log)
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    skill = _Skill(
        id="s",
        engine_config={"binary": str(stub), "args": ["--print", "--dangerously-skip-permissions"], "model": "sonnet"},
    )
    job = _Job(id="job-10", args={"prompt": "hello there"})

    ClaudeCliEngine().run(job=job, skill=skill, ctx=_ctx(vault_root))

    lines = [l for l in log.read_text().splitlines() if l != "=== invocation ==="]
    assert lines == ["--print", "--dangerously-skip-permissions", "--model", "sonnet", "hello there"]


def test_claude_cli_retry_context_appended_under_marker(tmp_path):
    stub = _write_stub(tmp_path / "claude", "#!/bin/sh\necho \"$1\"\n")
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    skill = _Skill(id="s", engine_config={"binary": str(stub)})
    job = _Job(id="job-11", args={"prompt": "original prompt"})

    result = ClaudeCliEngine().run(
        job=job, skill=skill, ctx=_ctx(vault_root), retry_context="check said: missing frobnicator",
    )

    assert result.summary.startswith("original prompt")
    assert RETRY_CONTEXT_MARKER.strip() in result.summary
    assert "check said: missing frobnicator" in result.summary


def test_claude_cli_timeout_raises(tmp_path):
    stub = _write_stub(tmp_path / "claude", "#!/bin/sh\nsleep 5\n")
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    skill = _Skill(id="s", engine_config={"binary": str(stub), "timeout_s": 0.05})
    job = _Job(id="job-12", args={"prompt": "hi"})

    with pytest.raises(ClaudeCliEngineError, match="job-12|s|failed to run"):
        ClaudeCliEngine().run(job=job, skill=skill, ctx=_ctx(vault_root))


def test_claude_cli_writes_run_log_under_state_root(tmp_path):
    stub = _write_stub(tmp_path / "claude", "#!/bin/sh\necho fine\n")
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    state_root = tmp_path / "state"
    skill = _Skill(id="s", engine_config={"binary": str(stub)})
    job = _Job(id="job-13", args={"prompt": "hi"})

    ClaudeCliEngine().run(job=job, skill=skill, ctx=_ctx(vault_root, state_root))

    log_path = state_root / "runs" / "job-13.log"
    assert log_path.exists()
    assert "fine" in log_path.read_text()
