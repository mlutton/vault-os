import json

from vaultos.db.conn import connect
from vaultos.jobs import store
from vaultos.jobs.reconcile import reconcile_from_files
from vaultos.registry import load_registry


def _write_run(
    tmp_vault,
    job_id,
    skill,
    source,
    ts_queued,
    ts_started,
    ts_completed,
    status,
    exit_code,
    summary,
    md_path,
    deliverable_path,
):
    record = {
        "id": job_id,
        "skill": skill,
        "args": {},
        "source": source,
        "ts_queued": ts_queued,
        "ts_started": ts_started,
        "ts_completed": ts_completed,
        "status": status,
        "exit_code": exit_code,
        "summary": summary,
        "md_path": md_path,
        "log_path": md_path,
        "deliverable_path": deliverable_path,
    }
    (tmp_vault / "system" / "runs" / f"{job_id}.json").write_text(json.dumps(record))


def test_reindex_output_matches_incrementally_built_database(tmp_vault, tmp_path):
    registry = load_registry(tmp_vault)

    # --- Build one DB incrementally, the way live events really arrive. ---
    incremental_conn = connect(tmp_path / "incremental.db")
    store.create_job(
        incremental_conn,
        job_id="a",
        skill="metrics-pull",
        args={},
        source="api",
        engine="claude",
        ts_queued="t0",
    )
    store.apply_event(
        incremental_conn,
        job_id="a",
        status="running",
        ts="t1",
        received_at="t1",
        pid=100,
        md_path="system/runs/a.md",
        deliverable_path="inbox/a.md",
    )
    store.apply_event(
        incremental_conn,
        job_id="a",
        status="ok",
        ts="t2",
        received_at="t2",
        exit_code=0,
        summary="done a",
    )

    store.create_job(
        incremental_conn,
        job_id="b",
        skill="ai-wire",
        args={},
        source="voice",
        engine="claude",
        ts_queued="t3",
    )

    store.create_job(
        incremental_conn,
        job_id="c",
        skill="metrics-pull",
        args={},
        source="api",
        engine="claude",
        ts_queued="t4",
    )
    store.apply_event(
        incremental_conn,
        job_id="c",
        status="running",
        ts="t5",
        received_at="t5",
        pid=101,
        md_path="system/runs/c.md",
    )
    store.apply_event(
        incremental_conn,
        job_id="c",
        status="error",
        ts="t6",
        received_at="t6",
        exit_code=1,
        summary="boom c",
    )

    # --- Write the FILES that reflect this same end state, matching what the real
    # system leaves on disk: "b" never ran, so its queue file is still there; "a" and
    # "c" finished, so their queue files are gone (runner.js deletes them on completion)
    # and their run records exist instead. ---
    (tmp_vault / "system" / "queue" / "b.json").write_text(
        json.dumps({"id": "b", "skill": "ai-wire", "args": {}, "ts": "t3", "source": "voice"})
    )
    _write_run(
        tmp_vault,
        "a",
        "metrics-pull",
        "api",
        "t0",
        "t1",
        "t2",
        "ok",
        0,
        "done a",
        "system/runs/a.md",
        "inbox/a.md",
    )
    _write_run(
        tmp_vault,
        "c",
        "metrics-pull",
        "api",
        "t4",
        "t5",
        "t6",
        "error",
        1,
        "boom c",
        "system/runs/c.md",
        None,
    )

    # --- Reindex a fresh DB purely from those files. ---
    reindexed_conn = connect(tmp_path / "reindexed.db")
    reconcile_from_files(tmp_vault, reindexed_conn, registry)

    for job_id in ("a", "b", "c"):
        inc = store.get_job(incremental_conn, job_id)
        rei = store.get_job(reindexed_conn, job_id)
        assert inc is not None and rei is not None, job_id
        assert inc.status == rei.status, job_id
        assert inc.skill == rei.skill, job_id
        assert inc.source == rei.source, job_id
        assert inc.ts_queued == rei.ts_queued, job_id
        assert inc.ts_started == rei.ts_started, job_id
        assert inc.ts_completed == rei.ts_completed, job_id
        assert inc.exit_code == rei.exit_code, job_id
        assert inc.summary == rei.summary, job_id
        assert inc.deliverables == rei.deliverables, job_id
        # runner_pid is deliberately excluded: it's ephemeral operational metadata
        # runner.js reports live but never persists to a file, so a reindexed job
        # legitimately has no way to recover it -- that's not a divergence bug.
