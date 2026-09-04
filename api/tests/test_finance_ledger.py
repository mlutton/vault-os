import pytest

from vaultos.db.conn import connect
from vaultos.modules.finance import ledger, store


@pytest.fixture
def conn(tmp_path):
    return connect(tmp_path / "vaultos.db")


@pytest.fixture
def account_id(conn):
    return store.create_account(
        conn, account_id="a1", nickname="Checking", institution=None, account_type="checking",
        last_four=None, balance_cents=0, is_primary=True, created_at="2026-01-01T00:00:00Z",
    ).id


def _make_item(conn, account_id, **over):
    defaults = dict(
        item_id="p1", name="Rent", estimate_cents=-150000, plan_type="Rent", payee="Landlord",
        day_of_month=1, cadence="dated", cadence_unit="month", cadence_frequency=1,
        anchor_period=None, account_id=account_id,
        verified=True, is_catch_all=False, match_text=[],
    )
    defaults.update(over)
    return store.create_plan_item(conn, **defaults)


def _make_txn(conn, account_id, **over):
    defaults = dict(
        txn_id="t1", date="2026-03-01", merchant_raw="COMCAST", merchant="COMCAST",
        amount_cents=-1000, category=None, category_source=None, plan_item_id=None,
        match_source=None, dedupe_hash="h1",
    )
    defaults.update(over)
    conn.execute(
        "INSERT INTO txn (id, account_id, date, merchant_raw, merchant, amount_cents, category, "
        "category_source, plan_item_id, match_source, dedupe_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            defaults["txn_id"], account_id, defaults["date"], defaults["merchant_raw"], defaults["merchant"],
            defaults["amount_cents"], defaults["category"], defaults["category_source"],
            defaults["plan_item_id"], defaults["match_source"], defaults["dedupe_hash"],
        ),
    )
    conn.commit()
    return defaults["txn_id"]


def test_build_ledger_empty_state(conn):
    result = ledger.build_ledger(conn, None)
    assert result["rows"] == []
    assert result["guessed_count"] == 0
    assert result["unmatched_count"] == 0
    assert result["matched_by_rule_count"] == 0


def test_build_ledger_enriches_rows_with_account_and_plan_item_names(conn, account_id):
    _make_item(conn, account_id)
    _make_txn(conn, account_id, plan_item_id="p1", match_source="rule")
    result = ledger.build_ledger(conn, None)
    row = result["rows"][0]
    assert row["account_nickname"] == "Checking"
    assert row["plan_item_name"] == "Rent"
    assert row["why"] == "Matched by rule to Rent."


def test_build_ledger_includes_import_provenance(conn, account_id):
    store.commit_import(
        conn, import_id="imp1", account_id=account_id, filename="statement.csv",
        imported_at="2026-08-18T00:00:00Z", rows_to_add=[], rows_skipped=0,
    )
    _make_txn(conn, account_id, txn_id="t1")
    conn.execute("UPDATE txn SET import_id = 'imp1' WHERE id = 't1'")
    conn.commit()
    row = ledger.build_ledger(conn, None)["rows"][0]
    assert row["import_filename"] == "statement.csv"
    assert row["imported_at"] == "2026-08-18T00:00:00Z"


def test_build_ledger_import_provenance_is_null_for_a_hand_inserted_row(conn, account_id):
    _make_txn(conn, account_id)
    row = ledger.build_ledger(conn, None)["rows"][0]
    assert row["import_filename"] is None
    assert row["imported_at"] is None


def test_build_ledger_why_text_covers_all_match_states(conn, account_id):
    _make_item(conn, account_id)
    _make_txn(conn, account_id, txn_id="t1", plan_item_id="p1", match_source="rule", dedupe_hash="h1")
    _make_txn(conn, account_id, txn_id="t2", plan_item_id="p1", match_source="auto", dedupe_hash="h2")
    _make_txn(conn, account_id, txn_id="t3", plan_item_id="p1", match_source="user", dedupe_hash="h3")
    _make_txn(conn, account_id, txn_id="t4", plan_item_id=None, match_source="user", dedupe_hash="h4")
    _make_txn(conn, account_id, txn_id="t5", plan_item_id=None, match_source=None, dedupe_hash="h5")
    rows = {r["id"]: r for r in ledger.build_ledger(conn, None)["rows"]}
    assert rows["t1"]["why"] == "Matched by rule to Rent."
    assert rows["t2"]["why"] == "Guessed a resemblance to Rent — confirm or change it."
    assert rows["t3"]["why"] == "You confirmed this counts toward Rent."
    assert rows["t4"]["why"] == "You confirmed this doesn't count toward any plan item."
    assert rows["t5"]["why"] == "No plan item has matched this transaction yet."


def test_build_ledger_footer_counts_ignore_the_active_filter(conn, account_id):
    _make_item(conn, account_id)
    _make_txn(conn, account_id, txn_id="t1", plan_item_id="p1", match_source="rule", dedupe_hash="h1")
    _make_txn(conn, account_id, txn_id="t2", plan_item_id="p1", match_source="auto", dedupe_hash="h2")
    _make_txn(conn, account_id, txn_id="t3", plan_item_id=None, dedupe_hash="h3")

    result = ledger.build_ledger(conn, "unmatched")
    assert len(result["rows"]) == 1  # only t3 is visible...
    assert result["guessed_count"] == 1  # ...but the footer still counts everything
    assert result["matched_by_rule_count"] == 1
    assert result["unmatched_count"] == 1


def test_remember_match_text_appends_and_dedupes(conn, account_id):
    _make_item(conn, account_id, match_text=["EXISTING"])
    ledger.remember_match_text(conn, "p1", "  COMCAST CABLE  ")
    assert store.get_plan_item(conn, "p1").match_text == ["EXISTING", "COMCAST CABLE"]

    ledger.remember_match_text(conn, "p1", "COMCAST CABLE")  # exact dup -- no-op
    assert store.get_plan_item(conn, "p1").match_text == ["EXISTING", "COMCAST CABLE"]


def test_remember_match_text_on_a_missing_plan_item_is_a_noop(conn):
    ledger.remember_match_text(conn, "nope", "COMCAST")  # must not raise


def test_remember_match_text_against_a_budget_kind_item_is_a_noop(conn, account_id):
    # Regression test: match_text is meaningless for a Budget (ADR-0019), so
    # store.update_plan_item raises InvalidPlanItemError if this ever tries to append
    # to one. LedgerPanel.tsx lists Budget items in the "counts toward" dropdown right
    # alongside Postings with no kind filter, and offers "remember this" unconditionally
    # -- a user matching a transaction to a Budget with remember checked must not crash.
    _make_item(
        conn, account_id, kind="budget", reset_period="monthly", cadence="budget",
        day_of_month=None, cadence_unit=None, cadence_frequency=None, anchor_period=None,
        match_text=[],
    )
    ledger.remember_match_text(conn, "p1", "COFFEE SHOP")  # must not raise
    assert store.get_plan_item(conn, "p1").match_text == []
