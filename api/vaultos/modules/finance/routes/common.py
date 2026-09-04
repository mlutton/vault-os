"""Helpers shared by more than one of the finance route modules."""

from fastapi import HTTPException

from .. import money, store


def validate_period(period: str) -> None:
    # money.is_valid_period also checks the month is 01-12, not just the digit shape --
    # a bare regex here previously let '2026-13' through to calendar.monthrange() and
    # crashed with an uncaught calendar.IllegalMonthError instead of this 400.
    if not money.is_valid_period(period):
        raise HTTPException(400, detail=f"period must be YYYY-MM, got {period!r}")


def planned_posting_to_dict(pp: store.PlannedPosting) -> dict:
    return {
        "id": pp.id,
        "plan_item_id": pp.plan_item_id,
        "period": pp.period,
        "expected_date": pp.expected_date,
        "expected_amount_cents": pp.expected_amount_cents,
        "created_at": pp.created_at,
        "matched_txn_id": pp.matched_txn_id,
        "deferred_date": pp.deferred_date,
    }
