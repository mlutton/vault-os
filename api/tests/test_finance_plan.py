from datetime import date

import pytest

from vaultos.db.conn import connect
from vaultos.modules.finance import store
from vaultos.modules.finance.plan import build_plan_summary


@pytest.fixture
def conn(tmp_path):
    return connect(tmp_path / "vaultos.db")


@pytest.fixture
def account_id(conn):
    account = store.create_account(
        conn, account_id="a1", nickname="Checking", institution=None, account_type="checking",
        last_four=None, balance_cents=0, is_primary=True, created_at="2026-08-17T00:00:00Z",
    )
    return account.id


def _make_item(conn, account_id, **over):
    defaults = dict(
        item_id="p1", name="Rent", estimate_cents=-150000, plan_type="Rent", payee="Landlord",
        day_of_month=1, cadence="dated", cadence_unit="month", cadence_frequency=1,
        anchor_period=None, account_id=account_id,
        verified=True, is_catch_all=False, match_text=[],
    )
    defaults.update(over)
    return store.create_plan_item(conn, **defaults)


TODAY = date(2026, 3, 20)


def test_empty_plan_has_no_rows_and_zeroed_totals(conn):
    summary = build_plan_summary(conn, "2026-03", TODAY)
    assert summary["items"] == []
    assert summary["out_cents"] == 0
    assert summary["progress_total"] == 0
    assert summary["previous_month_note"] is None


def test_monthly_item_occurring_this_period_is_a_checkable_row(conn, account_id):
    _make_item(conn, account_id, day_of_month=14)
    summary = build_plan_summary(conn, "2026-03", TODAY)
    assert len(summary["items"]) == 1
    row = summary["items"][0]
    assert row["checkable"] is True
    assert row["lands"] == "2026-03-14"
    assert row["status"] == "overdue"  # 14th has passed relative to TODAY (the 20th), unticked, unmatched
    assert summary["out_cents"] == -150000
    assert summary["dated_count"] == 1
    assert summary["progress_total"] == 1
    assert summary["progress_landed"] == 0


def test_no_materialized_planned_postings_falls_back_to_computing_fresh(conn, account_id):
    # ticket #20: before Month-End Close has ever run for a period (or for any period
    # that isn't the current Open Period, which nothing materializes), the Plan screen
    # must keep working exactly as it did before this ticket -- computed fresh from the
    # item's own Cadence, not silently blank.
    _make_item(conn, account_id, day_of_month=14)
    summary = build_plan_summary(conn, "2026-03", TODAY)
    row = summary["items"][0]
    assert row["lands"] == "2026-03-14"
    assert row["estimate_cents"] == -150000


def test_a_materialized_planned_posting_is_used_instead_of_a_fresh_occurrence(conn, account_id):
    # ticket #20's own acceptance criterion: a directly-edited Planned Posting row is
    # reflected on the Plan screen -- both its expected_date AND its expected_amount_cents
    # (a frozen snapshot, not a live join back to plan_item.estimate_cents) override
    # what fresh Cadence computation would otherwise say.
    _make_item(conn, account_id, day_of_month=14, estimate_cents=-150000)
    store.create_planned_posting(
        conn, posting_id="pp1", plan_item_id="p1", period="2026-03",
        expected_date="2026-03-25", expected_amount_cents=-160000, created_at="2026-03-01T00:00:00Z",
    )
    summary = build_plan_summary(conn, "2026-03", TODAY)
    row = summary["items"][0]
    assert row["lands"] == "2026-03-25"  # the materialized date, not the 14th
    assert row["estimate_cents"] == -160000  # the frozen snapshot, not item.estimate_cents
    assert summary["out_cents"] == -160000


