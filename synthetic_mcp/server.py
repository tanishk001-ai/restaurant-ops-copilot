"""
Synthetic Swiggy-compatible MCP server.

Exposes the same tool names as Swiggy's live MCP surface so that switching
to MCP_MODE=real requires only a client config change, not code changes.

Run:
    python -m synthetic_mcp.server          # stdio (default)
    python -m synthetic_mcp.server --sse    # SSE on port 8001
"""

from __future__ import annotations

import datetime
import json

from mcp.server.fastmcp import FastMCP

from synthetic_mcp.cart_store import cart
from synthetic_mcp.db import query

mcp = FastMCP(
    "Swiggy Synthetic MCP",
    instructions=(
        "Synthetic Swiggy-compatible MCP server for Restaurant Ops Copilot. "
        "Supports Instamart product search and cart management. "
        "instamart_place_order is a SIMULATION — no real order is ever placed."
    ),
)

# ═══════════════════════════════════════════════════════════════════════════════
# INSTAMART TOOLS
# ═══════════════════════════════════════════════════════════════════════════════


@mcp.tool()
def instamart_product_search(query_str: str, location: str = "Indiranagar") -> str:
    """
    Search for products on Instamart by ingredient name or product name.

    Returns a JSON array of matching products with product_id, name, price,
    pack_size, unit, category, and raw_material slug.
    """
    rows = query(
        """
        SELECT instamart_product_id, product_name, pack_size, unit, price,
               category, name
        FROM   raw_material_catalog
        WHERE  (product_name ILIKE %s OR name ILIKE %s)
          AND  in_stock = TRUE
        ORDER  BY price
        LIMIT  20
        """,
        (f"%{query_str}%", f"%{query_str}%"),
    )
    products = [
        {
            "product_id":   r[0],
            "name":         r[1],
            "pack_size":    float(r[2]),
            "unit":         r[3],
            "price":        float(r[4]),
            "category":     r[5],
            "raw_material": r[6],
        }
        for r in rows
    ]
    return json.dumps(products)


@mcp.tool()
def instamart_view_cart() -> str:
    """
    View the current draft Instamart cart.

    Returns a JSON object with items list and total cost.
    """
    result = {
        "items":      cart.items,
        "item_count": len(cart),
        "total":      cart.total(),
        "currency":   "INR",
    }
    return json.dumps(result)


@mcp.tool()
def instamart_add_to_cart(product_id: str, qty: int) -> str:
    """
    Add a product to the draft Instamart cart.

    Args:
        product_id: Instamart product ID (e.g. "IM_001")
        qty: number of packs to add (must be >= 1)

    Returns JSON with success flag and updated cart total.
    """
    if qty < 1:
        return json.dumps({"success": False, "message": "qty must be >= 1"})

    rows = query(
        "SELECT instamart_product_id, product_name, price "
        "FROM raw_material_catalog WHERE instamart_product_id = %s AND in_stock = TRUE",
        (product_id,),
    )
    if not rows:
        return json.dumps({"success": False, "message": f"Product {product_id!r} not found or out of stock"})

    pid, name, price = rows[0]
    cart.add(pid, name, qty, float(price))
    return json.dumps({
        "success":    True,
        "message":    f"Added {qty}× {name} to cart",
        "cart_total": cart.total(),
    })


@mcp.tool()
def instamart_remove_from_cart(product_id: str) -> str:
    """Remove a product from the draft Instamart cart by product_id."""
    removed = cart.remove(product_id)
    if removed:
        return json.dumps({"success": True,  "message": f"Removed {product_id} from cart", "cart_total": cart.total()})
    return json.dumps({"success": False, "message": f"{product_id!r} is not in the cart"})


@mcp.tool()
def instamart_clear_cart() -> str:
    """Clear all items from the draft Instamart cart."""
    cart.clear()
    return json.dumps({"success": True, "message": "Cart cleared"})


