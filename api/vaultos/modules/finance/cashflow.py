import sqlite3
from datetime import date, timedelta

from . import money, plan, store

HORIZON_DAYS = 30


class _SpreadCache:
    """spread_amount_for_date()'s own docstring warns against calling it in a per-day
    loop within the same window -- it rebuilds the whole window's spread_daily_amounts()
    distribution (a spread_window lookup + a divmod-based list build) on every call. A
    30-day-horizon walk with several Budgets does exactly that dozens of times over;
    this caches each (estimate_cents, reset_period, window_start) distribution once.
    Keyed by window_start (ADR-0019 ticket #19), not year/month, so a Weekly Budget's
    ISO-week windows (which can span a month boundary) cache correctly too."""

    def __init__(self) -> None:
        self._cache: dict[tuple, list[int]] = {}

    def amount_for(
        self, estimate_cents: int, d: date, reset_period: str,
        adjustment: tuple[int, int, str] | None = None,
    ) -> int:
        """ticket #23: `adjustment`, when given (plan.active_budget_adjustment's own
        return shape), applies ONLY when its own window_start matches the window `d`
        falls in -- a Weekly Budget projected across the horizon walks through several
        DIFFERENT windows, and an override set for one specific week must not leak into
        another week's days just because they share the same cache/call site."""
        window_start, day_count = money.spread_window(reset_period, d)
        applies = adjustment is not None and adjustment[2] == window_start.isoformat()
        key = (estimate_cents, reset_period, window_start.isoformat(), adjustment if applies else None)
        distribution = self._cache.get(key)
        if distribution is None:
            if applies:
                adjusted_target_cents, elapsed_days, _ = adjustment
                distribution = money.adjusted_spread_daily_amounts(estimate_cents, day_count, elapsed_days, adjusted_target_cents)
            else:
                distribution = money.spread_daily_amounts(estimate_cents, day_count)
            self._cache[key] = distribution
        return distribution[(d - window_start).days]


def _period_of(d: date) -> str:
    return d.strftime("%Y-%m")


def _periods_between(start: date, end: date) -> list[str]:
    periods = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        periods.append(f"{y:04d}-{m:02d}")
        m = m + 1 if m < 12 else 1
        y = y if m != 1 else y + 1
    return periods


def _dated_items(items: list[store.PlanItem]) -> list[store.PlanItem]:
    return [i for i in items if i.kind == "posting"]


def _spread_items(items: list[store.PlanItem]) -> list[store.PlanItem]:
    # ADR-0019: a Budget's `cadence` column holds the sentinel "budget" (never a real
    # `spread *` string) once migration 0008 runs -- `kind` is the authoritative
    # discriminator now, not the old cadence string.
    return [i for i in items if i.kind == "budget"]


def _occurrences_in_range(items: list[store.PlanItem], start: date, end: date) -> list[tuple[date, store.PlanItem]]:
    """Every dated item's occurrence date(s) landing within [start, end] (both
    inclusive). "one-off" is still resolved per calendar period (its only occurrence is
    scoped to a single specific period); "dated" (Unit x Frequency, ADR-0018) is
    resolved directly over the whole range instead -- a week-unit item can land more
    than once within a single calendar month, which the old per-period occurrence_date()
    had no way to express (it returned at most one date). Every downstream consumer of
    this list (carried_rows, the forward walk, processed_count below) already iterates
    (occurrence, item) tuples generically, so a "dated" item contributing 2-3 tuples for
    one item needs no further change there -- occurrence_index (below, near
    count_matched_transactions_for_period) is what keeps per-occurrence `matched` status
    correct for those tuples; `ticked` is the one piece still collapsed per item per
    period (plan_period has no per-occurrence column, ADR-0018's deliberate choice)."""
    out = []
    for item in items:
        if item.cadence == "one-off":
            for period in _periods_between(start, end):
                occ = money.one_off_occurrence(item.day_of_month, item.anchor_period, period)
                if occ is not None and start <= occ <= end:
                    out.append((occ, item))
        elif item.cadence == "dated":
            for occ in money.dated_occurrences_in_range(
                item.cadence_unit, item.cadence_frequency, item.day_of_month,
                item.anchor_period, item.anchor_date, start, end,
            ):
                out.append((occ, item))
    out.sort(key=lambda pair: pair[0])
    return out


