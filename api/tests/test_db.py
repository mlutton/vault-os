import sqlite3

import pytest

from vaultos.db.conn import connect, migrate


def test_connect_creates_schema_and_enables_wal(tmp_path):
    db_path = tmp_path / "sub" / "vaultos.db"
    conn = connect(db_path)

    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"

    tables = {
        row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"jobs", "job_events", "seen_items"} <= tables
    assert {"account", "column_mapping", "plan_item", "txn", "plan_period", "import"} <= tables
    assert {"finance_settings", "balance_adjustment", "planned_posting"} <= tables

    # ticket vault-os-api#18: NULL until first use -- store.get_open_period() lazily
    # establishes it, migration time never guesses "today".
    open_period = conn.execute("SELECT open_period FROM finance_settings WHERE id = 1").fetchone()[0]
    assert open_period is None

    version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == 14


def test_connect_is_idempotent(tmp_path):
    db_path = tmp_path / "vaultos.db"
    connect(db_path)
    conn2 = connect(db_path)
    version = conn2.execute("PRAGMA user_version").fetchone()[0]
    assert version == 14


def test_job_events_unique_constraint(tmp_path):
    conn = connect(tmp_path / "vaultos.db")
    conn.execute(
        "INSERT INTO jobs (id, skill, args, source, status, last_event_ts) "
        "VALUES ('j1', 'metrics-pull', '{}', 'api', 'queued', 't0')"
    )
    conn.execute(
        "INSERT INTO job_events (job_id, status, ts, received_at) VALUES ('j1', 'queued', 't0', 't0')"
    )
    conn.commit()
    import sqlite3
    import pytest

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO job_events (job_id, status, ts, received_at) VALUES ('j1', 'queued', 't0', 't0-again')"
        )


def test_jobs_chain_source_unique_constraint(tmp_path):
    conn = connect(tmp_path / "vaultos.db")
    conn.execute(
        "INSERT INTO jobs (id, skill, args, source, status, last_event_ts) "
        "VALUES ('j1', 'daily-topic-digest', '{}', 'chain:acquire:parent-1', 'queued', 't0')"
    )
    conn.commit()
    import sqlite3
    import pytest

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO jobs (id, skill, args, source, status, last_event_ts) "
            "VALUES ('j2', 'daily-topic-digest', '{}', 'chain:acquire:parent-1', 'queued', 't1')"
        )

    # Ordinary (non-chain) sources are never constrained.
    conn.execute(
        "INSERT INTO jobs (id, skill, args, source, status, last_event_ts) "
        "VALUES ('j3', 'metrics-pull', '{}', 'api', 'queued', 't2')"
    )
    conn.execute(
        "INSERT INTO jobs (id, skill, args, source, status, last_event_ts) "
        "VALUES ('j4', 'metrics-pull', '{}', 'api', 'queued', 't3')"
    )


def test_migration_0003_backfills_colliding_legacy_chain_sources(tmp_path):
    # Simulate a real pre-existing DB (schema version 2) with two pairs of
    # jobs sharing the same legacy "chain:{skill}" source -- this is exactly
    # what production had when migration 0003 first ran, and it must not
    # fail startup or lose either row.
    db_path = tmp_path / "vaultos.db"
    conn = connect(db_path)
    conn.execute("PRAGMA user_version = 2")
    conn.execute("DROP INDEX jobs_chain_source")
    # A real version-2 DB predates migrations 0004-0014 too -- drop what they
    # created so re-running migrate() below recreates them fresh instead of colliding.
    for table in ("account", "column_mapping", "plan_item", "txn", "plan_period", "import",
                  "finance_settings", "balance_adjustment", "planned_posting"):
        conn.execute(f"DROP TABLE {table}")
    conn.executescript(
        """
        INSERT INTO jobs (id, skill, args, source, status, last_event_ts) VALUES
          ('j1', 'daily-topic-digest', '{}', 'chain:acquire', 'ok', 't0'),
          ('j2', 'daily-topic-digest', '{}', 'chain:acquire', 'ok', 't1'),
          ('j3', 'research-into-draft', '{}', 'chain:deep-research', 'ok', 't2');
        """
    )
    conn.commit()

    migrate(conn)  # re-runs migrations 0003-0014 against pre-existing colliding rows

    assert conn.execute("PRAGMA user_version").fetchone()[0] == 14
    sources = {row["id"]: row["source"] for row in conn.execute("SELECT id, source FROM jobs")}
    assert sources["j1"] == "chain:acquire:legacy-j1"
    assert sources["j2"] == "chain:acquire:legacy-j2"
    assert sources["j3"] == "chain:deep-research:legacy-j3"

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO jobs (id, skill, args, source, status, last_event_ts) "
            "VALUES ('j4', 'daily-topic-digest', '{}', 'chain:acquire:legacy-j1', 'queued', 't3')"
        )


