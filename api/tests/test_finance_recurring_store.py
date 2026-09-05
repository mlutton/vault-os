import pytest

from vaultos.db.conn import connect
from vaultos.modules.finance import recurring, store


@pytest.fixture
def conn(tmp_path):
    return connect(tmp_path / "vaultos.db")


@pytest.fixture
def account_id(conn):
    return store.create_account(
        conn,
        account_id="a1",
        nickname="Checking",
        institution=None,
        account_type="checking",
        last_four=None,
        balance_cents=0,
        is_primary=True,
        created_at="2026-01-01T00:00:00Z",
    ).id


def _make_txn(conn, account_id, **over):
    defaults = dict(
        txn_id="t1",
        date="2026-03-01",
        merchant_raw="NETFLIX",
        merchant="Netflix",
        amount_cents=-1599,
        category=None,
        category_source=None,
        plan_item_id=None,
        match_source=None,
        dedupe_hash="h1",
    )
    defaults.update(over)
    conn.execute(
        "INSERT INTO txn (id, account_id, date, merchant_raw, merchant, amount_cents, category, "
        "category_source, plan_item_id, match_source, dedupe_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            defaults["txn_id"],
            account_id,
            defaults["date"],
            defaults["merchant_raw"],
            defaults["merchant"],
            defaults["amount_cents"],
            defaults["category"],
            defaults["category_source"],
            defaults["plan_item_id"],
            defaults["match_source"],
            defaults["dedupe_hash"],
        ),
    )
    conn.commit()
    return defaults["txn_id"]


def test_build_recurring_empty_state(conn):
    result = recurring.build_recurring(conn)
    assert result["rows"] == []
    assert result["monthly_total_cents"] == 0
    assert result["annual_total_cents"] == 0


def test_build_recurring_scans_the_full_history_not_one_month(conn, account_id):
    _make_txn(conn, account_id, txn_id="t1", date="2026-01-15", dedupe_hash="h1")
    _make_txn(conn, account_id, txn_id="t2", date="2026-02-14", dedupe_hash="h2")
    _make_txn(conn, account_id, txn_id="t3", date="2026-03-16", dedupe_hash="h3")
    result = recurring.build_recurring(conn)
    assert len(result["rows"]) == 1
    assert result["rows"][0]["merchant"] == "Netflix"
    assert result["monthly_total_cents"] == -1599
    assert result["annual_total_cents"] == -1599 * 12


def test_build_recurring_totals_sum_across_multiple_detected_merchants(conn, account_id):
    for i, d in enumerate(["2026-01-15", "2026-02-14", "2026-03-16"]):
        _make_txn(
            conn,
            account_id,
            txn_id=f"n{i}",
            date=d,
            merchant="Netflix",
            amount_cents=-1599,
            dedupe_hash=f"n{i}",
        )
    for i, d in enumerate(["2026-01-05", "2026-02-04", "2026-03-06"]):
        _make_txn(
            conn,
            account_id,
            txn_id=f"s{i}",
            date=d,
            merchant="Spotify",
            amount_cents=-999,
            dedupe_hash=f"s{i}",
        )
    result = recurring.build_recurring(conn)
    assert result["monthly_total_cents"] == -1599 + -999