def _month_opening_balance(
    account_balance_cents: int, month_start: date, today: date,
    txns: list[tuple[str, int]], adjustments: list[store.BalanceAdjustment],
) -> tuple[int, dict[date, int]]:
    """The balance at the start of `month_start`, plus the map of known anchor dates
    within [month_start, today] (every balance_adjustment in the window, plus `today`
    itself, which is always anchored to the account's current balance_cents).

    Anchors are what let _reconstruct_actual_series snap the walk at correction points
    instead of one current balance silently flattening the whole month back to it (a
    real bug caught in code review, 2026-08-17: a mid-month "set today's balance"
    correction was overwriting the true pre-correction history). One known, accepted
    gap this doesn't solve: a plain balance edit via PATCH /finance/accounts (ticket
    #3's ordinary Account editing, as opposed to "set today's balance") carries no
    timestamp, so days before the EARLIEST anchor in the window fall back to being
    derived by subtracting transactions from that earliest anchor -- the best available
    approximation, not a guarantee, when no anchor exists further back to check against."""
    anchors: dict[date, int] = {
        date.fromisoformat(a.as_of_date): a.real_balance_cents
        for a in adjustments if a.as_of_date <= today.isoformat()
    }
    anchors[today] = account_balance_cents
    earliest_date = min(anchors)
    earliest_balance = anchors[earliest_date]
    txns_before_earliest = sum(amt for d, amt in txns if d < earliest_date.isoformat())
    opening_balance = earliest_balance - txns_before_earliest
    return opening_balance, anchors


def _reconstruct_actual_series(
    month_start: date, today: date, opening_balance: int, anchors: dict[date, int],
    txns: list[tuple[str, int]],
) -> list[tuple[date, int]]:
    """Walks forward from month_start, applying each real transaction on its own date --
    EXCEPT on a date present in `anchors`, where the running balance snaps directly to
    the known-true value instead of being derived from accumulation."""
    by_date: dict[str, int] = {}
    for d, amt in txns:
        by_date[d] = by_date.get(d, 0) + amt

    series = []
    running = opening_balance
    d = month_start
    while d <= today:
        if d in anchors:
            running = anchors[d]
        else:
            running += by_date.get(d.isoformat(), 0)
        series.append((d, running))
        d += timedelta(days=1)
    return series


def _planned_series(
    conn: sqlite3.Connection,
    dated_in_month: list[tuple[date, store.PlanItem]], spread_items: list[store.PlanItem],
    opening_balance_cents: int, month_start: date, today: date, spread_cache: _SpreadCache,
) -> list[tuple[date, int]]:
    """What the plan ALONE would say the balance is, for every day from month_start
    through today -- the same forward walk as _reconstruct_actual_series, but applying
    Plan Item estimates instead of real transactions. Kept as a full day-by-day series
    (fable-os-web#69's offset line needs it; #66 confirmed extending the old
    today-only walk to a list is a cheap refactor since it already iterates day-by-day).

    Deliberately anchor-free, unlike _reconstruct_actual_series: this never snaps to a
    real BalanceAdjustment. It's a pure "what the plan alone predicts" simulation, and
    anchor-snapping would silently corrupt that meaning by absorbing a real-world
    correction into a hypothetical line.

    ticket #23: each spread item's own active Adjusted override (if any, resolved fresh
    per day since a Weekly Budget's window can change mid-walk) is honored the same way
    build_cash_flow's own forward walk does, so the Plan Offset headline (and now this
    series) never disagrees with either screen about a Budget that's been Adjusted this
    window. Takes `conn` (not a pre-fetched existing_periods dict) because
    active_budget_adjustment itself needs to check more than one plan_period bucket --
    see its own docstring (a code review finding: a Weekly Budget's window can straddle
    a month boundary)."""
    series = []
    running = opening_balance_cents
    d = month_start
    while d <= today:
        for occ, item in dated_in_month:
            if occ == d:
                running += item.estimate_cents
        for item in spread_items:
            adjustment = plan.active_budget_adjustment(conn, item, d)
            running += spread_cache.amount_for(item.estimate_cents, d, item.reset_period, adjustment)
        series.append((d, running))
        d += timedelta(days=1)
    return series


