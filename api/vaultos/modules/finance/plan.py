import sqlite3
from datetime import date, timedelta

from . import money, store

# Worst case wins for a row's single displayed status when an item lands more than
# once in one period (ADR-0018's "lighter" per-occurrence decision -- one row per item,
# not one per occurrence) -- if even one occurrence is overdue, the row should read as
# overdue, not silently average out to "upcoming" because a later occurrence hasn't
# come due yet.
_STATUS_PRIORITY = {"overdue": 0, "due_today": 1, "upcoming": 2, "processed": 3}


def fresh_occurrences_for_item(item: store.PlanItem, period: str) -> list[date]:
    """Every date `item` lands on within `period`, computed fresh from its own Cadence
    -- 0 or 1 for "one-off", and for "dated" (Unit x Frequency, ADR-0018) whatever
    dated_occurrences_in_range finds, which can be more than one for a week-unit item
    (a biweekly paycheck can land twice in a single calendar month). A Budget-kind item
    has no discrete occurrence at all -- callers branch on item.kind themselves
    (ADR-0019) rather than calling this.

    Public (not `_occurrences_for_item`) because close.py's Month-End Close (ticket
    #20) needs exactly this same fresh computation to know what to materialize --
    unlike occurrences_for_item below, this never reads planned_posting; it's the
    thing that FILLS planned_posting in the first place."""
    if item.cadence == "one-off":
        occ = money.one_off_occurrence(item.day_of_month, item.anchor_period, period)
        return [occ] if occ is not None else []
    if item.cadence == "dated":
        start_s, end_s = money.period_bounds(period)
        start_d = date.fromisoformat(start_s)
        end_d = date.fromisoformat(end_s) - timedelta(days=1)
        return money.dated_occurrences_in_range(
            item.cadence_unit,
            item.cadence_frequency,
            item.day_of_month,
            item.anchor_period,
            item.anchor_date,
            start_d,
            end_d,
        )
    return []


def occurrences_for_item(
    conn: sqlite3.Connection,
    item: store.PlanItem,
    period: str,
    matched_count_fallback: int = 0,
) -> list[tuple[date, int, bool, store.PlannedPosting | None]]:
    """Every (date, expected_amount_cents, matched, posting) quadruple `item` lands on
    within `period` -- ticket #20/#21/#22. Sourced from materialized Planned Posting
    rows when Month-End Close has run for this period: each carries its own EFFECTIVE
    date (ticket #22's Deferred override, when set, else the frozen Cadence-derived
    expected_date -- never a live join back to item.estimate_cents for the amount,
    which stays the frozen snapshot too) and its own real matched_txn_id -- ticket #21
    replaces the old count-based chronological pairing (a shared per-item COUNT
    re-paired against sorted occurrences on every read, which could flip an unrelated
    occurrence's status if the count ever shrank) with this real, independently-tracked
    link per occurrence. `posting` is the real PlannedPosting row in this case -- the
    only way a caller can reach its `id`/`expected_date`/`deferred_date` individually
    (e.g. to offer a Defer action per occurrence).

    Falls back to fresh_occurrences_for_item otherwise (pre-#20 behavior, uniformly at
    item.estimate_cents, matched via the caller-supplied count-based fallback paired
    chronologically, `posting` always None -- there's no real row yet to Defer -- true
    for any period nothing has materialized yet, e.g. before the user's first
    Month-End Close, or a still-open prior period being caught up on that Month-End
    Close never touches)."""
    materialized = store.list_planned_postings_for_item_period(conn, item.id, period)
    if materialized:
        return [
            (
                date.fromisoformat(p.deferred_date or p.expected_date),
                p.expected_amount_cents,
                p.matched_txn_id is not None,
                p,
            )
            for p in materialized
        ]
    return [
        (occ, item.estimate_cents, i < matched_count_fallback, None)
        for i, occ in enumerate(fresh_occurrences_for_item(item, period))
    ]


