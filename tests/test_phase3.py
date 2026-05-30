"""
Phase 3 acceptance tests — procurement engine.

Tests:
  BOM explosion
    1. All 21 BOM materials are represented in the exploded needs
    2. Paneer need reconciles by hand (cross-multiply forecast × BOM qty)
    3. Onion need reconciles by hand

  Shortfall
    4. All shortfall_qty values are strictly positive
    5. Shortfall = qty_needed + reorder_point − current_qty (exact arithmetic)
    6. Materials with enough stock are NOT in the shortfall list

  Cart drafting
    7. Pack rounding is strictly ceiling division for every line item
    8. Subtotal = packs × unit_price for every line item
    9. Total cost matches sum of subtotals
   10. Total cost is in a believable range (₹200 – ₹50,000)
   11. No uncatalogued materials
   12. Cart client reflects drafted items (add_to_cart was called)

  Safety
   13. place_order raises RuntimeError — never callable from procurement engine

  End-to-end
   14. Full pipeline runs without error and returns all intermediate data
"""

from __future__ import annotations

import math
import os
from datetime import date, timedelta

import pytest

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://copilot:copilot@localhost:5432/restaurant_ops",
)

TOMORROW = date.today() + timedelta(days=1)


# ── fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def pipeline_result(seeded_db):
    """
    Populate forecasts table, then run the full procurement pipeline once.
    Shared across all tests in this module.
    """
    # Phase 2 run.py populates the forecasts table; seeded_db wiped it
    from forecasting.run import run_forecast
    run_forecast(TOMORROW, models=["xgb"], database_url=seeded_db)

    from procurement.cart import run_procurement_pipeline
    return run_procurement_pipeline(TOMORROW, database_url=seeded_db, verbose=False)


@pytest.fixture(scope="module")
def needs(pipeline_result):
    return pipeline_result["needs"]


@pytest.fixture(scope="module")
def shortfalls(pipeline_result):
    return pipeline_result["shortfalls"]


@pytest.fixture(scope="module")
def cart(pipeline_result):
    return pipeline_result["cart"]


@pytest.fixture(scope="module")
def line_items_by_mat(cart):
    return {li["raw_material"]: li for li in cart["line_items"]}


# ── BOM explosion ──────────────────────────────────────────────────────────────


def test_bom_all_materials_present(needs):
    """explode_to_ingredients returns all 21 raw materials used in the BOM."""
    from data_gen.constants import BILL_OF_MATERIALS

    expected_mats = {
        ing["raw_material"]
        for ingredients in BILL_OF_MATERIALS.values()
        for ing in ingredients
    }
    assert expected_mats == set(needs.keys()), (
        f"Missing: {expected_mats - set(needs.keys())}  "
        f"Extra: {set(needs.keys()) - expected_mats}"
    )


def test_bom_paneer_reconciles(pipeline_result, needs):
    """
    Hand-reconcile paneer:
      need = sum over dishes with paneer in BOM of (predicted_qty × qty_per_unit)
    """
    from procurement.bom import load_bom

    forecast = pipeline_result["forecast"]
    bom = load_bom(database_url=DATABASE_URL)

    manual_paneer = 0.0
    for item_id, predicted_qty in forecast.items():
        for ing in bom.get(item_id, []):
            if ing["raw_material"] == "paneer":
                manual_paneer += predicted_qty * ing["qty_per_unit"]

    assert manual_paneer > 0, "No paneer demand — check BOM/forecast"
    assert abs(needs["paneer"] - manual_paneer) < 0.01, (
        f"Paneer mismatch: pipeline={needs['paneer']:.4f}  manual={manual_paneer:.4f}"
    )


