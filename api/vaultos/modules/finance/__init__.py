"""Finance -- the first conformant module under ADR-0022.

Domain code (``store``, ``money``, ``plan``, ...) lives at this package's
top level; the HTTP surface lives in ``routes/``, split along its natural
seams. Table renames to the ``finance_`` prefix and per-module migrations
wait on the migration-runner change (#29).
"""

from fastapi import APIRouter, FastAPI


def register(app: FastAPI, ctx) -> APIRouter:
    """ADR-0022 entry point: build and return the module's router.

    The route imports happen here rather than at package top level so that
    importing ``vaultos.modules.finance`` for its domain code never drags
    the FastAPI route wiring in with it.
    """
    from .routes import accounts, cashflow, categories, imports, ledger, plan

    router = APIRouter()
    router.include_router(accounts.router)
    router.include_router(plan.router)
    router.include_router(imports.router)
    router.include_router(ledger.router)
    router.include_router(categories.router)
    router.include_router(cashflow.router)
    return router
