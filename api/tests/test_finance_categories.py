from datetime import date

import pytest

from vaultos.db.conn import connect
from vaultos.modules.finance import categories, store

TODAY_PERIOD = "2026-03"
TODAY = date(2026, 3, 20)


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


def _make_item(conn, account_id, **over):
    defaults = dict(
        item_id="p1",
        name="Rent",
        estimate_cents=-150000,
        plan_type="Rent",
        payee=None,
        day_of_month=1,
        cadence="dated",
        cadence_unit="month",
        cadence_frequency=1,
        anchor_period=None,
        account_id=account_id,
        verified=True,
        is_catch_all=False,
        match_text=[],
    )
    defaults.update(over)
    return store.create_plan_item(conn, **defaults)


def _make_txn(conn, account_id, **over):
    defaults = dict(
        txn_id="t1",
        date="2026-03-05",
        merchant_raw="X",
        merchant="X",
        amount_cents=-1000,
        category=None,
        category_source=None,
        plan_item_id=None,
        match_source=None,
        excluded_from_charts=0,
        dedupe_hash="h1",
    )
    defaults.update(over)
    conn.execute(
        "INSERT INTO txn (id, account_id, date, merchant_raw, merchant, amount_cents, category, "
        "category_source, plan_item_id, match_source, excluded_from_charts, dedupe_hash) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
            defaults["excluded_from_charts"],
            defaults["dedupe_hash"],
        ),
    )
    conn.commit()
    return defaults["txn_id"]


def test_empty_state(conn):
    result = categories.build_categories(conn, TODAY_PERIOD, TODAY)
    assert result["rows"] == []
    assert result["total_actual_cents"] == 0


def test_a_category_with_a_plan_but_no_transaction_stays_unseen_not_backfilled(conn, account_id):
    _make_item(conn, account_id, plan_type="Insurance", estimate_cents=-27500)
    result = categories.build_categories(conn, TODAY_PERIOD, TODAY)
    row = result["rows"][0]
    assert row["category"] == "Insurance"
    assert row["actual_cents"] is None
    assert row["planned_cents"] == -27500
    assert row["seen"] is False


def test_a_category_with_a_transaction_but_no_plan_shows_only_actual(conn, account_id):
    _make_txn(conn, account_id, category="Food", amount_cents=-4200)
    result = categories.build_categories(conn, TODAY_PERIOD, TODAY)
    row = result["rows"][0]
    assert row["category"] == "Food"
    assert row["actual_cents"] == -4200
    assert row["planned_cents"] is None
    assert row["seen"] is True


def test_rows_are_the_union_of_planned_and_actual_key_sets(conn, account_id):
    _make_item(conn, account_id, item_id="p1", plan_type="Rent", estimate_cents=-150000)
    _make_txn(
        conn, account_id, txn_id="t1", category="Rent", amount_cents=-150000, dedupe_hash="h1"
    )
    _make_txn(conn, account_id, txn_id="t2", category="Food", amount_cents=-3000, dedupe_hash="h2")
    result = categories.build_categories(conn, TODAY_PERIOD, TODAY)
    cats = {r["category"] for r in result["rows"]}
    assert cats == {"Rent", "Food"}


def test_actual_never_derived_from_planned_when_both_exist(conn, account_id):
    _make_item(conn, account_id, plan_type="Rent", estimate_cents=-150000)
    _make_txn(conn, account_id, category="Rent", amount_cents=-140000)
    row = categories.build_categories(conn, TODAY_PERIOD, TODAY)["rows"][0]
    assert row["actual_cents"] == -140000  # the real amount, not the estimate
    assert row["planned_cents"] == -150000
    # Magnitude-based: spent $1400 against a $1500 plan -> under by $100, a negative
    # variance (spent less than planned), not the signed -140000 - (-150000) = +10000.
    assert row["variance_cents"] == -10000


