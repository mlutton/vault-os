"""Unit-level tests for the `cursor-cli` engine adapter (ticket #24), mirroring
test_runner_engine_claude_cli.py's pattern: a stub shell script stands in for
the vendor CLI (never a real one, no keys, no network), and every test drives
CursorCliEngine().run() directly against it."""

import stat
from dataclasses import dataclass

import pytest

from vaultos.config import Settings
from vaultos.runner.engines.base import EngineContext
from vaultos.runner.engines.cursor_cli import (
    RETRY_CONTEXT_MARKER,
    TRUST_FLAG,
    CursorCliEngine,
    CursorCliEngineError,
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
    """A stub `cursor-agent` binary that appends each argv element (one per
    line, with a delimiter between invocations) to `log_path`, then prints a
    fixed line to stdout -- lets tests assert on exactly what the adapter
    invoked it with."""
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
        vault_root=vault_root,
        state_root=state_root or (vault_root / "system"),
        settings=settings_stub,
        emit=lambda event: None,
    )


# A POSIX-sh stub that echoes only its final argv element (the prompt is
# always the last one this adapter appends, per the module docstring).
_LAST_ARG_STUB_BODY = '#!/bin/sh\nfor a in "$@"; do last="$a"; done\necho "{prefix}$last"\n'


def test_cursor_cli_success_prompt_from_job_arg(tmp_path):
    stub = _write_stub(tmp_path / "cursor-agent", _LAST_ARG_STUB_BODY.format(prefix="reply to: "))
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    skill = _Skill(id="ask-cursor", engine_config={"binary": str(stub)})
    job = _Job(id="job-1", args={"prompt": "what time is it"})

    result = CursorCliEngine().run(job=job, skill=skill, ctx=_ctx(vault_root))

    assert result.success is True
    assert result.exit_code == 0
    assert result.summary == "reply to: what time is it"


def test_cursor_cli_prompt_template_interpolation(tmp_path):
    stub = _write_stub(tmp_path / "cursor-agent", _LAST_ARG_STUB_BODY.format(prefix=""))
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    skill = _Skill(
        id="summarize",
        engine_config={"binary": str(stub), "prompt": "Summarize {topic} for job {job_id}."},
    )
    job = _Job(id="job-2", args={"topic": "chicago transit"})

    result = CursorCliEngine().run(job=job, skill=skill, ctx=_ctx(vault_root))

    assert result.success is True
    assert result.summary == "Summarize chicago transit for job job-2."


def test_cursor_cli_prompt_template_unknown_placeholder_raises(tmp_path):
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    skill = _Skill(id="s", engine_config={"binary": "/bin/true", "prompt": "{nonexistent}"})
    job = _Job(id="job-3", args={})

    with pytest.raises(CursorCliEngineError, match="nonexistent"):
        CursorCliEngine().run(job=job, skill=skill, ctx=_ctx(vault_root))


def test_cursor_cli_no_prompt_source_raises(tmp_path):
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    skill = _Skill(id="no-prompt-skill", engine_config={"binary": "/bin/true"})
    job = _Job(id="job-4", args={})

    with pytest.raises(CursorCliEngineError, match="no-prompt-skill"):
        CursorCliEngine().run(job=job, skill=skill, ctx=_ctx(vault_root))


def test_cursor_cli_no_binary_configured_raises(tmp_path):
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    skill = _Skill(id="unconfigured-skill", engine_config={})
    job = _Job(id="job-5", args={"prompt": "hi"})

    with pytest.raises(CursorCliEngineError, match="unconfigured-skill"):
        CursorCliEngine().run(job=job, skill=skill, ctx=_ctx(vault_root))


def test_cursor_cli_misconfigured_binary_path_fails_fast(tmp_path):
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    skill = _Skill(
        id="s",
        engine_config={"binary": str(tmp_path / "does-not-exist-cursor-agent")},
    )
    job = _Job(id="job-6", args={"prompt": "hi"})

    with pytest.raises(CursorCliEngineError, match="failed to run"):
        CursorCliEngine().run(job=job, skill=skill, ctx=_ctx(vault_root))


def test_cursor_cli_nonzero_exit_is_failure(tmp_path):
    stub = _write_stub(tmp_path / "cursor-agent", "#!/bin/sh\necho boom >&2\nexit 2\n")
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    skill = _Skill(id="s", engine_config={"binary": str(stub)})
    job = _Job(id="job-7", args={"prompt": "hi"})

    result = CursorCliEngine().run(job=job, skill=skill, ctx=_ctx(vault_root))

    assert result.success is False
    assert result.exit_code == 2
    assert "boom" in result.summary


