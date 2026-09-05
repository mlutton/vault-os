# Cash flow, balance adjustments, and Month-End Close

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ....api.deps import get_conn, get_settings
from ....timeutil import today_in_tz, utcnow_z
from .. import close, store
from ..cashflow import build_cash_flow, plan_predicted_for_primary_today
from .common import planned_posting_to_dict

router = APIRouter()


@router.get("/finance/cash-flow")
def get_cash_flow(conn=Depends(get_conn), settings=Depends(get_settings)):
    today = date.fromisoformat(today_in_tz(settings.hud_tz))
    return build_cash_flow(conn, today)


class BalanceAdjustmentCreate(BaseModel):
    real_balance_cents: int


@router.post("/finance/balance-adjustments", status_code=201)
def create_balance_adjustment(
    body: BalanceAdjustmentCreate, conn=Depends(get_conn), settings=Depends(get_settings)
):
    # Scoped to the primary account only -- "set today's balance" (README) exists to
    # re-anchor THE PROJECTION, and only the primary account's balance feeds that.
    # Correcting a non-primary account's balance is just ordinary Account editing
    # (PATCH /finance/accounts/{id}), already covered by ticket #3.
    # Known, accepted limitation (code review, 2026-08-17): reading
    # plan_predicted_for_primary_today and writing the adjustment aren't atomic, so a
    # second request (another tab, a plan-item edit) landing in between could make
    # plan_predicted_cents stale by the time it's stored. Not worth a cross-module lock
    # for a single-user local app -- store.py's locks are per-table by design, and
    # plan_predicted_cents here is a point-in-time snapshot for the record, not a value
    # anything re-derives from later.
    primary = store.get_primary_account(conn)
    if primary is None:
        raise HTTPException(400, detail="no primary account is set")
    today = date.fromisoformat(today_in_tz(settings.hud_tz))
    plan_predicted_cents = plan_predicted_for_primary_today(conn, today)
    if plan_predicted_cents is None:
        # plan_predicted_for_primary_today re-reads the primary account itself and can
        # legitimately find none now even though the check above just passed -- a
        # concurrent PATCH clearing is_primary in the narrow window between the two
        # reads. Re-checking here turns that race into the same clean 400 the initial
        # check produces, instead of an uncaught TypeError from `real_balance_cents -
        # None` a few lines down in store.create_balance_adjustment.
        raise HTTPException(400, detail="no primary account is set")
    adjustment = store.create_balance_adjustment(
        conn,
        adjustment_id=str(uuid.uuid4()),
        account_id=primary.id,
        as_of_date=today.isoformat(),
        real_balance_cents=body.real_balance_cents,
        plan_predicted_cents=plan_predicted_cents,
        created_at=utcnow_z(),
    )
    return {
        "id": adjustment.id,
        "account_id": adjustment.account_id,
        "as_of_date": adjustment.as_of_date,
        "real_balance_cents": adjustment.real_balance_cents,
        "plan_predicted_cents": adjustment.plan_predicted_cents,
        "difference_cents": adjustment.difference_cents,
    }


@router.post("/finance/month-end-close")
def month_end_close(conn=Depends(get_conn), settings=Depends(get_settings)):
    # ADR-0019 tickets #20+#24: an explicit, user-triggered action -- never runs on a
    # schedule. Closes the current Open Period for good and opens the next one
    # (close.close_month): materializes anything still missing for the closing period
    # (a Plan Item added mid-month, say), carries forward whatever's still
    # unreconciled there, materializes the new period's own fresh occurrences, then
    # advances open_period and records last_closed_period. Safely re-runnable in the
    # sense that matters (ticket #15 Story #35) -- store.close_period's own
    # compare-and-swap guard rejects a genuine double-invocation (a network retry
    # racing the first call) rather than silently closing the wrong period twice;
    # see its own docstring. Calling this a second time deliberately, after the first
    # already succeeded, is not a no-op -- it closes whatever is open NOW, exactly the
    # same as clicking "close the month" again next month would.
    today_period = today_in_tz(settings.hud_tz)[:7]
    open_period = store.get_open_period(conn, today_period)
    try:
        result = close.close_month(conn, open_period, utcnow_z())
    except store.PeriodClosedError as exc:
        raise HTTPException(409, detail=exc.message)
    return {
        "closed_period": result.old_period,
        "new_period": result.new_period,
        "materialized_count": len(result.materialized),
        "materialized": [planned_posting_to_dict(pp) for pp in result.materialized],
        "carried_forward_count": len(result.carried_forward),
        "carried_forward": [planned_posting_to_dict(pp) for pp in result.carried_forward],
    }
