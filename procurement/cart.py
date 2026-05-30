"""
Draft procurement cart: shortfall list → Instamart draft cart.

draft_procurement_cart() maps each shortfall item to an Instamart product
via raw_material_catalog, computes how many packs to buy (ALWAYS ceiling
division — never short-order a restaurant), calls the MCP client's
instamart_add_to_cart for each line, and returns a fully structured draft cart.

HARD RULE: this module NEVER calls instamart_place_order.
Order placement requires explicit human approval (Phase 4/5 approval gate).
"""

from __future__ import annotations

import math
import os
from datetime import date
from typing import TYPE_CHECKING

import psycopg2

if TYPE_CHECKING:
    from mcp_client.client import MCPClient

DEFAULT_DATABASE_URL = "postgresql://copilot:copilot@localhost:5432/restaurant_ops"


def _get_conn(database_url: str | None):
    return psycopg2.connect(database_url or os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL))


# ── DB loader ──────────────────────────────────────────────────────────────────


def load_catalog(database_url: str | None = None) -> dict[str, dict]:
    """
    Load the Instamart product catalog keyed by raw_material slug.
    Returns {slug: {instamart_product_id, product_name, pack_size, unit, price}}.
    """
    conn = _get_conn(database_url)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT name, instamart_product_id, product_name,
               pack_size::float, unit, price::float
        FROM   raw_material_catalog
        WHERE  in_stock = TRUE
        """
    )
    rows = cur.fetchall()
    conn.close()

    return {
        row[0]: {
            "instamart_product_id": row[1],
            "product_name":         row[2],
            "pack_size":            row[3],
            "unit":                 row[4],
            "price":                row[5],
        }
        for row in rows
    }


# ── Core logic ─────────────────────────────────────────────────────────────────


def draft_procurement_cart(
    shortfalls: list[dict],
    client: "MCPClient | None" = None,
    database_url: str | None = None,
) -> dict:
    """
    Map shortfall items → Instamart products → draft cart.

    For each shortfall item:
      1. Look up the Instamart product via raw_material_catalog.
      2. Compute packs_needed = ceil(shortfall_qty / pack_size).  ← always round UP
      3. Call client.instamart_add_to_cart(product_id, packs_needed).
      4. Record the line item with full cost breakdown.

    Args:
        shortfalls: output of procurement.shortfall.compute_shortfall()
        client:     MCPClient instance (created fresh if None)
        database_url: override DATABASE_URL env var

    Returns:
        {
          line_items:    sorted list of cart line dicts (highest cost first)
          total_items:   number of distinct products ordered
          total_packs:   total pack count across all products
          total_cost:    float, INR
          currency:      "INR"
          uncatalogued:  raw materials with no matching Instamart product
          note:          reminder that human approval is required
        }

    NEVER calls instamart_place_order.
    """
    if client is None:
        from mcp_client.client import get_client
        client = get_client(database_url=database_url)

    catalog = load_catalog(database_url)

    # Start with a clean cart for this procurement run
    client.instamart_clear_cart()

    line_items: list[dict] = []
    uncatalogued: list[str] = []

    for item in shortfalls:
        mat          = item["raw_material"]
        shortfall_qty = item["shortfall_qty"]

        if mat not in catalog:
            uncatalogued.append(mat)
            continue

        product   = catalog[mat]
        pack_size = product["pack_size"]

        # ── CEILING division — never short-order ───────────────────────────
        packs_needed = math.ceil(shortfall_qty / pack_size)

        if packs_needed <= 0:
            continue

        unit_price  = product["price"]
        subtotal    = round(packs_needed * unit_price, 2)
        qty_ordered = round(packs_needed * pack_size, 4)   # actual grams/ml that arrive

        line_item = {
            "raw_material":          mat,
            "instamart_product_id":  product["instamart_product_id"],
            "product_name":          product["product_name"],
            "shortfall_qty":         item["shortfall_qty"],
            "pack_size":             pack_size,
            "unit":                  product["unit"],
            "packs_needed":          packs_needed,
            "qty_ordered":           qty_ordered,   # packs × pack_size
            "unit_price":            unit_price,
            "subtotal":              subtotal,
        }
        line_items.append(line_item)

        # Add to MCP cart — this is the only MCP tool called here
        result = client.instamart_add_to_cart(product["instamart_product_id"], packs_needed)
        if not result.get("success"):
            # Non-fatal: log and continue so the rest of the cart is still built
            import sys
            print(
                f"  [WARN] add_to_cart failed for {product['instamart_product_id']}: "
                f"{result.get('message')}",
                file=sys.stderr,
            )

    total_cost = round(sum(li["subtotal"] for li in line_items), 2)

    return {
        "line_items":    sorted(line_items, key=lambda x: x["subtotal"], reverse=True),
        "total_items":   len(line_items),
        "total_packs":   sum(li["packs_needed"] for li in line_items),
        "total_cost":    total_cost,
        "currency":      "INR",
        "uncatalogued":  uncatalogued,
        "note":          "DRAFT — awaiting human approval before order is placed",
    }


# ── Full pipeline convenience function ─────────────────────────────────────────


def run_procurement_pipeline(
    forecast_date: date,
    model_version: str = "xgb_v1",
    restaurant_id: int = 1,
    client: "MCPClient | None" = None,
    database_url: str | None = None,
    verbose: bool = True,
) -> dict:
    """
    End-to-end procurement pipeline:
        forecast (DB)  →  BOM explosion  →  shortfall  →  draft cart

    Returns a full result dict with all intermediate data plus the draft cart.
    """
    from procurement.bom import explode_to_ingredients, load_forecast_from_db
    from procurement.shortfall import compute_shortfall

    if verbose:
        print(f"\n{'─'*55}")
        print(f"  Procurement Pipeline — {forecast_date}")
        print(f"{'─'*55}")

    # 1. Load forecast
    if verbose:
        print(f"  [1/4] Loading forecast ({model_version}) …", end=" ", flush=True)
    forecast = load_forecast_from_db(forecast_date, model_version, restaurant_id, database_url)
    if verbose:
        print(f"{len(forecast)} dishes")

    # 2. BOM explosion
    if verbose:
        print("  [2/4] Exploding BOM …", end=" ", flush=True)
    needs = explode_to_ingredients(forecast, restaurant_id, database_url)
    if verbose:
        print(f"{len(needs)} raw materials needed")

    # 3. Shortfall
    if verbose:
        print("  [3/4] Computing shortfall …", end=" ", flush=True)
    shortfalls = compute_shortfall(needs, restaurant_id, database_url)
    if verbose:
        print(f"{len(shortfalls)} materials to replenish")

    # 4. Draft cart
    if verbose:
        print("  [4/4] Drafting Instamart cart …", end=" ", flush=True)
    cart = draft_procurement_cart(shortfalls, client=client, database_url=database_url)
    if verbose:
        print(f"{cart['total_items']} products, {cart['total_packs']} packs, ₹{cart['total_cost']:,.2f}")
        print()
        _print_cart(cart)

    return {
        "forecast_date": forecast_date,
        "forecast":      forecast,
        "needs":         needs,
        "shortfalls":    shortfalls,
        "cart":          cart,
    }


def _print_cart(cart: dict) -> None:
    """Pretty-print the draft cart to stdout."""
    print(f"  {'Product':<35} {'Shortfall':>12} {'Packs':>6} {'Unit ₹':>8} {'Subtotal':>10}")
    print(f"  {'─'*35} {'─'*12} {'─'*6} {'─'*8} {'─'*10}")
    for li in cart["line_items"]:
        print(
            f"  {li['product_name']:<35} "
            f"{li['shortfall_qty']:>9.1f}{li['unit']:<3} "
            f"{li['packs_needed']:>6} "
            f"{li['unit_price']:>8.2f} "
            f"₹{li['subtotal']:>9.2f}"
        )
    print(f"  {'─'*73}")
    print(f"  {'TOTAL':>55} ₹{cart['total_cost']:>9.2f}")
    if cart["uncatalogued"]:
        print(f"\n  [!] No Instamart product for: {', '.join(cart['uncatalogued'])}")
    print(f"\n  {cart['note']}")
    print(f"{'─'*55}\n")