def test_a_multi_occurrence_materialized_item_sums_its_own_per_occurrence_amounts(conn, account_id):
    # A week-unit item with two materialized rows -- the period total sums each row's
    # OWN expected_amount_cents, not a flat item.estimate_cents x occurrence_count
    # multiply, so a per-occurrence edit (once Deferred/a raw edit sets one) is honored.
    _make_item(
        conn, account_id, name="Paycheck", estimate_cents=200000,
        day_of_month=None, cadence_unit="week", cadence_frequency=2, anchor_date="2026-03-06",
    )
    store.create_planned_posting(
        conn, posting_id="pp1", plan_item_id="p1", period="2026-03",
        expected_date="2026-03-06", expected_amount_cents=200000, created_at="2026-03-01T00:00:00Z",
    )
    store.create_planned_posting(
        conn, posting_id="pp2", plan_item_id="p1", period="2026-03",
        expected_date="2026-03-20", expected_amount_cents=210000, created_at="2026-03-01T00:00:00Z",
    )
    summary = build_plan_summary(conn, "2026-03", TODAY)
    row = summary["items"][0]
    assert row["occurrence_count"] == 2
    assert row["estimate_cents"] == 410000  # 200000 + 210000, not 200000 x 2
    assert summary["in_cents"] == 410000


def test_quarterly_item_outside_its_cycle_contributes_no_row_and_no_total(conn, account_id):
    _make_item(conn, account_id, cadence_frequency=3, anchor_period="2026-01", day_of_month=1)
    # January/April/July/October -- March is outside the cycle.
    summary = build_plan_summary(conn, "2026-03", TODAY)
    assert summary["items"] == []
    assert summary["out_cents"] == 0
    assert summary["dated_count"] == 0


def test_spread_item_contributes_to_totals_but_is_never_checkable(conn, account_id):
    _make_item(
        conn, account_id, kind="budget", reset_period="monthly", cadence="budget",
        day_of_month=None, cadence_unit=None, cadence_frequency=None, estimate_cents=-15000,
    )
    summary = build_plan_summary(conn, "2026-03", TODAY)
    row = summary["items"][0]
    assert row["checkable"] is False
    assert row["lands"] is None
    assert row["ticked"] is None
    assert row["status"] is None
    assert summary["spread_count"] == 1
    assert summary["dated_count"] == 0
    assert summary["out_cents"] == -15000
    assert summary["progress_total"] == 0  # spread items never enter the checkable set


def test_weekly_spread_item_scales_its_total_by_how_many_resets_land_this_period(conn, account_id):
    # ticket #19: a Weekly Budget resets more than once within a calendar-month period
    # -- its row and the header total must both reflect every reset that lands this
    # period. The MONEY total is day-precise (matching cash-flow's own per-day math),
    # not 5 whole weeks x -7000 = -35000 -- the two boundary weeks (starting
    # 2026-02-23 and 2026-03-30) only partly fall in March, so the true total is
    # -31000 (a real cross-screen inconsistency an earlier whole-week-count version of
    # this fix had, caught in code review). occurrence_count (a separate, display-only
    # "how many times did this reset" figure) still reports the whole-week count.
    _make_item(
        conn, account_id, kind="budget", reset_period="weekly", cadence="budget",
        day_of_month=None, cadence_unit=None, cadence_frequency=None, estimate_cents=-7000,
    )
    summary = build_plan_summary(conn, "2026-03", TODAY)
    row = summary["items"][0]
    assert row["estimate_cents"] == -31000
    # March 2026 has 5 Mondays (the 2nd, 9th, 16th, 23rd, 30th) -- 5 weekly resets.
    assert row["occurrence_count"] == 5
    assert row["checkable"] is False
    assert summary["spread_count"] == 1
    assert summary["out_cents"] == -31000


def test_budget_row_reflects_an_active_adjustment_for_the_current_window(conn, account_id):
    # ticket #23: $150/mo (31 days in March) cut to $93 total, set on the 11th -- 10
    # days already elapsed at the original -484/day rate (-4840 total), the remaining
    # -4460 splits over the 21 remaining days. Hand-verified against
    # adjusted_spread_daily_amounts's own money-layer tests; here we just confirm the
    # Plan row surfaces the adjusted total and the adjustment itself.
    _make_item(
        conn, account_id, kind="budget", reset_period="monthly", cadence="budget",
        day_of_month=None, cadence_unit=None, cadence_frequency=None, estimate_cents=-15000,
    )
    store.set_adjusted(conn, "p1", "2026-03", -9300, "2026-03-01", "2026-03-11T00:00:00Z", open_period="2026-03")
    summary = build_plan_summary(conn, "2026-03", TODAY)
    row = summary["items"][0]
    assert row["estimate_cents"] == -9300
    assert row["adjusted_target_cents"] == -9300
    assert summary["out_cents"] == -9300


