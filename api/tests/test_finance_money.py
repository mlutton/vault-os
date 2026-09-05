import hashlib
from datetime import date

import pytest

from vaultos.modules.finance.money import (
    adjusted_spread_daily_amounts,
    budget_planned_cents_in_period,
    budget_reset_count_in_period,
    count_weekly_resets_in_range,
    dated_cadence_label,
    dated_occurrences_in_range,
    dedupe_hash,
    is_valid_period,
    next_period,
    occurrence_status,
    one_off_occurrence,
    partition_new_rows,
    period_bounds,
    spread_amount_for_date,
    spread_daily_amounts,
    spread_window,
)


def test_dedupe_hash_is_deterministic():
    a = dedupe_hash("acc1", "2026-03-14", -1225, "COMCAST CABLE")
    b = dedupe_hash("acc1", "2026-03-14", -1225, "COMCAST CABLE")
    assert a == b


def test_dedupe_hash_differs_on_any_input_change():
    base = dedupe_hash("acc1", "2026-03-14", -1225, "COMCAST CABLE")
    assert base != dedupe_hash("acc2", "2026-03-14", -1225, "COMCAST CABLE")
    assert base != dedupe_hash("acc1", "2026-03-15", -1225, "COMCAST CABLE")
    assert base != dedupe_hash("acc1", "2026-03-14", -1226, "COMCAST CABLE")
    assert base != dedupe_hash("acc1", "2026-03-14", -1225, "COMCAST CABLE #2")


def test_partition_new_rows_all_new_when_nothing_existing():
    rows = [
        {"date": "2026-03-01", "merchant_raw": "COMCAST", "amount_cents": -1000},
        {"date": "2026-03-02", "merchant_raw": "PAYCHECK", "amount_cents": 200000},
    ]
    to_add, skipped = partition_new_rows("acc1", rows, set())
    assert skipped == 0
    assert len(to_add) == 2
    assert to_add[0]["dedupe_hash"] == dedupe_hash("acc1", "2026-03-01", -1000, "COMCAST")


def test_partition_new_rows_skips_rows_already_in_existing_hashes():
    rows = [{"date": "2026-03-01", "merchant_raw": "COMCAST", "amount_cents": -1000}]
    existing = {dedupe_hash("acc1", "2026-03-01", -1000, "COMCAST")}
    to_add, skipped = partition_new_rows("acc1", rows, existing)
    assert to_add == []
    assert skipped == 1


def test_partition_new_rows_treats_same_batch_repeats_as_distinct_transactions():
    # Two identical $4.50 coffees at the same Starbucks on the same day: date, amount,
    # and merchant text alone can't distinguish "the same statement row duplicated" from
    # "two genuinely separate purchases" -- occurrence-suffixed hashes (dedupe_hash)
    # mean neither is silently dropped. This intentionally changed from the original
    # behavior (which skipped every repeat as a false duplicate) once that turned out
    # to be a real bug, not a feature -- see vault-os-api#11.
    row = {"date": "2026-03-01", "merchant_raw": "COMCAST", "amount_cents": -1000}
    to_add, skipped = partition_new_rows("acc1", [row, dict(row), dict(row)], set())
    assert len(to_add) == 3
    assert skipped == 0
    assert len({r["dedupe_hash"] for r in to_add}) == 3  # each gets its own distinct hash


def test_two_identical_same_day_purchases_are_both_kept_not_one_silently_dropped():
    coffee = {"date": "2026-03-14", "merchant_raw": "STARBUCKS #4471", "amount_cents": -450}
    to_add, skipped = partition_new_rows("acc1", [dict(coffee), dict(coffee)], set())
    assert len(to_add) == 2
    assert skipped == 0


