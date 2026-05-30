"""
Generate synthetic daily order rows for Spice Junction.

Algorithm per (dish, day):
  expected = BASE_DEMAND[dish]
           × WEEKLY_MULTIPLIERS[weekday]
           × festival_multiplier(day)
           × (1 + MONTHLY_GROWTH_RATE) ^ month_index   ← compound growth
  qty = Poisson(expected)   ← natural count noise

One row per (dish, day) at noon; qty = total daily quantity for that dish.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Iterator

import numpy as np

from data_gen.constants import (
    BASE_DEMAND,
    FESTIVAL_DATES,
    MENU_ITEMS,
    MONTHLY_GROWTH_RATE,
    WEEKLY_MULTIPLIERS,
)

# Data window: 17 months of history
DATA_START = date(2025, 1, 1)
DATA_END   = date(2026, 5, 28)   # yesterday relative to project start


def _festival_multiplier(d: date) -> float:
    if d in FESTIVAL_DATES:
        return FESTIVAL_DATES[d][1]
    return 1.0


def _month_index(d: date) -> int:
    """0-based months since DATA_START."""
    return (d.year - DATA_START.year) * 12 + (d.month - DATA_START.month)


def generate_orders(
    restaurant_id: int,
    item_ids: dict[str, int],
    seed: int = 42,
) -> list[tuple]:
    """
    Return a list of (restaurant_id, item_id, qty, ordered_at) tuples.
    ordered_at is always at 12:00 UTC for the given day.
    """
    rng = np.random.default_rng(seed)

    rows: list[tuple] = []
    slugs = [item["slug"] for item in MENU_ITEMS]

    current = DATA_START
    while current <= DATA_END:
        mi = _month_index(current)
        growth = (1.0 + MONTHLY_GROWTH_RATE) ** mi
        weekly = WEEKLY_MULTIPLIERS[current.weekday()]
        festival = _festival_multiplier(current)
        multiplier = weekly * festival * growth

        noon = datetime(current.year, current.month, current.day, 12, 0, 0)

        for slug in slugs:
            if slug not in item_ids:
                continue
            base = BASE_DEMAND.get(slug, 10)
            expected = base * multiplier
            qty = int(rng.poisson(expected))
            if qty > 0:
                rows.append((restaurant_id, item_ids[slug], qty, noon))

        current += timedelta(days=1)

    return rows


def compute_avg_daily_need(item_ids: dict[str, int]) -> dict[str, float]:
    """
    Compute average daily quantity of each raw material consumed,
    based on BASE_DEMAND and BILL_OF_MATERIALS.
    Used to seed inventory levels.
    """
    from data_gen.constants import BILL_OF_MATERIALS

    need: dict[str, float] = {}
    for dish_slug, ingredients in BILL_OF_MATERIALS.items():
        daily_servings = BASE_DEMAND.get(dish_slug, 0)
        for ing in ingredients:
            mat = ing["raw_material"]
            qty = ing["qty_per_unit"] * daily_servings
            need[mat] = need.get(mat, 0.0) + qty

    return need