@mcp.tool()
def instamart_place_order() -> str:
    """
    SIMULATION ONLY — logs the draft cart as a placed order, then clears it.

    In production this is a COD order and non-cancellable.
    The agent NEVER calls this autonomously; it requires explicit human approval.
    Real Swiggy integration: MCP_MODE=real + human approval gate.
    """
    if len(cart) == 0:
        return json.dumps({"success": False, "message": "Cart is empty"})

    items_snapshot = cart.items
    total = cart.total()
    order_id = f"SIM-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"

    import sys
    print(
        f"[SIMULATED ORDER] {order_id}: {len(items_snapshot)} items, "
        f"total ₹{total:.2f}",
        file=sys.stderr,
        flush=True,
    )

    cart.clear()
    return json.dumps({
        "success":  True,
        "order_id": order_id,
        "items":    items_snapshot,
        "total":    total,
        "payment":  "COD",
        "status":   "SIMULATED — no real order placed",
        "eta_mins": 45,
    })


# ═══════════════════════════════════════════════════════════════════════════════
# FOOD TOOLS (stub — mirrors Swiggy Food surface)
# ═══════════════════════════════════════════════════════════════════════════════


@mcp.tool()
def food_restaurant_search(query_str: str, location: str = "Indiranagar") -> str:
    """Search for restaurants on Swiggy Food. (Stub — returns Spice Junction.)"""
    results = [
        {
            "id":                "REST_001",
            "name":              "Spice Junction",
            "locality":          "Indiranagar, Bengaluru",
            "cuisine":           ["North Indian"],
            "rating":            4.3,
            "delivery_time_min": 35,
            "min_order_inr":     200,
        }
    ]
    return json.dumps(results)


@mcp.tool()
def food_get_menu(restaurant_id: str) -> str:
    """Get the full menu for a restaurant (reads live data from DB)."""
    rows = query(
        """
        SELECT mi.id, mi.name, mi.price, mi.category
        FROM   menu_items mi
        JOIN   restaurants r ON mi.restaurant_id = r.id
        WHERE  r.id = 1 AND mi.active = TRUE
        ORDER  BY mi.category, mi.name
        """,
    )
    categories: dict[str, list] = {}
    for row in rows:
        cat = row[3]
        categories.setdefault(cat, []).append(
            {"id": row[0], "name": row[1], "price": float(row[2])}
        )
    return json.dumps({"restaurant_id": restaurant_id, "menu": categories})


@mcp.tool()
def food_get_order_status(order_id: str) -> str:
    """Get the delivery status of a Swiggy Food order. (Stub.)"""
    return json.dumps({
        "order_id": order_id,
        "status":   "DELIVERED",
        "note":     "Stub — synthetic server only",
    })


# ═══════════════════════════════════════════════════════════════════════════════
# DINEOUT TOOLS (stub)
# ═══════════════════════════════════════════════════════════════════════════════


@mcp.tool()
def dineout_list_restaurants(locality: str = "Indiranagar") -> str:
    """List restaurants available for dine-in booking. (Stub.)"""
    results = [
        {
            "id":               "REST_001",
            "name":             "Spice Junction",
            "locality":         locality,
            "cuisine":          ["North Indian"],
            "rating":           4.3,
            "dineout_available": True,
        }
    ]
    return json.dumps(results)


@mcp.tool()
def dineout_book_table(restaurant_id: str, date: str, guests: int) -> str:
    """Book a dine-in table. (Stub — simulation only, no real booking.)"""
    return json.dumps({
        "success":     True,
        "booking_id":  f"DINE-{restaurant_id}-{date.replace('-', '')}",
        "restaurant":  "Spice Junction",
        "date":        date,
        "guests":      guests,
        "status":      "SIMULATED — no real booking made",
    })


# ═══════════════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    if "--sse" in sys.argv:
        mcp.run(transport="sse")
    else:
        mcp.run()   # stdio (default)
