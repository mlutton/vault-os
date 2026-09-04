import sqlite3

from fastapi import Request

from ..config import Settings
from ..registry import Registry


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_conn(request: Request) -> sqlite3.Connection:
    return request.app.state.conn


def get_registry(request: Request) -> Registry:
    return request.app.state.registry