def test_migration_0008_backfills_kind_and_reset_period(tmp_path):
    # Simulate a real pre-0008 DB (schema version 7, post-ADR-0018) with one plan_item
    # of each pre-existing cadence shape -- exactly what production had (25 "dated"
    # items plus whatever spread items existed) when migration 0008 first runs.
    # connect() already migrated fully to the latest version -- drop everything 0008
    # through 0014 add and roll user_version back to 7 so migrate() below has all of
    # those pending, against a table shaped exactly like a real pre-0008 production DB.
    conn = connect(tmp_path / "vaultos.db")
    conn.execute("ALTER TABLE plan_item DROP COLUMN kind")
    conn.execute("ALTER TABLE plan_item DROP COLUMN reset_period")
    conn.execute("ALTER TABLE finance_settings DROP COLUMN open_period")
    conn.execute("ALTER TABLE finance_settings DROP COLUMN last_closed_period")
    conn.execute("DROP TABLE planned_posting")
    conn.execute("ALTER TABLE plan_period DROP COLUMN adjusted_target_cents")
    conn.execute("ALTER TABLE plan_period DROP COLUMN adjusted_window_start")
    conn.execute("ALTER TABLE plan_period DROP COLUMN adjusted_set_at")
    conn.execute("PRAGMA user_version = 7")
    conn.execute(
        "INSERT INTO account (id, nickname, type, balance_cents, is_primary, created_at) "
        "VALUES ('a1', 'Checking', 'checking', 0, 1, 't0')"
    )
    conn.executescript(
        """
        INSERT INTO plan_item (id, name, estimate_cents, type, day_of_month, cadence,
          cadence_unit, cadence_frequency, anchor_period, account_id, match_text) VALUES
          ('p_dated', 'Rent', -150000, 'Rent', 1, 'dated', 'month', 1, NULL, 'a1', '["RENT"]'),
          ('p_oneoff', 'Vet Bill', -30000, 'Other', 4, 'one-off', NULL, NULL, '2026-05', 'a1', '[]'),
          ('p_spread_monthly', 'Groceries', -50000, 'Food', NULL, 'spread monthly', NULL, NULL, NULL, 'a1', '[]'),
          ('p_spread_daily', 'Ad-hoc', -10000, 'Other', NULL, 'spread daily', NULL, NULL, NULL, 'a1', '[]'),
          ('p_spread_weekly', 'Lunch', -10000, 'Food', NULL, 'spread weekly', NULL, NULL, NULL, 'a1', '[]'),
          ('p_spread_weekdays', 'Coffee', -2000, 'Food', NULL, 'spread weekdays', NULL, NULL, NULL, 'a1', '[]');
        """
    )
    conn.commit()

    migrate(conn)  # re-runs migrations 0008-0014 against pre-existing cadence-only rows

    assert conn.execute("PRAGMA user_version").fetchone()[0] == 14
    rows = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM plan_item")}

    assert rows["p_dated"]["kind"] == "posting"
    assert rows["p_dated"]["reset_period"] is None
    assert rows["p_dated"]["cadence"] == "dated"  # untouched -- still a real Cadence

    assert rows["p_oneoff"]["kind"] == "posting"
    assert rows["p_oneoff"]["reset_period"] is None

    assert rows["p_spread_monthly"]["kind"] == "budget"
    assert rows["p_spread_monthly"]["reset_period"] == "monthly"
    assert rows["p_spread_daily"]["kind"] == "budget"
    assert rows["p_spread_daily"]["reset_period"] == "monthly"  # daily collapses onto monthly

    assert rows["p_spread_weekly"]["kind"] == "budget"
    assert rows["p_spread_weekly"]["reset_period"] == "weekly"
    assert rows["p_spread_weekdays"]["kind"] == "budget"
    assert rows["p_spread_weekdays"]["reset_period"] == "weekly"  # weekdays collapses onto weekly

    # Every migrated Budget had its now-meaningless Cadence fields cleared, not left stale.
    for budget_id in ("p_spread_monthly", "p_spread_daily", "p_spread_weekly", "p_spread_weekdays"):
        assert rows[budget_id]["day_of_month"] is None
        assert rows[budget_id]["anchor_period"] is None
        assert rows[budget_id]["match_text"] == "[]"


