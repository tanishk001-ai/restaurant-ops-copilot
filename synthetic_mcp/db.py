"""
Thin DB helper for the synthetic MCP server.
Uses a module-level connection that reconnects on failure.
"""

from __future__ import annotations

import os

import psycopg2
import psycopg2.extras

DEFAULT_DATABASE_URL = "postgresql://copilot:copilot@localhost:5432/restaurant_ops"

_conn: psycopg2.extensions.connection | None = None


def _get_conn() -> psycopg2.extensions.connection:
    global _conn
    if _conn is None or _conn.closed:
        _conn = psycopg2.connect(
            os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
        )
        _conn.autocommit = True
    return _conn


def query(sql: str, params: tuple = ()) -> list[tuple]:
    """Execute a SELECT and return all rows."""
    cur = _get_conn().cursor()
    cur.execute(sql, params)
    return cur.fetchall()


def execute(sql: str, params: tuple = ()) -> None:
    """Execute a DML statement (INSERT/UPDATE/DELETE)."""
    cur = _get_conn().cursor()
    cur.execute(sql, params)