def test_variance_is_positive_when_spend_exceeds_the_plan(conn, account_id):
    _make_item(conn, account_id, plan_type="Rent", estimate_cents=-150000)
    _make_txn(conn, account_id, category="Rent", amount_cents=-160000)
    row = categories.build_categories(conn, TODAY_PERIOD, TODAY)["rows"][0]
    assert row["variance_cents"] == 10000  # spent $100 more than planned


def test_uncategorized_transactions_are_invisible_to_this_screen(conn, account_id):
    _make_txn(conn, account_id, category=None, amount_cents=-999)
    result = categories.build_categories(conn, TODAY_PERIOD, TODAY)
    assert result["rows"] == []


def test_excluded_from_charts_transactions_are_omitted_from_actual(conn, account_id):
    _make_txn(conn, account_id, category="Food", amount_cents=-1000, excluded_from_charts=1)
    result = categories.build_categories(conn, TODAY_PERIOD, TODAY)
    assert result["rows"] == []


def test_transactions_outside_the_period_are_excluded(conn, account_id):
    _make_txn(conn, account_id, category="Food", date="2026-02-28", amount_cents=-1000)
    result = categories.build_categories(conn, TODAY_PERIOD, TODAY)
    assert result["rows"] == []


def test_catch_all_plan_item_never_contributes_a_planned_row(conn, account_id):
    _make_item(
        conn,
        account_id,
        item_id="catch",
        name="Everything else",
        plan_type="Other",
        estimate_cents=-5000,
        kind="budget",
        reset_period="monthly",
        cadence="budget",
        day_of_month=None,
        cadence_unit=None,
        cadence_frequency=None,
        is_catch_all=True,
    )
    result = categories.build_categories(conn, TODAY_PERIOD, TODAY)
    assert result["rows"] == []


def test_catch_all_collected_transactions_still_count_under_their_own_category(conn, account_id):
    # The catch-all absorbs unmatched spend at the PLAN level, but each transaction it
    # collects keeps its own independently-assigned category (or none) here.
    _make_item(
        conn,
        account_id,
        item_id="catch",
        name="Everything else",
        plan_type="Other",
        estimate_cents=-5000,
        kind="budget",
        reset_period="monthly",
        cadence="budget",
        day_of_month=None,
        cadence_unit=None,
        cadence_frequency=None,
        is_catch_all=True,
    )
    _make_txn(conn, account_id, category="Travel", plan_item_id=None, amount_cents=-2000)
    result = categories.build_categories(conn, TODAY_PERIOD, TODAY)
    cats = {r["category"] for r in result["rows"]}
    assert cats == {"Travel"}
    assert "Other" not in cats


def test_a_dated_plan_item_only_contributes_planned_in_periods_it_actually_occurs(conn, account_id):
    _make_item(
        conn,
        account_id,
        plan_type="Insurance",
        cadence_frequency=3,
        day_of_month=1,
        anchor_period="2026-01",
        estimate_cents=-30000,
    )
    result_march = categories.build_categories(
        conn, "2026-03", TODAY
    )  # not a quarterly month from a Jan anchor
    result_april = categories.build_categories(
        conn, "2026-04", TODAY
    )  # 3 months after Jan -> occurs
    assert result_march["rows"] == []
    assert result_april["rows"][0]["planned_cents"] == -30000


def test_a_spread_item_contributes_planned_every_period(conn, account_id):
    _make_item(
        conn,
        account_id,
        plan_type="Food",
        kind="budget",
        reset_period="monthly",
        cadence="budget",
        day_of_month=None,
        cadence_unit=None,
        cadence_frequency=None,
        estimate_cents=-40000,
    )
    for period in ("2026-01", "2026-06", "2026-12"):
        result = categories.build_categories(conn, period, TODAY)
        assert result["rows"][0]["planned_cents"] == -40000


