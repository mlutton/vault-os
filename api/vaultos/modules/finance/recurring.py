"""Recurring charges detection (ticket vault-os-api#10) -- "the optimize spend
surface... living on the Categories screen" (ticket body). Pure detection over the
full transaction history (no period scoping -- the whole point is looking back across
as much history as exists). "Mark to cut" is deliberately NOT modeled here or
persisted server-side: a recurring charge isn't a stored entity of its own, it's
re-derived fresh from the merchant/pattern analysis on every call, and the reference
prototype's own `this.state.cuts` is itself just ephemeral client-side UI state that
resets on reload -- Fable-Os-Web's CategoriesPanel mirrors that with local React
state, not a new DB table.
"""

import sqlite3
from datetime import date

from . import store

# "3+ charges from one merchant within +/-3 days of a monthly interval, amounts
# within 5% of each other" (README / ticket acceptance criteria).
MIN_OCCURRENCES = 3
INTERVAL_DAYS = 30
INTERVAL_TOLERANCE_DAYS = 3
AMOUNT_TOLERANCE = 0.05


def _parse(d: str) -> date:
    return date.fromisoformat(d)


def _amounts_within_tolerance(amounts_cents: list[int]) -> bool:
    magnitudes = [abs(a) for a in amounts_cents]
    lo, hi = min(magnitudes), max(magnitudes)
    if lo == 0:
        return hi == 0
    return (hi - lo) / lo <= AMOUNT_TOLERANCE


def _find_recurring_run(sorted_pairs: list[tuple[str, int]]) -> list[tuple[str, int]] | None:
    """sorted_pairs: [(date_iso, amount_cents), ...] for ONE merchant, sorted by date
    ascending. Grows a run one transaction at a time, extending it only when BOTH the
    gap from the previous transaction is within INTERVAL_DAYS +/- INTERVAL_TOLERANCE_
    DAYS AND adding it would keep the whole run's amounts within AMOUNT_TOLERANCE of
    each other -- checked incrementally, not just once at the end. A late amount
    outlier (a price increase) breaks the run at that point rather than invalidating
    the whole thing retroactively: three genuinely matching charges followed by a
    fourth that jumped 10% still correctly detects the first three, with the price
    increase starting a fresh (not yet long enough) run of its own. Returns the MOST
    RECENT qualifying run (a merchant charged monthly for a year, then stopped, then
    resumed a different recurring charge, should report the resumed one -- most
    relevant to "should I cut this today"), or None."""
    if not sorted_pairs:
        return None
    best: list[tuple[str, int]] | None = None
    run = [sorted_pairs[0]]
    for i in range(1, len(sorted_pairs)):
        gap = (_parse(sorted_pairs[i][0]) - _parse(sorted_pairs[i - 1][0])).days
        gap_ok = (
            INTERVAL_DAYS - INTERVAL_TOLERANCE_DAYS
            <= gap
            <= INTERVAL_DAYS + INTERVAL_TOLERANCE_DAYS
        )
        amount_ok = _amounts_within_tolerance([a for _, a in run] + [sorted_pairs[i][1]])
        if gap_ok and amount_ok:
            run.append(sorted_pairs[i])
        else:
            if len(run) >= MIN_OCCURRENCES:
                best = run
            run = [sorted_pairs[i]]
    if len(run) >= MIN_OCCURRENCES:
        best = run
    return best


def _signal_for(run: list[tuple[str, int]]) -> str:
    """A factual, data-derivable usage signal -- no invented "no file opened in 4
    months" flavor text (the reference prototype's own hand-authored examples), since
    this schema has no app-usage data to draw on. Just what the ledger itself can
    honestly say: how many times, over how long, and when it last landed."""
    first = _parse(run[0][0])
    last = _parse(run[-1][0])
    months_span = max(1, round((last - first).days / 30))
    return f"{len(run)}× charged over {months_span} month{'s' if months_span != 1 else ''}, last on {run[-1][0]}"


def detect_recurring(transactions: list) -> list[dict]:
    """transactions: any objects with .merchant, .date, .amount_cents,
    .excluded_from_charts (store.Txn's own shape). Groups by `merchant` -- the
    editable display name, not the immutable merchant_raw -- so a user who's already
    cleaned up variant statement text ("COMCAST CABLE COMM" vs "COMCAST CABLE
    COMMUNICATIONS") into one name gets that consolidation for free; unedited
    transactions still group correctly since merchant defaults to merchant_raw on
    import. Outflow-only (a recurring paycheck isn't something to "cut") and
    excluded_from_charts transactions are skipped, same reasoning as categories.py.
    Sorted by monthly cost, biggest first -- the most worth reviewing sits at the
    top."""
    by_merchant: dict[str, list[tuple[str, int]]] = {}
    for txn in transactions:
        if txn.excluded_from_charts or txn.amount_cents >= 0:
            continue
        by_merchant.setdefault(txn.merchant, []).append((txn.date, txn.amount_cents))

    rows = []
    for merchant, pairs in by_merchant.items():
        pairs.sort(key=lambda p: p[0])
        run = _find_recurring_run(pairs)
        if run is None:
            continue
        monthly_cents = run[-1][1]  # the most recent charge -- what they're paying now
        rows.append(
            {
                "merchant": merchant,
                "monthly_cents": monthly_cents,
                "annual_cents": monthly_cents * 12,
                "occurrences": len(run),
                "first_date": run[0][0],
                "last_date": run[-1][0],
                "signal": _signal_for(run),
            }
        )

    rows.sort(key=lambda r: abs(r["monthly_cents"]), reverse=True)
    return rows


def build_recurring(conn: sqlite3.Connection) -> dict:
    """The Recurring charges section's whole state, in one call -- no period param,
    unlike every other Finance composition module: detection scans the FULL
    transaction history, not one month, since a monthly cadence can't be judged from
    a single period's data. monthly_total_cents/annual_total_cents are the "N charges
    repeat every month -- $X a month, $Y a year" header figures (README); "marked to
    cut" has no server-side total to compute, since marking itself isn't tracked
    here (see module docstring)."""
    rows = detect_recurring(store.list_transactions(conn))
    monthly_total_cents = sum(r["monthly_cents"] for r in rows)
    return {
        "rows": rows,
        "monthly_total_cents": monthly_total_cents,
        "annual_total_cents": monthly_total_cents * 12,
    }