def test_budget_row_ignores_a_stale_adjustment_from_a_previous_reset_window(conn, account_id):
    # An adjustment set for FEBRUARY's window must not bleed into March once a new
    # Reset Period has begun -- "reverts automatically at the next Reset Period"
    # (CONTEXT.md), with nothing anywhere explicitly clearing it.
    _make_item(
        conn, account_id, kind="budget", reset_period="monthly", cadence="budget",
        day_of_month=None, cadence_unit=None, cadence_frequency=None, estimate_cents=-15000,
    )
    store.set_adjusted(conn, "p1", "2026-02", -5000, "2026-02-01", "2026-02-11T00:00:00Z", open_period="2026-03")
    summary = build_plan_summary(conn, "2026-03", TODAY)
    row = summary["items"][0]
    assert row["estimate_cents"] == -15000  # the baseline, untouched
    assert row["adjusted_target_cents"] is None


def test_budget_row_finds_a_weekly_adjustment_recorded_under_the_previous_months_bucket(conn, account_id):
    # ticket #23 code review: a Weekly Budget's Reset window can straddle a month
    # boundary -- set on Aug 31 (a Monday, this window's own start) for the Aug 31-Sep 6
    # window, the override is stored under period="2026-08" (August was the Open Period
    # when it was set). Viewing SEPTEMBER's Plan summary on Sep 1 (still within that
    # same live window) must still find and apply it, even though September's own
    # plan_period bucket has no row for this item at all.
    #
    # $70/week (-7000) cut to $35 (-3500), set exactly at the window's own start (0
    # elapsed days). Hand-verified September total: the boundary week's in-September
    # days (Sep 1-6, 6 of them) drop from -1000/day baseline to -500/day adjusted (-3000
    # instead of -6000); the three fully-in-September weeks (-7000 each) and the next
    # boundary week's in-September days (Sep 28-30, 3 days at -1000/day = -3000) are
    # untouched -- baseline September total -30000, adjusted -27000.
    _make_item(
        conn, account_id, kind="budget", reset_period="weekly", cadence="budget",
        day_of_month=None, cadence_unit=None, cadence_frequency=None, estimate_cents=-7000,
    )
    store.set_adjusted(conn, "p1", "2026-08", -3500, "2026-08-31", "2026-08-31T00:00:00Z", open_period="2026-08")

    summary = build_plan_summary(conn, "2026-09", date(2026, 9, 1))
    row = summary["items"][0]
    assert row["adjusted_target_cents"] == -3500
    assert row["estimate_cents"] == -27000


def test_income_item_counts_toward_in_cents_and_income_count_not_out(conn, account_id):
    _make_item(conn, account_id, item_id="p2", name="Paycheck", estimate_cents=325000, day_of_month=1)
    summary = build_plan_summary(conn, "2026-03", TODAY)
    assert summary["in_cents"] == 325000
    assert summary["out_cents"] == 0
    assert summary["income_count"] == 1


def test_unverified_item_is_counted(conn, account_id):
    _make_item(conn, account_id, verified=False)
    summary = build_plan_summary(conn, "2026-03", TODAY)
    assert summary["unverified_count"] == 1


def test_ticking_an_item_makes_it_processed_and_landed(conn, account_id):
    _make_item(conn, account_id, day_of_month=14)
    store.set_ticked(conn, "p1", "2026-03", True, "2026-03-14T00:00:00Z", open_period="2026-03")
    summary = build_plan_summary(conn, "2026-03", TODAY)
    row = summary["items"][0]
    assert row["status"] == "processed"
    assert row["ticked"] is True
    assert summary["progress_landed"] == 1
    assert summary["cleared_outflow_cents"] == -150000


