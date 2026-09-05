import sqlite3
from datetime import date, timedelta

from . import money, plan, store

# "Committed = Rent, Mortgage, Utility, Insurance, Loan, Credit Card; the caption's
# point is that the flexible remainder is the only part that can move." (README)
COMMITTED_TYPES = {"Rent", "Mortgage", "Utility", "Insurance", "Loan", "Credit Card"}

TOP_N = 8


def _occurrence_count_in_period(item: store.PlanItem, period: str) -> int:
    """How many times `item` LANDS within `period` -- used only to decide whether it
    contributes at all this period (0 means skip). 1 for a Budget (money.
    budget_reset_count_in_period -- display-only count of how many times its Reset
    Period cycled; NOT used to scale its money contribution, see
    _planned_cents_in_period below for why), 0 or 1 for "one-off", and for "dated"
    (Unit x Frequency, ADR-0018) whatever dated_occurrences_in_range actually finds --
    a week-unit item can land 2 or even 3 times in one calendar month, and each landing
    is a real, separate contribution to the period's planned total, not one."""
    if item.kind == "budget":
        return money.budget_reset_count_in_period(item.reset_period, period)
    if item.cadence == "one-off":
        return (
            1
            if money.one_off_occurrence(item.day_of_month, item.anchor_period, period) is not None
            else 0
        )
    if item.cadence == "dated":
        start_s, end_s = money.period_bounds(period)
        start_d = date.fromisoformat(start_s)
        end_d = date.fromisoformat(end_s) - timedelta(days=1)
        occurrences = money.dated_occurrences_in_range(
            item.cadence_unit,
            item.cadence_frequency,
            item.day_of_month,
            item.anchor_period,
            item.anchor_date,
            start_d,
            end_d,
        )
        return len(occurrences)
    return 0


def _planned_cents_in_period(
    conn: sqlite3.Connection,
    item: store.PlanItem,
    period: str,
    today: date,
) -> int:
    """The exact cents `item` contributes to `period`'s planned total. A Budget uses
    day-precise summation (money.budget_planned_cents_in_period) so this always agrees
    EXACTLY with cash-flow's own per-day math -- multiplying estimate_cents by
    _occurrence_count_in_period's whole-reset count (the pre-#19-review-fix approach)
    disagreed with cash-flow whenever a Weekly Budget's reset window straddled a month
    boundary, since that count treats a boundary week as either fully in or fully out
    rather than splitting it by actual day. Everything else scales by its discrete
    occurrence count, unchanged: a "dated" item landing twice this period (a biweekly
    bill, say) contributes its estimate TWICE, not once.

    ticket #23: a Budget's active Adjusted override (if any, resolved the same way
    plan.py's own Plan-screen summary does) is honored here too -- `today` is always
    the REAL current date regardless of which `period` is being viewed, so
    plan.active_budget_adjustment naturally reads as absent for any period other than
    the item's own current Reset Period window (an Adjusted override can never apply to
    a past or future period's breakdown). Takes `conn` (not a pre-fetched plan_period)
    because active_budget_adjustment itself needs to check more than one plan_period
    bucket -- see its own docstring (a code review finding: a Weekly Budget's window
    can straddle a month boundary)."""
    if item.kind == "budget":
        adjustment = plan.active_budget_adjustment(conn, item, today)
        return money.budget_planned_cents_in_period(
            item.estimate_cents, item.reset_period, period, adjustment=adjustment
        )
    return item.estimate_cents * _occurrence_count_in_period(item, period)


def _planned_by_category(
    items: list[store.PlanItem],
    conn: sqlite3.Connection,
    period: str,
    today: date,
) -> tuple[dict[str, int], set[str], set[str]]:
    """Sum of planned cents by type, for every non-catch-all, outflow-only Plan Item
    genuinely contributing to `period` (see _planned_cents_in_period), mirroring plan.py's
    own per-period totals so "planned" means the same thing here as it does on the Plan
    screen's own header totals -- undercounting or overcounting either would make this
    screen disagree with the Plan screen's for the same period. Income items
    (estimate_cents > 0) are excluded: this screen is "actual SPEND by category"
    (README) and the reference prototype's own implementation filters to amt < 0 before
    building either side -- a paycheck isn't a spending category, and including it
    would break the pie's percentage math (a positive slice against an otherwise-
    negative total). The catch-all is never a category source: "excluded entirely from
    this table" (README) -- its spend already surfaces under whichever category each
    transaction it collects independently carries. Takes `items` (fetched once by the
    caller) rather than calling store.list_plan_items itself -- build_categories also
    needs the same list for budget-type detection, and a second full-table query per
    request was pure waste.

    Also returns (posting_types, budget_types): the set of types contributed by a
    Posting/Budget item that actually occurs THIS period (ticket #25 code-review
    round 4) -- a store-wide version of these sets wrongly treated a Posting that
    doesn't land this period (e.g. a quarterly Insurance bill viewed in a non-quarter
    month) as still "real" for same-name collision resolution, misclassifying a
    same-named Budget's entire planned figure as Committed even though only the
    Budget actually contributes anything this period. Scoped by this same
    occurrence check, not built separately."""
    totals: dict[str, int] = {}
    posting_types: set[str] = set()
    budget_types: set[str] = set()
    for item in items:
        if item.is_catch_all or item.estimate_cents >= 0:
            continue
        if _occurrence_count_in_period(item, period) == 0:
            continue
        planned = _planned_cents_in_period(conn, item, period, today)
        totals[item.type] = totals.get(item.type, 0) + planned
        (posting_types if item.kind == "posting" else budget_types).add(item.type)
    return totals, posting_types, budget_types