def test_dedupe_hash_occurrence_zero_matches_the_pre_fix_formula():
    # Backward compatibility: every dedupe_hash already stored in a real database was
    # computed without an occurrence parameter at all -- occurrence=0 (the default,
    # and the overwhelmingly common non-duplicate case) must keep producing the exact
    # same hash, or every already-imported transaction would look "new" again on a
    # routine re-import of the same statement.
    pre_fix_key = "acc1|2026-03-14|-1225|COMCAST CABLE"
    pre_fix_hash = hashlib.sha256(pre_fix_key.encode("utf-8")).hexdigest()
    assert dedupe_hash("acc1", "2026-03-14", -1225, "COMCAST CABLE") == pre_fix_hash
    assert dedupe_hash("acc1", "2026-03-14", -1225, "COMCAST CABLE", occurrence=0) == pre_fix_hash


def test_dedupe_hash_occurrence_one_differs_from_occurrence_zero():
    base = dedupe_hash("acc1", "2026-03-14", -1225, "COMCAST CABLE", occurrence=0)
    second = dedupe_hash("acc1", "2026-03-14", -1225, "COMCAST CABLE", occurrence=1)
    assert base != second


def test_partition_new_rows_still_skips_a_genuine_cross_import_duplicate():
    # The core re-import scenario (re-uploading the same statement, or an overlapping
    # export range) must still be caught -- existing_hashes already contains this
    # row's occurrence=0 hash from a prior import, so it's correctly skipped rather
    # than probing to occurrence=1 and treating it as new.
    row = {"date": "2026-03-01", "merchant_raw": "COMCAST", "amount_cents": -1000}
    existing = {dedupe_hash("acc1", "2026-03-01", -1000, "COMCAST")}
    to_add, skipped = partition_new_rows("acc1", [row], existing)
    assert to_add == []
    assert skipped == 1


def test_partition_new_rows_documents_the_accepted_compound_edge_case():
    # Known, accepted residual gap (see partition_new_rows' own docstring): once 2+
    # genuine duplicates for one exact key are already on file, a later genuinely-new
    # transaction sharing that same key still matches existing_hashes at occurrence=0
    # and is (mis)treated as a re-import duplicate rather than a new 3rd transaction --
    # hash-based dedupe alone can't tell those apart. Narrower than the original bug,
    # which dropped the 2nd of even the FIRST two same-key transactions.
    existing = {
        dedupe_hash("acc1", "2026-03-01", -450, "STARBUCKS"),
        dedupe_hash("acc1", "2026-03-01", -450, "STARBUCKS", occurrence=1),
    }
    third_coffee = {"date": "2026-03-01", "merchant_raw": "STARBUCKS", "amount_cents": -450}
    to_add, skipped = partition_new_rows("acc1", [third_coffee], existing)
    assert to_add == []
    assert skipped == 1


def test_spread_daily_amounts_sums_exactly_to_estimate():
    # 10000 cents over 3 days doesn't divide evenly -- no cent may go missing.
    amounts = spread_daily_amounts(10000, 3)
    assert len(amounts) == 3
    assert sum(amounts) == 10000
    assert amounts == [3334, 3333, 3333]


def test_spread_daily_amounts_handles_negative_outflow_estimates():
    amounts = spread_daily_amounts(-10000, 3)
    assert sum(amounts) == -10000
    assert amounts == [-3334, -3333, -3333]


def test_spread_daily_amounts_even_division_has_no_remainder():
    amounts = spread_daily_amounts(9000, 30)
    assert sum(amounts) == 9000
    assert all(a == 300 for a in amounts)


def test_spread_daily_amounts_rejects_non_positive_day_count():
    with pytest.raises(ValueError):
        spread_daily_amounts(1000, 0)


def test_adjusted_spread_daily_amounts_before_any_elapsed_days_spreads_the_full_target():
    # ticket #23: set on day 0 of the window itself -- nothing has elapsed yet, so the
    # whole window spreads the ADJUSTED target, identical to spread_daily_amounts.
    amounts = adjusted_spread_daily_amounts(-9000, 3, elapsed_days=0, adjusted_target_cents=-4500)
    assert amounts == spread_daily_amounts(-4500, 3)
    assert sum(amounts) == -4500


