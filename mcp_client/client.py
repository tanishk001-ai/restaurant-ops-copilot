"""
Swappable Swiggy MCP client.

Phase 3 — synthetic mode (this file):
  · Product search  → queries raw_material_catalog directly via DB
  · Cart operations → in-memory dict, same semantics as the MCP server's cart
  · place_order     → always raises; human approval gate is enforced here

Phase 4 (to be built on top of this interface):
  · MCP_MODE=synthetic → connects via MCP stdio/SSE to synthetic_mcp.server
  · MCP_MODE=real      → connects to https://mcp.swiggy.com/im (Builders Club)

The procurement engine, agent planner, and human approval gate all call this
client — never the MCP server directly. Swapping MCP_MODE is the only change
needed to go from development to production.
"""

from __future__ import annotations

import math
import os
from typing import Any

import psycopg2

DEFAULT_DATABASE_URL = "postgresql://copilot:copilot@localhost:5432/restaurant_ops"


def _get_conn(database_url: str | None):
    return psycopg2.connect(database_url or os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL))


class MCPClient:
    """
    Swappable Swiggy Instamart MCP client.

    Instantiate once per procurement session and pass it through the pipeline;
    the cart state lives on the instance.
    """

    def __init__(
        self,
        mode: str | None = None,
        database_url: str | None = None,
    ) -> None:
        self.mode = (mode or os.getenv("MCP_MODE", "synthetic")).lower()
        self._database_url = database_url
        self._cart: dict[str, dict] = {}  # product_id → item dict
        self._catalog_cache: dict[str, dict] | None = None  # lazy-loaded

    # ── internal helpers ───────────────────────────────────────────────────────

    def _catalog(self) -> dict[str, dict]:
        """Lazily load the full Instamart product catalog from DB (keyed by product_id)."""
        if self._catalog_cache is None:
            conn = _get_conn(self._database_url)
            cur = conn.cursor()
            cur.execute(
                "SELECT instamart_product_id, product_name, price, pack_size, unit "
                "FROM raw_material_catalog WHERE in_stock = TRUE"
            )
            self._catalog_cache = {
                row[0]: {"name": row[1], "price": float(row[2]),
                         "pack_size": float(row[3]), "unit": row[4]}
                for row in cur.fetchall()
            }
            conn.close()
        return self._catalog_cache

    def _cart_total(self) -> float:
        return round(sum(v["subtotal"] for v in self._cart.values()), 2)

    # ── Instamart tools (mirror Swiggy MCP tool names exactly) ─────────────────

    def instamart_product_search(
        self, query: str, location: str = "Indiranagar"
    ) -> list[dict]:
        """Search for products by ingredient or product name."""
        if self.mode != "synthetic":
            raise NotImplementedError("Real mode: Phase 4 + Builders Club access")

        conn = _get_conn(self._database_url)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT instamart_product_id, product_name, pack_size, unit,
                   price, category, name
            FROM   raw_material_catalog
            WHERE  (product_name ILIKE %s OR name ILIKE %s) AND in_stock = TRUE
            ORDER  BY price
            LIMIT  20
            """,
            (f"%{query}%", f"%{query}%"),
        )
        rows = cur.fetchall()
        conn.close()
        return [
            {"product_id": r[0], "name": r[1], "pack_size": float(r[2]),
             "unit": r[3], "price": float(r[4]), "category": r[5], "raw_material": r[6]}
            for r in rows
        ]

    def instamart_add_to_cart(self, product_id: str, qty: int) -> dict:
        """Add `qty` packs of `product_id` to the draft cart."""
        if self.mode != "synthetic":
            raise NotImplementedError("Real mode: Phase 4 + Builders Club access")
        if qty < 1:
            return {"success": False, "message": "qty must be >= 1"}

        product = self._catalog().get(product_id)
        if product is None:
            return {"success": False, "message": f"Product {product_id!r} not in catalog"}

        if product_id in self._cart:
            self._cart[product_id]["qty"] += qty
            self._cart[product_id]["subtotal"] = round(
                self._cart[product_id]["qty"] * product["price"], 2
            )
        else:
            self._cart[product_id] = {
                "product_id":  product_id,
                "name":        product["name"],
                "qty":         qty,
                "unit_price":  product["price"],
                "subtotal":    round(qty * product["price"], 2),
            }

        return {"success": True, "cart_total": self._cart_total()}

    def instamart_remove_from_cart(self, product_id: str) -> dict:
        if product_id in self._cart:
            del self._cart[product_id]
            return {"success": True, "cart_total": self._cart_total()}
        return {"success": False, "message": f"{product_id!r} not in cart"}

    def instamart_clear_cart(self) -> dict:
        self._cart.clear()
        return {"success": True, "message": "Cart cleared"}

    def instamart_view_cart(self) -> dict:
        items = list(self._cart.values())
        return {
            "items":      items,
            "item_count": len(items),
            "total":      self._cart_total(),
            "currency":   "INR",
        }

    def instamart_place_order(self) -> dict:
        """
        COD order placement.

        INTENTIONALLY raises — this method must NEVER be called autonomously.
        Only the human approval gate (Phase 4/5) may trigger it, after the user
        has seen and confirmed the draft cart.
        """
        raise RuntimeError(
            "instamart_place_order requires explicit human approval. "
            "Call this only from the human approval gate (Phase 4/5), "
            "never from the planner or procurement engine."
        )

    def approve_and_place_order(self) -> dict:
        """
        Called ONLY by agent/approval.py after explicit human confirmation.

        This is the approved path to order placement.  It bypasses the
        RuntimeError guard in instamart_place_order() because human approval
        has already been collected by the approval gate.
        """
        import datetime

        if self.mode != "synthetic":
            raise NotImplementedError("Real mode: Builders Club access required")

        if not self._cart:
            return {"success": False, "message": "Cart is empty — nothing to order"}

        items_snapshot = list(self._cart.values())
        total          = self._cart_total()
        order_id       = f"SIM-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"

        self._cart.clear()

        return {
            "success":  True,
            "order_id": order_id,
            "items":    items_snapshot,
            "total":    total,
            "payment":  "COD",
            "status":   "SIMULATED — no real order placed",
            "eta_mins": 45,
        }


def get_client(
    mode: str | None = None,
    database_url: str | None = None,
) -> MCPClient:
    """Factory — returns a fresh MCPClient with an empty cart."""
    return MCPClient(mode=mode, database_url=database_url)