def test_cursor_cli_trust_flag_always_present(tmp_path):
    log = tmp_path / "argv.log"
    stub = _echo_argv_stub(tmp_path / "cursor-agent", log)
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    skill = _Skill(id="s", engine_config={"binary": str(stub)})
    job = _Job(id="job-8b", args={"prompt": "hi"})

    CursorCliEngine().run(job=job, skill=skill, ctx=_ctx(vault_root))

    assert TRUST_FLAG in log.read_text().splitlines()


def test_cursor_cli_trust_flag_present_even_with_model_and_args(tmp_path):
    log = tmp_path / "argv.log"
    stub = _echo_argv_stub(tmp_path / "cursor-agent", log)
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    skill = _Skill(
        id="s",
        engine_config={"binary": str(stub), "args": ["--print"], "model": "grok"},
    )
    job = _Job(id="job-8c", args={"prompt": "hi"})

    CursorCliEngine().run(job=job, skill=skill, ctx=_ctx(vault_root))

    assert TRUST_FLAG in log.read_text().splitlines()


def test_cursor_cli_model_flag_appended_when_configured(tmp_path):
    log = tmp_path / "argv.log"
    stub = _echo_argv_stub(tmp_path / "cursor-agent", log)
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    skill = _Skill(id="s", engine_config={"binary": str(stub), "model": "grok"})
    job = _Job(id="job-8", args={"prompt": "hi"})

    CursorCliEngine().run(job=job, skill=skill, ctx=_ctx(vault_root))

    lines = log.read_text().splitlines()
    assert "--model" in lines
    assert lines[lines.index("--model") + 1] == "grok"


def test_cursor_cli_no_model_flag_when_not_configured(tmp_path):
    log = tmp_path / "argv.log"
    stub = _echo_argv_stub(tmp_path / "cursor-agent", log)
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    skill = _Skill(id="s", engine_config={"binary": str(stub)})
    job = _Job(id="job-9", args={"prompt": "hi"})

    CursorCliEngine().run(job=job, skill=skill, ctx=_ctx(vault_root))

    assert "--model" not in log.read_text().splitlines()


def test_cursor_cli_base_args_precede_trust_model_and_prompt(tmp_path):
    log = tmp_path / "argv.log"
    stub = _echo_argv_stub(tmp_path / "cursor-agent", log)
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    skill = _Skill(
        id="s",
        engine_config={"binary": str(stub), "args": ["--print", "--force"], "model": "sonnet"},
    )
    job = _Job(id="job-10", args={"prompt": "hello there"})

    CursorCliEngine().run(job=job, skill=skill, ctx=_ctx(vault_root))

    lines = [line for line in log.read_text().splitlines() if line != "=== invocation ==="]
    assert lines == ["--print", "--force", TRUST_FLAG, "--model", "sonnet", "hello there"]


def test_cursor_cli_retry_context_appended_under_marker(tmp_path):
    stub = _write_stub(tmp_path / "cursor-agent", _LAST_ARG_STUB_BODY.format(prefix=""))
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    skill = _Skill(id="s", engine_config={"binary": str(stub)})
    job = _Job(id="job-11", args={"prompt": "original prompt"})

    result = CursorCliEngine().run(
        job=job,
        skill=skill,
        ctx=_ctx(vault_root),
        retry_context="check said: missing frobnicator",
    )

    assert result.summary.startswith("original prompt")
    assert RETRY_CONTEXT_MARKER.strip() in result.summary
    assert "check said: missing frobnicator" in result.summary


def test_cursor_cli_timeout_raises(tmp_path):
    stub = _write_stub(tmp_path / "cursor-agent", "#!/bin/sh\nsleep 5\n")
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    skill = _Skill(id="s", engine_config={"binary": str(stub), "timeout_s": 0.05})
    job = _Job(id="job-12", args={"prompt": "hi"})

    with pytest.raises(CursorCliEngineError, match="job-12|s|failed to run"):
        CursorCliEngine().run(job=job, skill=skill, ctx=_ctx(vault_root))


def test_cursor_cli_writes_run_log_under_state_root(tmp_path):
    stub = _write_stub(tmp_path / "cursor-agent", "#!/bin/sh\necho fine\n")
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    state_root = tmp_path / "state"
    skill = _Skill(id="s", engine_config={"binary": str(stub)})
    job = _Job(id="job-13", args={"prompt": "hi"})

    CursorCliEngine().run(job=job, skill=skill, ctx=_ctx(vault_root, state_root))

    log_path = state_root / "runs" / "job-13.log"
    assert log_path.exists()
    assert "fine" in log_path.read_text()