def test_adjusted_spread_daily_amounts_keeps_elapsed_days_at_the_original_rate():
    # $90 over 3 days = -3000/day. Cut to $45 total after day 1 has already elapsed --
    # day 1 keeps its original -3000 ("can't retroactively change what you already
    # spent"); the remaining $15 (4500 - 3000 already "spent") splits over days 2-3.
    amounts = adjusted_spread_daily_amounts(-9000, 3, elapsed_days=1, adjusted_target_cents=-4500)
    assert amounts == [-3000, -750, -750]
    assert sum(amounts) == -4500


def test_adjusted_spread_daily_amounts_sums_to_the_adjusted_target_when_days_remain():
    amounts = adjusted_spread_daily_amounts(15000, 30, elapsed_days=14, adjusted_target_cents=8000)
    assert sum(amounts) == 8000
    assert len(amounts) == 30


def test_adjusted_spread_daily_amounts_with_no_days_remaining_keeps_the_original_distribution():
    # The whole window has already elapsed by the time this is evaluated -- nothing left
    # to adjust, so the ORIGINAL baseline distribution stands untouched (not the
    # adjusted_target_cents, which would silently rewrite already-elapsed history).
    amounts = adjusted_spread_daily_amounts(-9000, 3, elapsed_days=3, adjusted_target_cents=-100)
    assert amounts == spread_daily_amounts(-9000, 3)


def test_adjusted_spread_daily_amounts_clamps_an_out_of_range_elapsed_days():
    # Defensive against a caller passing elapsed_days > day_count (shouldn't happen in
    # practice, but must not crash or silently drop days).
    amounts = adjusted_spread_daily_amounts(-9000, 3, elapsed_days=99, adjusted_target_cents=-100)
    assert amounts == spread_daily_amounts(-9000, 3)


def test_adjusted_spread_daily_amounts_clamps_instead_of_flipping_sign_when_cut_below_elapsed_spend():
    # ticket #23 code review: $90 over 3 days = -3000/day. 1 day has already elapsed
    # (-3000 "spent" in the projection) when the user cuts the WHOLE window's target to
    # -1000 -- below what's already elapsed. Naively, remaining_total = -1000 - (-3000)
    # = +2000, which would make the remaining 2 days read as POSITIVE (income) --
    # cutting an expense budget must never flip it to income. Clamped to 0 instead:
    # "nothing left to spend for the rest of this window."
    amounts = adjusted_spread_daily_amounts(-9000, 3, elapsed_days=1, adjusted_target_cents=-1000)
    assert amounts == [-3000, 0, 0]


def test_adjusted_spread_daily_amounts_clamps_the_symmetric_income_case():
    # The mirror image: an INCOME budget's target raised so high the "remaining" split
    # would go negative must clamp to 0 too, not flip to an expense.
    amounts = adjusted_spread_daily_amounts(9000, 3, elapsed_days=1, adjusted_target_cents=1000)
    assert amounts == [3000, 0, 0]


def test_spread_amount_for_date_uses_that_days_own_month_rate():
    # $150/mo spread across a 28-day February vs a 31-day March -- crossing the boundary
    # must not blend the two months' schedules. Hand-computed: 15000 // 28 = 535 r20 (last
    # day, index 27, falls outside the first-20-days remainder band -> 535); 15000 // 31 =
    # 483 r27 (day 1, index 0, falls inside the first-27-days remainder band -> 484).
    feb_amount = spread_amount_for_date(15000, date(2026, 2, 28), "monthly")
    mar_amount = spread_amount_for_date(15000, date(2026, 3, 1), "monthly")
    assert feb_amount == 535
    assert mar_amount == 484


def test_spread_amount_for_date_full_month_sums_to_estimate():
    total = sum(spread_amount_for_date(15000, date(2026, 4, d), "monthly") for d in range(1, 31))
    assert total == 15000


def test_spread_window_monthly_is_the_whole_calendar_month():
    # ticket #19: unchanged from the pre-Reset-Period behavior -- matches today's
    # existing (Monthly-only) spread math exactly.
    window_start, day_count = spread_window("monthly", date(2026, 2, 15))
    assert window_start == date(2026, 2, 1)
    assert day_count == 28  # 2026 isn't a leap year