def test_a_matched_transaction_makes_it_processed_even_if_unticked(conn, account_id):
    _make_item(conn, account_id, day_of_month=14)
    conn.execute(
        "INSERT INTO txn (id, account_id, date, merchant_raw, merchant, amount_cents, plan_item_id, dedupe_hash) "
        "VALUES ('t1', ?, '2026-03-14', 'RENT', 'Landlord', -150000, 'p1', 'h1')",
        (account_id,),
    )
    conn.commit()
    summary = build_plan_summary(conn, "2026-03", TODAY)
    row = summary["items"][0]
    assert row["status"] == "processed"
    assert row["ticked"] is False
    assert row["has_imports"] is True
    assert row["actual_cents"] == -150000


def test_materialized_occurrence_status_uses_its_own_matched_txn_id_not_a_count(conn, account_id):
    # ticket #21: once materialized, status is sourced from the row's OWN
    # matched_txn_id, not the old count_matched_transactions_for_period pairing.
    _make_item(conn, account_id, day_of_month=1)
    store.create_planned_posting(
        conn, posting_id="pp1", plan_item_id="p1", period="2026-03",
        expected_date="2026-03-01", expected_amount_cents=-150000, created_at="2026-03-01T00:00:00Z",
    )
    conn.execute(
        "INSERT INTO txn (id, account_id, date, merchant_raw, merchant, amount_cents, plan_item_id, dedupe_hash) "
        "VALUES ('t1', ?, '2026-03-01', 'RENT', 'Landlord', -150000, 'p1', 'h1')",
        (account_id,),
    )
    conn.commit()
    store.attribute_transaction_to_planned_posting(conn, "p1", "2026-03-01", "t1")

    summary = build_plan_summary(conn, "2026-03", TODAY)
    row = summary["items"][0]
    assert row["status"] == "processed"
    assert summary["progress_landed"] == 1


def test_posting_row_exposes_occurrences_with_both_cadence_and_deferred_dates(conn, account_id):
    # ticket #22: the Plan screen needs both dates per occurrence to offer a Defer
    # action and show what it did -- the permanent Cadence reference alongside the
    # deferred override, not just the merged effective date.
    _make_item(conn, account_id, day_of_month=14)
    store.create_planned_posting(
        conn, posting_id="pp1", plan_item_id="p1", period="2026-03",
        expected_date="2026-03-14", expected_amount_cents=-150000, created_at="2026-03-01T00:00:00Z",
    )
    store.update_planned_posting(conn, "pp1", {"deferred_date": "2026-03-20"}, "2026-03")

    summary = build_plan_summary(conn, "2026-03", TODAY)
    row = summary["items"][0]
    assert row["occurrences"] == [
        {"id": "pp1", "cadence_date": "2026-03-14", "deferred_date": "2026-03-20", "date": "2026-03-20", "status": "due_today"},
    ]
    assert row["lands"] == "2026-03-20"  # the effective (deferred) date


def test_posting_row_occurrences_is_none_before_month_end_close_has_materialized_anything(conn, account_id):
    # Deferred moves a real Planned Posting's date -- there's nothing to defer until
    # one exists, so the frontend must not be offered a Defer action here at all.
    _make_item(conn, account_id, day_of_month=14)
    summary = build_plan_summary(conn, "2026-03", TODAY)
    row = summary["items"][0]
    assert row["occurrences"] is None


def test_budget_row_occurrences_is_always_none(conn, account_id):
    # Deferred has no equivalent for a Budget (ADR-0019) -- Adjusted is the Budget-only
    # override, and never materializes anything occurrence-shaped to point a Defer
    # action at.
    _make_item(
        conn, account_id, kind="budget", reset_period="monthly", cadence="budget",
        day_of_month=None, cadence_unit=None, cadence_frequency=None, estimate_cents=-31000,
    )
    summary = build_plan_summary(conn, "2026-03", TODAY)
    row = summary["items"][0]
    assert row["occurrences"] is None


