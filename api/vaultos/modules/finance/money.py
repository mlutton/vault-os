"""Money-math core for Finance: cents-safe arithmetic, dedupe hashing, and
Processed/Overdue derivation. Pure functions only -- no DB, no API; see
docs/adr/0017-finance-data-lives-in-the-spine.md for why this lives here, and
design_handoff_finance/README.md (Personal-OS) for the full behavioral spec this
implements a slice of. "The plan owns the future, the ledger owns the past" --
see CONTEXT.md's "Finance" section for the vocabulary these functions speak.
"""

import calendar
import hashlib
import re
from datetime import date, timedelta

_PERIOD_RE = re.compile(r"^\d{4}-\d{2}$")


def is_valid_period(period: str) -> bool:
    """Whether `period` is a genuine 'YYYY-MM' -- both call sites that need this
    (the API's ?period= query param and plan_item.anchor_period at write time) were
    independently checking only the digit shape, not that the month is 01-12, letting
    a value like '2026-13' reach calendar.monthrange() here and crash with an
    uncaught calendar.IllegalMonthError. One canonical check for both."""
    if not _PERIOD_RE.match(period):
        return False
    month = int(period.split("-")[1])
    return 1 <= month <= 12


def period_bounds(period: str) -> tuple[str, str]:
    """The half-open [start, end) ISO-date range covering `period` -- for a store-layer
    caller that wants `txn.date >= start AND txn.date < end` instead of wrapping the
    column in strftime('%Y-%m', date), which SQLite can't use an index on. Rolls the end
    date into January of the next year at a December period."""
    year, month = (int(p) for p in period.split("-"))
    start = f"{year:04d}-{month:02d}-01"
    end_year, end_month = (year + 1, 1) if month == 12 else (year, month + 1)
    end = f"{end_year:04d}-{end_month:02d}-01"
    return start, end


# Dated cadences ("one-off" and "dated" -- see ADR-0018 for why the latter isn't called
# "recurring", which is a distinct, unrelated domain term) land on a discrete date.
# ADR-0019 moved float allocations to the Budget Kind (Reset Period, not a cadence
# string) -- a Budget's estimate is still smeared across every day of the period for
# the cash-flow graph, see spread_daily_amounts / spread_amount_for_date for those.

CADENCE_UNITS = {"week", "month"}

# The fixed (unit, frequency) combinations the UI offers for a "dated" cadence --
# deliberately a closed list, not a free-typed frequency number (ADR-0018), so
# occurrence math stays one formula (dated_occurrences_in_range) while the write-time
# surface stays as constrained as the old flat-string cadence list was. Extending to
# e.g. "every 3 weeks" later is just adding one entry here.
DATED_CADENCE_PRESETS = [
    {"unit": "week", "frequency": 1, "label": "Weekly"},
    {"unit": "week", "frequency": 2, "label": "Every 2 weeks"},
    {"unit": "month", "frequency": 1, "label": "Monthly"},
    {"unit": "month", "frequency": 3, "label": "Quarterly"},
    {"unit": "month", "frequency": 6, "label": "Semiannual"},
    {"unit": "month", "frequency": 12, "label": "Annual"},
]

# "twice a month" needs two landing days, not a single day_of_month -- a genuinely
# different shape (fixed calendar days each month) from "dated"'s Unit x Frequency
# cycle, deferred rather than guessed here. "every 2 weeks" used to live here too,
# before ADR-0018 folded it into "dated" as week-unit frequency 2.
UNSUPPORTED_DATED_CADENCES = {"twice a month"}


def next_period(period: str) -> str:
    """The calendar month immediately following `period`, in the same 'YYYY-MM' shape
    (ticket #24: Month-End Close opens the next period). Reuses period_bounds' own
    year/month rollover math -- its exclusive `end` bound IS the first of the next
    period -- rather than duplicating it."""
    _, end = period_bounds(period)
    return end[:7]


def dated_cadence_label(cadence_unit: str, cadence_frequency: int) -> str:
    """Human label for a (unit, frequency) pair, e.g. ("week", 2) -> "Every 2 weeks" --
    looked up from DATED_CADENCE_PRESETS first so the UI's own wording wins; falls back
    to a generated phrase for any pair outside that closed list (shouldn't happen once
    store._validate_cadence_fields enforces membership, but stays descriptive rather
    than raising if it ever does)."""
    for preset in DATED_CADENCE_PRESETS:
        if preset["unit"] == cadence_unit and preset["frequency"] == cadence_frequency:
            return preset["label"]
    if cadence_frequency == 1:
        return f"{cadence_unit.capitalize()}ly"
    return f"Every {cadence_frequency} {cadence_unit}s"