def test_spread_window_weekly_is_the_iso_week_monday_through_sunday():
    # ticket #19: a Weekly Budget resets on the ISO week (Monday-Sunday), independent
    # of month boundaries -- "its own smaller cycle nested inside the monthly one"
    # (CONTEXT.md's Reset Period entry).
    window_start, day_count = spread_window("weekly", date(2026, 8, 20))  # a Thursday
    assert window_start == date(2026, 8, 17)  # that week's Monday
    assert day_count == 7


def test_spread_window_weekly_on_the_monday_itself_starts_that_day():
    window_start, day_count = spread_window("weekly", date(2026, 8, 17))
    assert window_start == date(2026, 8, 17)
    assert day_count == 7


def test_spread_window_rejects_an_unknown_reset_period():
    with pytest.raises(ValueError):
        spread_window("daily", date(2026, 8, 20))


def test_spread_amount_for_date_weekly_resets_at_the_week_boundary_not_the_month():
    # A week spanning the month boundary (Mon 2026-08-31 - Sun 2026-09-06) must not
    # blend with the surrounding calendar months -- the whole point of "independent of
    # Month-End Close." $70/week over 7 days divides evenly: 1000/day throughout,
    # regardless of which month each day falls in.
    aug_31 = spread_amount_for_date(7000, date(2026, 8, 31), "weekly")
    sep_1 = spread_amount_for_date(7000, date(2026, 9, 1), "weekly")
    assert aug_31 == 1000
    assert sep_1 == 1000

    # But crossing OUT of that week (into the next Monday) resets to a fresh 7000.
    sep_6 = spread_amount_for_date(7000, date(2026, 9, 6), "weekly")  # last day, this week
    sep_7 = spread_amount_for_date(7000, date(2026, 9, 7), "weekly")  # first day, next week
    assert sep_6 == 1000
    assert sep_7 == 1000  # a fresh week's own first day, not a continuation


def test_spread_amount_for_date_weekly_full_week_sums_to_estimate():
    total = sum(spread_amount_for_date(10000, date(2026, 8, 17 + d), "weekly") for d in range(7))
    assert total == 10000


def test_count_weekly_resets_in_range_counts_mondays():
    # August 2026 has Mondays on the 3rd, 10th, 17th, 24th, and 31st -- five resets.
    assert count_weekly_resets_in_range(date(2026, 8, 1), date(2026, 8, 31)) == 5


def test_count_weekly_resets_in_range_a_single_full_week_is_one_reset():
    assert count_weekly_resets_in_range(date(2026, 8, 17), date(2026, 8, 23)) == 1


def test_count_weekly_resets_in_range_a_range_with_no_monday_is_zero():
    # Tuesday through Sunday of the same week -- no Monday falls inside it.
    assert count_weekly_resets_in_range(date(2026, 8, 18), date(2026, 8, 23)) == 0


def test_count_weekly_resets_in_range_an_inverted_range_is_zero():
    assert count_weekly_resets_in_range(date(2026, 8, 23), date(2026, 8, 17)) == 0


def test_count_weekly_resets_in_range_starting_exactly_on_a_sunday():
    # Regression guard for the closed-form rewrite's isoweekday arithmetic -- a Sunday
    # start (isoweekday 7) is the one value where the "days until the next Monday"
    # calculation is most likely to be off-by-one if done wrong (should be 1 day away).
    assert count_weekly_resets_in_range(date(2026, 8, 16), date(2026, 8, 17)) == 1  # Sun, Mon


def test_budget_reset_count_in_period_monthly_is_always_one():
    # Unchanged from the pre-Reset-Period behavior -- a Monthly Budget contributes its
    # target exactly once per calendar-month period, regardless of the period.
    assert budget_reset_count_in_period("monthly", "2026-08") == 1
    assert budget_reset_count_in_period("monthly", "2026-02") == 1


