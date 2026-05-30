"""
Seed the database with synthetic data for Spice Junction.

Usage:
    python -m data_gen.seed
    python -m data_gen.seed --database-url postgresql://...

Idempotent: applies the schema first (IF NOT EXISTS), then truncates and
re-inserts all data. Safe to run multiple times.
"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

import psycopg2
import psycopg2.extras

from data_gen.constants import (
    BILL_OF_MATERIALS,
    MENU_ITEMS,
    RAW_MATERIAL_CATALOG,
    RESTAURANT,
)
from data_gen.generate import compute_avg_daily_need, generate_orders

DEFAULT_DATABASE_URL = "postgresql://copilot:copilot@localhost:5432/restaurant_ops"
SCHEMA_PATH = Path(__file__).parent.parent / "db" / "schema.sql"


def _get_conn(database_url: str):
    return psycopg2.connect(database_url)


def seed_database(database_url: str | None = None) -> None:
    database_url = database_url or os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
    conn = _get_conn(database_url)
    cur = conn.cursor()

    # ── 1. Apply schema (idempotent) ──────────────────────────────────────────
    print("Applying schema …")
    cur.execute(SCHEMA_PATH.read_text())
    conn.commit()

    # ── 2. Truncate existing data ─────────────────────────────────────────────
    print("Truncating existing data …")
    cur.execute("""
        TRUNCATE TABLE
            forecasts,
            menu_embeds,
            bill_of_materials,
            orders,
            inventory,
            raw_material_catalog,
            menu_items,
            restaurants
        RESTART IDENTITY CASCADE
    """)
    conn.commit()

    # ── 3. Restaurant ─────────────────────────────────────────────────────────
    print("Seeding restaurant …")
    cur.execute(
        "INSERT INTO restaurants (name, locality, cuisine) VALUES (%s, %s, %s) RETURNING id",
        (RESTAURANT["name"], RESTAURANT["locality"], RESTAURANT["cuisine"]),
    )
    restaurant_id: int = cur.fetchone()[0]

    # ── 4. Menu items ─────────────────────────────────────────────────────────
    print(f"Seeding {len(MENU_ITEMS)} menu items …")
    item_ids: dict[str, int] = {}
    for item in MENU_ITEMS:
        cur.execute(
            "INSERT INTO menu_items (restaurant_id, name, price, category) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (restaurant_id, item["name"], item["price"], item["category"]),
        )
        item_ids[item["slug"]] = cur.fetchone()[0]

    # ── 5. Bill of materials ──────────────────────────────────────────────────
    print(f"Seeding BOM for {len(BILL_OF_MATERIALS)} dishes …")
    bom_rows = []
    for dish_slug, ingredients in BILL_OF_MATERIALS.items():
        dish_id = item_ids[dish_slug]
        for ing in ingredients:
            bom_rows.append((dish_id, ing["raw_material"], ing["qty_per_unit"], ing["unit"]))
    psycopg2.extras.execute_values(
        cur,
        "INSERT INTO bill_of_materials (dish_id, raw_material, qty_per_unit, unit) VALUES %s",
        bom_rows,
    )

    # ── 6. Raw material catalog ───────────────────────────────────────────────
    print(f"Seeding {len(RAW_MATERIAL_CATALOG)} catalog entries …")
    catalog_rows = [
        (slug, p["instamart_product_id"], p["product_name"], p["pack_size"], p["unit"], p["price"], p["category"])
        for slug, p in RAW_MATERIAL_CATALOG.items()
    ]
    psycopg2.extras.execute_values(
        cur,
        "INSERT INTO raw_material_catalog "
        "(name, instamart_product_id, product_name, pack_size, unit, price, category) VALUES %s",
        catalog_rows,
    )

    # ── 7. Orders (bulk insert — biggest table) ───────────────────────────────
    print("Generating order history (17 months, Poisson-sampled) …")
    t0 = time.time()
    order_rows = generate_orders(restaurant_id, item_ids)
    print(f"  Generated {len(order_rows):,} order rows in {time.time()-t0:.1f}s")

    print("  Bulk-inserting orders …")
    t0 = time.time()
    psycopg2.extras.execute_values(
        cur,
        "INSERT INTO orders (restaurant_id, item_id, qty, ordered_at) VALUES %s",
        order_rows,
        page_size=5000,
    )
    print(f"  Inserted in {time.time()-t0:.1f}s")

    # ── 8. Inventory (2× avg daily need; reorder_point = 0.5× avg) ───────────
    print("Computing and seeding inventory …")
    avg_daily = compute_avg_daily_need(item_ids)
    inventory_rows = []
    for mat_slug, daily_qty in avg_daily.items():
        unit = RAW_MATERIAL_CATALOG[mat_slug]["unit"] if mat_slug in RAW_MATERIAL_CATALOG else "g"
        inventory_rows.append((
            restaurant_id,
            mat_slug,
            round(daily_qty * 2, 2),          # current_qty  = 2 days of stock
            unit,
            round(daily_qty * 0.5, 2),         # reorder_point = 0.5 days
        ))
    psycopg2.extras.execute_values(
        cur,
        "INSERT INTO inventory (restaurant_id, raw_material, current_qty, unit, reorder_point) VALUES %s",
        inventory_rows,
    )

    conn.commit()
    conn.close()

    print(
        f"\nDone. Seeded: 1 restaurant | {len(MENU_ITEMS)} menu items | "
        f"{sum(len(v) for v in BILL_OF_MATERIALS.values())} BOM lines | "
        f"{len(RAW_MATERIAL_CATALOG)} catalog entries | "
        f"{len(order_rows):,} orders | {len(inventory_rows)} inventory lines"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed the restaurant-ops-copilot database")
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args()
    seed_database(args.database_url)
