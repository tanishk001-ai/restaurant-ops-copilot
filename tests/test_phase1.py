"""
Phase 1 acceptance tests — synthetic MCP server.

Tests:
  1. instamart_product_search("paneer")  → structured results containing IM_001
  2. Cart add → view round-trip          → IM_001 present with correct qty
  3. Tool list                            → all expected tools exposed

Requires a running Postgres (docker compose up db -d) and seeded data.
The seeded_db fixture in conftest.py handles both automatically.
"""

from __future__ import annotations

import json
import os
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://copilot:copilot@localhost:5432/restaurant_ops",
)

_SERVER_PARAMS = StdioServerParameters(
    command=sys.executable,
    args=["-m", "synthetic_mcp.server"],
    env={**os.environ, "DATABASE_URL": DATABASE_URL},
)

EXPECTED_TOOLS = {
    "instamart_product_search",
    "instamart_view_cart",
    "instamart_add_to_cart",
    "instamart_remove_from_cart",
    "instamart_clear_cart",
    "instamart_place_order",
    "food_restaurant_search",
    "food_get_menu",
    "food_get_order_status",
    "dineout_list_restaurants",
    "dineout_book_table",
}


# ── helpers ────────────────────────────────────────────────────────────────────

def _parse(result) -> object:
    """Extract and JSON-parse the first content block from a CallToolResult."""
    assert result.content, "Tool returned empty content"
    text = result.content[0].text
    return json.loads(text)


# ── tests ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tool_list_complete(seeded_db):
    """Server exposes all expected Instamart, Food, and Dineout tools."""
    async with stdio_client(_SERVER_PARAMS) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools_result = await session.list_tools()
            names = {t.name for t in tools_result.tools}
            missing = EXPECTED_TOOLS - names
            assert not missing, f"Tools missing from server: {missing}"


@pytest.mark.asyncio
async def test_instamart_product_search_paneer(seeded_db):
    """
    Searching 'paneer' must return at least one result.
    The paneer product (IM_001) must be present with correct shape.
    """
    async with stdio_client(_SERVER_PARAMS) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            result = await session.call_tool(
                "instamart_product_search", {"query_str": "paneer"}
            )
            products = _parse(result)

            assert isinstance(products, list)
            assert len(products) >= 1, "No products returned for 'paneer'"

            ids = [p["product_id"] for p in products]
            assert "IM_001" in ids, f"IM_001 not found; got: {ids}"

            paneer = next(p for p in products if p["product_id"] == "IM_001")
            assert "paneer" in paneer["name"].lower()
            assert paneer["price"] > 0
            assert paneer["pack_size"] > 0
            assert paneer["unit"] in ("g", "ml", "piece")
            assert paneer["category"] == "dairy"
            assert "raw_material" in paneer


@pytest.mark.asyncio
async def test_instamart_product_search_no_match(seeded_db):
    """Searching for a nonsense string returns an empty list, not an error."""
    async with stdio_client(_SERVER_PARAMS) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "instamart_product_search", {"query_str": "xyzzy_nonexistent_item"}
            )
            products = _parse(result)
            assert isinstance(products, list)
            assert len(products) == 0


@pytest.mark.asyncio
async def test_cart_add_view_roundtrip(seeded_db):
    """
    Add IM_001 (qty=5) → view cart → verify item is present with correct qty
    and subtotal.
    """
    async with stdio_client(_SERVER_PARAMS) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # Add 5 packs of paneer
            add = _parse(await session.call_tool(
                "instamart_add_to_cart", {"product_id": "IM_001", "qty": 5}
            ))
            assert add["success"] is True
            assert add["cart_total"] > 0

            # View cart
            view = _parse(await session.call_tool("instamart_view_cart", {}))
            assert view["item_count"] == 1

            item = view["items"][0]
            assert item["product_id"] == "IM_001"
            assert item["qty"] == 5
            assert item["unit_price"] == pytest.approx(75.0)
            assert item["subtotal"] == pytest.approx(375.0)
            assert view["total"] == pytest.approx(375.0)


@pytest.mark.asyncio
async def test_cart_add_invalid_product(seeded_db):
    """Adding a non-existent product ID returns success=False."""
    async with stdio_client(_SERVER_PARAMS) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = _parse(await session.call_tool(
                "instamart_add_to_cart", {"product_id": "IM_FAKE", "qty": 1}
            ))
            assert result["success"] is False


@pytest.mark.asyncio
async def test_cart_add_invalid_qty(seeded_db):
    """Adding qty=0 returns success=False."""
    async with stdio_client(_SERVER_PARAMS) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = _parse(await session.call_tool(
                "instamart_add_to_cart", {"product_id": "IM_001", "qty": 0}
            ))
            assert result["success"] is False


@pytest.mark.asyncio
async def test_instamart_place_order_simulation(seeded_db):
    """
    place_order returns a simulated order ID and clears the cart.
    It must NOT raise an error and must include 'SIMULATED' in status.
    """
    async with stdio_client(_SERVER_PARAMS) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # Populate cart first
            await session.call_tool(
                "instamart_add_to_cart", {"product_id": "IM_002", "qty": 2}
            )

            order = _parse(await session.call_tool("instamart_place_order", {}))
            assert order["success"] is True
            assert "SIM-" in order["order_id"]
            assert "SIMULATED" in order["status"]
            assert order["total"] > 0

            # Cart must be cleared after simulated place
            view = _parse(await session.call_tool("instamart_view_cart", {}))
            assert view["item_count"] == 0


@pytest.mark.asyncio
async def test_food_restaurant_search(seeded_db):
    """Food search stub returns Spice Junction."""
    async with stdio_client(_SERVER_PARAMS) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            results = _parse(await session.call_tool(
                "food_restaurant_search", {"query_str": "north indian"}
            ))
            assert isinstance(results, list)
            assert len(results) >= 1
            assert results[0]["name"] == "Spice Junction"


@pytest.mark.asyncio
async def test_food_get_menu_has_items(seeded_db):
    """Menu endpoint returns dishes from the seeded menu_items table."""
    async with stdio_client(_SERVER_PARAMS) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            menu = _parse(await session.call_tool(
                "food_get_menu", {"restaurant_id": "REST_001"}
            ))
            assert "menu" in menu
            all_items = [item for items in menu["menu"].values() for item in items]
            assert len(all_items) == 25
