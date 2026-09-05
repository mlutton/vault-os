from datetime import date

import pytest

from vaultos.db.conn import connect
from vaultos.modules.finance import money, store
from vaultos.modules.finance.cashflow import build_cash_flow, plan_predicted_for_primary_today

TODAY = date(2026, 3, 14)


@pytest.fixture
def conn(tmp_path):
    return connect(tmp_path / "vaultos.db")


def _make_primary_account(conn, balance_cents=812000):
    return store.create_account(
        conn,
        account_id="a1",
        nickname="Checking",
        institution=None,
        account_type="checking",
        last_four=None,
        balance_cents=balance_cents,
        is_primary=True,
        created_at="2026-01-01T00:00:00Z",
    ).id


def _make_item(conn, account_id, **over):
    defaults = dict(
        item_id="p1",
        name="Rent",
        estimate_cents=-150000,
        plan_type="Rent",
        payee="Landlord",
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


def test_empty_state_no_primary_account(conn):
    result = build_cash_flow(conn, TODAY)
    assert result["empty_state"] == "no_primary_account"


def test_empty_state_no_plan_items(conn):
    _make_primary_account(conn)
    result = build_cash_flow(conn, TODAY)
    assert result["empty_state"] == "no_plan_items"


def test_cash_on_hand_is_the_primary_accounts_real_balance(conn):
    account_id = _make_primary_account(conn, balance_cents=812000)
    _make_item(conn, account_id, day_of_month=20)  # not yet due -- no carry-forward noise
    result = build_cash_flow(conn, TODAY)
    assert result["cash_on_hand_cents"] == 812000


def test_actual_series_is_flat_with_no_transactions(conn):
    account_id = _make_primary_account(conn, balance_cents=100000)
    _make_item(conn, account_id, day_of_month=20)
    result = build_cash_flow(conn, TODAY)
    balances = {p["balance_cents"] for p in result["actual_series"]}
    assert balances == {100000}
    assert result["actual_series"][0]["date"] == "2026-03-01"
    assert result["actual_series"][-1]["date"] == "2026-03-14"


def test_actual_series_reconstruction_ends_exactly_at_the_real_balance(conn):
    account_id = _make_primary_account(conn, balance_cents=100000)
    _make_item(conn, account_id, day_of_month=20)
    conn.execute(
        "INSERT INTO txn (id, account_id, date, merchant_raw, merchant, amount_cents, dedupe_hash) "
        "VALUES ('t1', ?, '2026-03-05', 'X', 'X', -25000, 'h1')",
        (account_id,),
    )
    conn.commit()
    result = build_cash_flow(conn, TODAY)
    # Sanity invariant: however the opening balance was derived, walking every real
    # transaction back forward must land exactly on today's known-true balance.
    assert result["actual_series"][-1]["balance_cents"] == 100000
    by_date = {p["date"]: p["balance_cents"] for p in result["actual_series"]}
    assert by_date["2026-03-04"] == 125000  # before the transaction
    assert by_date["2026-03-05"] == 100000  # the day it landed


def test_projected_series_has_no_step_when_nothing_is_carried_forward(conn):
    account_id = _make_primary_account(conn, balance_cents=100000)
    _make_item(conn, account_id, day_of_month=20)  # in the future -- nothing overdue/due today
    result = build_cash_flow(conn, TODAY)
    today_points = [p for p in result["projected_series"] if p["date"] == "2026-03-14"]
    assert len(today_points) == 1  # no second point at the same x -- no step to draw


def test_overdue_item_creates_a_visible_step_and_carries_into_the_projection(conn):
    account_id = _make_primary_account(conn, balance_cents=100000)
    _make_item(conn, account_id, day_of_month=5)  # 9 days before TODAY, unticked, unmatched
    result = build_cash_flow(conn, TODAY)
    today_points = [p for p in result["projected_series"] if p["date"] == "2026-03-14"]
    assert len(today_points) == 2
    assert today_points[0]["balance_cents"] == 100000
    assert today_points[1]["balance_cents"] == -50000  # 100000 - 150000
    assert result["expected_next"]["recon_note"] == "1 overdue and 0 due today, carried in"
    assert result["expected_next"]["recon_amount_cents"] == 150000
    assert result["expected_next"]["rows"][0]["status"] == "overdue"


def test_due_today_only_gets_its_own_distinct_message(conn):
    account_id = _make_primary_account(conn, balance_cents=100000)
    _make_item(conn, account_id, day_of_month=14)  # lands exactly TODAY
    result = build_cash_flow(conn, TODAY)
    assert result["expected_next"]["recon_note"] == "1 due today, not through yet"


def test_clean_state_names_the_processed_count(conn):
    account_id = _make_primary_account(conn, balance_cents=100000)
    _make_item(conn, account_id, day_of_month=5)
    store.set_ticked(conn, "p1", "2026-03", True, "2026-03-05T00:00:00Z", open_period="2026-03")
    result = build_cash_flow(conn, TODAY)
    assert (
        result["expected_next"]["recon_note"]
        == "1 of this month's items already in today's balance"
    )
    # March's occurrence is processed and gone, but a monthly item recurs -- April 5th
    # is still 22 days inside the 30-day horizon and legitimately still upcoming.
    assert [r["date"] for r in result["expected_next"]["rows"]] == ["2026-04-05"]
    assert result["expected_next"]["rows"][0]["status"] == "upcoming"


def test_a_matched_transaction_processes_the_item_without_ticking(conn):
    account_id = _make_primary_account(conn, balance_cents=100000)
    _make_item(conn, account_id, day_of_month=5)
    conn.execute(
        "INSERT INTO txn (id, account_id, date, merchant_raw, merchant, amount_cents, plan_item_id, dedupe_hash) "
        "VALUES ('t1', ?, '2026-03-05', 'RENT', 'Landlord', -150000, 'p1', 'h1')",
        (account_id,),
    )
    conn.commit()
    result = build_cash_flow(conn, TODAY)
    # Same reasoning -- March's occurrence is processed (matched), April's recurs and
    # is still correctly upcoming.
    assert [r["date"] for r in result["expected_next"]["rows"]] == ["2026-04-05"]
    assert "already in today's balance" in result["expected_next"]["recon_note"]


def test_spread_items_contribute_to_totals_but_never_appear_as_event_rows(conn):
    account_id = _make_primary_account(conn, balance_cents=100000)
    _make_item(
        conn,
        account_id,
        kind="budget",
        reset_period="monthly",
        cadence="budget",
        day_of_month=None,
        cadence_unit=None,
        cadence_frequency=None,
        estimate_cents=-31000,
    )
    result = build_cash_flow(conn, TODAY)
    assert result["expected_next"]["rows"] == []
    # 17 remaining days in March (15..31) each draw from the spread -- the horizon end
    # value must differ from cash on hand by roughly the spread total, not be flat.
    assert result["projected_end_cents"] != result["cash_on_hand_cents"]


def test_plan_offset_reflects_a_budgets_active_adjustment(conn):
    # ticket #23: $150/mo (31-day March) cut to $93 total, set on the 11th -- days 1-10
    # keep the original -484/day rate, days 11-14 (today=14th) draw from the adjusted
    # remaining rate. Hand-verified: 10*(-484) + 4*(-213) = -5692 total for month_start
    # through today, against the same money-layer math adjusted_spread_daily_amounts's
    # own tests already prove correct in isolation.
    account_id = _make_primary_account(conn, balance_cents=100000)
    _make_item(
        conn,
        account_id,
        kind="budget",
        reset_period="monthly",
        cadence="budget",
        day_of_month=None,
        cadence_unit=None,
        cadence_frequency=None,
        estimate_cents=-15000,
    )
    store.set_adjusted(
        conn, "p1", "2026-03", -9300, "2026-03-01", "2026-03-11T00:00:00Z", open_period="2026-03"
    )

    result = build_cash_flow(conn, TODAY)
    # cash_on_hand (100000) - planned_today (100000 + -5692) = 5692
    assert result["plan_offset_cents"] == 5692


def test_materialized_matched_occurrence_drops_out_of_the_projection_via_its_own_matched_txn_id(
    conn,
):
    # ticket #21: once materialized, cash-flow must source "processed" from the row's
    # own matched_txn_id too (not just plan.py's Plan screen) -- the two screens must
    # never disagree about which occurrence dropped out of the carried-forward step.
    account_id = _make_primary_account(conn, balance_cents=100000)
    _make_item(conn, account_id, day_of_month=5)
    store.create_planned_posting(
        conn,
        posting_id="pp1",
        plan_item_id="p1",
        period="2026-03",
        expected_date="2026-03-05",
        expected_amount_cents=-150000,
        created_at="2026-03-01T00:00:00Z",
    )
    conn.execute(
        "INSERT INTO txn (id, account_id, date, merchant_raw, merchant, amount_cents, plan_item_id, dedupe_hash) "
        "VALUES ('t1', ?, '2026-03-05', 'RENT', 'Landlord', -150000, 'p1', 'h1')",
        (account_id,),
    )
    conn.commit()
    store.attribute_transaction_to_planned_posting(conn, "p1", "2026-03-05", "t1")

    result = build_cash_flow(conn, TODAY)
    assert [r["date"] for r in result["expected_next"]["rows"]] == ["2026-04-05"]
    assert "already in today's balance" in result["expected_next"]["recon_note"]


def test_materialized_unmatching_one_occurrence_never_flips_a_different_occurrences_status_in_cashflow(
    conn,
):
    # Same core fix as plan.py's own test of this scenario: each occurrence's matched
    # state is tracked independently, so clearing one transaction's match never
    # accidentally flips a DIFFERENT occurrence's Processed/Overdue status.
    account_id = _make_primary_account(conn, balance_cents=500000)
    _make_item(
        conn,
        account_id,
        name="Weekly gig",
        estimate_cents=10000,
        day_of_month=None,
        cadence_unit="week",
        cadence_frequency=1,
        anchor_date="2026-03-01",
    )
    store.create_planned_posting(
        conn,
        posting_id="pp1",
        plan_item_id="p1",
        period="2026-03",
        expected_date="2026-03-01",
        expected_amount_cents=10000,
        created_at="2026-03-01T00:00:00Z",
    )
    store.create_planned_posting(
        conn,
        posting_id="pp2",
        plan_item_id="p1",
        period="2026-03",
        expected_date="2026-03-08",
        expected_amount_cents=10000,
        created_at="2026-03-01T00:00:00Z",
    )
    conn.execute(
        "INSERT INTO txn (id, account_id, date, merchant_raw, merchant, amount_cents, plan_item_id, dedupe_hash) "
        "VALUES ('t1', ?, '2026-03-01', 'GIG PAYMENT', 'Client', 10000, 'p1', 'h1')",
        (account_id,),
    )
    conn.execute(
        "INSERT INTO txn (id, account_id, date, merchant_raw, merchant, amount_cents, plan_item_id, dedupe_hash) "
        "VALUES ('t2', ?, '2026-03-08', 'GIG PAYMENT', 'Client', 10000, 'p1', 'h2')",
        (account_id,),
    )
    conn.commit()
    store.attribute_transaction_to_planned_posting(conn, "p1", "2026-03-01", "t1")
    store.attribute_transaction_to_planned_posting(conn, "p1", "2026-03-08", "t2")

    # The user realizes t1 was a mismatch and clears it -- t2's own match is untouched.
    store.unattribute_transaction_from_planned_posting(conn, "t1")

    result = build_cash_flow(conn, TODAY)
    rows = [r for r in result["expected_next"]["rows"] if r["name"] == "Weekly gig"]
    carried = [r for r in rows if r["status"] != "upcoming"]
    # pp1 (03-01) is open again -> carried, overdue. pp2 (03-08) is STILL processed
    # (its own link to t2 was never touched) -> never appears in carried_rows.
    assert [r["date"] for r in carried] == ["2026-03-01"]
    assert carried[0]["status"] == "overdue"


def test_materialized_edited_amount_is_honored_not_the_items_live_estimate_cents(conn):
    # ticket #21 code review: cash-flow was discarding the materialized/edited amount
    # and always using item.estimate_cents -- a real, permanent disagreement with the
    # Plan screen (which correctly reads the edited amount) whenever a Planned Posting
    # is PATCHed after materializing.
    account_id = _make_primary_account(conn, balance_cents=100000)
    _make_item(conn, account_id, day_of_month=5)  # overdue -- carried forward
    store.create_planned_posting(
        conn,
        posting_id="pp1",
        plan_item_id="p1",
        period="2026-03",
        expected_date="2026-03-05",
        expected_amount_cents=-150000,
        created_at="2026-03-01T00:00:00Z",
    )
    store.update_planned_posting(conn, "pp1", {"expected_amount_cents": -175000}, "2026-03")

    result = build_cash_flow(conn, TODAY)
    today_points = [p for p in result["projected_series"] if p["date"] == "2026-03-14"]
    assert today_points[1]["balance_cents"] == -75000  # 100000 - 175000, not -150000
    assert result["expected_next"]["rows"][0]["estimate_cents"] == -175000


def test_materialized_deferred_date_within_the_period_does_not_double_count_or_vanish(conn):
    # ticket #21/#22 code review: the forward walk used to recompute occurrence dates
    # purely from the item's live Cadence, ignoring any Deferred edit -- an occurrence
    # Deferred from a future date to a past-or-today date (crossing the `today`
    # boundary) could be double-counted (once via the carried path, once via the stale
    # forward-walk date) or vanish (the reverse direction). Here it's deferred from the
    # 20th (future, would've been in the forward walk) to the 5th (already past
    # TODAY=14th).
    account_id = _make_primary_account(conn, balance_cents=100000)
    _make_item(conn, account_id, day_of_month=20)
    store.create_planned_posting(
        conn,
        posting_id="pp1",
        plan_item_id="p1",
        period="2026-03",
        expected_date="2026-03-20",
        expected_amount_cents=-150000,
        created_at="2026-03-01T00:00:00Z",
    )
    store.update_planned_posting(conn, "pp1", {"deferred_date": "2026-03-05"}, "2026-03")

    result = build_cash_flow(conn, TODAY)
    rows = [r for r in result["expected_next"]["rows"] if r["name"] == "Rent"]
    # Exactly one appearance -- carried in as overdue at its new (deferred) date, not
    # ALSO still upcoming on the 20th via a stale fresh-computed forward-walk date.
    assert [r["date"] for r in rows] == ["2026-03-05"]
    assert rows[0]["status"] == "overdue"
    today_points = [p for p in result["projected_series"] if p["date"] == "2026-03-14"]
    assert today_points[1]["balance_cents"] == -50000  # 100000 - 150000, counted exactly once


def test_materialized_deferred_date_across_a_month_boundary_does_not_vanish_from_the_projection(
    conn,
):
    # ticket #22: Deferred is explicitly allowed to push a date into the next real
    # calendar month (e.g. "align with an upcoming paycheck" landing just after
    # month-end) while the row's own period stays put (single-period override) --
    # capping the forward walk's materialized-aware branch at today_period's own last
    # day made a forward-deferred occurrence vanish from the projection entirely, a real
    # gap found testing this ticket live in a browser.
    account_id = _make_primary_account(conn, balance_cents=100000)
    _make_item(conn, account_id, day_of_month=25)
    store.create_planned_posting(
        conn,
        posting_id="pp1",
        plan_item_id="p1",
        period="2026-03",
        expected_date="2026-03-25",
        expected_amount_cents=-150000,
        created_at="2026-03-01T00:00:00Z",
    )
    store.update_planned_posting(conn, "pp1", {"deferred_date": "2026-04-01"}, "2026-03")

    result = build_cash_flow(conn, TODAY)
    rows = [r for r in result["expected_next"]["rows"] if r["name"] == "Rent"]
    assert [r["date"] for r in rows] == ["2026-04-01"]
    assert rows[0]["status"] == "upcoming"
    assert rows[0]["estimate_cents"] == -150000


def test_materialized_deferred_date_before_month_start_does_not_vanish_from_the_projection(conn):
    # ticket #23 code review: the mirror image of the forward-across-a-boundary fix
    # above -- a materialized occurrence Deferred BACKWARD, before month_start, used to
    # fall into neither the carried-forward branch (month_start <= occ <= today) nor the
    # forward-walk branch (today < occ), silently vanishing from the projection instead
    # of carrying forward as overdue like any other unreconciled item.
    account_id = _make_primary_account(conn, balance_cents=100000)
    _make_item(conn, account_id, day_of_month=5)
    store.create_planned_posting(
        conn,
        posting_id="pp1",
        plan_item_id="p1",
        period="2026-03",
        expected_date="2026-03-05",
        expected_amount_cents=-150000,
        created_at="2026-03-01T00:00:00Z",
    )
    store.update_planned_posting(conn, "pp1", {"deferred_date": "2026-02-25"}, "2026-03")

    result = build_cash_flow(conn, TODAY)
    rows = [r for r in result["expected_next"]["rows"] if r["name"] == "Rent"]
    # Two legitimately separate rows -- the deferred-backward March occurrence (now
    # overdue), and April's own regular occurrence (a genuinely different obligation,
    # fresh-computed since Month-End Close never materializes a future period; same
    # "not a duplicate" reasoning as the forward-deferred test above).
    assert [r["date"] for r in rows] == ["2026-02-25", "2026-04-05"]
    assert rows[0]["status"] == "overdue"
    today_points = [p for p in result["projected_series"] if p["date"] == "2026-03-14"]
    assert today_points[1]["balance_cents"] == -50000  # 100000 - 150000, carried into today's step


def test_plan_predicted_uses_a_weekly_budgets_own_reset_period_not_always_monthly(conn):
    # ticket #19: cashflow.py must plumb each Budget's own reset_period through to the
    # spread math, not assume Monthly for every Budget. Expected value independently
    # computed via money.spread_amount_for_date (unit-tested on its own, with hand-
    # verified numbers, in test_finance_money.py) -- this test verifies the WIRING
    # (does the item's real reset_period reach the spread cache), not the formula.
    account_id = _make_primary_account(conn, balance_cents=100000)
    _make_item(
        conn,
        account_id,
        kind="budget",
        reset_period="weekly",
        cadence="budget",
        day_of_month=None,
        cadence_unit=None,
        cadence_frequency=None,
        estimate_cents=-7000,
    )
    predicted = plan_predicted_for_primary_today(
        conn, TODAY
    )  # TODAY = 2026-03-14, month_start = 2026-03-01
    expected_spread_total = sum(
        money.spread_amount_for_date(-7000, date(2026, 3, d), "weekly") for d in range(1, 15)
    )
    assert predicted == 100000 + expected_spread_total
    # Sanity check this isn't accidentally the Monthly total instead -- the two must
    # genuinely differ for this to be a meaningful assertion.
    monthly_total = sum(
        money.spread_amount_for_date(-7000, date(2026, 3, d), "monthly") for d in range(1, 15)
    )
    assert expected_spread_total != monthly_total


def test_plan_predicted_monthly_budget_reset_period_still_matches_the_old_behavior(conn):
    # Regression guard: a Monthly Budget's total through this same refactor must still
    # match what plain calendar-month spreading always computed.
    account_id = _make_primary_account(conn, balance_cents=100000)
    _make_item(
        conn,
        account_id,
        kind="budget",
        reset_period="monthly",
        cadence="budget",
        day_of_month=None,
        cadence_unit=None,
        cadence_frequency=None,
        estimate_cents=-31000,
    )
    predicted = plan_predicted_for_primary_today(conn, TODAY)
    expected_spread_total = sum(
        money.spread_amount_for_date(-31000, date(2026, 3, d), "monthly") for d in range(1, 15)
    )
    assert predicted == 100000 + expected_spread_total


def test_floor_breach_is_detected_and_lowest_point_names_the_causing_item(conn):
    account_id = _make_primary_account(conn, balance_cents=100000)
    _make_item(conn, account_id, name="Big Bill", estimate_cents=-90000, day_of_month=20)
    store.set_floor_cents(conn, 50000)
    result = build_cash_flow(conn, TODAY)
    # Nothing brings the balance back above the floor for the rest of the 30-day
    # horizon once Big Bill hits on 2026-03-20, so every day from then through
    # horizon_end (2026-04-13) is below floor -- 25 days, not just the one event row.
    assert result["days_below_floor"] == 25
    assert len(result["breach_points"]) == 25
    assert result["lowest_point"]["after_item"] == "Big Bill"
    assert result["lowest_point"]["balance_cents"] == 10000  # 100000 - 90000
    assert result["lowest_point"]["date"] == "2026-03-20"


def test_lowest_point_attributes_to_an_overdue_item_even_though_its_own_date_is_in_the_past(conn):
    # Real bug (code review, 2026-08-17->18): an overdue item's event_row is dated at
    # its own historical occurrence (here, 9 days before TODAY), but its balance
    # effect actually lands AT today in projected_series once carried forward. The old
    # lookup matched on the row's own date and found nothing, silently losing
    # attribution even though this item is unambiguously the sole cause of the dip.
    account_id = _make_primary_account(conn, balance_cents=100000)
    # one-off, not monthly -- otherwise this same item recurs again within the 30-day
    # horizon (2026-04-05) and becomes the actual lowest point there instead, which
    # would test the forward-walk case this fix already handled, not the overdue case.
    _make_item(
        conn,
        account_id,
        name="Overdue Bill",
        estimate_cents=-90000,
        day_of_month=5,
        cadence="one-off",
        anchor_period="2026-03",
    )  # 2026-03-05, 9 days overdue
    result = build_cash_flow(conn, TODAY)
    assert result["lowest_point"]["after_item"] == "Overdue Bill"
    assert (
        result["lowest_point"]["balance_cents"] == 10000
    )  # 100000 - 90000, the post-step balance today
    assert result["lowest_point"]["date"] == "2026-03-14"  # today, not 2026-03-05


def test_days_below_floor_does_not_double_count_todays_two_projected_series_points(conn):
    # Real bug (code review, 2026-08-18): projected_series deliberately holds two
    # points for `today` when something carries forward (the raw balance, then the
    # post-step balance) -- that's the chart's own "step drawn, not implied"
    # requirement, not a bug. But days_below_floor/breach_points used to count both as
    # separate days. Here BOTH today-points are below the floor and every other day is
    # above it (a same-day income item recovers the balance from day+1 onward), so the
    # correct count is exactly 1, not 2.
    account_id = _make_primary_account(conn, balance_cents=140000)
    _make_item(
        conn, account_id, name="Overdue Bill", estimate_cents=-50000, day_of_month=5
    )  # 2026-03-05, overdue
    _make_item(
        conn,
        account_id,
        item_id="p2",
        name="Paycheck",
        estimate_cents=200000,
        day_of_month=15,
        payee=None,
    )  # 2026-03-15, tomorrow -- recovers the balance from day+1 onward
    store.set_floor_cents(conn, 150000)
    result = build_cash_flow(conn, TODAY)
    assert result["days_below_floor"] == 1
    assert len(result["breach_points"]) == 1
    assert result["breach_points"][0]["date"] == "2026-03-14"
    assert (
        result["breach_points"][0]["balance_cents"] == 90000
    )  # 140000 - 50000, the post-step (lower) value


def test_floor_breach_driven_purely_by_a_spread_item_is_still_counted(conn):
    """A spread item has no discrete occurrence row -- it accrues daily -- so the
    lowest-point/breach-count scan has to cover the full continuous projected_series,
    not just event_rows, or a spread-only breach is invisible to these headlines while
    still plainly visible on the chart (code review finding, 2026-08-17)."""
    account_id = _make_primary_account(conn, balance_cents=100000)
    _make_item(
        conn,
        account_id,
        name="Daily burn",
        kind="budget",
        reset_period="monthly",
        cadence="budget",
        day_of_month=None,
        cadence_unit=None,
        cadence_frequency=None,
        estimate_cents=-120000,
    )
    store.set_floor_cents(conn, 50000)
    result = build_cash_flow(conn, TODAY)
    assert result["days_below_floor"] > 0
    assert len(result["breach_points"]) == result["days_below_floor"]
    assert result["lowest_point"]["after_item"] is None
    assert result["lowest_point"]["balance_cents"] < 50000


def test_lowest_point_falls_back_to_horizon_end_when_no_dated_items_exist(conn):
    account_id = _make_primary_account(conn, balance_cents=100000)
    _make_item(
        conn,
        account_id,
        kind="budget",
        reset_period="monthly",
        cadence="budget",
        day_of_month=None,
        cadence_unit=None,
        cadence_frequency=None,
        estimate_cents=-3000,
    )
    result = build_cash_flow(conn, TODAY)
    assert result["lowest_point"]["after_item"] is None
    assert result["lowest_point"]["date"] == result["horizon_end"]


def test_plan_offset_reflects_drift_between_actual_and_plan_only_projection(conn):
    account_id = _make_primary_account(conn, balance_cents=100000)
    _make_item(conn, account_id, day_of_month=5, estimate_cents=-15000)
    # Real transaction differs from the plan's estimate for the same day -- a genuine
    # drift the offset headline should surface.
    conn.execute(
        "INSERT INTO txn (id, account_id, date, merchant_raw, merchant, amount_cents, plan_item_id, dedupe_hash) "
        "VALUES ('t1', ?, '2026-03-05', 'RENT', 'Landlord', -10000, 'p1', 'h1')",
        (account_id,),
    )
    conn.commit()
    result = build_cash_flow(conn, TODAY)
    # Plan alone predicted -15000; actual was -10000 -- 5000 cents ahead of plan.
    assert result["plan_offset_cents"] == 5000


def test_horizon_crossing_two_month_boundaries_still_includes_a_far_future_item(conn):
    # TODAY (Jan 31) + 30 days lands in early March -- a monthly item due Feb 15 falls
    # inside the horizon despite Jan/Feb/Mar all being touched.
    account_id = _make_primary_account(conn, balance_cents=100000)
    _make_item(conn, account_id, day_of_month=15)
    result = build_cash_flow(conn, date(2026, 1, 31))
    dates = {r["date"] for r in result["expected_next"]["rows"]}
    assert "2026-02-15" in dates


def test_adjustment_markers_reflect_real_records_in_window(conn):
    account_id = _make_primary_account(conn, balance_cents=100000)
    _make_item(conn, account_id, day_of_month=20)
    store.create_balance_adjustment(
        conn,
        adjustment_id="adj1",
        account_id=account_id,
        as_of_date="2026-03-10",
        real_balance_cents=95000,
        plan_predicted_cents=100000,
        created_at="2026-03-10T00:00:00Z",
    )
    result = build_cash_flow(conn, TODAY)
    assert result["adjustment_markers"] == [{"date": "2026-03-10", "balance_cents": 95000}]
    assert result["latest_adjustment"] == {"date": "2026-03-10"}


def test_no_adjustments_means_no_latest_adjustment(conn):
    account_id = _make_primary_account(conn, balance_cents=100000)
    _make_item(conn, account_id, day_of_month=20)
    result = build_cash_flow(conn, TODAY)
    assert result["latest_adjustment"] is None
    assert result["adjustment_markers"] == []


def test_actual_series_steps_at_a_correction_instead_of_flattening_the_whole_month(conn):
    """Code review finding, 2026-08-17: _reconstruct_actual_series used to derive the
    whole month's opening balance from the CURRENT account.balance_cents, so a
    mid-month correction silently rewrote the entire "actual" line back to month-start
    instead of showing a step at the correction date. Reproduces the reviewer's exact
    scenario: $1000 (month-start) -> corrected to $900 on day 5 -> corrected to $850 on
    day 10 -> today (day 14) still $850, no further transactions."""
    account_id = _make_primary_account(conn, balance_cents=85000)
    _make_item(conn, account_id, day_of_month=25)  # no occurrence in [month_start, today]
    store.create_balance_adjustment(
        conn,
        adjustment_id="adj1",
        account_id=account_id,
        as_of_date="2026-03-05",
        real_balance_cents=90000,
        plan_predicted_cents=100000,
        created_at="2026-03-05T00:00:00Z",
    )
    store.create_balance_adjustment(
        conn,
        adjustment_id="adj2",
        account_id=account_id,
        as_of_date="2026-03-10",
        real_balance_cents=85000,
        plan_predicted_cents=90000,
        created_at="2026-03-10T00:00:00Z",
    )
    result = build_cash_flow(conn, TODAY)
    by_date = {row["date"]: row["balance_cents"] for row in result["actual_series"]}
    # Before the first correction: derived backward from it (no earlier anchor exists),
    # so it reads as flat $900 -- an accepted approximation, not the bug under test.
    assert by_date["2026-03-01"] == 90000
    assert by_date["2026-03-04"] == 90000
    # The corrections themselves land as real steps, not smoothed away.
    assert by_date["2026-03-05"] == 90000
    assert by_date["2026-03-09"] == 90000
    assert by_date["2026-03-10"] == 85000
    assert by_date["2026-03-14"] == 85000


def test_plan_predicted_series_covers_month_start_through_today(conn):
    account_id = _make_primary_account(conn, balance_cents=100000)
    _make_item(conn, account_id, day_of_month=20)  # not yet due -- no noise in the walk
    result = build_cash_flow(conn, TODAY)
    dates = [p["date"] for p in result["plan_predicted_series"]]
    assert dates[0] == "2026-03-01"
    assert dates[-1] == "2026-03-14"
    assert len(dates) == 14


def test_plan_predicted_series_last_point_matches_the_plan_offset_relationship(conn):
    account_id = _make_primary_account(conn, balance_cents=100000)
    _make_item(conn, account_id, day_of_month=5, estimate_cents=-15000)
    conn.execute(
        "INSERT INTO txn (id, account_id, date, merchant_raw, merchant, amount_cents, plan_item_id, dedupe_hash) "
        "VALUES ('t1', ?, '2026-03-05', 'RENT', 'Landlord', -10000, 'p1', 'h1')",
        (account_id,),
    )
    conn.commit()
    result = build_cash_flow(conn, TODAY)
    last_point = result["plan_predicted_series"][-1]
    assert last_point["date"] == "2026-03-14"
    # Same invariant test_plan_predicted_for_primary_today_matches_the_cash_flow_offset_relationship
    # proves for the scalar -- the series' own last point must agree with it exactly.
    assert last_point["balance_cents"] == 100000 - result["plan_offset_cents"]


def test_plan_predicted_series_reflects_a_budgets_active_adjustment_day_by_day(conn):
    # Same fixture as test_plan_offset_reflects_a_budgets_active_adjustment (ticket #23):
    # $150/mo (31-day March) cut to $93 total, set on the 11th -- days 1-10 keep the
    # original -484/day rate, days 11-14 draw from the adjusted -213/day rate. That test
    # only proves the AGGREGATE offset; this proves the rate change is visible DAY BY
    # DAY in the series (fable-os-web#69's offset line needs the actual shape, not just
    # the endpoint).
    account_id = _make_primary_account(conn, balance_cents=100000)
    _make_item(
        conn,
        account_id,
        kind="budget",
        reset_period="monthly",
        cadence="budget",
        day_of_month=None,
        cadence_unit=None,
        cadence_frequency=None,
        estimate_cents=-15000,
    )
    store.set_adjusted(
        conn, "p1", "2026-03", -9300, "2026-03-01", "2026-03-11T00:00:00Z", open_period="2026-03"
    )

    result = build_cash_flow(conn, TODAY)
    by_date = {p["date"]: p["balance_cents"] for p in result["plan_predicted_series"]}
    # Pre-adjustment rate: -484/day.
    assert by_date["2026-03-01"] == 100000 - 484
    assert by_date["2026-03-02"] == 100000 - 484 * 2
    assert by_date["2026-03-10"] == 100000 - 484 * 10
    # Post-adjustment rate kicks in on the 11th: -213/day off the day-10 balance.
    assert by_date["2026-03-11"] == by_date["2026-03-10"] - 213
    # Final point matches the hand-verified total from the sibling aggregate test.
    assert by_date["2026-03-14"] == 100000 - 5692


def test_plan_predicted_series_does_not_anchor_snap_to_a_balance_correction(conn):
    """Same fixture as test_actual_series_steps_at_a_correction_instead_of_flattening_the_whole_month,
    proving the OPPOSITE requirement for plan_predicted_series: it must NOT step at a
    real correction the way actual_series correctly does. It's a pure "what the plan
    alone predicts" simulation with no corrections to snap to (fable-os-web#69) -- with
    no dated occurrence or spread item landing in [month_start, today], it should stay
    flat at the SAME opening balance the actual walk starts from (90000, derived from
    the earliest anchor), never dropping to the day-10 correction's 85000."""
    account_id = _make_primary_account(conn, balance_cents=85000)
    _make_item(conn, account_id, day_of_month=25)  # no occurrence in [month_start, today]
    store.create_balance_adjustment(
        conn,
        adjustment_id="adj1",
        account_id=account_id,
        as_of_date="2026-03-05",
        real_balance_cents=90000,
        plan_predicted_cents=100000,
        created_at="2026-03-05T00:00:00Z",
    )
    store.create_balance_adjustment(
        conn,
        adjustment_id="adj2",
        account_id=account_id,
        as_of_date="2026-03-10",
        real_balance_cents=85000,
        plan_predicted_cents=90000,
        created_at="2026-03-10T00:00:00Z",
    )
    result = build_cash_flow(conn, TODAY)
    by_date = {p["date"]: p["balance_cents"] for p in result["plan_predicted_series"]}
    assert by_date["2026-03-01"] == 90000
    assert by_date["2026-03-10"] == 90000
    assert by_date["2026-03-14"] == 90000
    # Meanwhile actual_series DOES step, per the sibling test -- confirming the two
    # series genuinely diverge in behavior here, not just in value.
    actual_by_date = {p["date"]: p["balance_cents"] for p in result["actual_series"]}
    assert actual_by_date["2026-03-10"] == 85000


def test_plan_predicted_for_primary_today_returns_none_with_no_primary_account(conn):
    assert plan_predicted_for_primary_today(conn, TODAY) is None


def test_plan_predicted_for_primary_today_matches_the_cash_flow_offset_relationship(conn):
    account_id = _make_primary_account(conn, balance_cents=100000)
    _make_item(conn, account_id, day_of_month=5, estimate_cents=-15000)
    conn.execute(
        "INSERT INTO txn (id, account_id, date, merchant_raw, merchant, amount_cents, plan_item_id, dedupe_hash) "
        "VALUES ('t1', ?, '2026-03-05', 'RENT', 'Landlord', -10000, 'p1', 'h1')",
        (account_id,),
    )
    conn.commit()
    result = build_cash_flow(conn, TODAY)
    predicted = plan_predicted_for_primary_today(conn, TODAY)
    assert predicted == 100000 - result["plan_offset_cents"]


def test_week_unit_dated_item_lands_more_than_once_in_the_forward_horizon(conn):
    # ADR-0018: a biweekly item can land twice within the 30-day forward window --
    # the old occurrence_date() couldn't express this at all (one occurrence per
    # period, max). TODAY=2026-03-14, anchored 2026-03-20 (a Friday) -> next occurrence
    # 2026-04-03, both inside (TODAY, TODAY+30] = (2026-03-14, 2026-04-13].
    account_id = _make_primary_account(conn, balance_cents=500000)
    _make_item(
        conn,
        account_id,
        name="Paycheck",
        estimate_cents=200000,
        day_of_month=None,
        cadence_unit="week",
        cadence_frequency=2,
        anchor_date="2026-03-20",
    )
    result = build_cash_flow(conn, TODAY)
    dates = [r["date"] for r in result["expected_next"]["rows"] if r["name"] == "Paycheck"]
    assert dates == ["2026-03-20", "2026-04-03"]
    # Both occurrences actually stepped the projected balance, not just listed.
    balances = {
        r["date"]: r["balance_cents"]
        for r in result["expected_next"]["rows"]
        if r["name"] == "Paycheck"
    }
    assert balances["2026-03-20"] == 500000 + 200000
    assert balances["2026-04-03"] == 500000 + 200000 + 200000


def test_a_matched_occurrence_does_not_mark_a_later_unmatched_occurrence_processed_too(conn):
    # The bug this guards against (caught live-testing the first real week-unit item):
    # a plain "did ANYTHING match this item this period" boolean would carry BOTH
    # occurrences as processed the moment just the earlier one's real transaction
    # landed. TODAY=2026-03-14, weekly item anchored 2026-03-01 -> occurrences at
    # 03-01 and 03-08, both <= today. Only 03-01 has a real matching transaction.
    account_id = _make_primary_account(conn, balance_cents=500000)
    _make_item(
        conn,
        account_id,
        name="Weekly gig",
        estimate_cents=10000,
        day_of_month=None,
        cadence_unit="week",
        cadence_frequency=1,
        anchor_date="2026-03-01",
    )
    conn.execute(
        "INSERT INTO txn (id, account_id, date, merchant_raw, merchant, amount_cents, plan_item_id, dedupe_hash) "
        "VALUES ('t1', ?, '2026-03-01', 'GIG PAYMENT', 'Client', 10000, 'p1', 'h1')",
        (account_id,),
    )
    conn.commit()
    result = build_cash_flow(conn, TODAY)
    # Only the 2nd (still-unmatched) occurrence carries forward as overdue -- the 1st
    # is genuinely processed and must not appear there at all. (Later weekly occurrences
    # also show up as ordinary "upcoming" forward-walk rows -- unconditional regardless
    # of matched status, not what this test is about; filtered out below.)
    rows = [r for r in result["expected_next"]["rows"] if r["name"] == "Weekly gig"]
    carried = [r for r in rows if r["status"] != "upcoming"]
    assert [r["date"] for r in carried] == ["2026-03-08"]
    assert carried[0]["status"] == "overdue"
    assert "2026-03-01" not in [r["date"] for r in rows]  # the matched one never appears
