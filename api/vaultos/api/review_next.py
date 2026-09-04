from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from .. import seen
from ..review_next import build_email_review, build_review_next
from .deps import get_conn, get_settings

router = APIRouter()


@router.get("/review-next")
def get_review_next(
    limit: int = Query(5, ge=1, le=50),
    conn=Depends(get_conn),
    settings=Depends(get_settings),
):
    if not settings.vault_readable():
        raise HTTPException(503, detail="vault root is missing or unreadable")
    return build_review_next(conn, settings.vault_root, limit=limit)


@router.get("/email-review")
def get_email_review(
    limit: int = Query(5, ge=1, le=50),
    conn=Depends(get_conn),
    settings=Depends(get_settings),
):
    if not settings.vault_readable():
        raise HTTPException(503, detail="vault root is missing or unreadable")
    return build_email_review(conn, settings.vault_root, limit=limit)


class MarkSeenIn(BaseModel):
    item_type: str
    item_id: str


@router.post("/seen", status_code=204)
def mark_seen(body: MarkSeenIn, conn=Depends(get_conn)):
    # Generic across every item type (ADR-0011) -- reused later by Active &
    # Recent / Results for job-backed items, not Review-Next-specific in
    # name or shape. Idempotent: marking an already-seen item again is a
    # no-op, not an error (see vaultos/seen.py's mark_seen docstring).
    seen.mark_seen(conn, item_type=body.item_type, item_id=body.item_id)