def test_a_budgets_planned_cents_reflects_its_active_adjustment(conn, account_id):
    # ticket #23: same math already hand-verified in test_finance_money.py and
    # test_finance_plan.py -- here we only confirm the Categories screen's planned
    # total picks up the Adjusted override too, so it never disagrees with the Plan
    # screen about a Budget that's been Adjusted this window.
    _make_item(
        conn,
        account_id,
        plan_type="Food",
        kind="budget",
        reset_period="monthly",
        cadence="budget",
        day_of_month=None,
        cadence_unit=None,
        cadence_frequency=None,
        estimate_cents=-15000,
    )
    store.set_adjusted(
        conn,
        "p1",
        TODAY_PERIOD,
        -9300,
        "2026-03-01",
        "2026-03-11T00:00:00Z",
        open_period=TODAY_PERIOD,
    )
    result = categories.build_categories(conn, TODAY_PERIOD, TODAY)
    assert result["rows"][0]["planned_cents"] == -9300


def test_a_budget_ignores_a_stale_adjustment_from_a_previous_window(conn, account_id):
    _make_item(
        conn,
        account_id,
        plan_type="Food",
        kind="budget",
        reset_period="monthly",
        cadence="budget",
        day_of_month=None,
        cadence_unit=None,
        cadence_frequency=None,
        estimate_cents=-15000,
    )
    store.set_adjusted(
        conn, "p1", "2026-02", -5000, "2026-02-01", "2026-02-11T00:00:00Z", open_period=TODAY_PERIOD
    )
    result = categories.build_categories(conn, TODAY_PERIOD, TODAY)
    assert result["rows"][0]["planned_cents"] == -15000


def test_a_weekly_budget_sums_its_per_week_contributions_within_the_period(conn, account_id):
    # ticket #19: a Weekly Budget's planned total for a whole calendar-month period
    # must sum its per-week contributions, not report one flat monthly figure. Must
    # be DAY-PRECISE (matching cash-flow's own per-day math), not 5 whole weeks x
    # -7000 = -35000 -- the two boundary weeks (starting 2026-07-27 and 2026-08-31)
    # only partly fall in August (2 and 1 in-month days respectively), so the true
    # total is -31000. An earlier whole-week-count version of this fix disagreed with
    # cash-flow for exactly this reason (caught in code review).
    _make_item(
        conn,
        account_id,
        plan_type="Food",
        kind="budget",
        reset_period="weekly",
        cadence="budget",
        day_of_month=None,
        cadence_unit=None,
        cadence_frequency=None,
        estimate_cents=-7000,
    )
    result = categories.build_categories(conn, "2026-08", TODAY)
    assert result["rows"][0]["planned_cents"] == -31000


def test_a_week_unit_dated_item_landing_twice_in_a_period_contributes_the_estimate_twice(
    conn, account_id
):
    # ADR-0018: undercounting a multi-occurrence "dated" item's planned total was a real
    # bug -- the old truthiness-only check added estimate_cents once regardless of how
    # many times the item actually landed. A biweekly item anchored on the 1st completes
    # 3 cycles in a 31-day August (1st, 15th, 29th); anchored on the 7th, only 2 (7th, 21st).
    _make_item(
        conn,
        account_id,
        plan_type="Subscription",
        cadence_unit="week",
        cadence_frequency=2,
        day_of_month=None,
        anchor_period=None,
        anchor_date="2026-08-07",
        estimate_cents=-20000,
    )
    result = categories.build_categories(conn, "2026-08", TODAY)
    assert result["rows"][0]["planned_cents"] == -40000  # 2 occurrences x -20000


def test_top_8_and_everything_else_aggregate(conn, account_id):
    for i in range(9):
        _make_txn(
            conn,
            account_id,
            txn_id=f"t{i}",
            category=f"Cat{i}",
            amount_cents=-(1000 * (9 - i)),
            dedupe_hash=f"h{i}",
        )
    result = categories.build_categories(conn, TODAY_PERIOD, TODAY)
    # 8 leading categories + one "Everything else" aggregate row for the 9th.
    assert len(result["rows"]) == 9
    assert result["rows"][0]["category"] == "Cat0"  # -9000, biggest magnitude, sorts first
    everything_else = result["rows"][-1]
    assert everything_else["category"] == "Everything else"
    assert everything_else["is_aggregate"] is True
    assert everything_else["actual_cents"] == -1000  # Cat8's -1000, the 9th and smallest


