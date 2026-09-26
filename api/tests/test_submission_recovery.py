"""Submission and chain crash proofs use synthetic files, SQLite and fake engines."""

import json
import os
import sqlite3
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import vaultos.api.jobs as jobs
import vaultos.runner.core as core
import vaultos.vault.durable as durable
from vaultos.cli import main, reindex, settle_intents
from vaultos.config import Settings
from vaultos.db.conn import connect
from vaultos.jobs import store
from vaultos.jobs.reconcile import reconcile_from_files
from vaultos.registry import load_registry
from vaultos.runner.core import Runner
from vaultos.runner.engines import EngineResult
from vaultos.runner.records import write_record
from vaultos.vault.intents import write_intent


class FakeEngine:
    def __init__(self):
        self.skills = []

    def run(self, *, job, ctx, **kwargs):
        self.skills.append(job.skill)
        deliverable = None
        if job.skill == "acquire":
            deliverable = f"inbox/research/acquire-{job.id}.md"
            path = ctx.vault_root / deliverable
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# Acquired evidence\n")
        return EngineResult(
            success=True, exit_code=0, summary="complete", deliverable_path=deliverable
        )


@pytest.fixture
def setup(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    root = vault / "system"
    (root / "queue").mkdir(parents=True)
    (root / "runs").mkdir()
    (root / "skills.json").write_text(
        json.dumps(
            {
                "version": 1,
                "skills": [
                    {"id": name, "label": name, "deck": True, "engine": "fake"}
                    for name in (
                        "acquire",
                        "daily-topic-digest",
                        "deep-research",
                        "research-into-draft",
                        "sample",
                    )
                ],
            }
        )
    )
    monkeypatch.setenv("VAULT_ROOT", str(vault))
    monkeypatch.setenv("VAULTOS_DB", str(tmp_path / "jobs.db"))
    monkeypatch.delenv("VAULTOS_STATE_ROOT", raising=False)
    settings = Settings()
    conn = connect(settings.db_path)
    registry = load_registry(vault)
    engine = FakeEngine()
    runner = Runner(conn, registry, settings, engines={"fake": engine})
    yield vault, settings, conn, registry, engine, runner
    conn.close()


def submit(setup, skill="acquire"):
    vault, _, conn, registry, _, _ = setup
    return jobs.dispatch_skill(conn, registry, vault, skill, {}, "api")[0]


def terminal_path(vault, job_id):
    return vault / "system" / "runs" / f"{job_id}.json"


def intents(vault):
    return sorted((vault / "system" / "queue").glob("*.json"))


def crash_parent(setup, monkeypatch, stage):
    vault, _, conn, _, _, runner = setup
    job_id = submit(setup)
    with monkeypatch.context() as patch:
        if stage == "before_intent_removal":
            original = core.write_record

            def crash_after_terminal(path, record):
                original(path, record)
                if path == terminal_path(vault, job_id):
                    raise SystemExit("simulated crash")

            patch.setattr(core, "write_record", crash_after_terminal)
        elif stage == "before_index_event":
            original = core.remove_intent

            def crash_after_removal(path):
                original(path)
                raise SystemExit("simulated crash")

            patch.setattr(core, "remove_intent", crash_after_removal)
        else:

            def crash_before_dispatch(*args, **kwargs):
                assert store.get_job(conn, job_id).status == "ok"
                raise SystemExit("simulated crash")

            patch.setattr(jobs, "dispatch_skill", crash_before_dispatch)
        with pytest.raises(SystemExit, match="simulated crash"):
            runner.run_once()
    return job_id


@pytest.mark.parametrize(
    "stage", ["before_intent_removal", "before_index_event", "before_child_dispatch"]
)
def test_crash_between_terminal_and_chain_dispatch_yields_one_child(setup, monkeypatch, stage):
    vault, _, conn, _, engine, runner = setup
    parent_id = crash_parent(setup, monkeypatch, stage)
    record = json.loads(terminal_path(vault, parent_id).read_text())
    derived = jobs.child_job_id(record["attempt_id"], "acquire->daily-topic-digest", 1)
    assert record["transitions"] == [
        {
            "rule_id": "acquire->daily-topic-digest",
            "rule_version": 1,
            "child_id": derived,
            "child_skill": "daily-topic-digest",
        }
    ]
    runner.recover()
    runner.recover()
    assert [path.stem for path in intents(vault)] == [derived]
    assert store.get_job(conn, parent_id).status == "ok"
    children = store.list_jobs(conn, statuses=["queued"])
    assert [child.id for child in children] == [derived]
    assert children[0].source == f"chain:acquire:{parent_id}"
    child_intent = json.loads(intents(vault)[0].read_text())
    assert child_intent["chain"]["parent_attempt_id"] == record["attempt_id"]
    assert runner.run_once() is True
    assert runner.run_once() is False
    assert engine.skills == ["acquire", "daily-topic-digest"]
    assert json.loads(terminal_path(vault, derived).read_text())["chain"] == child_intent["chain"]


@pytest.mark.parametrize("child_state", ["pending_transition", "intent", "terminal"])
def test_replay_twice_after_index_loss_yields_one_child(setup, monkeypatch, child_state):
    vault, settings, conn, registry, engine, runner = setup
    if child_state == "pending_transition":
        parent_id = crash_parent(setup, monkeypatch, "before_child_dispatch")
    else:
        parent_id = submit(setup)
        assert runner.run_once()
    record = json.loads(terminal_path(vault, parent_id).read_text())
    child_id = record["transitions"][0]["child_id"]
    if child_state == "terminal":
        assert runner.run_once()
    conn.close()
    settings.db_path.unlink()
    fresh = connect(settings.db_path)
    try:
        reconcile_from_files(vault, fresh, registry)
        recovered = Runner(fresh, registry, settings, engines={"fake": engine})
        recovered.recover()
        recovered.recover()
        children = [
            job
            for job in store.list_jobs(fresh, statuses=["queued", "ok"])
            if job.skill == "daily-topic-digest"
        ]
        assert [job.id for job in children] == [child_id]
        assert len([path for path in intents(vault) if path.stem == child_id]) == (
            child_state != "terminal"
        )
        if child_state == "terminal":
            assert recovered.run_once() is False
        else:
            assert recovered.run_once()
        assert engine.skills == ["acquire", "daily-topic-digest"]
    finally:
        fresh.close()


def test_db_only_row_without_intent_never_executes(setup):
    vault, _, conn, _, engine, runner = setup
    store.create_job(
        conn, job_id="db-only", skill="sample", args={}, source="api", engine="fake", ts_queued="t0"
    )
    assert runner.run_once() is False
    assert engine.skills == []
    assert store.get_job(conn, "db-only").status == "queued"
    assert not intents(vault)
    assert not list((vault / "system" / "runs").glob("*.json"))


def test_rebuild_never_enqueues_work(setup, monkeypatch):
    vault, settings, conn, _, _, _ = setup
    parent_id = crash_parent(setup, monkeypatch, "before_intent_removal")
    before = {path: path.read_bytes() for path in (vault / "system").rglob("*.json")}

    def forbidden(*args, **kwargs):
        pytest.fail("index rebuild dispatched work")

    monkeypatch.setattr(jobs, "dispatch_skill", forbidden)
    result = reindex(vault, settings.db_path)
    assert result.run_files_seen == 1
    assert store.get_job(conn, parent_id).status == "ok"
    assert before == {path: path.read_bytes() for path in (vault / "system").rglob("*.json")}
    assert not [
        job
        for job in store.list_jobs(conn, statuses=["queued"])
        if job.skill == "daily-topic-digest"
    ]


def test_submission_writes_intent_before_row(setup, monkeypatch):
    vault, _, conn, registry, _, _ = setup
    original = store.create_job
    observed = []

    def fail_index(*args, **kwargs):
        path = vault / "system" / "queue" / f"{kwargs['job_id']}.json"
        observed.append(json.loads(path.read_text()))
        assert store.get_job(conn, kwargs["job_id"]) is None
        raise sqlite3.OperationalError("synthetic index failure")

    with monkeypatch.context() as patch:
        patch.setattr(store, "create_job", fail_index)
        job_id = submit(setup, "sample")
    assert len(observed) == 1 and observed[0]["id"] == job_id
    assert store.get_job(conn, job_id) is None
    reconcile_from_files(vault, conn, registry)
    reconcile_from_files(vault, conn, registry)
    assert [job.id for job in store.list_jobs(conn, statuses=["queued"])] == [job_id]
    assert (
        conn.execute("SELECT count(*) FROM job_events WHERE job_id = ?", (job_id,)).fetchone()[0]
        == 1
    )
    assert store.create_job is original


@pytest.mark.parametrize("existing", ["intent", "terminal", "attempt"])
def test_chain_dispatch_dedupes_from_files(setup, monkeypatch, existing):
    vault, _, conn, registry, _, _ = setup
    child_id = jobs.child_job_id("parent-attempt", "acquire->daily-topic-digest", 1)
    if existing == "intent":
        write_intent(
            vault,
            job_id=child_id,
            skill="daily-topic-digest",
            args={},
            ts="t0",
            source="chain:acquire:parent",
        )
        path = vault / "system" / "queue" / f"{child_id}.json"
    else:
        path = (
            terminal_path(vault, child_id)
            if existing == "terminal"
            else vault / "system" / "runs" / f"{child_id}.attempt-1.json"
        )
        write_record(path, {"id": child_id, "skill": "daily-topic-digest", "status": "ok"})
    before = path.read_bytes()

    def forbidden(*args, **kwargs):
        pytest.fail("deduped dispatch attempted an index insert")

    monkeypatch.setattr(store, "create_job", forbidden)
    assert (
        jobs.dispatch_skill(
            conn, registry, vault, "daily-topic-digest", {}, "chain:acquire:parent", job_id=child_id
        )[0]
        == child_id
    )
    assert path.read_bytes() == before
    assert store.get_job(conn, child_id) is None
    assert len(intents(vault)) == (existing == "intent")


def test_child_id_is_deterministic():
    child = jobs.child_job_id("parent-attempt", "acquire->daily-topic-digest", 1)
    assert child == jobs.child_job_id("parent-attempt", "acquire->daily-topic-digest", 1)
    assert child != jobs.child_job_id("parent-attempt", "acquire->daily-topic-digest", 2)
    assert child != jobs.child_job_id("other-attempt", "acquire->daily-topic-digest", 1)
    assert child != jobs.child_job_id("parent-attempt", "other-rule", 1)


@pytest.mark.parametrize("record_kind", ["terminal", "intent"])
def test_child_record_path_does_not_glob_when_record_exists(tmp_path, monkeypatch, record_kind):
    directory = tmp_path / "system" / ("runs" if record_kind == "terminal" else "queue")
    directory.mkdir(parents=True)
    path = directory / "child.json"
    path.write_text("{}")

    def unexpected_glob(*args, **kwargs):
        pytest.fail("attempt glob called despite existing child record")

    monkeypatch.setattr(Path, "glob", unexpected_glob)
    assert jobs.child_record_path(tmp_path, "child") == path


def test_db_only_legacy_chain_owner_does_not_discard_acknowledged_intent(setup):
    vault, settings, conn, registry, engine, runner = setup
    source = "chain:acquire:parent"
    store.create_job(
        conn,
        job_id="legacy-child",
        skill="daily-topic-digest",
        args={},
        source=source,
        engine="fake",
        ts_queued="t0",
    )
    child_id = jobs.child_job_id("attempt", "acquire->daily-topic-digest", 1)
    accepted, _ = jobs.dispatch_skill(
        conn, registry, vault, "daily-topic-digest", {}, source, job_id=child_id
    )
    assert accepted == child_id
    assert [path.stem for path in intents(vault)] == [child_id]
    assert runner.run_once() is False
    assert engine.skills == []
    reindex(vault, settings.db_path)
    assert store.get_job(conn, "legacy-child") is None
    assert runner.run_once() is True
    assert engine.skills == ["daily-topic-digest"]


def prepare_projection_collision(setup, record_kind):
    vault, _, conn, registry, engine, runner = setup
    source = "chain:acquire:parent"
    store.create_job(
        conn,
        job_id="legacy-child",
        skill="daily-topic-digest",
        args={},
        source=source,
        engine="fake",
        ts_queued="t0",
    )
    child_id = jobs.child_job_id("attempt", "acquire->daily-topic-digest", 1)
    jobs.dispatch_skill(conn, registry, vault, "daily-topic-digest", {}, source, job_id=child_id)
    collision_path = vault / "system" / "queue" / f"{child_id}.json"
    if record_kind == "run":
        collision_path.unlink()
        collision_path = terminal_path(vault, child_id)
        write_record(
            collision_path,
            {
                "id": child_id,
                "skill": "daily-topic-digest",
                "args": {},
                "source": source,
                "ts_queued": "t1",
                "ts_started": "t2",
                "status": "ok",
                "ts_completed": "t3",
                "attempt_id": "child-attempt",
                "attempt_ids": ["child-attempt"],
                "exit_code": 0,
                "summary": "complete",
                "deliverable_path": None,
                "completion_evidence": {
                    "engine": "fake",
                    "exit_code": 0,
                    "summary": "complete",
                    "deliverable_path": None,
                },
                "transitions": [],
            },
        )
    write_intent(vault, job_id="zz-unrelated", skill="sample", args={}, ts="t3", source="api")

    return child_id, collision_path


@pytest.mark.parametrize("record_kind", ["queue", "run"])
@pytest.mark.parametrize("projector", ["reconcile", "recovery"])
def test_reconcile_skips_chain_source_collision_and_continues(
    setup, caplog, record_kind, projector
):
    vault, _, conn, registry, engine, runner = setup
    child_id, collision_path = prepare_projection_collision(setup, record_kind)
    if projector == "reconcile":
        result = reconcile_from_files(vault, conn, registry)
        assert result.skipped == 1
    else:
        runner.recover()

    assert engine.skills == []
    assert store.get_job(conn, "zz-unrelated").status == "queued"
    assert store.get_job(conn, child_id) is None
    assert collision_path.name in caplog.text
    assert "legacy-child" in caplog.text
    assert not conn.in_transaction
    assert (
        conn.execute("SELECT COUNT(*) FROM job_events WHERE job_id = ?", (child_id,)).fetchone()[0]
        == 0
    )


def test_legacy_event_child_identity_uses_parent_job_id(setup):
    vault, _, conn, registry, _, _ = setup
    parent_id = submit(setup)
    for _ in range(2):
        jobs.apply_event_and_chain(conn, registry, vault, job_id=parent_id, status="ok", ts="t1")
    expected = jobs.child_job_id(parent_id, "acquire->daily-topic-digest", 1)
    children = store.list_jobs(conn, statuses=["queued"])
    assert [child.id for child in children] == [expected]


def test_http_replay_after_index_loss_uses_recorded_attempt_identity(setup):
    vault, settings, conn, registry, _, runner = setup
    parent_id = submit(setup)
    assert runner.run_once()
    record = json.loads(terminal_path(vault, parent_id).read_text())
    expected = record["transitions"][0]["child_id"]
    reindex(vault, settings.db_path)
    jobs.apply_event_and_chain(conn, registry, vault, job_id=parent_id, status="ok", ts="t1")
    runner.recover()
    assert [path.stem for path in intents(vault)] == [expected]


def test_submission_acknowledges_durable_intent_when_index_write_fails(setup, monkeypatch):
    vault, settings, conn, registry, _, _ = setup

    def fail_index(*args, **kwargs):
        raise sqlite3.OperationalError("synthetic index failure")

    monkeypatch.setattr(store, "create_job", fail_index)
    response = jobs.submit_job(jobs.JobCreate(skill="sample"), conn, registry, settings)
    assert response["status"] == "queued"
    assert [path.stem for path in intents(vault)] == [response["id"]]
    assert store.get_job(conn, response["id"]) is None


def test_runner_projects_child_intent_after_index_failure_without_restart(setup, monkeypatch):
    vault, _, conn, _, engine, runner = setup
    parent_id = submit(setup)
    original = store.create_job

    def fail_child(conn, **kwargs):
        if kwargs["skill"] == "daily-topic-digest":
            raise sqlite3.OperationalError("synthetic child index failure")
        return original(conn, **kwargs)

    monkeypatch.setattr(store, "create_job", fail_child)
    assert runner.run_once() is True
    assert store.get_job(conn, parent_id).status == "ok"
    child_id = json.loads(terminal_path(vault, parent_id).read_text())["transitions"][0]["child_id"]
    assert (vault / "system" / "queue" / f"{child_id}.json").exists()
    assert store.get_job(conn, child_id) is None

    assert runner.run_once() is True
    assert runner.run_once() is False
    assert engine.skills == ["acquire", "daily-topic-digest"]
    assert store.get_job(conn, child_id).status == "ok"


def test_submission_rolls_back_partial_index_write(setup, monkeypatch):
    vault, _, conn, registry, _, _ = setup
    execute = conn.execute

    def failing_execute(sql, parameters=()):
        result = execute(sql, parameters)
        if sql.startswith("INSERT OR IGNORE INTO job_events"):
            raise sqlite3.OperationalError("synthetic partial index failure")
        return result

    monkeypatch.setattr(conn, "execute", failing_execute)
    accepted, _ = jobs.dispatch_skill(conn, registry, vault, "sample", {}, "api")
    assert (vault / "system" / "queue" / f"{accepted}.json").exists()
    assert not conn.in_transaction
    assert (
        conn.execute("SELECT COUNT(*) FROM job_events WHERE job_id = ?", (accepted,)).fetchone()[0]
        == 0
    )


def test_recovery_is_idempotent(setup, monkeypatch):
    vault, _, conn, _, engine, runner = setup
    crash_parent(setup, monkeypatch, "before_intent_removal")
    runner.recover()
    before = {path: path.read_bytes() for path in (vault / "system").rglob("*.json")}
    rows = [tuple(row) for row in conn.execute("SELECT * FROM jobs ORDER BY id")]
    events = [tuple(row) for row in conn.execute("SELECT * FROM job_events ORDER BY id")]
    runner.recover()
    assert before == {path: path.read_bytes() for path in (vault / "system").rglob("*.json")}
    assert rows == [tuple(row) for row in conn.execute("SELECT * FROM jobs ORDER BY id")]
    assert events == [tuple(row) for row in conn.execute("SELECT * FROM job_events ORDER BY id")]
    assert engine.skills == ["acquire"]


def test_recovery_runs_before_claim_and_at_daemon_startup(setup, monkeypatch):
    vault, _, conn, _, engine, runner = setup
    parent_id = crash_parent(setup, monkeypatch, "before_intent_removal")
    monkeypatch.setattr(runner, "_install_signal_handlers", lambda: None)

    def stop_before_claim():
        assert store.get_job(conn, parent_id).status == "ok"
        assert len(intents(vault)) == 1
        runner.request_shutdown()
        return False

    monkeypatch.setattr(runner, "run_once", stop_before_claim)
    runner.run_forever()
    assert engine.skills == ["acquire"]


def test_recovery_runs_before_each_claim(setup, monkeypatch):
    vault, _, conn, _, engine, runner = setup
    parent_id = crash_parent(setup, monkeypatch, "before_index_event")
    assert runner.run_once() is True
    assert store.get_job(conn, parent_id).status == "ok"
    assert engine.skills == ["acquire", "daily-topic-digest"]
    assert runner.run_once() is False
    assert not intents(vault)


def test_recovery_replaces_orphan_assessment_with_durable_outcome(setup, monkeypatch):
    _, _, conn, _, _, runner = setup
    parent_id = crash_parent(setup, monkeypatch, "before_index_event")
    store.apply_event(conn, job_id=parent_id, status="orphaned", ts="t1", received_at="t1")
    runner.recover()
    assert store.get_job(conn, parent_id).status == "ok"
    assert len(store.list_jobs(conn, statuses=["queued"])) == 1


def test_recovery_replays_recorded_rule_after_current_rules_change(setup, monkeypatch):
    vault, _, conn, _, _, runner = setup
    parent_id = crash_parent(setup, monkeypatch, "before_child_dispatch")
    recorded = json.loads(terminal_path(vault, parent_id).read_text())["transitions"][0]
    monkeypatch.setattr(jobs, "CHAIN_MAP", {})
    runner.recover()
    child = store.list_jobs(conn, statuses=["queued"])[0]
    assert child.id == recorded["child_id"]
    assert child.skill == recorded["child_skill"]
    assert json.loads(intents(vault)[0].read_text())["chain"]["rule_version"] == 1


def test_recovery_retains_pending_transition_until_child_is_registered(setup, monkeypatch):
    vault, _, conn, registry, _, runner = setup
    parent_id = crash_parent(setup, monkeypatch, "before_child_dispatch")
    expected = json.loads(terminal_path(vault, parent_id).read_text())["transitions"][0]["child_id"]
    original_get = registry.get
    with monkeypatch.context() as patch:
        patch.setattr(
            type(registry),
            "get",
            lambda self, skill_id: (
                None if skill_id == "daily-topic-digest" else original_get(skill_id)
            ),
        )
        runner.recover()
        assert store.get_job(conn, parent_id).status == "ok"
        assert not intents(vault)
    runner.recover()
    assert [path.stem for path in intents(vault)] == [expected]


@pytest.mark.parametrize(
    "damage", ["legacy", "partial", "wrong_child", "bad_version", "error_transition"]
)
def test_recovery_does_not_consume_invalid_or_legacy_terminal(setup, monkeypatch, damage):
    vault, _, conn, _, engine, runner = setup
    parent_id = crash_parent(setup, monkeypatch, "before_intent_removal")
    path = terminal_path(vault, parent_id)
    record = json.loads(path.read_text())
    if damage == "legacy":
        record.pop("attempt_id")
        record.pop("attempt_ids")
        (vault / "system" / "runs" / f"{parent_id}.attempt-1.json").unlink()
    elif damage == "partial":
        record.pop("completion_evidence")
    elif damage == "wrong_child":
        record["transitions"][0]["child_id"] = "../outside"
    elif damage == "bad_version":
        record["transitions"][0]["rule_version"] = True
    else:
        record["status"] = "error"
    path.write_text(json.dumps(record))
    runner.recover()
    assert [path.stem for path in intents(vault)] == [parent_id]
    assert store.get_job(conn, parent_id).status == "running"
    assert engine.skills == ["acquire"]


def test_settle_intents_reports_by_default_and_applies_only_terminal(setup, capsys):
    vault, _, conn, _, _, runner = setup
    ids = {}
    for status in ("queued", "running", "ok", "error", "orphaned", "attempt", "record"):
        job_id = submit(setup, "sample")
        ids[status] = job_id
        if status != "queued":
            store.apply_event(
                conn,
                job_id=job_id,
                status="ok" if status in {"attempt", "record"} else status,
                ts="t1",
                received_at="t1",
            )
        if status == "attempt":
            write_record(
                vault / "system" / "runs" / f"{job_id}.attempt-2.json", {"attempt_id": "held"}
            )
        elif status == "record":
            write_record(
                terminal_path(vault, job_id), {"id": job_id, "skill": "sample", "status": "ok"}
            )
    before = {path: path.read_bytes() for path in (vault / "system").rglob("*.json")}
    assert main(["settle-intents"]) == 0
    report = capsys.readouterr().out
    for status in ("ok", "error"):
        assert ids[status] in report
    for status in ("queued", "running", "orphaned", "attempt", "record"):
        assert ids[status] not in report
    assert before == {path: path.read_bytes() for path in (vault / "system").rglob("*.json")}
    assert main(["settle-intents", "--apply"]) == 0
    assert {path.stem for path in intents(vault)} == {
        ids[status] for status in ("queued", "running", "orphaned", "attempt", "record")
    }
    for status in ("ok", "error"):
        record = json.loads(terminal_path(vault, ids[status]).read_text())
        assert record["settled_from_index"] is True
        assert record["status"] == status
        assert "attempt_id" not in record
    assert settle_intents(vault, conn, apply=True) == []
    with runner._runner_lock():
        with pytest.raises(BlockingIOError):
            settle_intents(vault, conn, apply=True)


def test_ordinary_submissions_always_create_new_jobs(setup):
    first = submit(setup, "sample")
    second = submit(setup, "sample")
    assert first != second
    assert len(intents(setup[0])) == 2


def test_concurrent_chain_dispatch_creates_one_intent_and_row(setup):
    vault, _, conn, registry, _, _ = setup
    child_id = jobs.child_job_id("attempt", "acquire->daily-topic-digest", 1)
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(
            executor.map(
                lambda _: jobs.dispatch_skill(
                    conn,
                    registry,
                    vault,
                    "daily-topic-digest",
                    {},
                    "chain:acquire:parent",
                    job_id=child_id,
                )[0],
                range(16),
            )
        )
    assert set(results) == {child_id}
    assert [path.stem for path in intents(vault)] == [child_id]
    assert [job.id for job in store.list_jobs(conn, statuses=["queued"])] == [child_id]


def test_chain_duplicate_is_durable_before_acknowledgment(setup, monkeypatch):
    vault, _, conn, registry, _, _ = setup
    child_id = jobs.child_job_id("attempt", "acquire->daily-topic-digest", 1)
    linked = threading.Event()
    finish = threading.Event()
    publisher_ident = []
    acknowledged_syncs = []
    results = []
    original_fsync = durable.os.fsync

    def fsync(fd):
        is_directory = stat.S_ISDIR(os.fstat(fd).st_mode)
        if threading.get_ident() == publisher_ident[0]:
            if is_directory and (vault / "system" / "queue" / f"{child_id}.json").exists():
                linked.set()
                assert finish.wait(2)
        else:
            acknowledged_syncs.append("directory" if is_directory else "file")
        original_fsync(fd)

    def dispatch():
        return jobs.dispatch_skill(
            conn, registry, vault, "daily-topic-digest", {}, "chain:acquire:parent", job_id=child_id
        )[0]

    def publish():
        publisher_ident.append(threading.get_ident())
        results.append(dispatch())

    monkeypatch.setattr(durable.os, "fsync", fsync)
    worker = threading.Thread(target=publish)
    worker.start()
    try:
        assert linked.wait(2)
        assert dispatch() == child_id
        assert acknowledged_syncs == ["file", "directory", "directory"]
        assert store.get_job(conn, child_id) is None
    finally:
        finish.set()
        worker.join(timeout=3)
    assert not worker.is_alive()
    assert results == [child_id]
    assert [path.stem for path in intents(vault)] == [child_id]
    assert [job.id for job in store.list_jobs(conn, statuses=["queued"])] == [child_id]


def test_intent_publication_is_atomic_and_fsynced(tmp_path, monkeypatch):
    calls = []
    original_fsync = durable.os.fsync
    original_replace = durable.os.replace
    path = tmp_path / "system" / "queue" / "job.json"
    monkeypatch.setattr(durable, "_durable_directories", set())
    path.parent.mkdir(parents=True)

    def fsync(fd):
        calls.append("fsync")
        return original_fsync(fd)

    def replace(source, target):
        assert not Path(target).exists()
        assert json.loads(Path(source).read_text())["id"] == "job"
        assert calls == ["fsync", "fsync"]
        calls.append("publish")
        return original_replace(source, target)

    monkeypatch.setattr(durable.os, "fsync", fsync)
    monkeypatch.setattr(durable.os, "replace", replace)
    write_intent(tmp_path, job_id="job", skill="sample", args={}, ts="t0", source="api")
    assert calls == ["fsync", "fsync", "publish", "fsync"]
    assert json.loads(path.read_text())["id"] == "job"
    assert not list(path.parent.glob(".*.tmp"))


def test_exclusive_intent_does_not_replace_existing_payload(tmp_path):
    path = write_intent(
        tmp_path,
        job_id="job",
        skill="sample",
        args={"first": True},
        ts="t0",
        source="chain:sample:parent",
        exclusive=True,
    )
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        write_intent(
            tmp_path,
            job_id="job",
            skill="sample",
            args={},
            ts="t1",
            source="chain:sample:parent",
            exclusive=True,
        )
    assert path.read_bytes() == before
    assert not list(path.parent.glob(".*.tmp"))


def test_submission_failure_does_not_rollback_concurrent_insert(setup, monkeypatch):
    vault, _, conn, registry, _, _ = setup
    inserted = threading.Event()
    finish = threading.Event()
    original_create = store.create_job
    original_get = store.get_job
    execute = conn.execute

    def pausing_execute(sql, parameters=()):
        result = execute(sql, parameters)
        if sql.strip().startswith("INSERT INTO jobs"):
            inserted.set()
            assert finish.wait(3)
        return result

    def create(connection, **kwargs):
        if kwargs["job_id"] == "failure":
            assert inserted.wait(3)
            raise sqlite3.OperationalError("database is locked")
        return original_create(connection, **kwargs)

    def get(connection, job_id):
        if job_id == "failure":
            finish.set()
        return original_get(connection, job_id)

    monkeypatch.setattr(store, "create_job", create)
    monkeypatch.setattr(store, "get_job", get)
    monkeypatch.setattr(conn, "execute", pausing_execute)
    with ThreadPoolExecutor(max_workers=2) as pool:
        successful = pool.submit(
            jobs.dispatch_skill,
            conn,
            registry,
            vault,
            "sample",
            {},
            "api",
            job_id="success",
        )
        try:
            assert (
                jobs.dispatch_skill(conn, registry, vault, "sample", {}, "api", job_id="failure")[0]
                == "failure"
            )
        finally:
            finish.set()
        assert successful.result(timeout=3)[0] == "success"
    assert store.get_job(conn, "success").status == "queued"


@pytest.mark.parametrize("operation", ["create", "event"])
def test_store_failure_rolls_back_partial_write(setup, operation, monkeypatch):
    _, _, conn, _, _, _ = setup
    execute = conn.execute

    def failing_execute(sql, parameters=()):
        result = execute(sql, parameters)
        if sql.strip().startswith("INSERT INTO jobs"):
            raise sqlite3.OperationalError("synthetic failure after insert")
        return result

    monkeypatch.setattr(conn, "execute", failing_execute)
    with pytest.raises(sqlite3.OperationalError):
        if operation == "create":
            store.create_job(
                conn,
                job_id="partial",
                skill="sample",
                args={},
                source="api",
                engine="fake",
                ts_queued="t0",
            )
        else:
            store.apply_event(
                conn,
                job_id="partial",
                skill="sample",
                args={},
                source="api",
                status="queued",
                ts="t0",
                received_at="t0",
            )
    assert not conn.in_transaction
    assert store.get_job(conn, "partial") is None
    assert conn.execute("SELECT COUNT(*) FROM job_events").fetchone()[0] == 0


def test_submission_recovery_index_race_has_no_error(setup, monkeypatch, caplog):
    vault, settings, conn, registry, _, _ = setup
    original_create = store.create_job

    def create(connection, **kwargs):
        fresh = connect(settings.db_path)
        try:
            from vaultos.runner.recovery import recover_terminal_records

            recover_terminal_records(fresh, registry, vault)
        finally:
            fresh.close()
        return original_create(connection, **kwargs)

    monkeypatch.setattr(store, "create_job", create)
    job_id = submit(setup, "sample")
    assert store.get_job(conn, job_id).status == "queued"
    assert not [record for record in caplog.records if record.levelname == "ERROR"]


@pytest.mark.parametrize(
    "damage",
    [
        {"skill": ["x"]},
        {"ts": {"bad": "time"}},
        {"id": ["bad"]},
        {"id": ""},
        {"skill": ""},
        {"ts": ""},
        {"args": []},
        {"source": {}},
    ],
)
def test_runner_skips_malformed_intent_and_runs_valid_job(setup, caplog, damage):
    vault, _, conn, _, engine, runner = setup
    payload = {"id": "bad", "skill": "sample", "ts": "t0", **damage}
    (vault / "system" / "queue" / "bad.json").write_text(json.dumps(payload))
    valid_id = submit(setup, "sample")
    assert runner.run_once() is True
    assert engine.skills == ["sample"]
    assert store.get_job(conn, valid_id).status == "ok"
    assert store.get_job(conn, "bad") is None
    assert "skipping invalid intent file bad.json" in caplog.text


def test_runner_continues_after_intent_projection_sqlite_error(setup, monkeypatch, caplog):
    vault, _, conn, _, engine, runner = setup
    write_intent(vault, job_id="bad", skill="sample", args={}, ts="t0", source="api")
    valid_id = submit(setup, "sample")
    original_apply = store.apply_event

    def apply(connection, **kwargs):
        if kwargs["job_id"] == "bad":
            raise sqlite3.OperationalError("synthetic projection failure")
        return original_apply(connection, **kwargs)

    monkeypatch.setattr(store, "apply_event", apply)
    assert runner.run_once() is True
    assert engine.skills == ["sample"]
    assert store.get_job(conn, valid_id).status == "ok"
    assert "bad.json" in caplog.text
    assert not conn.in_transaction


@pytest.mark.parametrize("record_kind", ["queue", "run"])
def test_runner_reports_projection_collision_once(setup, caplog, record_kind):
    prepare_projection_collision(setup, record_kind)
    runner = setup[-1]
    runner.run_once()
    runner.run_once()
    runner.run_once()
    warnings = [r for r in caplog.records if "projection: skipping file" in r.getMessage()]
    assert len(warnings) == 1


@pytest.mark.parametrize("directory", ["queue", "runs"])
def test_runner_reports_invalid_file_once(setup, caplog, directory):
    vault, _, _, _, _, runner = setup
    (vault / "system" / directory / "bad.json").write_text("{")
    for _ in range(3):
        assert runner.run_once() is False
    warnings = [r for r in caplog.records if "skipping invalid" in r.getMessage()]
    assert len(warnings) == 1


@pytest.mark.parametrize("directory", ["queue", "runs"])
def test_recovery_reports_new_failure_after_success(setup, caplog, directory):
    from vaultos.runner.recovery import recover_terminal_records

    vault, _, conn, registry, _, runner = setup
    job_id = submit(setup, "sample")
    if directory == "runs":
        assert runner.run_once() is True
    path = vault / "system" / directory / f"{job_id}.json"
    valid = path.read_text()
    path.write_text("{")
    reported = set()
    recover_terminal_records(conn, registry, vault, reported_skips=reported)
    path.write_text(valid)
    if directory == "queue":
        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        conn.commit()
    recover_terminal_records(conn, registry, vault, reported_skips=reported)
    assert store.get_job(conn, job_id) is not None
    path.write_text("[]")
    recover_terminal_records(conn, registry, vault, reported_skips=reported)
    warnings = [r for r in caplog.records if "skipping invalid" in r.getMessage()]
    assert len(warnings) == 2


def test_runner_continues_after_terminal_projection_sqlite_error(setup, monkeypatch, caplog):
    vault, _, conn, _, engine, runner = setup
    parent_id = crash_parent(setup, monkeypatch, "before_index_event")
    valid_id = submit(setup, "sample")
    original_apply = store.apply_event

    def apply(connection, **kwargs):
        if kwargs["job_id"] == parent_id:
            raise sqlite3.OperationalError("synthetic terminal projection failure")
        return original_apply(connection, **kwargs)

    monkeypatch.setattr(store, "apply_event", apply)
    assert runner.run_once() is True
    assert engine.skills == ["acquire", "sample"]
    assert store.get_job(conn, valid_id).status == "ok"
    assert f"{parent_id}.json" in caplog.text