def _resolve_linked_item(
    conn: sqlite3.Connection,
    items_by_id: dict[str, store.PlanItem | None],
    plan_item_id: str,
) -> store.PlanItem | None:
    """`items_by_id` is seeded from store.list_plan_items, which filters retired_at IS
    NULL -- a transaction can still carry plan_item_id pointing at a now-retired item,
    and store.get_plan_item (unlike list_plan_items) has no such filter, so this falls
    back to it and caches the result (even None, for a genuinely dangling id) rather
    than re-querying every time the same id shows up again."""
    if plan_item_id not in items_by_id:
        items_by_id[plan_item_id] = store.get_plan_item(conn, plan_item_id)
    return items_by_id[plan_item_id]


def _actual_by_category(
    conn: sqlite3.Connection,
    period: str,
    items_by_id: dict[str, store.PlanItem | None],
    budget_types: set[str],
) -> tuple[dict[str, int], dict[str, int]]:
    """Sum of amount_cents by category, from outflow-only transactions dated within
    `period` alone -- derived only from the ledger, never from the plan.
    excluded_from_charts transactions are omitted, matching that field's whole
    purpose; inflows (amount_cents > 0 -- paychecks, transfers in) are omitted too,
    same reasoning as _planned_by_category above. A transaction carrying no category
    (never matched, never manually categorized) contributes to no row here:
    "Categories are plan_item.type values plus whatever transactions carry" (README)
    -- not "every transaction," so uncategorized spend is simply invisible to this
    screen, the same way the catch-all's own bucket never becomes its own row.

    Returns (totals, committed_totals). ticket #25: a category string alone can't
    tell a Posting's spend from a Budget's -- plan_item.type has no uniqueness
    constraint, so a Budget can share a Committed name with an unrelated Posting.
    matching.py's auto-matcher explicitly never links a transaction to a Budget
    (ADR-0019 -- "match_text is meaningless for a Budget"), so a Budget's own spend
    almost always reaches here with plan_item_id still null, category set only by
    manual tagging; a Posting's spend, by contrast, is exactly the case that DOES get
    a real plan_item_id from auto-matching. So a linked transaction settles this from
    its own item's kind directly (never committed if that item is a Budget); an
    UNlinked transaction falls back to whether its category name is a Budget type
    actually occurring THIS period (`budget_types`, from _planned_by_category --
    period-scoped, not store-wide) before falling back further to the plain
    Committed-name check. This keeps a same-category Posting/Budget collision from
    misclassifying the WHOLE row (and the Posting's real committed dollars) as
    Flexible, while still catching the far more common unlinked-Budget-spend case.

    Residual, accepted gap: an UNlinked transaction whose category collides with an
    occurring Budget's type is indistinguishable, from data alone, from a Posting
    transaction that simply lost its plan_item_id link (a manual re-tag, a CSV
    re-import, an edit that only touched category) -- both look identical here. No
    signal in this schema can tell them apart; closing this fully would need either a
    kind-aware uniqueness constraint on plan_item.type or a per-transaction override,
    both real feature work beyond this ticket's "closing a small correctness gap"
    scope."""
    start, end = money.period_bounds(period)
    totals: dict[str, int] = {}
    committed_totals: dict[str, int] = {}
    for txn in store.list_transactions(conn):
        if txn.category is None or txn.excluded_from_charts or txn.amount_cents >= 0:
            continue
        if not (start <= txn.date < end):
            continue
        totals[txn.category] = totals.get(txn.category, 0) + txn.amount_cents
        if txn.category in COMMITTED_TYPES:
            linked = (
                _resolve_linked_item(conn, items_by_id, txn.plan_item_id)
                if txn.plan_item_id
                else None
            )
            is_budget_txn = (
                linked.kind == "budget" if linked is not None else txn.category in budget_types
            )
            if not is_budget_txn:
                committed_totals[txn.category] = (
                    committed_totals.get(txn.category, 0) + txn.amount_cents
                )
    return totals, committed_totals


