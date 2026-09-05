# Ledger + transaction edits (ticket vault-os-api#8)

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ....api.deps import get_conn
from .. import ledger, store

router = APIRouter()

_VALID_LEDGER_FILTERS = {"all", "needs_review", "unmatched", "spending"}


@router.get("/finance/ledger")
def get_ledger(filter: str = "all", conn=Depends(get_conn)):  # noqa: A002 -- matches the query param name the client sends
    if filter not in _VALID_LEDGER_FILTERS:
        raise HTTPException(
            400, detail=f"filter must be one of {sorted(_VALID_LEDGER_FILTERS)}, got {filter!r}"
        )
    return ledger.build_ledger(conn, filter)


class TransactionUpdate(BaseModel):
    merchant: str | None = Field(default=None, min_length=1)
    plan_item_id: str | None = None
    category: str | None = None
    excluded_from_charts: bool | None = None
    remember: bool = False


@router.patch("/finance/transactions/{txn_id}")
def update_transaction(txn_id: str, body: TransactionUpdate, conn=Depends(get_conn)):
    existing = store.get_transaction(conn, txn_id)
    if existing is None:
        raise HTTPException(404, detail="transaction not found")

    fields = body.model_fields_set  # which keys the request body actually included --
    # plan_item_id/category are both legitimately nullable TARGET values, so "included
    # with value null" (clear it) has to be distinguishable from "omitted" (leave alone).

    if "plan_item_id" in fields and body.plan_item_id is not None:
        if store.get_plan_item(conn, body.plan_item_id) is None:
            raise HTTPException(400, detail=f"plan item {body.plan_item_id!r} does not exist")

    kwargs: dict = {}
    if "merchant" in fields:
        kwargs["merchant"] = body.merchant
    if "excluded_from_charts" in fields and body.excluded_from_charts is not None:
        kwargs["excluded_from_charts"] = body.excluded_from_charts

    # Confirming or changing a match (README: "Confirming a guess sets match_source =
    # 'user'") -- an explicit plan_item_id in the request is always a human decision,
    # whether it names a real Plan Item or "-- nothing" (a deliberate not-this-one).
    if "plan_item_id" in fields:
        kwargs["plan_item_id"] = body.plan_item_id
        kwargs["match_source"] = "user"
        # Category assignment follows the match unless the user ALSO overrode it in
        # this same request -- "a matched transaction inherits its plan item's type
        # unless the user overrides" (README).
        if "category" not in fields:
            matched_item = (
                store.get_plan_item(conn, body.plan_item_id) if body.plan_item_id else None
            )
            kwargs["category"] = matched_item.type if matched_item else None
            kwargs["category_source"] = "user"

    if "category" in fields:
        kwargs["category"] = body.category
        kwargs["category_source"] = "user"

    updated = store.update_transaction(conn, txn_id, **kwargs)

    if "plan_item_id" in fields and body.remember and body.plan_item_id is not None:
        ledger.remember_match_text(conn, body.plan_item_id, existing.merchant_raw)

    return ledger.txn_to_dict(conn, updated)