def dedupe_hash(account_id: str, txn_date: str, amount_cents: int, merchant_raw: str, occurrence: int = 0) -> str:
    """Deterministic identity for one imported statement row. Same inputs always produce
    the same hash -- that equality IS the "this is a duplicate" signal CSV import dedupes
    on (skip-and-report, never silent).

    `occurrence` disambiguates genuinely distinct transactions that would otherwise
    collide on (account_id, date, amount_cents, merchant_raw) alone -- two identical
    $4.50 coffees at the same Starbucks on the same day, say. The first (occurrence=0,
    the overwhelmingly common case) hashes identically to before this parameter
    existed, so every dedupe_hash already stored in a real database still matches on
    a re-import; only the 2nd+ occurrence of an exact duplicate combination gets a
    distinct suffix (see partition_new_rows, which is what actually assigns these)."""
    suffix = f"|{occurrence}" if occurrence else ""
    key = f"{account_id}|{txn_date}|{amount_cents}|{merchant_raw}{suffix}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


# A same-day, same-amount, same-merchant transaction repeating this many times within
# one account is realistically unreachable (two duplicate coffees is common; two
# hundred is not) -- bounds the occurrence probe below rather than looping unbounded,
# and beyond this the (extremely unlikely) extra rows are still safely treated as
# duplicates rather than the loop hanging.
_MAX_OCCURRENCE_PROBE = 1000


def partition_new_rows(
    account_id: str, rows: list[dict], existing_hashes: set[str]
) -> tuple[list[dict], int]:
    """Splits parsed CSV rows (each a {date, merchant_raw, amount_cents} dict) into
    (rows to actually insert, count skipped as duplicates) -- skip-and-report dedupe,
    never silent, never a blocking dialog (design_handoff_finance/README.md).

    `existing_hashes` (already in the DB, from a PRIOR import) and same-batch repeats
    (this CSV containing the same date/amount/merchant more than once) are handled
    differently on purpose, because they answer different questions:
    - A row whose occurrence=0 hash is already in `existing_hashes` is treated as a
      genuine re-import (re-uploading the same statement, or an overlapping export
      date range) and skipped outright -- this is the common case, and the one the
      "re-import a file, nothing gets duplicated" feature depends on.
    - A row that collides only with something already accepted from THIS SAME batch
      gets the next occurrence-suffixed hash instead (see dedupe_hash) and is kept as
      a genuinely distinct transaction -- two identical $4.50 coffees at the same
      Starbucks on the same day, say, which used to be silently dropped as a false
      duplicate (vault-os-api#11).

    Known, accepted residual gap: once 2+ real same-key transactions are already
    recorded in the DB from an earlier import, a later genuinely-new 3rd one that
    happens to share the exact same date/amount/merchant will still match
    `existing_hashes` at occurrence=0 and be (mis)treated as a re-import duplicate --
    hash-based dedupe alone cannot tell "this occurrence appearing again is the same
    real transaction" from "a new transaction that coincidentally collides." Accepted
    because it only bites a rare compound scenario (2+ genuine duplicates already on
    file for one exact key), not the common single-transaction case."""
    to_add = []
    skipped = 0
    batch_claimed: set[str] = set()
    for row in rows:
        for occurrence in range(_MAX_OCCURRENCE_PROBE):
            h = dedupe_hash(account_id, row["date"], row["amount_cents"], row["merchant_raw"], occurrence)
            if h in existing_hashes:
                skipped += 1
                break
            if h not in batch_claimed:
                batch_claimed.add(h)
                to_add.append({**row, "dedupe_hash": h})
                break
        else:
            skipped += 1
    return to_add, skipped


def spread_daily_amounts(estimate_cents: int, day_count: int) -> list[int]:
    """Divides estimate_cents across day_count days with no float drift: the returned
    list always sums to exactly estimate_cents. Remainder cents land on the first N days
    (distributed by magnitude, so this holds for negative/outflow estimates too, per the
    sign convention: outflows negative, inflows positive, everywhere)."""
    if day_count <= 0:
        raise ValueError("day_count must be positive")
    sign = -1 if estimate_cents < 0 else 1
    magnitude = abs(estimate_cents)
    base, remainder = divmod(magnitude, day_count)
    return [sign * (base + (1 if i < remainder else 0)) for i in range(day_count)]


