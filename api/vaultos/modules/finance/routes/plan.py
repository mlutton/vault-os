import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ....api.deps import get_conn, get_settings
from ....timeutil import today_in_tz, utcnow_z
from .. import money, store
from ..plan import build_plan_summary
from .common import planned_posting_to_dict, validate_period

router = APIRouter()


def _cadence_label(item: store.PlanItem) -> str:
    """Human label for the row/inspector -- computed from (cadence_unit,
    cadence_frequency) for a "dated" item (ADR-0018: `cadence` itself is just the
    discriminator "dated" for these, not a display string anymore); the raw `cadence`
    value doubles as its own label for "one-off" and every "spread *" cadence, same as
    always."""
    if item.cadence == "dated":
        return money.dated_cadence_label(item.cadence_unit, item.cadence_frequency)
    return item.cadence


def _plan_item_to_dict(item: store.PlanItem) -> dict:
    return {
        "id": item.id,
        "name": item.name,
        "estimate_cents": item.estimate_cents,
        "type": item.type,
        "payee": item.payee,
        "day_of_month": item.day_of_month,
        "cadence": item.cadence if item.kind == "posting" else None,
        # Null for a Budget here -- Cadence itself is meaningless for a Budget
        # (ADR-0019), unlike GET /finance/plan's own per-period row cadence_label
        # (vaultos/modules/finance/plan.py), which deliberately still populates a
        # display string (from reset_period) for a Budget row's "when does this land"
        # gutter. Same field name, two different concepts by design -- this one
        # describes the item's actual Cadence configuration; that one is a display
        # fallback.
        "cadence_label": _cadence_label(item) if item.kind == "posting" else None,
        "anchor_period": item.anchor_period,
        "cadence_unit": item.cadence_unit,
        "cadence_frequency": item.cadence_frequency,
        "anchor_date": item.anchor_date,
        "kind": item.kind,
        "reset_period": item.reset_period,
        "account_id": item.account_id,
        "verified": item.verified,
        "is_catch_all": item.is_catch_all,
        "in_projection": item.in_projection,
        "match_text": item.match_text,
        "retired_at": item.retired_at,
    }


class PlanItemCreate(BaseModel):
    name: str = Field(min_length=1)
    estimate_cents: int
    type: str = Field(min_length=1)
    payee: str | None = None
    day_of_month: int | None = None
    cadence: str | None = None
    anchor_period: str | None = None
    cadence_unit: str | None = None
    cadence_frequency: int | None = None
    anchor_date: str | None = None
    kind: str = "posting"
    reset_period: str | None = None
    account_id: str
    verified: bool = False
    is_catch_all: bool = False
    match_text: list[str] = Field(default_factory=list)


class PlanItemUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    estimate_cents: int | None = None
    type: str | None = Field(default=None, min_length=1)
    payee: str | None = None
    day_of_month: int | None = None
    cadence: str | None = None
    anchor_period: str | None = None
    cadence_unit: str | None = None
    cadence_frequency: int | None = None
    anchor_date: str | None = None
    kind: str | None = None
    reset_period: str | None = None
    account_id: str | None = Field(default=None, min_length=1)
    verified: bool | None = None
    is_catch_all: bool | None = None
    match_text: list[str] | None = None


class PlanItemTick(BaseModel):
    period: str
    ticked: bool


@router.get("/finance/plan-items")
def list_plan_items(conn=Depends(get_conn)):
    return [_plan_item_to_dict(i) for i in store.list_plan_items(conn)]


@router.post("/finance/plan-items", status_code=201)
def create_plan_item(body: PlanItemCreate, conn=Depends(get_conn), settings=Depends(get_settings)):
    # A Budget has no Cadence at all (ADR-0019) -- a client creating one is never
    # required to send `cadence`; store.create_plan_item forces the NOT NULL sentinel
    # itself when kind == "budget", regardless of what (if anything) is passed here.
    #
    # ADR-0019 ticket #17: a new monthly (frequency 1) Posting with no explicit Anchor
    # defaults to the current calendar period, so it never retroactively shows a
    # phantom Overdue occurrence for a date before the item existed. Only frequency 1
    # gets this default -- every other frequency already requires an explicit anchor
    # (store.py rejects its absence), so there's nothing to default there.
    anchor_period = body.anchor_period
    if (
        anchor_period is None
        and body.kind == "posting"
        and body.cadence == "dated"
        and body.cadence_unit == "month"
        and body.cadence_frequency == 1
    ):
        anchor_period = today_in_tz(settings.hud_tz)[:7]
    try:
        item = store.create_plan_item(
            conn,
            item_id=str(uuid.uuid4()),
            name=body.name,
            estimate_cents=body.estimate_cents,
            plan_type=body.type,
            payee=body.payee,
            day_of_month=body.day_of_month,
            cadence=body.cadence,
            anchor_period=anchor_period,
            cadence_unit=body.cadence_unit,
            cadence_frequency=body.cadence_frequency,
            anchor_date=body.anchor_date,
            kind=body.kind,
            reset_period=body.reset_period,
            account_id=body.account_id,
            verified=body.verified,
            is_catch_all=body.is_catch_all,
            match_text=body.match_text,
        )
    except store.DuplicateCatchAllError:
        raise HTTPException(409, detail="only one Plan Item may be the catch-all")
    except store.InvalidPlanItemError as exc:
        raise HTTPException(400, detail=exc.message)
    return _plan_item_to_dict(item)