def test_budget_reset_count_in_period_weekly_counts_that_months_mondays():
    # August 2026 has 5 Mondays; February 2026 has 4.
    assert budget_reset_count_in_period("weekly", "2026-08") == 5


def test_budget_planned_cents_in_period_monthly_equals_the_estimate_exactly():
    # The window IS the period for Monthly, so day-precise summation must land on
    # exactly estimate_cents, cents-exact -- same "matches today's existing behavior"
    # guarantee spread_daily_amounts already gives.
    assert budget_planned_cents_in_period(-40000, "monthly", "2026-08") == -40000
    assert budget_planned_cents_in_period(-40000, "monthly", "2026-02") == -40000


def test_budget_planned_cents_in_period_weekly_is_day_precise_not_whole_weeks():
    # ticket #19 code-review finding: a whole-week-landing COUNT (5 Mondays x -7000 =
    # -35000) disagreed with cash-flow's own day-by-day sum for the same month, because
    # the boundary weeks (starting 2026-07-27 and 2026-08-31) only partly fall in
    # August. This must match cash-flow's math exactly, not just "correctly" on its own
    # -- 5 whole weeks would be -35000; the true day-precise total is -31000 (hand-
    # verified: the two boundary weeks contribute 2 and 1 in-month days respectively,
    # the three fully-contained weeks contribute their whole 7000 each).
    assert budget_planned_cents_in_period(-7000, "weekly", "2026-08") == -31000
    assert budget_reset_count_in_period("weekly", "2026-02") == 4


def test_budget_planned_cents_in_period_weekly_applies_adjustment_only_to_its_own_window():
    # ticket #23: adjusting the week starting 2026-08-03 (one of the 3 fully-contained
    # weeks in the baseline -31000 total) from its original -7000 down to -3500, with 2
    # days already elapsed at the original rate -- delta of +3500 vs baseline, landing
    # on -27500. The OTHER weeks in this same period (including the two boundary weeks)
    # are untouched by an adjustment scoped to only this one window.
    adjustment = (-3500, 2, "2026-08-03")
    assert (
        budget_planned_cents_in_period(-7000, "weekly", "2026-08", adjustment=adjustment) == -27500
    )


def test_budget_planned_cents_in_period_ignores_an_adjustment_for_a_different_window():
    # An adjustment recorded for a window that doesn't land in this period at all must
    # have zero effect -- the plain baseline total.
    adjustment = (-100, 0, "2026-09-07")
    assert (
        budget_planned_cents_in_period(-7000, "weekly", "2026-08", adjustment=adjustment) == -31000
    )


def test_budget_planned_cents_in_period_monthly_applies_the_adjustment_to_the_whole_period():
    # Monthly: the window IS the period, so the adjustment applies across the whole
    # month -- the day-precise sum simplifies to exactly the target whenever any days
    # remain (per-day math itself already covered by adjusted_spread_daily_amounts's
    # own tests).
    adjustment = (-20000, 10, "2026-08-01")
    assert (
        budget_planned_cents_in_period(-40000, "monthly", "2026-08", adjustment=adjustment)
        == -20000
    )


def test_one_off_occurrence_only_occurs_in_its_anchor_period():
    assert one_off_occurrence(4, "2026-05", "2026-05") == date(2026, 5, 4)
    assert one_off_occurrence(4, "2026-05", "2026-06") is None


def test_one_off_occurrence_requires_day_of_month():
    with pytest.raises(ValueError):
        one_off_occurrence(None, "2026-05", "2026-05")


def test_dated_occurrences_month_unit_frequency_one_lands_every_month():
    # frequency=1 (monthly) needs no anchor_period -- lands unconditionally, matching
    # the old "monthly is the one dated cadence with no anchor" rule.
    occs = dated_occurrences_in_range(
        "month", 1, 15, None, None, date(2026, 1, 1), date(2026, 3, 31)
    )
    assert occs == [date(2026, 1, 15), date(2026, 2, 15), date(2026, 3, 15)]