def _planned_only_today(
    conn: sqlite3.Connection,
    dated_in_month: list[tuple[date, store.PlanItem]], spread_items: list[store.PlanItem],
    opening_balance_cents: int, month_start: date, today: date, spread_cache: _SpreadCache,
) -> int:
    """The single final value from _planned_series -- what plan_predicted_for_primary_today
    needs, without making that caller build (or care about) the full series."""
    return _planned_series(conn, dated_in_month, spread_items, opening_balance_cents, month_start, today, spread_cache)[-1][1]


def plan_predicted_for_primary_today(conn: sqlite3.Connection, today: date) -> int | None:
    """The single number the "set today's balance" flow needs (README: shows "what the
    plan predicted" before recording the difference) -- None when there's no primary
    account to compute it for."""
    primary = store.get_primary_account(conn)
    if primary is None:
        return None
    month_start = date(today.year, today.month, 1)
    items = store.list_plan_items(conn)
    dated = _dated_items(items)
    spread = _spread_items(items)
    txns = store.transactions_for_account_between(conn, primary.id, month_start.isoformat(), today.isoformat())
    adjustments = store.list_balance_adjustments_between(conn, primary.id, month_start.isoformat(), today.isoformat())
    opening_balance_cents, _ = _month_opening_balance(primary.balance_cents, month_start, today, txns, adjustments)
    dated_in_month = _occurrences_in_range(dated, month_start, today)
    return _planned_only_today(conn, dated_in_month, spread, opening_balance_cents, month_start, today, _SpreadCache())