@router.patch("/finance/plan-items/{item_id}")
def update_plan_item(item_id: str, body: PlanItemUpdate, conn=Depends(get_conn)):
    changes = body.model_dump(exclude_unset=True)
    try:
        item = store.update_plan_item(conn, item_id, changes)
    except store.DuplicateCatchAllError:
        raise HTTPException(409, detail="only one Plan Item may be the catch-all")
    except store.InvalidPlanItemError as exc:
        raise HTTPException(400, detail=exc.message)
    if item is None:
        raise HTTPException(404, detail="plan item not found")
    return _plan_item_to_dict(item)


@router.get("/finance/open-period")
def get_open_period(conn=Depends(get_conn), settings=Depends(get_settings)):
    today_period = today_in_tz(settings.hud_tz)[:7]
    return {"period": store.get_open_period(conn, today_period)}


@router.post("/finance/plan-items/{item_id}/tick")
def tick_plan_item(
    item_id: str, body: PlanItemTick, conn=Depends(get_conn), settings=Depends(get_settings)
):
    validate_period(body.period)
    if store.get_plan_item(conn, item_id) is None:
        raise HTTPException(404, detail="plan item not found")
    today_period = today_in_tz(settings.hud_tz)[:7]
    open_period = store.get_open_period(conn, today_period)
    last_closed_period = store.get_last_closed_period(conn)
    ticked_at = utcnow_z() if body.ticked else None
    try:
        plan_period = store.set_ticked(
            conn, item_id, body.period, body.ticked, ticked_at, open_period, last_closed_period
        )
    except store.PeriodClosedError as exc:
        raise HTTPException(409, detail=exc.message)
    return {
        "plan_item_id": plan_period.plan_item_id,
        "period": plan_period.period,
        "ticked": plan_period.ticked,
        "ticked_at": plan_period.ticked_at,
    }


class BudgetAdjustment(BaseModel):
    target_cents: int