def test_bom_onion_reconciles(pipeline_result, needs):
    """Hand-reconcile onion the same way."""
    from procurement.bom import load_bom

    forecast = pipeline_result["forecast"]
    bom = load_bom(database_url=DATABASE_URL)

    manual_onion = sum(
        predicted_qty * ing["qty_per_unit"]
        for item_id, predicted_qty in forecast.items()
        for ing in bom.get(item_id, [])
        if ing["raw_material"] == "onion"
    )

    assert manual_onion > 0
    assert abs(needs["onion"] - manual_onion) < 0.01, (
        f"Onion mismatch: pipeline={needs['onion']:.4f}  manual={manual_onion:.4f}"
    )


# ── Shortfall ──────────────────────────────────────────────────────────────────


def test_shortfall_all_positive(shortfalls):
    """Every shortfall_qty is > 0 (zero-shortfall items are never included)."""
    assert shortfalls, "Expected at least one shortfall — check inventory seeding"
    for s in shortfalls:
        assert s["shortfall_qty"] > 0, (
            f"{s['raw_material']}: shortfall_qty={s['shortfall_qty']}"
        )


def test_shortfall_arithmetic_correct(shortfalls):
    """shortfall_qty == qty_needed + reorder_point − current_qty for every item."""
    for s in shortfalls:
        expected = s["qty_needed"] + s["reorder_point"] - s["current_qty"]
        assert abs(s["shortfall_qty"] - expected) < 1e-6, (
            f"{s['raw_material']}: computed={expected:.6f}  stored={s['shortfall_qty']:.6f}"
        )


def test_shortfall_only_deficient_materials(needs, shortfalls):
    """Materials with enough stock (current_qty >= need + reorder_point) are absent."""
    from procurement.shortfall import load_inventory

    inventory = load_inventory(database_url=DATABASE_URL)
    shortfall_mats = {s["raw_material"] for s in shortfalls}

    for mat, qty_needed in needs.items():
        stock = inventory.get(mat, {"current_qty": 0.0, "reorder_point": 0.0})
        has_enough = stock["current_qty"] >= (qty_needed + stock["reorder_point"])
        in_shortfall = mat in shortfall_mats

        if has_enough:
            assert not in_shortfall, (
                f"{mat} has enough stock but appeared in shortfall list"
            )
        else:
            assert in_shortfall, (
                f"{mat} is deficient but is missing from shortfall list"
            )


# ── Cart drafting ──────────────────────────────────────────────────────────────


def test_cart_pack_rounding_is_ceiling(cart):
    """packs_needed == ceil(shortfall_qty / pack_size) for every line item."""
    for li in cart["line_items"]:
        expected = math.ceil(li["shortfall_qty"] / li["pack_size"])
        assert li["packs_needed"] == expected, (
            f"{li['raw_material']}: expected {expected} packs, got {li['packs_needed']} "
            f"(shortfall={li['shortfall_qty']:.4f}, pack_size={li['pack_size']})"
        )


def test_cart_subtotals_correct(cart):
    """subtotal == packs_needed × unit_price for every line item."""
    for li in cart["line_items"]:
        expected = round(li["packs_needed"] * li["unit_price"], 2)
        assert abs(li["subtotal"] - expected) < 0.01, (
            f"{li['raw_material']}: expected ₹{expected}  got ₹{li['subtotal']}"
        )


def test_cart_total_matches_sum(cart):
    """total_cost == sum of all subtotals."""
    expected = round(sum(li["subtotal"] for li in cart["line_items"]), 2)
    assert abs(cart["total_cost"] - expected) < 0.01, (
        f"total_cost={cart['total_cost']}  sum={expected}"
    )


def test_cart_total_believable(cart):
    """Total cost is in a realistic range for a restaurant Sunday order."""
    assert 200 <= cart["total_cost"] <= 50_000, (
        f"Cart total ₹{cart['total_cost']} is outside believable range"
    )


def test_cart_no_uncatalogued(cart):
    """Every shortfall material maps to an Instamart product."""
    assert cart["uncatalogued"] == [], (
        f"Uncatalogued materials: {cart['uncatalogued']}"
    )