def active_budget_adjustment(
    conn: sqlite3.Connection,
    item: store.PlanItem,
    on: date,
) -> tuple[int, int, str] | None:
    """(adjusted_target_cents, elapsed_days, window_start_iso) if `item` (a Budget) has
    a live Adjusted override (ADR-0019 ticket #23) for the Reset Period window `on`
    falls in, else None. "Live" means the window it was set for still matches the
    window `on` itself falls in (money.spread_window) -- once a new Reset Period
    begins, a stale override naturally reads as absent here, with nothing anywhere
    explicitly clearing it (CONTEXT.md: "Reverts automatically at the next Reset
    Period"). The returned tuple is exactly the shape money.budget_planned_cents_in_period
    and cashflow.py's _SpreadCache both expect as their own `adjustment` argument.

    Checks TWO plan_period buckets, not just `on`'s own calendar-month period (a code
    review finding): store.set_adjusted always persists a new override under
    whichever period was current AT THE MOMENT it was set, but a Weekly Budget's
    Reset window can straddle a month boundary -- an override set on the last day of
    August for a window running Aug 31-Sep 6 is stored under period="2026-08", yet
    must still read as live for Sep 1-6, when `on`'s own period is "2026-09" and a
    lookup scoped to just that bucket would miss it entirely. window_start's own
    period is always the OTHER candidate worth checking (the two coincide, and this
    degenerates to one lookup, whenever the window doesn't cross a boundary --
    Monthly's window is always exactly its own calendar month, so this never matters
    for it)."""
    window_start, _ = money.spread_window(item.reset_period, on)
    window_start_iso = window_start.isoformat()
    candidate_periods = {window_start.strftime("%Y-%m"), on.strftime("%Y-%m")}
    for period in candidate_periods:
        plan_period = store.get_plan_period_for_item(conn, item.id, period)
        if (
            plan_period is not None
            and plan_period.adjusted_target_cents is not None
            and plan_period.adjusted_window_start == window_start_iso
        ):
            set_date = date.fromisoformat(plan_period.adjusted_set_at[:10])
            elapsed_days = (set_date - window_start).days
            return plan_period.adjusted_target_cents, elapsed_days, window_start_iso
    return None


# Lowercase, matching the handoff spec's own example wording verbatim
# ("february not reconciled yet") -- these feed directly into that sentence.
_MONTH_NAMES = {
    1: "january",
    2: "february",
    3: "march",
    4: "april",
    5: "may",
    6: "june",
    7: "july",
    8: "august",
    9: "september",
    10: "october",
    11: "november",
    12: "december",
}


def _period_from_date(d: date) -> str:
    return d.strftime("%Y-%m")


def _previous_period(period: str) -> str:
    year, month = (int(p) for p in period.split("-"))
    return f"{year - 1}-12" if month == 1 else f"{year}-{month - 1:02d}"


def _month_name(period: str) -> str:
    return _MONTH_NAMES[int(period.split("-")[1])]


