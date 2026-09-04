import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ....api.deps import get_conn
from ....timeutil import utcnow_z
from .. import store

router = APIRouter()


def _account_to_dict(account: store.Account) -> dict:
    return {
        "id": account.id,
        "nickname": account.nickname,
        "institution": account.institution,
        "type": account.type,
        "last_four": account.last_four,
        "balance_cents": account.balance_cents,
        "is_primary": account.is_primary,
        "mapping_id": account.mapping_id,
        "created_at": account.created_at,
    }


class AccountCreate(BaseModel):
    # nickname/type are "required" per the handoff spec's field list -- reject
    # an empty string at the API boundary too, not just the client's UI, so a
    # direct request can't create an unlabeled account.
    nickname: str = Field(min_length=1)
    institution: str | None = None
    type: str = Field(min_length=1)
    last_four: str | None = None
    balance_cents: int = 0
    is_primary: bool = False


class AccountUpdate(BaseModel):
    # None means "leave unchanged" (store.update_account's semantics); an
    # empty string is never a valid nickname/type, so min_length still
    # applies to the string branch of each Optional.
    nickname: str | None = Field(default=None, min_length=1)
    institution: str | None = None
    type: str | None = Field(default=None, min_length=1)
    last_four: str | None = None
    balance_cents: int | None = None
    is_primary: bool | None = None


@router.get("/finance/accounts")
def list_accounts(conn=Depends(get_conn)):
    return [_account_to_dict(a) for a in store.list_accounts(conn)]


@router.post("/finance/accounts", status_code=201)
def create_account(body: AccountCreate, conn=Depends(get_conn)):
    account = store.create_account(
        conn,
        account_id=str(uuid.uuid4()),
        nickname=body.nickname,
        institution=body.institution,
        account_type=body.type,
        last_four=body.last_four,
        balance_cents=body.balance_cents,
        is_primary=body.is_primary,
        created_at=utcnow_z(),
    )
    return _account_to_dict(account)


@router.patch("/finance/accounts/{account_id}")
def update_account(account_id: str, body: AccountUpdate, conn=Depends(get_conn)):
    account = store.update_account(
        conn,
        account_id,
        nickname=body.nickname,
        institution=body.institution,
        account_type=body.type,
        last_four=body.last_four,
        balance_cents=body.balance_cents,
        is_primary=body.is_primary,
    )
    if account is None:
        raise HTTPException(404, detail="account not found")
    return _account_to_dict(account)
