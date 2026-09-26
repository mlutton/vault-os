"""Chained digests consume the recorded acquire output through the runner."""

import json
import stat
from dataclasses import dataclass

import pytest

from vaultos.api.jobs import dispatch_skill
from vaultos.config import Settings
from vaultos.db.conn import connect
from vaultos.jobs import store
from vaultos.registry import load_registry
from vaultos.runner.core import Runner
from vaultos.runner.engines import ClaudeCliEngine, CursorCliEngine, EngineResult


@dataclass
class AcquireEngine:
    deliverable: str

    def run(self, *, job, skill, ctx, retry_context=None):
        return EngineResult(True, 0, "acquired", self.deliverable)


@pytest.fixture(params=[ClaudeCliEngine, CursorCliEngine], ids=["claude-cli", "cursor-cli"])
def chain_runner(request, monkeypatch, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    cli = tmp_path / "cli"
    cli.write_text(
        '#!/bin/sh\nfor a in "$@"; do last="$a"; done\n'
        'printf \'%s\' "$last" > "$PROMPT_LOG"\necho "digest complete"\n'
    )
    cli.chmod(cli.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "prompt.log"
    monkeypatch.setenv("PROMPT_LOG", str(log))
    monkeypatch.setenv("VAULT_ROOT", str(vault))
    monkeypatch.setenv("VAULTOS_DB", str(tmp_path / "jobs.db"))
    monkeypatch.delenv("VAULTOS_STATE_ROOT", raising=False)
    settings = Settings()
    (vault / "system").mkdir()
    engine = request.param()
    (vault / "system" / "skills.json").write_text(
        json.dumps(
            {
                "version": 1,
                "skills": [
                    {
                        "id": "acquire",
                        "label": "Acquire",
                        "deck": True,
                        "engine": "fake-acquire",
                        "args": [],
                    },
                    {
                        "id": "daily-topic-digest",
                        "label": "Digest",
                        "deck": True,
                        "engine": engine.name,
                        "args": [],
                        "engine_config": {"binary": str(cli)},
                    },
                ],
            }
        )
    )
    # Deliberately predates the digest: the input comes from the record,
    # never a path recomputed from the child's local day or job ID.
    report = "inbox/research/2026-09-25-acquire-a1b2c3d4.md"
    path = vault / report
    path.parent.mkdir(parents=True)
    path.write_text("# Acquired evidence\n")
    conn = connect(settings.db_path)
    registry = load_registry(vault)
    runner = Runner(
        conn,
        registry,
        settings,
        engines={"fake-acquire": AcquireEngine(report), engine.name: engine},
    )
    try:
        yield runner, registry, vault, report, log
    finally:
        conn.close()


def _acquire_and_chain(chain_runner):
    runner, registry, vault, report, log = chain_runner
    parent_id, _ = dispatch_skill(runner.conn, registry, vault, "acquire", {}, "api")
    assert runner.run_once() is True
    assert store.get_job(runner.conn, parent_id).status == "ok"
    children = store.list_jobs(runner.conn, statuses=["queued"])
    assert len(children) == 1
    child = children[0]
    assert child.skill == "daily-topic-digest"
    assert child.args == {}
    intent = json.loads((runner.state_root / "queue" / f"{child.id}.json").read_text())
    assert intent["chain"]["parent_job_id"] == parent_id
    return parent_id, child.id


def test_chained_digest_receives_parent_deliverable(chain_runner):
    runner, registry, vault, report, log = chain_runner
    parent_id, child_id = _acquire_and_chain(chain_runner)
    terminal = json.loads((runner.state_root / "runs" / f"{parent_id}.json").read_text())
    assert terminal["deliverable_path"] == report

    assert runner.run_once() is True

    assert store.get_job(runner.conn, child_id).status == "ok"
    prompt = log.read_text()
    step_one = prompt.split("Step 1 --", 1)[1].split("Step 2 --", 1)[0]
    assert f"This chained run is for the parent report {report}." in step_one
    assert f"Read {report} in full" in step_one
    assert "Then scan: (1) sources/ -- every file; (2)" in step_one
    assert "inbox/research/*.md" not in prompt


@pytest.mark.parametrize(
    ("case", "failed_check"),
    [
        ("absolute", "must be relative"),
        ("traversal", "must not contain '..'"),
        ("other-directory", "outside inbox/research"),
        ("escaping-symlink", "outside inbox/research"),
        ("symlinked-research-directory", "outside inbox/research"),
        ("symlinked-inbox-directory", "outside inbox/research"),
        ("directory", "must be a regular file"),
        ("missing-file", "file is missing"),
        ("missing-record", "parent record is missing"),
        ("unreadable-record", "parent record is unreadable"),
        ("missing-path", "deliverable_path must be a nonempty string"),
    ],
)
def test_chained_digest_rejects_unsafe_parent_deliverable(chain_runner, case, failed_check):
    runner, registry, vault, report, log = chain_runner
    parent_id, child_id = _acquire_and_chain(chain_runner)
    record_path = runner.state_root / "runs" / f"{parent_id}.json"
    record = json.loads(record_path.read_text())
    if case == "absolute":
        record["deliverable_path"] = str(vault / report)
    elif case == "traversal":
        other = vault / "inbox" / "other.md"
        other.write_text("not research")
        record["deliverable_path"] = "inbox/research/../other.md"
    elif case == "other-directory":
        other = vault / "sources" / "other.md"
        other.parent.mkdir()
        other.write_text("not research")
        record["deliverable_path"] = "sources/other.md"
    elif case == "escaping-symlink":
        target = vault.parent / "outside.md"
        target.write_text("outside research")
        link = vault / "inbox" / "research" / "linked.md"
        link.symlink_to(target)
        record["deliverable_path"] = "inbox/research/linked.md"
    elif case == "symlinked-research-directory":
        research = vault / "inbox" / "research"
        target = vault / "relocated-research"
        research.rename(target)
        research.symlink_to(target, target_is_directory=True)
    elif case == "symlinked-inbox-directory":
        inbox = vault / "inbox"
        target = vault / "relocated-inbox"
        inbox.rename(target)
        inbox.symlink_to(target, target_is_directory=True)
    elif case == "directory":
        record["deliverable_path"] = "inbox/research"
    elif case == "missing-file":
        record["deliverable_path"] = "inbox/research/missing.md"
    elif case == "missing-record":
        record_path.unlink()
    elif case == "unreadable-record":
        record_path.write_text("{invalid json")
    elif case == "missing-path":
        record["deliverable_path"] = None
    if case not in ("missing-record", "unreadable-record"):
        record["completion_evidence"]["deliverable_path"] = record["deliverable_path"]
        record_path.write_text(json.dumps(record))

    assert runner.run_once() is True

    child = store.get_job(runner.conn, child_id)
    assert child.status == "error"
    assert "parent deliverable rejected" in child.summary
    assert failed_check in child.summary
    assert not log.exists(), "An invalid parent must never launch a scan-all digest"
    terminal = json.loads((runner.state_root / "runs" / f"{child_id}.json").read_text())
    assert terminal["status"] == "error"
    assert failed_check in terminal["summary"]


@pytest.mark.parametrize("case", ["dot-component", "internal-symlink"])
def test_chained_digest_receives_canonical_parent_deliverable(chain_runner, case):
    runner, registry, vault, report, log = chain_runner
    parent_id, child_id = _acquire_and_chain(chain_runner)
    record_path = runner.state_root / "runs" / f"{parent_id}.json"
    record = json.loads(record_path.read_text())
    if case == "dot-component":
        recorded = report.replace("inbox/research/", "inbox/./research/")
    else:
        (vault / "inbox" / "research" / "linked.md").symlink_to(vault / report)
        recorded = "inbox/research/linked.md"
    record["deliverable_path"] = recorded
    record["completion_evidence"]["deliverable_path"] = recorded
    record_path.write_text(json.dumps(record))

    assert runner.run_once() is True
    assert store.get_job(runner.conn, child_id).status == "ok"
    prompt = log.read_text()
    assert f"This chained run is for the parent report {report}." in prompt
    assert f"Read {report} in full" in prompt
    assert recorded not in prompt
