import json

import pytest

from vaultos.cli import ReindexRefused, reindex
from vaultos.db.conn import connect
from vaultos.jobs import store
from vaultos.pidfile import write_pid


def test_reindex_refuses_when_spine_alive(tmp_vault, tmp_path):
    db_path = tmp_path / "vaultos.db"
    connect(db_path)  # ensure schema exists first
    write_pid(db_path)  # writes this test process's own (alive) pid

    with pytest.raises(ReindexRefused):
        reindex(tmp_vault, db_path)


def test_reindex_rebuilds_db_from_files(tmp_vault, tmp_path):
    db_path = tmp_path / "vaultos.db"
    (tmp_vault / "system" / "queue" / "a.json").write_text(
        json.dumps({"id": "a", "skill": "ai-wire", "args": {}, "ts": "t0", "source": "api"})
    )

    result = reindex(tmp_vault, db_path)

    assert result.queue_files_seen == 1
    conn = connect(db_path)
    job = store.get_job(conn, "a")
    assert job is not None
    assert job.status == "queued"


def test_reindex_wipes_stale_rows_not_present_in_files(tmp_vault, tmp_path):
    db_path = tmp_path / "vaultos.db"
    conn = connect(db_path)
    store.create_job(
        conn,
        job_id="stale",
        skill="ai-wire",
        args={},
        source="api",
        engine="claude",
        ts_queued="t0",
    )
    conn.close()

    # No files on disk for "stale" at all -- reindex must wipe it, not preserve it.
    reindex(tmp_vault, db_path)

    conn = connect(db_path)
    assert store.get_job(conn, "stale") is None


def test_reindex_does_not_wipe_db_if_registry_fails_to_load(tmp_vault, tmp_path):
    db_path = tmp_path / "vaultos.db"
    conn = connect(db_path)
    store.create_job(
        conn,
        job_id="preserve-me",
        skill="ai-wire",
        args={},
        source="api",
        engine="claude",
        ts_queued="t0",
    )
    conn.close()

    # Break the registry so load_registry() raises -- reindex must not have
    # already committed the DELETE by the time this happens.
    (tmp_vault / "system" / "skills.json").unlink()

    with pytest.raises(Exception):
        reindex(tmp_vault, db_path)

    conn = connect(db_path)
    assert store.get_job(conn, "preserve-me") is not None


def test_reindex_preserves_applied_migrations(tmp_vault, tmp_path):
    db_path = tmp_path / "vaultos.db"
    conn = connect(db_path)
    version_before = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()

    reindex(tmp_vault, db_path)

    conn = connect(db_path)
    version_after = conn.execute("PRAGMA user_version").fetchone()[0]
    assert version_after == version_before
    assert version_after > 0
