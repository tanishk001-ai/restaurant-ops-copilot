"""
Load demand data from Postgres for the forecasting engine.
Uses raw psycopg2 (pandas 3 deprecated read_sql with DBAPI2 connections).
"""

from __future__ import annotations

import os
from datetime import date

import numpy as np
import pandas as pd
import psycopg2

DEFAULT_DATABASE_URL = "postgresql://copilot:copilot@localhost:5432/restaurant_ops"


def _get_conn(database_url: str | None = None):
    return psycopg2.connect(database_url or os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL))


def load_daily_demand(database_url: str | None = None) -> pd.DataFrame:
    """
    Return a long-format DataFrame with columns:
        date (datetime64), item_id (int), item_name (str), qty (float)
    One row per (dish, day). Missing days for a dish are filled with 0
    after calling get_item_series().
    """
    conn = _get_conn(database_url)
    cur = conn.cursor()
    cur.execute("""
        SELECT
            DATE(o.ordered_at)   AS date,
            o.item_id,
            mi.name              AS item_name,
            SUM(o.qty)           AS qty
        FROM   orders o
        JOIN   menu_items mi ON o.item_id = mi.id
        WHERE  o.restaurant_id = 1
        GROUP  BY DATE(o.ordered_at), o.item_id, mi.name
        ORDER  BY date, o.item_id
    """)
    rows = cur.fetchall()
    conn.close()

    df = pd.DataFrame(rows, columns=["date", "item_id", "item_name", "qty"])
    df["date"] = pd.to_datetime(df["date"])
    df["qty"]  = df["qty"].astype(float)
    return df


def load_items(database_url: str | None = None) -> dict[int, str]:
    """Return {item_id: item_name} for all active menu items."""
    conn = _get_conn(database_url)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, name FROM menu_items "
        "WHERE restaurant_id = 1 AND active = TRUE ORDER BY id"
    )
    items = {row[0]: row[1] for row in cur.fetchall()}
    conn.close()
    return items


def get_item_series(df: pd.DataFrame, item_id: int) -> pd.Series:
    """
    Extract a COMPLETE daily time series (no gaps) for one dish.
    Missing days are filled with 0. Index is pd.DatetimeIndex.
    """
    sub = df[df["item_id"] == item_id].set_index("date")["qty"]
    full_range = pd.date_range(df["date"].min(), df["date"].max(), freq="D")
    return sub.reindex(full_range, fill_value=0.0).rename(item_id)


def write_forecasts(
    predictions: list[dict],   # [{item_id, forecast_date, predicted_qty, model_version}]
    restaurant_id: int = 1,
    database_url: str | None = None,
) -> None:
    """Upsert forecast rows into the forecasts table (idempotent)."""
    conn = _get_conn(database_url)
    cur = conn.cursor()
    for p in predictions:
        cur.execute(
            """
            INSERT INTO forecasts
                (restaurant_id, item_id, forecast_date, predicted_qty, model_version)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (restaurant_id, item_id, forecast_date, model_version)
            DO UPDATE SET
                predicted_qty = EXCLUDED.predicted_qty,
                created_at    = NOW()
            """,
            (
                restaurant_id,
                p["item_id"],
                p["forecast_date"],
                round(float(p["predicted_qty"]), 4),
                p["model_version"],
            ),
        )
    conn.commit()
    conn.close()