def test_unseen_rows_are_never_folded_into_everything_else_or_the_pie(conn, account_id):
    for i in range(9):
        _make_txn(
            conn,
            account_id,
            txn_id=f"t{i}",
            category=f"Cat{i}",
            amount_cents=-(1000 * (9 - i)),
            dedupe_hash=f"h{i}",
        )
    _make_item(conn, account_id, item_id="unseen1", plan_type="NeverObserved", estimate_cents=-500)
    result = categories.build_categories(conn, TODAY_PERIOD, TODAY)
    # 8 leading + 1 everything-else + 1 unseen = 10, and the unseen row is last.
    assert len(result["rows"]) == 10
    assert result["rows"][-1]["category"] == "NeverObserved"
    assert result["rows"][-1]["seen"] is False
    # None of the observed 9 categories got bumped out to make room for the unseen one.
    assert {r["category"] for r in result["rows"][:9]} == {f"Cat{i}" for i in range(8)} | {
        "Everything else"
    }


def test_committed_flexible_split_sums_over_every_observed_category_not_just_top_8(
    conn, account_id
):
    for i in range(9):
        _make_txn(
            conn,
            account_id,
            txn_id=f"t{i}",
            category="Rent" if i == 8 else f"Cat{i}",
            amount_cents=-(1000 * (9 - i)),
            dedupe_hash=f"h{i}",
        )
    result = categories.build_categories(conn, TODAY_PERIOD, TODAY)
    # "Rent" (committed) is the smallest (-1000) and falls into "everything else", but
    # its spend must still count toward committed_actual_cents.
    assert result["committed_actual_cents"] == -1000
    assert result["total_actual_cents"] == -(1000 * sum(range(1, 10)))


def test_flexible_is_total_minus_committed(conn, account_id):
    _make_txn(
        conn, account_id, txn_id="t1", category="Rent", amount_cents=-150000, dedupe_hash="h1"
    )
    _make_txn(
        conn,
        account_id,
        txn_id="t2",
        category="Entertainment",
        amount_cents=-5000,
        dedupe_hash="h2",
    )
    result = categories.build_categories(conn, TODAY_PERIOD, TODAY)
    assert result["committed_actual_cents"] == -150000
    assert result["flexible_actual_cents"] == -5000
    assert result["total_actual_cents"] == -155000


def test_income_transactions_are_excluded_this_is_a_spend_only_screen(conn, account_id):
    # Matches the reference prototype's own implementation, which filters to amt < 0
    # before building either actualByCat or planByCat -- a paycheck isn't a spending
    # category, and including it would break the pie's percentage math.
    _make_txn(conn, account_id, category="Income", amount_cents=250000)
    result = categories.build_categories(conn, TODAY_PERIOD, TODAY)
    assert result["rows"] == []


def test_income_plan_items_never_contribute_a_planned_row(conn, account_id):
    _make_item(conn, account_id, plan_type="Income", estimate_cents=250000)
    result = categories.build_categories(conn, TODAY_PERIOD, TODAY)
    assert result["rows"] == []


def test_a_budget_whose_type_matches_a_committed_name_is_flexible_not_committed(conn, account_id):
    # ticket #25: nothing stops a Budget's `type` from accidentally colliding with a
    # Committed category name -- when it does, the category must still bucket as
    # Flexible, since Budget spend is never Committed regardless of what it's named.
    _make_item(
        conn,
        account_id,
        plan_type="Insurance",
        kind="budget",
        reset_period="monthly",
        cadence="budget",
        day_of_month=None,
        cadence_unit=None,
        cadence_frequency=None,
        estimate_cents=-5000,
    )
    _make_txn(conn, account_id, category="Insurance", amount_cents=-5000)
    result = categories.build_categories(conn, TODAY_PERIOD, TODAY)
    row = result["rows"][0]
    assert row["category"] == "Insurance"
    assert row["committed"] is False
    assert result["committed_actual_cents"] == 0
    assert result["flexible_actual_cents"] == -5000