def test_dated_occurrences_month_unit_clamps_to_last_day_of_short_month():
    occs = dated_occurrences_in_range(
        "month", 1, 31, None, None, date(2026, 2, 1), date(2026, 2, 28)
    )
    assert occs == [date(2026, 2, 28)]


def test_dated_occurrences_month_unit_frequency_three_is_quarterly():
    occs = dated_occurrences_in_range(
        "month", 3, 1, "2026-01", None, date(2026, 1, 1), date(2027, 1, 31)
    )
    assert occs == [
        date(2026, 1, 1),
        date(2026, 4, 1),
        date(2026, 7, 1),
        date(2026, 10, 1),
        date(2027, 1, 1),
    ]


def test_dated_occurrences_month_unit_frequency_six_and_twelve_use_that_cycle():
    semiannual = dated_occurrences_in_range(
        "month", 6, 24, "2026-02", None, date(2026, 1, 1), date(2026, 12, 31)
    )
    assert semiannual == [date(2026, 2, 24), date(2026, 8, 24)]
    annual = dated_occurrences_in_range(
        "month", 12, 24, "2026-02", None, date(2026, 1, 1), date(2027, 12, 31)
    )
    assert annual == [date(2026, 2, 24), date(2027, 2, 24)]


def test_dated_occurrences_month_unit_never_occurs_before_its_anchor():
    occs = dated_occurrences_in_range(
        "month", 3, 1, "2026-04", None, date(2026, 1, 1), date(2026, 3, 31)
    )
    assert occs == []


def test_dated_occurrences_month_unit_frequency_above_one_requires_anchor_period():
    with pytest.raises(ValueError):
        dated_occurrences_in_range("month", 3, 1, None, None, date(2026, 1, 1), date(2026, 3, 31))


def test_dated_occurrences_month_unit_frequency_one_still_respects_an_anchor_as_a_lower_bound():
    # ADR-0019 ticket #17: a monthly item's anchor_period is optional (unlike frequency
    # > 1, where it's required to fix the cycle's phase), but when SET it must still act
    # as a lower bound -- a Posting created in August must never show a phantom Overdue
    # occurrence back in January just because frequency=1 used to ignore anchor_period
    # entirely.
    occs = dated_occurrences_in_range(
        "month", 1, 15, "2026-03", None, date(2026, 1, 1), date(2026, 5, 31)
    )
    assert occs == [date(2026, 3, 15), date(2026, 4, 15), date(2026, 5, 15)]


def test_dated_occurrences_month_unit_frequency_one_with_no_anchor_still_lands_unconditionally():
    # Regression guard for the case above -- an unset anchor_period must remain a no-op
    # (matches today's behavior for items with no Anchor set, ADR-0019 ticket #17's own
    # acceptance criterion), not silently start requiring one.
    occs = dated_occurrences_in_range(
        "month", 1, 15, None, None, date(2026, 1, 1), date(2026, 3, 31)
    )
    assert occs == [date(2026, 1, 15), date(2026, 2, 15), date(2026, 3, 15)]


def test_dated_occurrences_week_unit_weekly_lands_every_seven_days():
    occs = dated_occurrences_in_range(
        "week", 1, None, None, "2026-08-07", date(2026, 8, 1), date(2026, 8, 31)
    )
    assert occs == [date(2026, 8, 7), date(2026, 8, 14), date(2026, 8, 21), date(2026, 8, 28)]


def test_dated_occurrences_week_unit_biweekly_lands_every_fourteen_days():
    # 2026-08-07 is a Friday -- "every other Friday" from there.
    occs = dated_occurrences_in_range(
        "week", 2, None, None, "2026-08-07", date(2026, 8, 1), date(2026, 9, 30)
    )
    assert occs == [date(2026, 8, 7), date(2026, 8, 21), date(2026, 9, 4), date(2026, 9, 18)]


def test_dated_occurrences_week_unit_never_occurs_before_its_anchor():
    occs = dated_occurrences_in_range(
        "week", 2, None, None, "2026-08-07", date(2026, 7, 1), date(2026, 8, 6)
    )
    assert occs == []