def adjusted_spread_daily_amounts(
    baseline_cents: int, day_count: int, elapsed_days: int, adjusted_target_cents: int,
) -> list[int]:
    """Same shape as spread_daily_amounts (day_count ints) for one Reset Period window,
    but blends two rates at an Adjusted override's cutover point (ADR-0019 ticket #23,
    "forward-only from the moment it's set"): the first `elapsed_days` keep the
    ORIGINAL baseline distribution untouched -- already-elapsed days aren't
    retroactively rewritten ("you can't retroactively change what you already spent")
    -- and the remaining days recompute their own even split of whatever's left of
    adjusted_target_cents once the elapsed days' original amounts are subtracted out.
    The whole window's total therefore sums to adjusted_target_cents exactly whenever
    any days remain (the override IS the period's new effective target, not an addition
    on top) -- except when nothing remains (elapsed_days >= day_count), where the
    original baseline distribution stands entirely untouched instead, since there's
    nothing left to adjust. The remaining split is also clamped to the baseline's own
    sign/direction (a code review finding): cutting an expense Budget's target below
    what the elapsed days have already "spent" in the projection would otherwise make
    remaining_total positive, flipping the rest of the window to INCOME-signed amounts
    -- clamped to 0 instead ("nothing left to spend this window"), never a sign flip."""
    elapsed_days = max(0, min(elapsed_days, day_count))
    original = spread_daily_amounts(baseline_cents, day_count)
    remaining_days = day_count - elapsed_days
    if remaining_days == 0:
        return original
    elapsed_total = sum(original[:elapsed_days])
    remaining_total = adjusted_target_cents - elapsed_total
    if baseline_cents < 0 and remaining_total > 0:
        remaining_total = 0
    elif baseline_cents > 0 and remaining_total < 0:
        remaining_total = 0
    return original[:elapsed_days] + spread_daily_amounts(remaining_total, remaining_days)


def spread_window(reset_period: str, on: date) -> tuple[date, int]:
    """The [window_start, day_count) window a Budget's target resets within (ADR-0019
    ticket #19), for whichever date `on` falls in -- Monthly is the calendar month
    (unchanged from the pre-Reset-Period behavior); Weekly is the ISO week (Monday
    through Sunday), independent of month boundaries -- "its own smaller cycle nested
    inside the monthly one" (CONTEXT.md's Reset Period entry), not tied to Month-End
    Close at all."""
    if reset_period == "weekly":
        window_start = on - timedelta(days=on.isoweekday() - 1)
        return window_start, 7
    if reset_period == "monthly":
        day_count = calendar.monthrange(on.year, on.month)[1]
        return date(on.year, on.month, 1), day_count
    raise ValueError(f"unknown reset_period {reset_period!r}")


def spread_amount_for_date(estimate_cents: int, on: date, reset_period: str) -> int:
    """A Budget's contribution on one specific date. Each day draws from its own
    window's rate (spread_window above) -- the window's total divides by its own day
    count, so crossing a window boundary (a month for Monthly, an ISO week for Weekly)
    does not blend two windows' schedules.

    Recomputes the full window's distribution on every call, so a caller projecting
    many days within the same window (a 30-day cash-flow horizon, say) should call
    spread_daily_amounts(estimate_cents, day_count) once and index into it directly,
    rather than call this function in a per-day loop."""
    window_start, day_count = spread_window(reset_period, on)
    return spread_daily_amounts(estimate_cents, day_count)[(on - window_start).days]


def count_weekly_resets_in_range(start: date, end: date) -> int:
    """How many Weekly Reset Period cycles a Budget goes through within [start, end],
    both inclusive -- one per ISO week's Monday (spread_window's own window_start) that
    falls in range, the same "count discrete landings within the period" convention
    every other occurrence-counting function in this module already follows (see
    dated_occurrences_in_range). Closed-form (find the first Monday >= start, then how
    many 7-day steps fit before end), matching this module's own established style
    (_week_unit_occurrences avoids a day-by-day walk the same way) rather than a
    day-by-day loop."""
    if end < start:
        return 0
    days_until_monday = (7 - start.isoweekday() + 1) % 7
    first_monday = start + timedelta(days=days_until_monday)
    if first_monday > end:
        return 0
    return (end - first_monday).days // 7 + 1