def test_cart_paneer_line_item(line_items_by_mat):
    """
    Paneer hand-reconciliation (pack count and cost).
    Expected: 2464.5 g shortfall → ceil(2464.5/200) = 13 packs → ₹975
    """
    li = line_items_by_mat["paneer"]
    assert li["packs_needed"] == math.ceil(li["shortfall_qty"] / li["pack_size"])
    assert abs(li["shortfall_qty"] - 2464.5) < 1.0, (
        f"Paneer shortfall: expected ~2464.5 g, got {li['shortfall_qty']:.2f}"
    )
    assert li["packs_needed"] == 13
    assert abs(li["subtotal"] - 975.0) < 0.01


def test_cart_onion_line_item(line_items_by_mat):
    """
    Onion hand-reconciliation.
    Expected: 1345.5 g shortfall → ceil(1345.5/1000) = 2 packs → ₹76
    """
    li = line_items_by_mat["onion"]
    assert li["packs_needed"] == math.ceil(li["shortfall_qty"] / li["pack_size"])
    assert abs(li["shortfall_qty"] - 1345.5) < 1.0, (
        f"Onion shortfall: expected ~1345.5 g, got {li['shortfall_qty']:.2f}"
    )
    assert li["packs_needed"] == 2
    assert abs(li["subtotal"] - 76.0) < 0.01


def test_cart_qty_ordered_covers_shortfall(cart):
    """qty_ordered (packs × pack_size) always >= shortfall_qty — we never under-order."""
    for li in cart["line_items"]:
        qty_ordered = li["packs_needed"] * li["pack_size"]
        assert qty_ordered >= li["shortfall_qty"] - 1e-9, (
            f"{li['raw_material']}: ordered {qty_ordered} < shortfall {li['shortfall_qty']}"
        )


def test_cart_client_reflects_draft(seeded_db):
    """
    After draft_procurement_cart(), client.instamart_view_cart() shows all items.
    This verifies that instamart_add_to_cart was actually called for each line.
    """
    from mcp_client.client import get_client
    from procurement.bom import explode_to_ingredients, load_forecast_from_db
    from procurement.cart import draft_procurement_cart
    from procurement.shortfall import compute_shortfall

    client = get_client(database_url=seeded_db)
    forecast  = load_forecast_from_db(TOMORROW, database_url=seeded_db)
    needs     = explode_to_ingredients(forecast, database_url=seeded_db)
    shortfalls = compute_shortfall(needs, database_url=seeded_db)
    cart      = draft_procurement_cart(shortfalls, client=client, database_url=seeded_db)

    view = client.instamart_view_cart()
    assert view["item_count"] == cart["total_items"], (
        f"Cart has {view['item_count']} items; expected {cart['total_items']}"
    )
    assert abs(view["total"] - cart["total_cost"]) < 0.01


# ── Safety ─────────────────────────────────────────────────────────────────────


def test_place_order_raises(seeded_db):
    """place_order must never be callable from the procurement engine."""
    from mcp_client.client import get_client

    client = get_client(database_url=seeded_db)
    with pytest.raises(RuntimeError, match="human approval"):
        client.instamart_place_order()


# ── End-to-end pipeline ────────────────────────────────────────────────────────


def test_full_pipeline_structure(pipeline_result):
    """Pipeline result contains all expected keys with correct types."""
    assert "forecast_date" in pipeline_result
    assert "forecast"  in pipeline_result and len(pipeline_result["forecast"])  == 25
    assert "needs"     in pipeline_result and len(pipeline_result["needs"])     == 21
    assert "shortfalls"in pipeline_result and len(pipeline_result["shortfalls"])>  0
    assert "cart"      in pipeline_result
    cart = pipeline_result["cart"]
    assert cart["total_items"] > 0
    assert cart["total_cost"]  > 0
    assert cart["currency"] == "INR"