def test_dated_occurrences_week_unit_can_land_three_times_in_one_calendar_month():
    # A biweekly cycle phased onto day 1/2/3 of a 31-day month completes 3 cycles
    # (day 1, 15, 29) -- callers must not assume at most 2 occurrences per month.
    occs = dated_occurrences_in_range(
        "week", 2, None, None, "2026-08-01", date(2026, 8, 1), date(2026, 8, 31)
    )
    assert occs == [date(2026, 8, 1), date(2026, 8, 15), date(2026, 8, 29)]


def test_dated_occurrences_week_unit_requires_anchor_date():
    with pytest.raises(ValueError):
        dated_occurrences_in_range("week", 2, None, None, None, date(2026, 8, 1), date(2026, 8, 31))


def test_dated_occurrences_rejects_unknown_cadence_unit():
    with pytest.raises(ValueError):
        dated_occurrences_in_range("day", 1, None, None, None, date(2026, 8, 1), date(2026, 8, 31))


def test_dated_cadence_label_uses_the_preset_wording():
    assert dated_cadence_label("week", 1) == "Weekly"
    assert dated_cadence_label("week", 2) == "Every 2 weeks"
    assert dated_cadence_label("month", 1) == "Monthly"
    assert dated_cadence_label("month", 3) == "Quarterly"
    assert dated_cadence_label("month", 6) == "Semiannual"
    assert dated_cadence_label("month", 12) == "Annual"


def test_dated_cadence_label_falls_back_for_a_pair_outside_the_presets():
    assert dated_cadence_label("week", 3) == "Every 3 weeks"


def test_occurrence_status_matched_transaction_is_processed_even_if_unticked():
    status = occurrence_status(
        date(2026, 3, 14), today=date(2026, 3, 20), matched=True, ticked=False
    )
    assert status == "processed"


def test_occurrence_status_ticked_with_no_import_is_processed():
    status = occurrence_status(
        date(2026, 3, 14), today=date(2026, 3, 20), matched=False, ticked=True
    )
    assert status == "processed"


def test_occurrence_status_matched_and_ticked_agree_and_are_still_processed():
    status = occurrence_status(
        date(2026, 3, 14), today=date(2026, 3, 20), matched=True, ticked=True
    )
    assert status == "processed"


def test_occurrence_status_unprocessed_past_date_is_overdue_not_skipped():
    status = occurrence_status(
        date(2026, 3, 14), today=date(2026, 3, 20), matched=False, ticked=False
    )
    assert status == "overdue"


def test_occurrence_status_unprocessed_today_is_due_today():
    status = occurrence_status(
        date(2026, 3, 20), today=date(2026, 3, 20), matched=False, ticked=False
    )
    assert status == "due_today"


def test_occurrence_status_unprocessed_future_date_is_upcoming():
    status = occurrence_status(
        date(2026, 3, 25), today=date(2026, 3, 20), matched=False, ticked=False
    )
    assert status == "upcoming"


def test_is_valid_period_accepts_well_formed_periods():
    assert is_valid_period("2026-03") is True
    assert is_valid_period("2026-01") is True
    assert is_valid_period("2026-12") is True


def test_is_valid_period_rejects_bad_shapes():
    assert is_valid_period("banana") is False
    assert is_valid_period("2026-3") is False
    assert is_valid_period("26-03") is False


def test_is_valid_period_rejects_out_of_range_month():
    assert is_valid_period("2026-13") is False
    assert is_valid_period("2026-00") is False


def test_period_bounds_ordinary_month():
    assert period_bounds("2026-03") == ("2026-03-01", "2026-04-01")


def test_period_bounds_rolls_december_into_next_january():
    assert period_bounds("2026-12") == ("2026-12-01", "2027-01-01")


def test_next_period_ordinary_month():
    assert next_period("2026-03") == "2026-04"


def test_next_period_rolls_december_into_next_january():
    assert next_period("2026-12") == "2027-01"
