import stat
from dataclasses import dataclass

import pytest

from vaultos.config import Settings
from vaultos.runner.engines.base import EngineContext
from vaultos.runner.engines.script import ScriptEngine, ScriptEngineError


@dataclass
class _Job:
    id: str
    args: dict


@dataclass
class _Skill:
    id: str
    engine_config: dict


def _write_script(path, body):
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def _ctx(vault_root, state_root=None):
    settings_stub = object.__new__(Settings)  # unused by the engine itself
    return EngineContext(
        vault_root=vault_root,
        state_root=state_root or (vault_root / "system"),
        settings=settings_stub,
        emit=lambda event: None,
    )


def test_script_engine_success_and_deliverable(tmp_path):
    script = _write_script(
        tmp_path / "hello.sh",
        '#!/bin/sh\nmkdir -p "$(dirname "$2")"\necho "hello $1" > "$2"\n',
    )
    (tmp_path / "vault").mkdir()
    vault_root = tmp_path / "vault"

    skill = _Skill(
        id="hello-script",
        engine_config={
            "argv": [
                str(script),
                "{job_id}",
                "{vault_root}/inbox/reports/hello-script/{job_id}.md",
            ],
            "deliverable": "inbox/reports/hello-script/{job_id}.md",
        },
    )
    job = _Job(id="job-1", args={})

    result = ScriptEngine().run(job=job, skill=skill, ctx=_ctx(vault_root))

    assert result.success is True
    assert result.exit_code == 0
    assert result.deliverable_path == "inbox/reports/hello-script/job-1.md"
    deliverable = vault_root / "inbox" / "reports" / "hello-script" / "job-1.md"
    assert deliverable.read_text().strip() == "hello job-1"


def test_script_engine_substitutes_job_args(tmp_path):
    script = _write_script(tmp_path / "echo_arg.sh", '#!/bin/sh\necho "topic=$1"\n')
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    skill = _Skill(id="s", engine_config={"argv": [str(script), "{topic}"]})
    job = _Job(id="job-2", args={"topic": "chicago"})

    result = ScriptEngine().run(job=job, skill=skill, ctx=_ctx(vault_root))

    assert result.success is True
    assert result.summary == "topic=chicago"


def test_script_engine_nonzero_exit_is_failure(tmp_path):
    script = _write_script(tmp_path / "fail.sh", "#!/bin/sh\necho boom >&2\nexit 3\n")
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    skill = _Skill(id="s", engine_config={"argv": [str(script)]})
    job = _Job(id="job-3", args={})

    result = ScriptEngine().run(job=job, skill=skill, ctx=_ctx(vault_root))

    assert result.success is False
    assert result.exit_code == 3
    assert "boom" in result.summary


def test_script_engine_no_argv_configured_raises(tmp_path):
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    skill = _Skill(id="broken-skill", engine_config={})
    job = _Job(id="job-4", args={})

    with pytest.raises(ScriptEngineError, match="broken-skill"):
        ScriptEngine().run(job=job, skill=skill, ctx=_ctx(vault_root))


def test_script_engine_unknown_placeholder_raises(tmp_path):
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    skill = _Skill(id="s", engine_config={"argv": ["/bin/echo", "{nonexistent_arg}"]})
    job = _Job(id="job-5", args={})

    with pytest.raises(ScriptEngineError, match="nonexistent_arg"):
        ScriptEngine().run(job=job, skill=skill, ctx=_ctx(vault_root))


def test_script_engine_writes_run_log_under_state_root(tmp_path):
    script = _write_script(tmp_path / "ok.sh", "#!/bin/sh\necho fine\n")
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    state_root = tmp_path / "state"
    skill = _Skill(id="s", engine_config={"argv": [str(script)]})
    job = _Job(id="job-6", args={})

    ScriptEngine().run(job=job, skill=skill, ctx=_ctx(vault_root, state_root))

    log_path = state_root / "runs" / "job-6.log"
    assert log_path.exists()
    assert "fine" in log_path.read_text()


def test_script_engine_deliverable_missing_is_none(tmp_path):
    script = _write_script(tmp_path / "no_output.sh", "#!/bin/sh\nexit 0\n")
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    skill = _Skill(
        id="s",
        engine_config={
            "argv": [str(script)],
            "deliverable": "inbox/reports/never-written/{job_id}.md",
        },
    )
    job = _Job(id="job-7", args={})

    result = ScriptEngine().run(job=job, skill=skill, ctx=_ctx(vault_root))
    assert result.success is True
    assert result.deliverable_path is None


def test_script_engine_timeout_raises(tmp_path):
    script = _write_script(tmp_path / "slow.sh", "#!/bin/sh\nsleep 5\n")
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    skill = _Skill(id="s", engine_config={"argv": [str(script)], "timeout_s": 0.05})
    job = _Job(id="job-8", args={})

    with pytest.raises(ScriptEngineError, match="job-8|s|failed to run"):
        ScriptEngine().run(job=job, skill=skill, ctx=_ctx(vault_root))