def test_committed_flexible_totals_are_correct_for_a_mixed_set_of_postings_and_budgets(
    conn, account_id
):
    _make_item(conn, account_id, item_id="p1", plan_type="Rent", estimate_cents=-150000)
    _make_item(
        conn,
        account_id,
        item_id="p2",
        plan_type="Utility",
        kind="budget",
        reset_period="monthly",
        cadence="budget",
        day_of_month=None,
        cadence_unit=None,
        cadence_frequency=None,
        estimate_cents=-8000,
    )
    _make_txn(
        conn, account_id, txn_id="t1", category="Rent", amount_cents=-150000, dedupe_hash="h1"
    )
    _make_txn(
        conn, account_id, txn_id="t2", category="Utility", amount_cents=-8000, dedupe_hash="h2"
    )
    result = categories.build_categories(conn, TODAY_PERIOD, TODAY)
    rows = {r["category"]: r for r in result["rows"]}
    assert rows["Rent"]["committed"] is True
    assert rows["Utility"]["committed"] is False
    assert result["committed_actual_cents"] == -150000
    assert result["flexible_actual_cents"] == -8000


def test_a_committed_posting_keeps_its_own_spend_committed_even_when_a_budget_shares_its_type_name(
    conn, account_id
):
    # Nothing stops a Posting and a Budget from sharing the same `type` string (no
    # uniqueness constraint on plan_item.type) -- when they collide, the real
    # committed spend (linked to the Posting via plan_item_id) must stay committed,
    # and the Budget's own spend (linked to the Budget) must stay flexible, even
    # though both land under the identical "Rent" category key.
    _make_item(conn, account_id, item_id="p1", plan_type="Rent", estimate_cents=-150000)
    _make_item(
        conn,
        account_id,
        item_id="p2",
        plan_type="Rent",
        kind="budget",
        reset_period="monthly",
        cadence="budget",
        day_of_month=None,
        cadence_unit=None,
        cadence_frequency=None,
        estimate_cents=-8000,
    )
    _make_txn(
        conn,
        account_id,
        txn_id="t1",
        category="Rent",
        amount_cents=-150000,
        plan_item_id="p1",
        dedupe_hash="h1",
    )
    _make_txn(
        conn,
        account_id,
        txn_id="t2",
        category="Rent",
        amount_cents=-8000,
        plan_item_id="p2",
        dedupe_hash="h2",
    )
    result = categories.build_categories(conn, TODAY_PERIOD, TODAY)
    row = result["rows"][0]
    assert row["category"] == "Rent"
    assert row["actual_cents"] == -158000
    assert row["committed"] is True
    assert result["committed_actual_cents"] == -150000
    assert result["flexible_actual_cents"] == -8000


def test_the_catch_alls_type_never_poisons_an_unseen_committed_category(conn, account_id):
    # The catch-all is kind="budget" like any other Budget, and its `type` can
    # collide with a Committed name too -- but it's never a real category source
    # (README: "excluded entirely from this table"), so it must not be able to flip
    # an unrelated, genuinely committed Posting's still-unseen row to Flexible.
    _make_item(conn, account_id, item_id="p1", plan_type="Insurance", estimate_cents=-27500)
    _make_item(
        conn,
        account_id,
        item_id="catch",
        name="Everything else",
        plan_type="Insurance",
        estimate_cents=-5000,
        kind="budget",
        reset_period="monthly",
        cadence="budget",
        day_of_month=None,
        cadence_unit=None,
        cadence_frequency=None,
        is_catch_all=True,
    )
    result = categories.build_categories(conn, TODAY_PERIOD, TODAY)
    row = result["rows"][0]
    assert row["category"] == "Insurance"
    assert row["seen"] is False
    assert row["committed"] is True