def _row(
    category: str,
    actual: dict[str, int],
    planned: dict[str, int],
    committed_actual: dict[str, int],
    budget_types: set[str],
    posting_types: set[str],
) -> dict:
    seen = category in actual
    actual_cents = actual.get(category) if seen else None
    planned_cents = planned.get(category)
    # Magnitude-based, not a plain signed subtraction: actual_cents/planned_cents are
    # both negative (outflow), so a naive actual_cents - planned_cents comes out
    # NEGATIVE when spend is actually higher than planned -- backwards from "positive
    # variance means overspent." abs(actual) - abs(planned) gets the sign right:
    # positive = spent more than planned, negative = spent less, matching the
    # reference prototype's own v = amt - plan (computed from magnitudes there too).
    variance_cents = (
        abs(actual_cents) - abs(planned_cents)
        if actual_cents is not None and planned_cents is not None
        else None
    )
    # ticket #25: a seen row's committed status comes from the precise per-transaction
    # split (see _actual_by_category) -- committed if ANY of this category's real
    # spend is genuinely committed, which is exactly what committed_actual_cents
    # already sums over. An unseen (planned-only) row has no transactions to check, so
    # it falls back to whether this type is Budget-ONLY this period (no Posting also
    # contributing it) -- a Posting's presence always wins a collision, mirroring the
    # seen-row rule above, so an unrelated same-named Budget can never defeat a
    # genuinely committed but not-yet-observed Posting.
    budget_only = category in budget_types and category not in posting_types
    committed = (
        category in committed_actual if seen else category in COMMITTED_TYPES and not budget_only
    )
    return {
        "category": category,
        "actual_cents": actual_cents,
        "planned_cents": planned_cents,
        "seen": seen,
        "committed": committed,
        "variance_cents": variance_cents,
        "is_aggregate": False,
    }


def build_categories(conn: sqlite3.Connection, period: str, today: date) -> dict:
    """The Categories screen's whole state for one period (README): actual and planned
    derived from separate sources, unioned by category key, never backfilling one from
    the other. Display order matches the reference prototype exactly: the top-8
    OBSERVED categories by actual spend, then an "Everything else" aggregate of the
    observed remainder (if any), then every unseen (planned-but-never-observed)
    category -- unseen rows are never capped, aggregated, or blended into the pie,
    since a pie slice needs a real magnitude to draw. Committed/flexible totals sum
    over every OBSERVED category (not just the leading 8), so the headline matches
    the Ledger even when the table doesn't show every row.

    `today` (ticket #23) is the real current date, used only to resolve whether any
    Budget's Adjusted override is still live for whichever Reset Period window it's
    currently in -- see _planned_cents_in_period's own docstring."""
    items = store.list_plan_items(conn)
    items_by_id = {item.id: item for item in items}
    planned, posting_types, budget_types = _planned_by_category(items, conn, period, today)
    actual, committed_actual = _actual_by_category(conn, period, items_by_id, budget_types)
    keys = sorted(set(planned) | set(actual))
    all_rows = [
        _row(key, actual, planned, committed_actual, budget_types, posting_types) for key in keys
    ]

    observed = [r for r in all_rows if r["seen"]]
    unseen = [r for r in all_rows if not r["seen"]]
    observed.sort(key=lambda r: abs(r["actual_cents"]), reverse=True)

    lead = observed[:TOP_N]
    tail = observed[TOP_N:]

    total_actual_cents = sum(r["actual_cents"] for r in observed)
    # Summed directly from the per-transaction split (_actual_by_category), not from
    # observed rows' own "committed" tag -- a row can carry BOTH committed spend (a
    # Posting) and flexible spend (a same-named Budget) at once (ticket #25), so
    # deriving this from the row-level boolean would double-count or drop dollars.
    committed_actual_cents = sum(committed_actual.values())
    flexible_actual_cents = total_actual_cents - committed_actual_cents

    chartable = lead
    if tail:
        everything_else = {
            "category": "Everything else",
            "actual_cents": sum(r["actual_cents"] for r in tail),
            "planned_cents": sum(r["planned_cents"] or 0 for r in tail),
            "seen": True,
            "committed": False,
            "variance_cents": None,
            "is_aggregate": True,
        }
        chartable = lead + [everything_else]

    return {
        "period": period,
        "rows": chartable + unseen,
        "total_actual_cents": total_actual_cents,
        "committed_actual_cents": committed_actual_cents,
        "flexible_actual_cents": flexible_actual_cents,
    }