def budget_reset_count_in_period(reset_period: str, period: str) -> int:
    """How many times a Budget's Reset Period cycle LANDS within `period` (a calendar
    month, "YYYY-MM") -- ticket #19, a display-only count (Plan's row-level
    occurrence_count, "this reset N times this period"), NOT the money total. Monthly
    is always exactly 1, unchanged from the pre-Reset-Period behavior. Weekly counts
    every ISO-week reset whose Monday falls within the period (count_weekly_resets_in_range).

    Deliberately NOT used to scale the period's planned/estimate cents total --
    multiplying estimate_cents by this count double-counts (or under-counts) a Weekly
    Budget whenever a week straddles a month boundary (5 whole weeks touch most
    calendar months, but the boundary weeks only partly belong to it). See
    budget_planned_cents_in_period below for the money-accurate version, which agrees
    exactly with cash-flow's own day-by-day math instead (a real cross-screen
    inconsistency a code review caught between this function's first use in
    categories.py/plan.py and cashflow.py's pre-existing per-day spread)."""
    if reset_period == "weekly":
        start_s, end_s = period_bounds(period)
        start_d = date.fromisoformat(start_s)
        end_d = date.fromisoformat(end_s) - timedelta(days=1)
        return count_weekly_resets_in_range(start_d, end_d)
    return 1


def budget_planned_cents_in_period(
    estimate_cents: int, reset_period: str, period: str, adjustment: tuple[int, int, str] | None = None,
) -> int:
    """The exact cents a Budget contributes within `period` (a calendar month) --
    day-precise, summing each day's own window rate over every day in the period, so
    this always agrees EXACTLY with cash-flow's own per-day math (both call the same
    spread_window/spread_daily_amounts underneath). For Monthly, the window IS the
    period, so this is mathematically identical to estimate_cents itself -- nothing
    regresses there. For Weekly, a week spanning a month boundary contributes only its
    in-period days, not its whole target -- the fix for the cross-screen inconsistency
    budget_reset_count_in_period's own docstring describes.

    `adjustment`, when given, is (adjusted_target_cents, elapsed_days, window_start_iso)
    -- ticket #23's Adjusted override, typically from plan.active_budget_adjustment.
    Applied ONLY to the single window it names via adjusted_spread_daily_amounts; a
    Weekly Budget's OTHER windows within this same period (a different week that
    happens to also land in this calendar month) use the plain baseline rate,
    unaffected -- Adjusted is scoped to exactly one Reset Period window, never the
    whole period a Weekly Budget's multiple resets can span."""
    start_s, end_s = period_bounds(period)
    start_d = date.fromisoformat(start_s)
    end_d = date.fromisoformat(end_s) - timedelta(days=1)
    total = 0
    d = start_d
    distributions: dict[date, list[int]] = {}
    while d <= end_d:
        window_start, day_count = spread_window(reset_period, d)
        distribution = distributions.get(window_start)
        if distribution is None:
            if adjustment is not None and adjustment[2] == window_start.isoformat():
                adjusted_target_cents, elapsed_days, _ = adjustment
                distribution = adjusted_spread_daily_amounts(estimate_cents, day_count, elapsed_days, adjusted_target_cents)
            else:
                distribution = spread_daily_amounts(estimate_cents, day_count)
            distributions[window_start] = distribution
        total += distribution[(d - window_start).days]
        d += timedelta(days=1)
    return total


def one_off_occurrence(day_of_month: int, anchor_period: str | None, period: str) -> date | None:
    """A one-off's only occurrence is in the period it's anchored to -- it never repeats.
    The one cadence that never fit ADR-0018's Unit x Frequency model (frequency doesn't
    apply to something that fires exactly once), so it keeps its own function rather
    than being squeezed into dated_occurrences_in_range below."""
    if day_of_month is None:
        raise ValueError("one-off requires day_of_month")
    if anchor_period != period:
        return None
    year, month = (int(p) for p in period.split("-"))
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(day_of_month, last_day))