def test_migration_0009_adds_open_period_as_null(tmp_path):
    # Simulate a real pre-0009 DB (schema version 8) -- migration 0009 must add the
    # column without disturbing the existing floor_cents value, and leave open_period
    # NULL (store.get_open_period() is what lazily establishes it, not this migration).
    conn = connect(tmp_path / "vaultos.db")
    conn.execute("UPDATE finance_settings SET floor_cents = 350000 WHERE id = 1")
    conn.execute("ALTER TABLE finance_settings DROP COLUMN open_period")
    conn.execute("ALTER TABLE finance_settings DROP COLUMN last_closed_period")
    conn.execute("DROP TABLE planned_posting")
    conn.execute("ALTER TABLE plan_period DROP COLUMN adjusted_target_cents")
    conn.execute("ALTER TABLE plan_period DROP COLUMN adjusted_window_start")
    conn.execute("ALTER TABLE plan_period DROP COLUMN adjusted_set_at")
    conn.execute("PRAGMA user_version = 8")
    conn.commit()

    migrate(conn)  # re-runs migrations 0009-0014 against a pre-existing finance_settings row

    assert conn.execute("PRAGMA user_version").fetchone()[0] == 14
    row = conn.execute("SELECT floor_cents, open_period FROM finance_settings WHERE id = 1").fetchone()
    assert row["floor_cents"] == 350000
    assert row["open_period"] is None


def test_migration_0010_creates_the_planned_posting_table(tmp_path):
    # Simulate a real pre-0010 DB (schema version 9).
    conn = connect(tmp_path / "vaultos.db")
    conn.execute("ALTER TABLE finance_settings DROP COLUMN last_closed_period")
    conn.execute("DROP TABLE planned_posting")
    conn.execute("ALTER TABLE plan_period DROP COLUMN adjusted_target_cents")
    conn.execute("ALTER TABLE plan_period DROP COLUMN adjusted_window_start")
    conn.execute("ALTER TABLE plan_period DROP COLUMN adjusted_set_at")
    conn.execute("PRAGMA user_version = 9")
    conn.execute(
        "INSERT INTO account (id, nickname, type, balance_cents, is_primary, created_at) "
        "VALUES ('a1', 'Checking', 'checking', 0, 1, 't0')"
    )
    conn.execute(
        "INSERT INTO plan_item (id, name, estimate_cents, type, day_of_month, cadence, "
        "cadence_unit, cadence_frequency, account_id, kind, match_text) VALUES "
        "('p1', 'Rent', -150000, 'Rent', 1, 'dated', 'month', 1, 'a1', 'posting', '[]')"
    )
    conn.commit()

    migrate(conn)  # re-runs migrations 0010-0014 against a pre-existing plan_item

    assert conn.execute("PRAGMA user_version").fetchone()[0] == 14
    conn.execute(
        "INSERT INTO planned_posting (id, plan_item_id, period, expected_date, expected_amount_cents, created_at) "
        "VALUES ('pp1', 'p1', '2026-08', '2026-08-01', -150000, '2026-08-01T00:00:00Z')"
    )
    conn.commit()
    row = conn.execute("SELECT * FROM planned_posting WHERE id = 'pp1'").fetchone()
    assert row["plan_item_id"] == "p1"
    assert row["expected_amount_cents"] == -150000
    assert row["matched_txn_id"] is None

    # Idempotency's own enforcement mechanism -- a second occurrence for the same item
    # on the same date is a real UNIQUE violation, not silently accepted.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO planned_posting (id, plan_item_id, period, expected_date, expected_amount_cents, created_at) "
            "VALUES ('pp2', 'p1', '2026-08', '2026-08-01', -150000, '2026-08-01T00:00:00Z')"
        )


