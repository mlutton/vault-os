import sqlite3
import uuid
from dataclasses import dataclass

from . import money, plan, store


def run_month_end_close(conn: sqlite3.Connection, open_period: str, created_at: str) -> list[store.PlannedPosting]:
    """Materializes one Planned Posting per Posting occurrence landing in `open_period`
    (ADR-0019 ticket #20) -- a week-unit Posting landing twice gets two independent
    rows, each freezing the item's own estimate_cents as its expected_amount_cents at
    this moment (see migration 0010's own comment for why a snapshot, not a live join).

    Idempotent: every occurrence's date is recomputed fresh from the item's own
    Cadence on every call (fresh_occurrences_for_item, never reading what's already
    materialized), but store.create_planned_posting's INSERT OR IGNORE against the
    (plan_item_id, expected_date) UNIQUE index silently no-ops any occurrence already
    materialized -- running this twice in a row for the same period is a safe repeat,
    not a duplicate (ticket #20's own acceptance criterion). Returns only the NEWLY
    created rows, so a repeat run returns an empty list.

    First-run only, by design (ticket #20's own scope) -- this never closes the period
    it's materializing FROM, nor carries forward an Overdue Planned Posting from a
    prior closed period; that's ticket #24. Budgets never materialize here -- they
    have no discrete occurrence to freeze (ADR-0019's Budget entry: "never
    materializes a Planned Posting").

    ticket #21: after materializing an item's occurrences, immediately reconciles them
    against any real transaction that already existed for this item/period (imported
    or manually confirmed before this close ever ran) -- without this, a freshly
    materialized row always starts matched_txn_id NULL, silently regressing an
    already-"processed" occurrence to "overdue" the instant this runs (a real
    regression caught in code review)."""
    created_ids_by_item: dict[str, list[str]] = {}
    for item in store.list_plan_items(conn):
        if item.kind != "posting":
            continue
        new_ids = []
        for occ in plan.fresh_occurrences_for_item(item, open_period):
            row = store.create_planned_posting(
                conn,
                posting_id=str(uuid.uuid4()),
                plan_item_id=item.id,
                period=open_period,
                expected_date=occ.isoformat(),
                expected_amount_cents=item.estimate_cents,
                created_at=created_at,
            )
            if row is not None:
                new_ids.append(row.id)
        if new_ids:
            store.reconcile_existing_transactions_for_item_period(conn, item.id, open_period)
            created_ids_by_item[item.id] = new_ids

    created = []
    for ids in created_ids_by_item.values():
        for posting_id in ids:
            row = store.get_planned_posting(conn, posting_id)
            if row is not None:
                created.append(row)
    return created


@dataclass
class CloseResult:
    old_period: str
    new_period: str
    materialized: list[store.PlannedPosting]
    carried_forward: list[store.PlannedPosting]


def close_month(conn: sqlite3.Connection, current_open_period: str, created_at: str) -> CloseResult:
    """The real Month-End Close on a rollover (ticket #24) -- run_month_end_close above
    (#20) only ever materializes; this actually closes `current_open_period` for good
    and opens the next one.

    1. Safety-net materialize `current_open_period` itself first -- covers a Plan Item
       added mid-month that never got a chance to materialize before Close was clicked.
    2. Find every still-unreconciled Planned Posting in `current_open_period` (matched
       nor manually ticked -- store.list_unreconciled_planned_postings_for_period).
    3. Materialize the new period's own fresh occurrences BEFORE carrying anything
       forward -- a carried-forward row's (plan_item_id, expected_date) can never
       collide with a freshly materialized one (always a different expected_date by
       construction), but sequencing it this way keeps that invariant obvious rather
       than incidental.
    4. Carry the unreconciled rows forward into the new period, in place (same id,
       expected_date, and any Deferred date -- store.carry_forward_planned_postings).
    5. Advance open_period and record last_closed_period (store.close_period) --
       guarded there against a double-invocation race; a second call for the same
       `current_open_period` raises PeriodClosedError rather than silently re-closing.

    Idempotent by construction for steps 1-4 even under a race: re-materializing is
    already idempotent (#20's own guarantee), and a repeat carry-forward query simply
    finds nothing left in `current_open_period` once the first call already moved it
    out. Only step 5's period-advance itself needs (and has) an explicit guard."""
    safety_net = run_month_end_close(conn, current_open_period, created_at)

    carry_candidates = store.list_unreconciled_planned_postings_for_period(conn, current_open_period)
    new_period = money.next_period(current_open_period)

    materialized = safety_net + run_month_end_close(conn, new_period, created_at)
    carried_forward = store.carry_forward_planned_postings(
        conn, [pp.id for pp in carry_candidates], new_period,
    )

    store.close_period(conn, closed_period=current_open_period, new_open_period=new_period)

    return CloseResult(
        old_period=current_open_period, new_period=new_period,
        materialized=materialized, carried_forward=carried_forward,
    )