def _summarize_period(
    conn: sqlite3.Connection, items: list[store.PlanItem], period: str, today: date
):
    """One period's rows + aggregates. Spread items contribute to the money totals every
    period (they have no discrete occurrence) but are never part of the checkable/progress
    set -- there's no "did it land" question for something smeared across every day of the
    month. A dated item whose cadence doesn't occur in `period` at all (e.g. a quarterly
    item outside its cycle) contributes no row and no total here."""
    existing_periods = store.get_plan_periods_for_period(conn, period)
    actuals = store.sum_matched_transactions_for_period(conn, period)
    matched_counts = store.count_matched_transactions_for_period(conn, period)
    has_imports = store.any_transactions_for_period(conn, period)

    rows = []
    agg = {
        "out_cents": 0,
        "in_cents": 0,
        "dated_count": 0,
        "spread_count": 0,
        "income_count": 0,
        "unverified_count": 0,
        "checkable_total": 0,
        "checkable_landed": 0,
        "cleared_outflow_cents": 0,
    }

    for item in items:
        is_income = item.estimate_cents > 0
        # income_count/unverified_count are incremented per-branch below, not here --
        # a dated item outside its cadence's cycle this period (the `continue` after the
        # occurrence_date() check) contributes no row and no total anywhere else, so it
        # must not inflate these counts either; counting it here would have made the
        # header's "N dated, M spread, X income, Y unverified" line inconsistent with an
        # otherwise-empty table.
        # Three distinct states, per the handoff spec: no imports at all this period ->
        # None (frontend shows "-- / No statements imported for this month"); imports
        # exist but this item genuinely matched nothing -> a real 0, not None -- "A month
        # with no imports shows -- ... not 'no match'" draws exactly this line, and
        # actuals.get(item.id) alone can't tell the two apart (both come back missing).
        displayed_actual = actuals.get(item.id, 0) if has_imports else None

        # Deliberately still populated for a Budget, unlike _plan_item_to_dict's own
        # cadence_label (vaultos/modules/finance/routes/plan.py), which is always null for one --
        # that field describes the item's actual Cadence configuration (meaningless
        # for a Budget); this one is a display fallback shown in a row's "when does
        # this land" gutter whenever there's no real date (row["lands"] is None), so a
        # Budget needs SOME text here too, just not one derived from Cadence.
        if item.kind == "budget":
            cadence_label = item.reset_period.capitalize() if item.reset_period else "Budget"
        elif item.cadence == "dated":
            cadence_label = money.dated_cadence_label(item.cadence_unit, item.cadence_frequency)
        else:
            cadence_label = item.cadence
        # estimate_cents is deliberately NOT included here -- both places base_row gets
        # spread into a real row always override it with the period-scaled value
        # (planned_cents for a Budget, period_estimate_cents for a multi-occurrence
        # dated item), so a flat item.estimate_cents here would be dead and misleading.
        base_row = {
            "id": item.id,
            "name": item.name,
            "type": item.type,
            "payee": item.payee,
            "cadence": item.cadence if item.kind == "posting" else None,
            "cadence_label": cadence_label,
            "verified": item.verified,
            "is_catch_all": item.is_catch_all,
            "actual_cents": displayed_actual,
            "has_imports": has_imports,
        }

        if item.kind == "budget":
            # ticket #19: a Weekly Budget resets more than once within a calendar-month
            # period. occurrence_count (display only, "reset N times this period") uses
            # the whole-reset count; the money total uses day-precise summation instead
            # (money.budget_planned_cents_in_period) so it agrees EXACTLY with cash-
            # flow's own per-day math -- multiplying by the whole-reset count (this
            # ticket's first pass) disagreed with cash-flow whenever a reset window
            # straddled a month boundary, a real cross-screen inconsistency a code
            # review caught (see budget_reset_count_in_period's own docstring).
            reset_count = money.budget_reset_count_in_period(item.reset_period, period)
            # ticket #23: Adjusted -- a live override (if any) for the window `today`
            # falls in shifts the remaining days' rate; see active_budget_adjustment's
            # own docstring for what "live" means and why a stale one reads as absent.
            adjustment = active_budget_adjustment(conn, item, today)
            planned_cents = money.budget_planned_cents_in_period(
                item.estimate_cents,
                item.reset_period,
                period,
                adjustment=adjustment,
            )
            agg["spread_count"] += 1
            if is_income:
                agg["income_count"] += 1
            if not item.verified:
                agg["unverified_count"] += 1
            agg["in_cents" if is_income else "out_cents"] += planned_cents
            rows.append(
                {
                    **base_row,
                    "estimate_cents": planned_cents,
                    "lands": None,
                    "occurrence_count": reset_count,
                    "checkable": False,
                    "ticked": None,
                    "status": None,
                    "occurrences": None,  # ticket #22: Deferred has no equivalent for a Budget
                    "adjusted_target_cents": adjustment[0] if adjustment else None,
                }
            )
            continue

        # ticket #20/#21/#22: sourced from materialized Planned Posting rows once
        # Month-End Close has run for this period (each occurrence carrying its own
        # effective date -- Deferred override if set, else the frozen Cadence-derived
        # expected_date -- its own frozen expected_amount_cents, AND its own real
        # matched_txn_id), falling back to fresh Cadence computation + the old
        # count-based pairing otherwise -- see occurrences_for_item's own docstring.
        matched_count_fallback = matched_counts.get(item.id, 0)
        occ_triples = occurrences_for_item(conn, item, period, matched_count_fallback)
        if not occ_triples:
            continue
        occurrences = [occ for occ, _, _, _ in occ_triples]

        # ADR-0018's "lighter" per-occurrence decision: still one row per item per
        # period (dated_count/income_count/unverified_count count ITEMS, matching the
        # header's "N dated, M spread, X income, Y unverified" line), but the money and
        # checkable totals scale by how many times the item actually lands this period
        # -- a "dated" item landing twice (a biweekly paycheck) contributes its estimate
        # TWICE, and counts as 2 checkable occurrences, not 1. Summed from each
        # occurrence's OWN amount (not item.estimate_cents x count) so a materialized
        # row's edited expected_amount_cents is honored -- identical to the old flat
        # multiply for the fresh-computed (non-materialized) case, since every
        # occurrence there shares item.estimate_cents uniformly.
        agg["dated_count"] += 1
        if is_income:
            agg["income_count"] += 1
        if not item.verified:
            agg["unverified_count"] += 1
        period_estimate_cents = sum(amt for _, amt, _, _ in occ_triples)
        agg["in_cents" if is_income else "out_cents"] += period_estimate_cents

        # ticked is still looked up per ITEM per PERIOD (plan_period has no per-
        # occurrence column, ADR-0018's "lighter" decision) -- every occurrence this
        # period shares the same ticked flag; ticking the item processes all of them
        # at once. matched is per-OCCURRENCE (ticket #21: each triple's own real
        # matched_txn_id once materialized, or the old count-based chronological
        # pairing as a fallback) -- 1 real deposit matches only the 1st unmatched
        # occurrence "processed," not every occurrence in the period, fixing what a
        # plain "did anything match" boolean got wrong (a 2nd, not-yet-posted payday
        # would otherwise read as already landed the moment the 1st one's real
        # transaction matched -- caught live testing the first "dated" week-unit item
        # this shipped for, not hypothetical).
        plan_period = existing_periods.get(item.id)
        ticked = plan_period.ticked if plan_period else False
        statuses = [
            money.occurrence_status(occ, today, matched=matched, ticked=ticked)
            for occ, _, matched, _ in occ_triples
        ]
        status = min(statuses, key=lambda s: _STATUS_PRIORITY[s])
        landed_count = sum(1 for s in statuses if s == "processed")

        agg["checkable_total"] += len(occurrences)
        agg["checkable_landed"] += landed_count
        if not is_income:
            landed_amounts = [
                amt for (_, amt, _, _), s in zip(occ_triples, statuses) if s == "processed"
            ]
            agg["cleared_outflow_cents"] += sum(landed_amounts)

        # The row's single "lands" date: the earliest occurrence still awaiting
        # processing, or -- if every occurrence this period already landed -- the last
        # one, so a fully-reconciled item still shows something meaningful rather than
        # nothing at all.
        unlanded = [occ for occ, s in zip(occurrences, statuses) if s != "processed"]
        lands_date = unlanded[0] if unlanded else occurrences[-1]

        # ticket #22: per-occurrence detail for the frontend's Defer action -- only
        # present once real Planned Posting rows exist (posting is None for the
        # fresh-computed fallback; Deferred moves a PLANNED POSTING's date, and there's
        # nothing real to Defer until Month-End Close has materialized one). cadence_date
        # is the permanent, never-edited reference; date is the effective one (deferred
        # if set, else cadence_date) -- the same value already driving `status` above.
        row_occurrences = [
            {
                "id": posting.id,
                "cadence_date": posting.expected_date,
                "deferred_date": posting.deferred_date,
                "date": occ.isoformat(),
                "status": occ_status,
            }
            for (occ, _, _, posting), occ_status in zip(occ_triples, statuses)
            if posting is not None
        ] or None

        rows.append(
            {
                **base_row,
                "estimate_cents": period_estimate_cents,
                "lands": lands_date.isoformat(),
                "occurrence_count": len(occurrences),
                "checkable": True,
                "ticked": ticked,
                "status": status,
                "occurrences": row_occurrences,
                "adjusted_target_cents": None,  # ticket #23: Adjusted has no equivalent for a Posting
            }
        )

    return rows, agg