def test_migration_0011_adds_matched_txn_id_without_disturbing_existing_rows(tmp_path):
    # Simulate a real pre-0011 DB (schema version 10) with an already-materialized row.
    conn = connect(tmp_path / "vaultos.db")
    conn.execute(
        "INSERT INTO account (id, nickname, type, balance_cents, is_primary, created_at) "
        "VALUES ('a1', 'Checking', 'checking', 0, 1, 't0')"
    )
    conn.execute(
        "INSERT INTO plan_item (id, name, estimate_cents, type, day_of_month, cadence, "
        "cadence_unit, cadence_frequency, account_id, kind, match_text) VALUES "
        "('p1', 'Rent', -150000, 'Rent', 1, 'dated', 'month', 1, 'a1', 'posting', '[]')"
    )
    conn.execute(
        "INSERT INTO planned_posting (id, plan_item_id, period, expected_date, expected_amount_cents, created_at) "
        "VALUES ('pp1', 'p1', '2026-08', '2026-08-01', -150000, '2026-08-01T00:00:00Z')"
    )
    conn.execute("ALTER TABLE planned_posting DROP COLUMN matched_txn_id")
    conn.execute("ALTER TABLE planned_posting DROP COLUMN deferred_date")
    conn.execute("ALTER TABLE plan_period DROP COLUMN adjusted_target_cents")
    conn.execute("ALTER TABLE plan_period DROP COLUMN adjusted_window_start")
    conn.execute("ALTER TABLE plan_period DROP COLUMN adjusted_set_at")
    conn.execute("ALTER TABLE finance_settings DROP COLUMN last_closed_period")
    conn.execute("PRAGMA user_version = 10")
    conn.commit()

    migrate(conn)  # re-runs migrations 0011-0014 against a pre-existing planned_posting row

    assert conn.execute("PRAGMA user_version").fetchone()[0] == 14
    row = conn.execute("SELECT * FROM planned_posting WHERE id = 'pp1'").fetchone()
    assert row["plan_item_id"] == "p1"  # untouched
    assert row["expected_amount_cents"] == -150000  # untouched
    assert row["matched_txn_id"] is None  # the new column, defaulted
    assert row["deferred_date"] is None  # the new column, defaulted