def test_materialized_multi_occurrence_closes_in_order_never_reversed(conn, account_id):
    # A biweekly item landing twice (Mar 6, Mar 20) -- only the FIRST occurrence has a
    # real match; the second must read Overdue (its own date has passed with nothing
    # attributed to it), never silently marked processed just because SOME transaction
    # matched this item somewhere this period.
    _make_item(
        conn, account_id, name="Paycheck", estimate_cents=200000,
        day_of_month=None, cadence_unit="week", cadence_frequency=2, anchor_date="2026-03-06",
    )
    store.create_planned_posting(
        conn, posting_id="pp1", plan_item_id="p1", period="2026-03",
        expected_date="2026-03-06", expected_amount_cents=200000, created_at="2026-03-01T00:00:00Z",
    )
    store.create_planned_posting(
        conn, posting_id="pp2", plan_item_id="p1", period="2026-03",
        expected_date="2026-03-20", expected_amount_cents=200000, created_at="2026-03-01T00:00:00Z",
    )
    conn.execute(
        "INSERT INTO txn (id, account_id, date, merchant_raw, merchant, amount_cents, plan_item_id, dedupe_hash) "
        "VALUES ('t1', ?, '2026-03-06', 'ACME PAYROLL', 'Acme', 200000, 'p1', 'h1')",
        (account_id,),
    )
    conn.commit()
    store.attribute_transaction_to_planned_posting(conn, "p1", "2026-03-06", "t1")

    today = date(2026, 3, 25)  # both occurrences' dates are in the past
    summary = build_plan_summary(conn, "2026-03", today)
    row = summary["items"][0]
    # worst of {processed (pp1), overdue (pp2, no match)} -> overdue
    assert row["status"] == "overdue"
    assert summary["progress_landed"] == 1
    assert summary["progress_total"] == 2


def test_materialized_unmatching_one_occurrence_never_flips_a_different_occurrences_status(conn, account_id):
    # ticket #21's core fix, demonstrated concretely: each occurrence's matched state
    # is tracked independently via its own matched_txn_id, not re-inferred from a
    # shared per-item COUNT. Under the old count-based pairing, clearing the EARLIER
    # transaction's match would have shrunk the count from 2 to 1 and reassigned
    # "processed" credit to the chronologically-first occurrence by index -- silently
    # flipping the SECOND occurrence (which still has its own real, untouched match)
    # to look unprocessed, and the first (which just lost its match) to look processed.
    _make_item(
        conn, account_id, name="Paycheck", estimate_cents=200000,
        day_of_month=None, cadence_unit="week", cadence_frequency=2, anchor_date="2026-03-06",
    )
    store.create_planned_posting(
        conn, posting_id="pp1", plan_item_id="p1", period="2026-03",
        expected_date="2026-03-06", expected_amount_cents=200000, created_at="2026-03-01T00:00:00Z",
    )
    store.create_planned_posting(
        conn, posting_id="pp2", plan_item_id="p1", period="2026-03",
        expected_date="2026-03-20", expected_amount_cents=200000, created_at="2026-03-01T00:00:00Z",
    )
    conn.execute(
        "INSERT INTO txn (id, account_id, date, merchant_raw, merchant, amount_cents, plan_item_id, dedupe_hash) "
        "VALUES ('t1', ?, '2026-03-06', 'ACME PAYROLL', 'Acme', 200000, 'p1', 'h1')",
        (account_id,),
    )
    conn.execute(
        "INSERT INTO txn (id, account_id, date, merchant_raw, merchant, amount_cents, plan_item_id, dedupe_hash) "
        "VALUES ('t2', ?, '2026-03-20', 'ACME PAYROLL', 'Acme', 200000, 'p1', 'h2')",
        (account_id,),
    )
    conn.commit()
    store.attribute_transaction_to_planned_posting(conn, "p1", "2026-03-06", "t1")
    store.attribute_transaction_to_planned_posting(conn, "p1", "2026-03-20", "t2")

    # The user realizes t1 was a mismatch and clears it -- t2's own match is untouched.
    store.unattribute_transaction_from_planned_posting(conn, "t1")

    today = date(2026, 3, 25)
    summary = build_plan_summary(conn, "2026-03", today)
    row = summary["items"][0]
    # pp2 (Mar 20) is STILL processed (its own link to t2 was never touched); pp1
    # (Mar 6) is open again, its date passed -> overdue. Worst of the two -> overdue,
    # but progress_landed must correctly credit the SURVIVING match (pp2), not pp1.
    assert row["status"] == "overdue"
    assert summary["progress_landed"] == 1


