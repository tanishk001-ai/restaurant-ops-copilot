"""
Shortfall computation: BOM-exploded needs vs current inventory.

compute_shortfall() diffs projected raw-material quantities against current
stock, adding the reorder point as a mandatory safety buffer.

A material is only flagged as a shortfall if:
    current_qty  <  qty_needed  +  reorder_point

The shortfall is then:
    shortfall_qty = qty_needed + reorder_point − current_qty

This ensures the restaurant always stays above its reorder buffer even after
consuming the forecast quantity.
"""

from __future__ import annotations

import os

import psycopg2

DEFAULT_DATABASE_URL = "postgresql://copilot:copilot@localhost:5432/restaurant_ops"


def _get_conn(database_url: str | None):
    return psycopg2.connect(database_url or os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL))


# ── DB loader ──────────────────────────────────────────────────────────────────


def load_inventory(
    restaurant_id: int = 1,
    database_url: str | None = None,
) -> dict[str, dict]:
    """
    Load current inventory for a restaurant.
    Returns {raw_material_slug: {current_qty, unit, reorder_point}}.
    """
    conn = _get_conn(database_url)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT raw_material, current_qty::float, unit, reorder_point::float
        FROM   inventory
        WHERE  restaurant_id = %s
        """,
        (restaurant_id,),
    )
    rows = cur.fetchall()
    conn.close()

    return {
        row[0]: {
            "current_qty":   row[1],
            "unit":          row[2],
            "reorder_point": row[3],
        }
        for row in rows
    }


# ── Core logic ─────────────────────────────────────────────────────────────────


def compute_shortfall(
    needs: dict[str, float],
    restaurant_id: int = 1,
    database_url: str | None = None,
) -> list[dict]:
    """
    Compare projected raw-material needs against current inventory.

    Args:
        needs: {raw_material_slug: total_qty_needed}  (from bom.explode_to_ingredients)

    Returns:
        List of shortfall dicts, sorted by shortfall_qty descending, each with:
            raw_material   – slug (matches inventory + catalog)
            qty_needed     – grams/ml needed to fulfil the forecast
            reorder_point  – safety buffer to maintain
            current_qty    – what's in stock right now
            shortfall_qty  – how much to order  (qty_needed + reorder_point − current_qty)
            unit           – g | ml | piece (from inventory table)
    """
    inventory = load_inventory(restaurant_id, database_url)

    shortfalls: list[dict] = []
    for mat, qty_needed in needs.items():
        stock = inventory.get(mat)
        if stock is None:
            # Material not tracked in inventory — treat current as 0, reorder as 0
            current_qty   = 0.0
            reorder_point = 0.0
            unit          = "g"
        else:
            current_qty   = stock["current_qty"]
            reorder_point = stock["reorder_point"]
            unit          = stock["unit"]

        required = qty_needed + reorder_point      # must have this much after the day's cooking

        if current_qty < required:
            shortfall_qty = required - current_qty
            shortfalls.append(
                {
                    "raw_material":  mat,
                    "qty_needed":    round(qty_needed, 4),
                    "reorder_point": round(reorder_point, 4),
                    "current_qty":   round(current_qty, 4),
                    "shortfall_qty": round(shortfall_qty, 4),
                    "unit":          unit,
                }
            )

    return sorted(shortfalls, key=lambda x: x["shortfall_qty"], reverse=True)