def build_plan_summary(conn: sqlite3.Connection, period: str, today: date) -> dict:
    """The Plan screen's whole state for one period, in a single call -- header totals,
    the reconcile progress bar, the previous month's amber note (current period only), and
    every row. One screen-scoped composition, same granularity ADR-0004 already
    established for /state (thin, per-screen, not one universal blob)."""
    items = store.list_plan_items(conn)
    rows, agg = _summarize_period(conn, items, period, today)

    previous_month_note = None
    if period == _period_from_date(today):
        prev_period = _previous_period(period)
        _, prev_agg = _summarize_period(conn, items, prev_period, today)
        if prev_agg["checkable_total"] > 0:
            left = prev_agg["checkable_total"] - prev_agg["checkable_landed"]
            if prev_agg["checkable_landed"] == 0:
                previous_month_note = f"{_month_name(prev_period)} not reconciled yet"
            elif left > 0:
                previous_month_note = f"{left} items left in {_month_name(prev_period)}"
            # else: fully reconciled -- no note, nothing left to say.

    return {
        "period": period,
        "out_cents": agg["out_cents"],
        "in_cents": agg["in_cents"],
        "net_cents": agg["out_cents"] + agg["in_cents"],
        "dated_count": agg["dated_count"],
        "spread_count": agg["spread_count"],
        "income_count": agg["income_count"],
        "unverified_count": agg["unverified_count"],
        "progress_landed": agg["checkable_landed"],
        "progress_total": agg["checkable_total"],
        "cleared_outflow_cents": agg["cleared_outflow_cents"],
        "previous_month_note": previous_month_note,
        "items": rows,
    }
