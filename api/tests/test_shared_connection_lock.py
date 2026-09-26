import sqlite3
import threading
from contextlib import closing

import pytest

from vaultos import cli, seen
from vaultos.db import conn as db
from vaultos.jobs import store as jobs
from vaultos.modules.finance import store as finance


def test_one_lock_per_connection_and_plain_connections_rejected(tmp_path):
    with closing(db.connect(tmp_path / "first.db")) as first:
        with closing(db.connect(tmp_path / "second.db")) as second:
            assert db.connection_lock(first) is db.connection_lock(first)
            assert db.connection_lock(first) is not db.connection_lock(second)
            with closing(sqlite3.connect(tmp_path / "plain.db")) as plain:
                with pytest.raises(TypeError):
                    db.connection_lock(plain)
            lock = db.connection_lock(first)
            assert lock.acquire(blocking=False)
            try:
                assert not lock.acquire(blocking=False)
            finally:
                lock.release()


@pytest.mark.parametrize("writer", ["jobs", "seen", "reindex"])
def test_failed_finance_import_is_not_committed_by_another_writer(
    tmp_path, tmp_vault, monkeypatch, writer
):
    conn = db.connect(tmp_path / "shared.db")
    finance.create_account(
        conn,
        account_id="account",
        nickname="Checking",
        institution=None,
        account_type="checking",
        last_four=None,
        balance_cents=0,
        is_primary=True,
        created_at="2026-09-01T00:00:00Z",
    )
    finance.create_plan_item(
        conn,
        item_id="plan",
        name="Utility",
        estimate_cents=-1000,
        plan_type="Utilities",
        payee=None,
        day_of_month=1,
        cadence="dated",
        cadence_unit="month",
        cadence_frequency=1,
        anchor_period=None,
        account_id="account",
        verified=True,
        is_catch_all=False,
        match_text=["UTILITY"],
    )
    inserted = threading.Event()
    release_finance = threading.Event()
    writer_started = threading.Event()
    writer_finished = threading.Event()
    finance_errors = []
    writer_errors = []

    def fail_after_insert(*args):
        inserted.set()
        if not release_finance.wait(timeout=5):
            raise RuntimeError("finance release timed out")
        raise sqlite3.OperationalError("injected attribution failure")

    monkeypatch.setattr(finance, "_attribute_transaction_to_planned_posting", fail_after_insert)

    def import_rows():
        try:
            finance.commit_import(
                conn,
                import_id="import",
                account_id="account",
                filename="statement.csv",
                imported_at="2026-09-01T00:00:00Z",
                rows_skipped=0,
                rows_to_add=[
                    {
                        "date": "2026-09-01",
                        "merchant_raw": "UTILITY",
                        "amount_cents": -1000,
                        "dedupe_hash": "unique-row",
                    }
                ],
            )
        except BaseException as exc:
            finance_errors.append(exc)

    # reindex normally owns a fresh connection. Supply this shared connection to
    # exercise its write boundary; defer close until both threads have finished.
    if writer == "reindex":
        monkeypatch.setattr(cli, "connect", lambda path: conn)
        monkeypatch.setattr(type(conn), "close", lambda self: None)

    def write_other_store():
        writer_started.set()
        try:
            if writer == "jobs":
                jobs.create_job(
                    conn,
                    job_id="job",
                    skill="metrics-pull",
                    args={},
                    source="api",
                    engine="script",
                    ts_queued="2026-09-01T00:00:00Z",
                )
            elif writer == "seen":
                seen.mark_seen(conn, item_type="job", item_id="job")
            else:
                cli.reindex(tmp_vault, tmp_path / "shared.db")
        except BaseException as exc:
            writer_errors.append(exc)
        finally:
            writer_finished.set()

    finance_thread = threading.Thread(target=import_rows, daemon=True)
    writer_thread = threading.Thread(target=write_other_store, daemon=True)
    finance_thread.start()
    try:
        assert inserted.wait(timeout=5), "finance did not reach its uncommitted insert"
        writer_thread.start()
        assert writer_started.wait(timeout=5), "other writer did not start"
        finished_before_rollback = writer_finished.wait(timeout=0.5)
    finally:
        release_finance.set()
        finance_thread.join(timeout=5)
        if writer_thread.ident is not None:
            writer_thread.join(timeout=5)

    assert not finance_thread.is_alive(), "finance thread did not finish"
    assert not writer_thread.is_alive(), "other writer deadlocked"
    try:
        assert not writer_errors
        assert len(finance_errors) == 1
        assert isinstance(finance_errors[0], sqlite3.OperationalError)
        assert finance.list_transactions(conn) == []
        assert finance.list_all_imports(conn) == []
        assert not finished_before_rollback, "other writer bypassed the connection lock"
        if writer == "jobs":
            assert jobs.get_job(conn, "job").status == "queued"
        elif writer == "seen":
            assert seen.is_seen(conn, item_type="job", item_id="job")
    finally:
        sqlite3.Connection.close(conn)