def test_migration_0012_adds_deferred_date_without_disturbing_existing_rows(tmp_path):
    # Simulate a real pre-0012 DB (schema version 11) with an already-materialized,
    # already-matched row.
    conn = connect(tmp_path / "vaultos.db")
    conn.execute(
        "INSERT INTO account (id, nickname, type, balance_cents, is_primary, created_at) "
        "VALUES ('a1', 'Checking', 'checking', 0, 1, 't0')"
    )
    conn.execute(
        "INSERT INTO plan_item (id, name, estimate_cents, type, day_of_month, cadence, "
        "cadence_unit, cadence_frequency, account_id, kind, match_text) VALUES "
        "('p1', 'Rent', -150000, 'Rent', 1, 'dated', 'month', 1, 'a1', 'posting', '[]')"
    )
    conn.execute(
        "INSERT INTO txn (id, account_id, date, merchant_raw, merchant, amount_cents, dedupe_hash) "
        "VALUES ('t1', 'a1', '2026-08-01', 'RENT', 'Landlord', -150000, 'h1')"
    )
    conn.execute(
        "INSERT INTO planned_posting "
        "(id, plan_item_id, period, expected_date, expected_amount_cents, created_at, matched_txn_id) "
        "VALUES ('pp1', 'p1', '2026-08', '2026-08-01', -150000, '2026-08-01T00:00:00Z', 't1')"
    )
    conn.execute("ALTER TABLE planned_posting DROP COLUMN deferred_date")
    conn.execute("ALTER TABLE plan_period DROP COLUMN adjusted_target_cents")
    conn.execute("ALTER TABLE plan_period DROP COLUMN adjusted_window_start")
    conn.execute("ALTER TABLE plan_period DROP COLUMN adjusted_set_at")
    conn.execute("ALTER TABLE finance_settings DROP COLUMN last_closed_period")
    conn.execute("PRAGMA user_version = 11")
    conn.commit()

    migrate(conn)  # re-runs migrations 0012-0014 against a pre-existing, already-matched row

    assert conn.execute("PRAGMA user_version").fetchone()[0] == 14
    row = conn.execute("SELECT * FROM planned_posting WHERE id = 'pp1'").fetchone()
    assert row["expected_date"] == "2026-08-01"  # untouched
    assert row["matched_txn_id"] == "t1"  # untouched
    assert row["deferred_date"] is None  # the new column, defaulted


def test_migration_0013_adds_adjusted_columns_without_disturbing_existing_rows(tmp_path):
    # Simulate a real pre-0013 DB (schema version 12) with an already-ticked plan_period
    # row (a Posting's own state, unrelated to Adjusted).
    conn = connect(tmp_path / "vaultos.db")
    conn.execute(
        "INSERT INTO account (id, nickname, type, balance_cents, is_primary, created_at) "
        "VALUES ('a1', 'Checking', 'checking', 0, 1, 't0')"
    )
    conn.execute(
        "INSERT INTO plan_item (id, name, estimate_cents, type, day_of_month, cadence, "
        "cadence_unit, cadence_frequency, account_id, kind, match_text) VALUES "
        "('p1', 'Rent', -150000, 'Rent', 1, 'dated', 'month', 1, 'a1', 'posting', '[]')"
    )
    conn.execute(
        "INSERT INTO plan_period (id, plan_item_id, period, ticked, ticked_at) "
        "VALUES ('pp1', 'p1', '2026-08', 1, '2026-08-14T00:00:00Z')"
    )
    conn.execute("ALTER TABLE plan_period DROP COLUMN adjusted_target_cents")
    conn.execute("ALTER TABLE plan_period DROP COLUMN adjusted_window_start")
    conn.execute("ALTER TABLE plan_period DROP COLUMN adjusted_set_at")
    conn.execute("ALTER TABLE finance_settings DROP COLUMN last_closed_period")
    conn.execute("PRAGMA user_version = 12")
    conn.commit()

    migrate(conn)  # re-runs migrations 0013-0014 against a pre-existing ticked row

    assert conn.execute("PRAGMA user_version").fetchone()[0] == 14
    row = conn.execute("SELECT * FROM plan_period WHERE id = 'pp1'").fetchone()
    assert row["ticked"] == 1  # untouched
    assert row["ticked_at"] == "2026-08-14T00:00:00Z"  # untouched
    assert row["adjusted_target_cents"] is None  # the new columns, defaulted
    assert row["adjusted_window_start"] is None
    assert row["adjusted_set_at"] is None


def test_only_one_account_may_be_primary(tmp_path):
    conn = connect(tmp_path / "vaultos.db")
    conn.execute(
        "INSERT INTO account (id, nickname, type, balance_cents, is_primary, created_at) "
        "VALUES ('a1', 'Checking', 'checking', 0, 1, 't0')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO account (id, nickname, type, balance_cents, is_primary, created_at) "
            "VALUES ('a2', 'Savings', 'savings', 0, 1, 't0')"
        )
    # A second non-primary account is never constrained.
    conn.execute(
        "INSERT INTO account (id, nickname, type, balance_cents, is_primary, created_at) "
        "VALUES ('a3', 'Savings', 'savings', 0, 0, 't0')"
    )