def test_an_unseen_committed_posting_stays_committed_even_when_a_budget_shares_its_type_name(
    conn, account_id
):
    # Same collision as the seen-row case above, but neither item has a transaction
    # yet -- both only ever surface as planned. The real committed Posting must not
    # be flipped to Flexible just because an unrelated same-named Budget also exists.
    _make_item(conn, account_id, item_id="p1", plan_type="Insurance", estimate_cents=-27500)
    _make_item(
        conn,
        account_id,
        item_id="p2",
        plan_type="Insurance",
        kind="budget",
        reset_period="monthly",
        cadence="budget",
        day_of_month=None,
        cadence_unit=None,
        cadence_frequency=None,
        estimate_cents=-5000,
    )
    result = categories.build_categories(conn, TODAY_PERIOD, TODAY)
    row = result["rows"][0]
    assert row["category"] == "Insurance"
    assert row["seen"] is False
    assert row["planned_cents"] == -32500
    assert row["committed"] is True


def test_a_retired_postings_linked_transactions_still_count_as_committed(conn, account_id):
    # store.list_plan_items filters retired_at IS NULL, so a naive items_by_id built
    # only from that list would lose a retired Posting entirely -- a transaction still
    # linked to it (plan_item_id) must not silently fall back to the Budget-collision
    # string check and get misclassified as Flexible just because its item retired.
    _make_item(conn, account_id, item_id="p1", plan_type="Rent", estimate_cents=-150000)
    _make_item(
        conn,
        account_id,
        item_id="p2",
        plan_type="Rent",
        kind="budget",
        reset_period="monthly",
        cadence="budget",
        day_of_month=None,
        cadence_unit=None,
        cadence_frequency=None,
        estimate_cents=-8000,
    )
    _make_txn(
        conn,
        account_id,
        txn_id="t1",
        category="Rent",
        amount_cents=-150000,
        plan_item_id="p1",
        dedupe_hash="h1",
    )
    conn.execute("UPDATE plan_item SET retired_at = ? WHERE id = ?", ("2026-03-01T00:00:00Z", "p1"))
    conn.commit()
    result = categories.build_categories(conn, TODAY_PERIOD, TODAY)
    row = result["rows"][0]
    assert row["category"] == "Rent"
    assert row["committed"] is True
    assert result["committed_actual_cents"] == -150000
    assert result["flexible_actual_cents"] == 0


def test_an_unseen_row_reflects_only_what_actually_contributes_this_period_not_store_wide_existence(
    conn, account_id
):
    # code-review round 4: budget_types/posting_types must be scoped to what actually
    # contributes to THIS period, not to whether an item of that kind/type exists
    # anywhere in the store -- a quarterly Posting that simply doesn't land this
    # period must not "win" a same-name collision it isn't even part of, making a
    # 100%-Budget-sourced planned figure display as Committed.
    _make_item(
        conn,
        account_id,
        item_id="p1",
        plan_type="Insurance",
        cadence_frequency=3,
        day_of_month=1,
        anchor_period="2026-01",
        estimate_cents=-30000,
    )
    _make_item(
        conn,
        account_id,
        item_id="p2",
        plan_type="Insurance",
        kind="budget",
        reset_period="monthly",
        cadence="budget",
        day_of_month=None,
        cadence_unit=None,
        cadence_frequency=None,
        estimate_cents=-5000,
    )
    result = categories.build_categories(
        conn, "2026-03", TODAY
    )  # not a quarterly month from a Jan anchor
    row = result["rows"][0]
    assert row["category"] == "Insurance"
    assert row["planned_cents"] == -5000  # only the Budget actually contributes this period
    assert row["committed"] is False
