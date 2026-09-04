"""Module discovery (ADR-0022).

A module is a Python package directly under ``vaultos/modules/`` exposing
exactly one entry point, ``register(app, ctx)``, which builds and returns
the module's ``APIRouter``. ``main.py`` calls :func:`register_all` instead
of naming modules individually.

Modules own their endpoints, schemas, migrations, and events; everything
else is infrastructure. ``ModuleContext`` is the injection point for that
infrastructure, and it is deliberately empty today: registration runs at
import time, before the lifespan handler has created the connection,
settings, or registry, so handlers reach those through the FastAPI
dependencies on ``app.state`` (``vaultos/api/deps.py``) exactly as before.
Fields land here as the contract's later pieces do -- per-module migrations
(#29), the ``llm``/``exec`` seams (#30).
"""

import importlib
import pkgutil
from dataclasses import dataclass

from fastapi import FastAPI


@dataclass
class ModuleContext:
    pass


def register_all(app: FastAPI, ctx: ModuleContext) -> None:
    # Sorted so registration order (and thus the OpenAPI schema) never
    # depends on filesystem enumeration order.
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name):
        if not info.ispkg:
            continue
        module = importlib.import_module(f"{__name__}.{info.name}")
        app.include_router(module.register(app, ctx))