def _month_unit_occurrences(
    cadence_frequency: int, day_of_month: int, anchor_period: str | None, start: date, end: date,
) -> list[date]:
    """Every occurrence of a month-unit "dated" item (frequency in months: 1=monthly,
    3=quarterly, 6=semiannual, 12=annual, ADR-0018) landing within [start, end], both
    inclusive. At most one occurrence per calendar month -- unlike a week-unit item, a
    month-or-longer cycle can never complete twice within one calendar month. Never
    resolves to an occurrence before anchor_period, same "runs forward from the anchor,
    never backward" rule the old per-cadence branches already followed.

    frequency=1 (monthly) is the one shape where anchor_period is optional (ADR-0019
    ticket #17) rather than required to fix a multi-month cycle's phase -- but when a
    caller DOES set one (the API defaults new monthly Postings to the current period),
    it still acts as a lower bound, same as every other frequency. Unset stays a no-op,
    matching the pre-#17 "monthly needs no anchor at all" behavior."""
    if cadence_frequency == 1:
        anchor_year = anchor_month = None
        if anchor_period is not None:
            anchor_year, anchor_month = (int(p) for p in anchor_period.split("-"))
    else:
        if anchor_period is None:
            raise ValueError("month-unit cadence with frequency > 1 requires anchor_period")
        anchor_year, anchor_month = (int(p) for p in anchor_period.split("-"))

    occurrences = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        if cadence_frequency == 1:
            on_cycle = anchor_year is None or (y, m) >= (anchor_year, anchor_month)
        else:
            offset = (y - anchor_year) * 12 + (m - anchor_month)
            on_cycle = offset >= 0 and offset % cadence_frequency == 0
        if on_cycle:
            last_day = calendar.monthrange(y, m)[1]
            occ = date(y, m, min(day_of_month, last_day))
            if start <= occ <= end:
                occurrences.append(occ)
        m, y = (m + 1, y) if m < 12 else (1, y + 1)
    return occurrences


def _week_unit_occurrences(cadence_frequency: int, anchor_date: date, start: date, end: date) -> list[date]:
    """Every occurrence of a week-unit "dated" item (frequency in weeks: 1=weekly,
    2=biweekly, ..., ADR-0018) landing within [start, end], both inclusive. Never
    occurs before anchor_date -- the same forward-only rule as the month-unit cycles,
    just at day granularity. No separate day-of-week field exists or is needed: the
    weekday is whatever anchor_date's is, and every later occurrence is exactly
    `anchor_date + frequency * 7 * n` days, so it's implied automatically.

    A 14+ day interval CAN complete 3 cycles within one 30/31-day calendar month when
    its phase lines up (an anchor on the 1st, 2nd, or 3rd of the month) -- callers
    scoping a single calendar month must not assume at most 2 occurrences."""
    step_days = cadence_frequency * 7
    if anchor_date > end:
        return []
    cursor = max(start, anchor_date)
    remainder = (cursor - anchor_date).days % step_days
    if remainder:
        cursor += timedelta(days=step_days - remainder)
    occurrences = []
    while cursor <= end:
        occurrences.append(cursor)
        cursor += timedelta(days=step_days)
    return occurrences


def dated_occurrences_in_range(
    cadence_unit: str, cadence_frequency: int, day_of_month: int | None,
    anchor_period: str | None, anchor_date: str | None, start: date, end: date,
) -> list[date]:
    """Every occurrence of a "dated" Plan Item (Unit x Frequency, ADR-0018) landing
    within [start, end], both inclusive -- replaces four hand-written cadence-name
    cases (monthly/quarterly/semiannual/annual) with one formula, and extends it to
    week-unit cadences (weekly, biweekly, ...) the old occurrence_date() couldn't
    express at all. Ranges rather than a single period, unlike the old function,
    because a week-unit item can land more than once within one calendar month -- see
    one_off_occurrence for the separate, unrelated "one-off" cadence, and
    spread_amount_for_date for spread cadences (neither has a discrete occurrence)."""
    if cadence_unit == "week":
        if anchor_date is None:
            raise ValueError("week-unit cadence requires anchor_date")
        return _week_unit_occurrences(cadence_frequency, date.fromisoformat(anchor_date), start, end)
    if cadence_unit == "month":
        if day_of_month is None:
            raise ValueError("month-unit cadence requires day_of_month")
        return _month_unit_occurrences(cadence_frequency, day_of_month, anchor_period, start, end)
    raise ValueError(f"unknown cadence_unit {cadence_unit!r}")


def occurrence_status(occurrence: date, today: date, matched: bool, ticked: bool) -> str:
    """The rule the whole projection depends on. Ledger presence (a matched transaction)
    and the manual tick both independently set Processed -- neither is required if the
    other holds, and Ledger presence wins if they'd ever disagree (it's the stronger
    signal; the tick is the manual fallback for items with no import). Anything
    unprocessed whose date has passed is Overdue, not skipped -- it stays on the
    projection as still-to-come rather than dropping out of today's balance."""
    if matched or ticked:
        return "processed"
    if occurrence < today:
        return "overdue"
    if occurrence == today:
        return "due_today"
    return "upcoming"