def build_cash_flow(conn: sqlite3.Connection, today: date) -> dict:
    primary = store.get_primary_account(conn)
    if primary is None:
        return {"empty_state": "no_primary_account"}
    items = store.list_plan_items(conn)
    if not items:
        return {"empty_state": "no_plan_items"}

    floor_cents = store.get_floor_cents(conn)
    month_start = date(today.year, today.month, 1)
    horizon_end = today + timedelta(days=HORIZON_DAYS)
    today_period = _period_of(today)
    spread_cache = _SpreadCache()

    dated = _dated_items(items)
    spread = _spread_items(items)

    # --- Hand-set balance markers -- fetched early so the actual-arc reconstruction can
    #     use them as anchors, not just for the chart's hollow squares. -----------------
    adjustments = store.list_balance_adjustments_between(conn, primary.id, month_start.isoformat(), horizon_end.isoformat())
    latest_adjustment = store.get_latest_balance_adjustment(conn, primary.id)

    # --- Actual arc: month_start -> today, from real transactions -----------------
    txns = store.transactions_for_account_between(conn, primary.id, month_start.isoformat(), today.isoformat())
    opening_balance_cents, anchors = _month_opening_balance(
        primary.balance_cents, month_start, today, txns, [a for a in adjustments if a.as_of_date <= today.isoformat()]
    )
    actual_series = _reconstruct_actual_series(month_start, today, opening_balance_cents, anchors, txns)

    # Fetched once, up front, for the carry-forward section further down
    # (ticked/matched state, Posting-only, always scoped correctly to today_period's
    # own bucket) -- the Plan Offset headline below resolves its own Adjusted lookups
    # directly via plan.active_budget_adjustment(conn, ...) instead, since a Weekly
    # Budget's window can span more than this one bucket (see that function's own
    # docstring, a code review finding).
    existing_periods = store.get_plan_periods_for_period(conn, today_period)

    # --- Plan offset: today's real balance vs. what the plan alone predicted ------
    # One walk produces both the day-by-day series (the offset line, fable-os-web#69)
    # and the final "today" value the Plan Offset headline needs -- no duplicate walk.
    dated_in_month = _occurrences_in_range(dated, month_start, today)
    plan_predicted_series = _planned_series(
        conn, dated_in_month, spread, opening_balance_cents, month_start, today, spread_cache,
    )
    planned_today_cents = plan_predicted_series[-1][1]
    plan_offset_cents = primary.balance_cents - planned_today_cents

    # --- Carry-forward at today: unprocessed occurrences <= today. A fresh-computed
    #     occurrence always lands within today_period by construction (month_start is
    #     the 1st of today's own period), but a Deferred materialized one can land
    #     anywhere, including before month_start -- see the no-month_start-lower-bound
    #     comment further down for why this no longer assumes "this period only." -----
    # ticked is still looked up per ITEM per PERIOD (plan_period has no per-occurrence
    # column, ADR-0018's "lighter" decision) -- ticking an item processes every
    # occurrence it has this period at once. matched is per-OCCURRENCE (ticket #21):
    # plan.occurrences_for_item sources each occurrence's own real matched_txn_id once
    # Month-End Close has materialized this period, falling back to the old
    # count_matched_transactions_for_period chronological pairing otherwise -- the
    # SAME dispatch plan.py's own Plan-screen summary uses, so the two screens never
    # disagree about which occurrence dropped out of the carried-forward step. month_start
    # ..today is always entirely within today_period by construction (month_start is
    # the 1st of today's own month), so a single per-item call already covers it; each
    # item's own occurrences get filtered back down to that window below.
    matched_counts_this_period = store.count_matched_transactions_for_period(conn, today_period)

    # today_period's own last calendar day -- Month-End Close only ever materializes
    # the currently-open period (today_period, in current practice), so it's the
    # boundary past which fresh-computed occurrences (below) can't possibly have a real
    # materialized row to read a Deferred/edited value from.
    _period_start_iso, _period_end_iso_excl = money.period_bounds(today_period)
    today_period_last_day = date.fromisoformat(_period_end_iso_excl) - timedelta(days=1)

    dated_in_month_with_status = []  # (occ, item, status, amt)
    forward_occurrences = []  # (occ, item, amt) -- today+1 .. horizon_end
    for item in dated:
        plan_period = existing_periods.get(item.id)
        ticked = plan_period.ticked if plan_period else False
        # ticket #21/#22: sourced from materialized Planned Posting rows once Month-End
        # Close has run for this period (each occurrence's own frozen, possibly-edited
        # expected_amount_cents, and its EFFECTIVE date -- the Deferred override if set,
        # else the frozen Cadence-derived date), falling back to fresh Cadence + the old
        # count-based fallback otherwise -- the SAME dispatch plan.py's own Plan-screen
        # summary uses, so a PATCHed amount or Deferred date is honored here too instead
        # of silently reading the item's live estimate_cents. A materialized occurrence
        # is matched against `today` and `horizon_end` here by its EFFECTIVE date, not
        # capped to today_period's own calendar days -- Deferred is explicitly allowed
        # to push a date into the next real month (e.g. "align with an upcoming
        # paycheck" landing just after month-end) while the row's own period stays put
        # (ticket #22); capping this branch at today_period's last day made a
        # forward-deferred occurrence vanish from the projection entirely, a real gap
        # caught testing this ticket in a browser.
        occ_triples = plan.occurrences_for_item(
            conn, item, today_period, matched_counts_this_period.get(item.id, 0)
        )
        for occ, amt, matched, _posting in occ_triples:
            # No month_start lower bound (ticket #23 code review) -- a fresh-computed
            # occurrence always lands within today_period by construction, but a
            # Deferred materialized one can be pushed to ANY date, including before
            # month_start (the mirror image of the forward-deferred fix just above):
            # capping this at month_start made a backward-deferred occurrence fall into
            # neither branch and silently vanish from the projection. Anything <= today
            # is overdue/due-today and belongs in the carried-forward step regardless of
            # how far back it was pushed.
            if occ <= today:
                status = money.occurrence_status(occ, today, matched=matched, ticked=ticked)
                dated_in_month_with_status.append((occ, item, status, amt))
            elif today < occ <= horizon_end:
                forward_occurrences.append((occ, item, amt))
    dated_in_month_with_status.sort(key=lambda row: row[0])

    # Fresh-computed occurrences for periods AFTER today_period -- Month-End Close never
    # materializes a future period, so anything landing past today_period's own last day
    # has no real row to read a Deferred override from; unaffected by ticket #22 (a
    # Deferred occurrence for THIS period can itself land here too, via the materialized
    # branch above -- that's a distinct, real obligation from next period's own regular
    # occurrence, not a duplicate of it, so both are correctly shown independently).
    if horizon_end > today_period_last_day:
        beyond_start = today_period_last_day + timedelta(days=1)
        for occ, item in _occurrences_in_range(dated, beyond_start, horizon_end):
            forward_occurrences.append((occ, item, item.estimate_cents))
    forward_occurrences.sort(key=lambda row: row[0])

    carried_rows = [
        (occ, item, status, amt) for occ, item, status, amt in dated_in_month_with_status
        if status in ("overdue", "due_today")
    ]

    step_delta_cents = sum(amt for _, _, _, amt in carried_rows)
    today_after_step_cents = primary.balance_cents + step_delta_cents

    projected_series = [(today, primary.balance_cents)]
    if carried_rows:
        projected_series.append((today, today_after_step_cents))  # the step -- a real second point, not an overwrite

    # Each carried-forward row's running balance is cumulative in date order (occ is
    # always == today here, so this is really cumulative in the order they're listed
    # when several land the same day).
    running = primary.balance_cents
    event_rows = []
    for occ, item, status, amt in sorted(carried_rows, key=lambda r: r[0]):
        running += amt
        event_rows.append({"date": occ, "item": item, "status": status, "balance_cents": running, "estimate_cents": amt})

    running = today_after_step_cents
    d = today + timedelta(days=1)
    occ_by_date: dict[date, list[tuple[store.PlanItem, int]]] = {}
    for occ, item, amt in forward_occurrences:
        occ_by_date.setdefault(occ, []).append((item, amt))
    while d <= horizon_end:
        for item, amt in occ_by_date.get(d, []):
            running += amt
            event_rows.append({"date": d, "item": item, "status": "upcoming", "balance_cents": running, "estimate_cents": amt})
        for item in spread:
            # ticket #23: resolved fresh per day -- a Weekly Budget's window changes
            # mid-walk, and an Adjusted override only ever applies to the one window it
            # was set for (see _SpreadCache.amount_for's own docstring).
            adjustment = plan.active_budget_adjustment(conn, item, d)
            running += spread_cache.amount_for(item.estimate_cents, d, item.reset_period, adjustment)
        projected_series.append((d, running))
        d += timedelta(days=1)

    end_cents = projected_series[-1][1]

    # --- Headline stats: lowest point / breach count scan the FULL continuous series
    #     (every projected day, including spread-only contributions), not just the
    #     discrete dated-item occurrence rows -- a spread-driven breach with no dated
    #     item that day would otherwise be invisible to these headlines while still
    #     plainly visible on the chart itself (a real gap caught in code review,
    #     2026-08-17). ------------------------------------------------------------------
    #
    # Deduped by calendar date before counting: projected_series itself deliberately
    # keeps two points at `today` when anything carried forward (the raw balance, then
    # the post-step balance) -- that's the chart's own "step drawn, not implied"
    # requirement (README), not a bug. But counting both as separate days inflated
    # days_below_floor and duplicated a breach marker for one calendar day (caught in
    # code review, 2026-08-18). Keeping the LAST point per date is correct either way:
    # for a repeated `today`, that's the post-step balance -- the more current number.
    latest_by_date: dict[date, int] = {}
    for d, bal in projected_series:
        latest_by_date[d] = bal
    deduped_series = sorted(latest_by_date.items())

    # "after <item name>" attribution: a forward-walk item's event_row date already
    # matches the series date it affects, but a carried-forward (overdue/due-today)
    # item's event_row is dated at its OWN historical occurrence -- its balance effect
    # actually lands at `today` in the series, not on that past date. Matching on each
    # row's EFFECTIVE date instead of its own occurrence date fixes a real gap (caught
    # in code review, 2026-08-17->18): an item overdue by 9 days being the sole cause
    # of today's dip previously found no matching event_row and silently lost its
    # attribution, even though the file's own intent was to leave attribution unnamed
    # only when no discrete item genuinely caused the dip (the spread-only case), not
    # when one clearly did.
    def _effective_date(row):
        return today if row["status"] in ("overdue", "due_today") else row["date"]

    lowest_series_point = min(deduped_series, key=lambda p: p[1])
    lowest_event_row = next((r for r in event_rows if _effective_date(r) == lowest_series_point[0]), None)
    lowest_point = {
        "balance_cents": lowest_series_point[1],
        "date": lowest_series_point[0].isoformat(),
        "after_item": lowest_event_row["item"].name if lowest_event_row else None,
    }
    days_below_floor = sum(1 for _, bal in deduped_series if bal < floor_cents)
    breach_points = [{"date": d.isoformat(), "balance_cents": bal} for d, bal in deduped_series if bal < floor_cents]

    # --- "What the plan expects next" section header -- three distinct phrasings,
    #     matching the reference prototype's own branching exactly (the prose spec only
    #     documents the first and third; the due-today-only middle case only surfaced
    #     from reading the prototype's actual logic). Built here as a ready-to-display
    #     string, same as plan.py's previous_month_note, not left as raw counts for the
    #     client to reassemble into a sentence. -----------------------------------------
    processed_count = sum(1 for _, _, status, _ in dated_in_month_with_status if status == "processed")
    overdue_count = sum(1 for r in event_rows if r["status"] == "overdue")
    due_today_count = sum(1 for r in event_rows if r["status"] == "due_today")
    still_to_clear_cents = abs(step_delta_cents)
    if overdue_count > 0:
        recon_note = f"{overdue_count} overdue and {due_today_count} due today, carried in"
        recon_amount_cents = still_to_clear_cents
    elif due_today_count > 0:
        recon_note = f"{due_today_count} due today, not through yet"
        recon_amount_cents = still_to_clear_cents
    else:
        recon_note = f"{processed_count} of this month's items already in today's balance"
        recon_amount_cents = None

    return {
        "empty_state": None,
        "cash_on_hand_cents": primary.balance_cents,
        "today": today.isoformat(),
        "horizon_end": horizon_end.isoformat(),
        "projected_end_cents": end_cents,
        "lowest_point": lowest_point,
        "floor_cents": floor_cents,
        "days_below_floor": days_below_floor,
        "plan_offset_cents": plan_offset_cents,
        # Two separate arrays, not one flagged list -- the chart draws these as two
        # distinct <path> elements (solid vs. dashed), matching the reference
        # prototype's own actualPath/projPath split. projected_series starts AT today
        # (and includes the step's second point when anything carried forward), so the
        # two arrays share that one x-position by design, not by accident.
        "actual_series": [{"date": d.isoformat(), "balance_cents": bal} for d, bal in actual_series],
        "projected_series": [{"date": d.isoformat(), "balance_cents": bal} for d, bal in projected_series],
        # Offset line (fable-os-web#69): what the plan alone predicts, month_start
        # through today. Anchor-free by design -- see _planned_series's own docstring.
        "plan_predicted_series": [{"date": d.isoformat(), "balance_cents": bal} for d, bal in plan_predicted_series],
        "breach_points": breach_points,
        "adjustment_markers": [{"date": a.as_of_date, "balance_cents": a.real_balance_cents} for a in adjustments],
        "latest_adjustment": (
            {"date": latest_adjustment.as_of_date} if latest_adjustment else None
        ),
        "expected_next": {
            "recon_note": recon_note,
            "recon_amount_cents": recon_amount_cents,
            "rows": [
                {
                    "date": r["date"].isoformat(),
                    "status": r["status"],
                    "name": r["item"].name,
                    "type": r["item"].type,
                    "estimate_cents": r["estimate_cents"],
                    "balance_cents": r["balance_cents"],
                    "breach": r["balance_cents"] < floor_cents,
                }
                for r in event_rows
            ],
        },
    }