def test_no_imports_this_period_shows_none_not_a_misleading_zero(conn, account_id):
    _make_item(conn, account_id, day_of_month=14)
    summary = build_plan_summary(conn, "2026-03", TODAY)
    row = summary["items"][0]
    assert row["has_imports"] is False
    assert row["actual_cents"] is None


def test_imports_exist_but_this_item_has_no_match_shows_a_real_zero(conn, account_id):
    _make_item(conn, account_id, day_of_month=14)
    # A transaction landed this period, but for a DIFFERENT (nonexistent) plan item --
    # imports genuinely happened, this item just didn't match anything.
    conn.execute(
        "INSERT INTO txn (id, account_id, date, merchant_raw, merchant, amount_cents, dedupe_hash) "
        "VALUES ('t1', ?, '2026-03-05', 'COFFEE', 'Coffee Shop', -500, 'h1')",
        (account_id,),
    )
    conn.commit()
    summary = build_plan_summary(conn, "2026-03", TODAY)
    row = summary["items"][0]
    assert row["has_imports"] is True
    assert row["actual_cents"] == 0


def test_previous_month_note_absent_when_requesting_a_non_current_period(conn, account_id):
    _make_item(conn, account_id, day_of_month=1)
    summary = build_plan_summary(conn, "2026-02", TODAY)  # requesting the PREVIOUS month directly
    assert summary["previous_month_note"] is None


def test_previous_month_note_cold_and_untouched(conn, account_id):
    _make_item(conn, account_id, day_of_month=1)  # occurs every month, including February
    summary = build_plan_summary(conn, "2026-03", TODAY)
    assert summary["previous_month_note"] == "february not reconciled yet"


def test_previous_month_note_in_progress(conn, account_id):
    _make_item(conn, account_id, item_id="p1", day_of_month=1)
    _make_item(conn, account_id, item_id="p2", name="Utility", day_of_month=5)
    # open_period is the CURRENT month ("2026-03", matching TODAY) while this ticks the
    # PRIOR month -- exactly the catch-up-on-last-month workflow ticket #18's scoping
    # decision deliberately keeps working (see set_ticked's own docstring).
    store.set_ticked(conn, "p1", "2026-02", True, "2026-02-01T00:00:00Z", open_period="2026-03")
    summary = build_plan_summary(conn, "2026-03", TODAY)
    assert summary["previous_month_note"] == "1 items left in february"


def test_previous_month_note_absent_once_fully_reconciled(conn, account_id):
    _make_item(conn, account_id, day_of_month=1)
    store.set_ticked(conn, "p1", "2026-02", True, "2026-02-01T00:00:00Z", open_period="2026-03")
    summary = build_plan_summary(conn, "2026-03", TODAY)
    assert summary["previous_month_note"] is None


def test_previous_month_note_absent_when_nothing_was_checkable_last_month(conn, account_id):
    # A quarterly item anchored so it doesn't occur in February -- last month had nothing
    # to reconcile at all, so there's no "not reconciled yet" to report either.
    _make_item(conn, account_id, cadence_frequency=3, anchor_period="2026-01", day_of_month=1)
    summary = build_plan_summary(conn, "2026-03", TODAY)
    assert summary["previous_month_note"] is None


def test_january_rolls_back_to_december_of_the_previous_year(conn, account_id):
    _make_item(conn, account_id, day_of_month=1)
    summary = build_plan_summary(conn, "2026-01", date(2026, 1, 15))
    assert summary["previous_month_note"] == "december not reconciled yet"