def test_only_one_plan_item_may_be_catch_all(tmp_path):
    conn = connect(tmp_path / "vaultos.db")
    conn.execute(
        "INSERT INTO account (id, nickname, type, balance_cents, is_primary, created_at) "
        "VALUES ('a1', 'Checking', 'checking', 0, 1, 't0')"
    )
    conn.execute(
        "INSERT INTO plan_item (id, name, estimate_cents, type, cadence, account_id, is_catch_all) "
        "VALUES ('p1', 'Everything else', 0, 'Other', 'spread monthly', 'a1', 1)"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO plan_item (id, name, estimate_cents, type, cadence, account_id, is_catch_all) "
            "VALUES ('p2', 'Also everything else', 0, 'Other', 'spread monthly', 'a1', 1)"
        )
    # Ordinary (non-catch-all) Plan Items are never constrained.
    conn.execute(
        "INSERT INTO plan_item (id, name, estimate_cents, type, cadence, account_id, is_catch_all) "
        "VALUES ('p3', 'Rent', -150000, 'Rent', 'monthly', 'a1', 0)"
    )
    conn.execute(
        "INSERT INTO plan_item (id, name, estimate_cents, type, cadence, account_id, is_catch_all) "
        "VALUES ('p4', 'Mortgage', -275000, 'Mortgage', 'monthly', 'a1', 0)"
    )


def test_txn_dedupe_hash_is_unique(tmp_path):
    conn = connect(tmp_path / "vaultos.db")
    conn.execute(
        "INSERT INTO account (id, nickname, type, balance_cents, is_primary, created_at) "
        "VALUES ('a1', 'Checking', 'checking', 0, 1, 't0')"
    )
    conn.execute(
        "INSERT INTO txn (id, account_id, date, merchant_raw, merchant, amount_cents, dedupe_hash) "
        "VALUES ('t1', 'a1', '2026-03-14', 'COMCAST', 'Comcast', -15400, 'hash-1')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO txn (id, account_id, date, merchant_raw, merchant, amount_cents, dedupe_hash) "
            "VALUES ('t2', 'a1', '2026-03-14', 'COMCAST', 'Comcast', -15400, 'hash-1')"
        )


def test_column_mapping_is_unique_per_account(tmp_path):
    conn = connect(tmp_path / "vaultos.db")
    conn.execute(
        "INSERT INTO account (id, nickname, type, balance_cents, is_primary, created_at) "
        "VALUES ('a1', 'Checking', 'checking', 0, 1, 't0')"
    )
    conn.execute(
        "INSERT INTO column_mapping (id, account_id, source_date, source_merchant, confirmed_at) "
        "VALUES ('m1', 'a1', 'Date', 'Description', 't0')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO column_mapping (id, account_id, source_date, source_merchant, confirmed_at) "
            "VALUES ('m2', 'a1', 'Transaction Date', 'Merchant', 't1')"
        )


def test_plan_period_is_unique_per_item_and_month(tmp_path):
    conn = connect(tmp_path / "vaultos.db")
    conn.execute(
        "INSERT INTO account (id, nickname, type, balance_cents, is_primary, created_at) "
        "VALUES ('a1', 'Checking', 'checking', 0, 1, 't0')"
    )
    conn.execute(
        "INSERT INTO plan_item (id, name, estimate_cents, type, cadence, account_id) "
        "VALUES ('p1', 'Rent', -150000, 'Rent', 'monthly', 'a1')"
    )
    conn.execute(
        "INSERT INTO plan_period (id, plan_item_id, period, ticked) VALUES ('pp1', 'p1', '2026-03', 1)"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO plan_period (id, plan_item_id, period, ticked) VALUES ('pp2', 'p1', '2026-03', 0)"
        )