@router.post("/finance/plan-items/{item_id}/adjust")
def adjust_budget(
    item_id: str, body: BudgetAdjustment, conn=Depends(get_conn), settings=Depends(get_settings)
):
    # ADR-0019 ticket #23: Adjusted -- a manual, single-Reset-Period override changing a
    # Budget's target amount, forward-only from the moment it's set. Unlike tick (which
    # takes an explicit `period` so a client can catch up on a prior month), this always
    # targets whatever period/window `today` falls in right now -- there's no coherent
    # "catch up on Adjusted for a past period" case (a fully-elapsed period has no
    # forward days left to adjust), so the server computes both, never the client.
    item = store.get_plan_item(conn, item_id)
    if item is None:
        raise HTTPException(404, detail="plan item not found")
    if item.kind != "budget":
        raise HTTPException(400, detail="Adjusted only applies to a Budget")
    today = date.fromisoformat(today_in_tz(settings.hud_tz))
    today_period = today.strftime("%Y-%m")
    open_period = store.get_open_period(conn, today_period)
    # ticket #23 code review: unlike tick_plan_item (which takes a caller-supplied
    # period and legitimately wants to reject a genuinely FUTURE one), this endpoint
    # always targets today_period itself -- there's no other period it can ever
    # target. open_period can go stale BEHIND today_period whenever the user simply
    # hasn't clicked "close the month" in a while -- without this max(), every call in
    # that gap would reject its own today_period as "in the future," permanently
    # breaking the whole feature about a month after first use (a real, near-term bug
    # caught in code review, not a hypothetical). Taking the max never lets today's own
    # period read as closed, without touching the persisted open_period value itself
    # -- ADR-0019 deliberately keeps Month-End Close manual, not auto-advanced, and
    # this doesn't change that.
    #
    # ticket #24 code review: deliberately does NOT pass last_closed_period through to
    # set_adjusted, unlike tick/update_planned_posting -- Adjusted is genuinely
    # calendar-date-native, not period-native. Its read side (plan.active_budget_adjustment)
    # has no open_period/Closed-Period concept at all; it resolves purely from `on` (real
    # today) and money.spread_window. Month-End Close can now advance open_period AHEAD of
    # today_period too (an early close, before the calendar month itself ends) -- were
    # last_closed_period threaded through here, effective_open_period would then equal the
    # NEW (later) open_period while `period` stays today_period, permanently 409ing
    # Adjusted for the rest of the real month even though nothing about "the rest of this
    # Reset Period's remaining real days" actually became invalid. Reusing the generic
    # period-must-equal-open_period rule here would either loudly break the feature (this
    # case) or, if the write were retargeted to open_period instead, silently write an
    # override future reads can never find (open_period and window_start/today diverging
    # in the other direction, a stale-behind open_period). Left out of the new enforcement
    # entirely rather than risk either -- a real design gap, not an oversight, flagged for
    # its own follow-up rather than patched here under this ticket's own carry-forward
    # scope.
    effective_open_period = max(open_period, today_period)
    window_start, _ = money.spread_window(item.reset_period, today)
    try:
        plan_period = store.set_adjusted(
            conn,
            item_id,
            today_period,
            body.target_cents,
            window_start.isoformat(),
            utcnow_z(),
            effective_open_period,
        )
    except store.PeriodClosedError as exc:
        raise HTTPException(409, detail=exc.message)
    return {
        "plan_item_id": plan_period.plan_item_id,
        "period": plan_period.period,
        "adjusted_target_cents": plan_period.adjusted_target_cents,
        "adjusted_window_start": plan_period.adjusted_window_start,
        "adjusted_set_at": plan_period.adjusted_set_at,
    }


@router.get("/finance/plan")
def get_plan(period: str | None = None, conn=Depends(get_conn), settings=Depends(get_settings)):
    today = date.fromisoformat(today_in_tz(settings.hud_tz))
    resolved_period = period or today.strftime("%Y-%m")
    validate_period(resolved_period)
    return build_plan_summary(conn, resolved_period, today)


class PlannedPostingUpdate(BaseModel):
    deferred_date: str | None = None
    expected_amount_cents: int | None = None


@router.patch("/finance/planned-postings/{posting_id}")
def update_planned_posting(
    posting_id: str,
    body: PlannedPostingUpdate,
    conn=Depends(get_conn),
    settings=Depends(get_settings),
):
    # ticket #22: Deferred -- moves a Planned Posting's expected date away from its
    # Posting's Cadence-derived reference, which stays put and unreachable through this
    # endpoint (see store.update_planned_posting's own docstring). `deferred_date: null`
    # is a valid, meaningful request (clears the override, reverting to the Cadence
    # date), not an omission -- exclude_unset below is what tells the two apart.
    if "deferred_date" in body.model_fields_set and body.deferred_date is not None:
        try:
            date.fromisoformat(body.deferred_date)
        except ValueError:
            raise HTTPException(
                400, detail=f"deferred_date must be 'YYYY-MM-DD', got {body.deferred_date!r}"
            )
    # expected_amount_cents has no "clear" concept (planned_posting.expected_amount_cents
    # is NOT NULL, migration 0010) -- unlike deferred_date, an explicit null here isn't a
    # meaningful request, just a malformed one. Without this check it reached the store
    # layer unfiltered and hit the NOT NULL constraint as an unhandled 500 instead of a
    # clean 400 (a real bug caught in code review).
    if "expected_amount_cents" in body.model_fields_set and body.expected_amount_cents is None:
        raise HTTPException(400, detail="expected_amount_cents cannot be null")
    changes = body.model_dump(exclude_unset=True)
    today_period = today_in_tz(settings.hud_tz)[:7]
    open_period = store.get_open_period(conn, today_period)
    last_closed_period = store.get_last_closed_period(conn)
    try:
        updated = store.update_planned_posting(
            conn, posting_id, changes, open_period, last_closed_period
        )
    except store.PeriodClosedError as exc:
        raise HTTPException(409, detail=exc.message)
    if updated is None:
        raise HTTPException(404, detail="planned posting not found")
    return planned_posting_to_dict(updated)
