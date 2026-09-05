# Categories (ticket vault-os-api#9) + recurring charges (ticket vault-os-api#10)

from datetime import date

from fastapi import APIRouter, Depends

from ....api.deps import get_conn, get_settings
from ....timeutil import today_in_tz
from ..categories import build_categories
from ..recurring import build_recurring
from .common import validate_period

router = APIRouter()


@router.get("/finance/categories")
def get_categories(
    period: str | None = None, conn=Depends(get_conn), settings=Depends(get_settings)
):
    today = date.fromisoformat(today_in_tz(settings.hud_tz))
    resolved_period = period or today.strftime("%Y-%m")
    validate_period(resolved_period)
    return build_categories(conn, resolved_period, today)


@router.get("/finance/recurring")
def get_recurring(conn=Depends(get_conn)):
    return build_recurring(conn)