def test_income_and_unverified_counts_exclude_items_outside_their_cycle_this_period(conn, account_id):
    # A quarterly income item anchored to Jan/Apr/Jul/Oct, unverified -- queried for
    # March, which is outside its cycle. It must contribute nothing anywhere, including
    # the income/unverified counts, matching the empty items list.
    _make_item(
        conn, account_id, item_id="p1", name="Bonus", estimate_cents=50000, cadence_frequency=3,
        anchor_period="2026-01", day_of_month=1, verified=False,
    )
    summary = build_plan_summary(conn, "2026-03", TODAY)
    assert summary["items"] == []
    assert summary["income_count"] == 0
    assert summary["unverified_count"] == 0

    # The same item DOES count in a period where it actually occurs.
    summary_in_cycle = build_plan_summary(conn, "2026-04", TODAY)
    assert summary_in_cycle["income_count"] == 1
    assert summary_in_cycle["unverified_count"] == 1


def test_a_week_unit_item_landing_twice_this_period_is_still_one_row_with_scaled_totals(conn, account_id):
    # ADR-0018's "lighter" decision: one row, not two -- but the money and checkable
    # totals must still reflect BOTH occurrences, and the row's status is the worst of
    # the two (an unlanded 2026-03-06 occurrence + a due-today 2026-03-20 one -> the row
    # reads "overdue", not "due_today", so the user notices the older one first).
    _make_item(
        conn, account_id, name="Paycheck", estimate_cents=200000,
        day_of_month=None, cadence_unit="week", cadence_frequency=2, anchor_date="2026-03-06",
    )
    summary = build_plan_summary(conn, "2026-03", TODAY)  # TODAY = 2026-03-20
    assert len(summary["items"]) == 1  # one row, not two
    row = summary["items"][0]
    assert row["occurrence_count"] == 2
    assert row["estimate_cents"] == 400000  # 200000 x 2 occurrences
    assert row["lands"] == "2026-03-06"  # earliest unlanded occurrence
    assert row["status"] == "overdue"  # worst of {overdue, due_today}
    assert summary["in_cents"] == 400000
    assert summary["dated_count"] == 1  # counts the ITEM, not the occurrences
    assert summary["progress_total"] == 2  # counts the OCCURRENCES
    assert summary["progress_landed"] == 0

    # Ticking the item processes BOTH occurrences at once (ADR-0018's accepted
    # collapse -- one ticked flag per item per period, not per occurrence).
    store.set_ticked(conn, "p1", "2026-03", True, "2026-03-20T00:00:00Z", open_period="2026-03")
    summary_ticked = build_plan_summary(conn, "2026-03", TODAY)
    row_ticked = summary_ticked["items"][0]
    assert row_ticked["status"] == "processed"
    assert summary_ticked["progress_landed"] == 2
    assert summary_ticked["cleared_outflow_cents"] == 0  # income, never counted as cleared outflow


def test_a_matched_transaction_only_lands_its_own_occurrence_not_every_occurrence_this_item_has(conn, account_id):
    # The bug this guards against (caught live-testing the first real week-unit item):
    # a plain "did ANYTHING match this item this period" boolean would mark BOTH
    # occurrences Processed the moment just the first one's real transaction landed --
    # silently claiming a paycheck had arrived before it actually had.
    _make_item(
        conn, account_id, name="Paycheck", estimate_cents=200000,
        day_of_month=None, cadence_unit="week", cadence_frequency=2, anchor_date="2026-03-06",
    )
    conn.execute(
        "INSERT INTO txn (id, account_id, date, merchant_raw, merchant, amount_cents, plan_item_id, dedupe_hash) "
        "VALUES ('t1', ?, '2026-03-06', 'ACME PAYROLL', 'Acme', 200000, 'p1', 'h1')",
        (account_id,),
    )
    conn.commit()
    summary = build_plan_summary(conn, "2026-03", TODAY)  # TODAY = 2026-03-20
    row = summary["items"][0]
    # The 2nd occurrence (2026-03-20) is due_today, unmatched -- NOT processed just
    # because the 1st one matched.
    assert row["status"] == "due_today"  # worst of {processed, due_today}
    assert summary["progress_landed"] == 1  # only the 1st occurrence, not both
    assert summary["progress_total"] == 2
