"""
BOM explosion: dish-level forecast → raw material quantities.

explode_to_ingredients() multiplies each dish's predicted quantity by its
recipe BOM (from the bill_of_materials table) to produce the total amount of
every raw material needed to fulfil that day's demand.

Returns a flat dict  {raw_material_slug: total_qty_in_material_unit}
so the caller never has to think about which dishes consume which materials.
"""

from __future__ import annotations

import os
from datetime import date

import psycopg2

DEFAULT_DATABASE_URL = "postgresql://copilot:copilot@localhost:5432/restaurant_ops"


def _get_conn(database_url: str | None):
    return psycopg2.connect(database_url or os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL))


# ── DB loaders ─────────────────────────────────────────────────────────────────


def load_bom(
    restaurant_id: int = 1,
    database_url: str | None = None,
) -> dict[int, list[dict]]:
    """
    Load the full bill of materials for a restaurant.
    Returns {dish_id: [{raw_material, qty_per_unit, unit}, ...]}.
    """
    conn = _get_conn(database_url)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT bom.dish_id, bom.raw_material, bom.qty_per_unit::float, bom.unit
        FROM   bill_of_materials bom
        JOIN   menu_items mi ON bom.dish_id = mi.id
        WHERE  mi.restaurant_id = %s
        ORDER  BY bom.dish_id
        """,
        (restaurant_id,),
    )
    rows = cur.fetchall()
    conn.close()

    bom: dict[int, list[dict]] = {}
    for dish_id, raw_material, qty_per_unit, unit in rows:
        bom.setdefault(dish_id, []).append(
            {"raw_material": raw_material, "qty_per_unit": qty_per_unit, "unit": unit}
        )
    return bom


def load_forecast_from_db(
    forecast_date: date,
    model_version: str = "xgb_v1",
    restaurant_id: int = 1,
    database_url: str | None = None,
) -> dict[int, float]:
    """
    Load a forecast from the forecasts table.
    Returns {item_id: predicted_qty}.
    Raises ValueError if no rows are found (run `forecasting/run.py` first).
    """
    conn = _get_conn(database_url)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT item_id, predicted_qty::float
        FROM   forecasts
        WHERE  restaurant_id = %s
          AND  forecast_date  = %s
          AND  model_version  = %s
        """,
        (restaurant_id, forecast_date, model_version),
    )
    rows = cur.fetchall()
    conn.close()

    if not rows:
        raise ValueError(
            f"No forecast found for date={forecast_date}, model={model_version}. "
            "Run `python -m forecasting.run --date <date>` first."
        )
    return {row[0]: row[1] for row in rows}


# ── Core logic ─────────────────────────────────────────────────────────────────


def explode_to_ingredients(
    forecast: dict[int, float],
    restaurant_id: int = 1,
    database_url: str | None = None,
) -> dict[str, float]:
    """
    Multiply predicted dish quantities by recipe BOM to get raw material needs.

    Args:
        forecast: {item_id: predicted_qty_for_the_day}

    Returns:
        {raw_material_slug: total_qty_needed}  (same unit as the BOM column)
    """
    bom = load_bom(restaurant_id, database_url)

    needs: dict[str, float] = {}
    for item_id, predicted_qty in forecast.items():
        if item_id not in bom or predicted_qty <= 0:
            continue
        for ingredient in bom[item_id]:
            mat = ingredient["raw_material"]
            qty = ingredient["qty_per_unit"] * predicted_qty
            needs[mat] = needs.get(mat, 0.0) + qty

    # Round to 4 dp — avoids floating-point noise in downstream comparisons
    return {mat: round(qty, 4) for mat, qty in needs.items()}
