"""
Human approval gate — the only place that may call instamart_place_order.

RULE: the agent NEVER places an order autonomously.
      approve_and_place() requires approval=True to be passed explicitly.
      Without it the function returns the explained cart and waits.

explain_cart() uses a deterministic template (no LLM) so explanations are
always grounded in the actual numbers, never hallucinated.
"""

from __future__ import annotations

import sys
from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mcp_client.client import MCPClient


# ── Explanation generation (template-based, no LLM) ───────────────────────────


def explain_cart(
    cart:          dict,
    shortfalls:    list[dict],
    needs:         dict[str, float],
    forecast_date: date,
) -> dict:
    """
    Add a plain-English explanation to each line item.

    Each explanation answers "why are we ordering this?" with exact numbers:
      "Ordering 13 × 200g packs of Fresho Fresh Paneer Block because
       tomorrow's forecast needs 22,115g, current stock is 26,200g with a
       6,550g safety buffer → shortfall 2,465g.
       Cost: 13 × ₹75.00 = ₹975.00."

    Returns the cart dict with per-line 'explanation' fields and top-level
    summary fields added.  Does NOT mutate the input cart.
    """
    sf_map: dict[str, dict] = {s["raw_material"]: s for s in shortfalls}

    explained_items: list[dict] = []
    for li in cart["line_items"]:
        mat = li["raw_material"]
        sf  = sf_map.get(mat, {})

        qty_needed    = sf.get("qty_needed",    needs.get(mat, 0.0))
        current_qty   = sf.get("current_qty",   0.0)
        reorder_point = sf.get("reorder_point", 0.0)
        shortfall_qty = sf.get("shortfall_qty", li["shortfall_qty"])
        unit          = li["unit"]
        pack_size     = li["pack_size"]
        packs         = li["packs_needed"]
        unit_price    = li["unit_price"]
        subtotal      = li["subtotal"]

        explanation = (
            f"Ordering {packs} × {pack_size:.0f}{unit} pack(s) of "
            f"{li['product_name']} because {forecast_date}'s forecast needs "
            f"{qty_needed:,.0f}{unit}, current stock is {current_qty:,.0f}{unit} "
            f"with a {reorder_point:,.0f}{unit} safety buffer "
            f"→ shortfall: {shortfall_qty:,.0f}{unit}. "
            f"Cost: {packs} × ₹{unit_price:.2f} = ₹{subtotal:.2f}."
        )

        explained_items.append({**li, "explanation": explanation})

    return {
        **cart,
        "line_items":      explained_items,
        "forecast_date":   str(forecast_date),
        "approval_status": "PENDING",
        "note": (
            "Review each explanation above. "
            "Call approve_and_place(explained_cart, client, approval=True) "
            "to place the order, or discard to cancel."
        ),
    }


def print_explained_cart(explained_cart: dict) -> None:
    """Pretty-print the explained cart for a human to review."""
    fc   = explained_cart.get("forecast_date", "?")
    tot  = explained_cart.get("total_cost", 0)
    n    = explained_cart.get("total_items", 0)

    print(f"\n{'═'*70}")
    print(f"  DRAFT PROCUREMENT CART — {fc}")
    print(f"  {n} products | Total: ₹{tot:,.2f}")
    print(f"{'═'*70}")
    for li in explained_cart["line_items"]:
        print(f"\n  {li['product_name']}  ({li['instamart_product_id']})")
        print(f"  {li['explanation']}")
    print(f"\n{'─'*70}")
    print(f"  {explained_cart['note']}")
    print(f"{'═'*70}\n")


# ── Approval gate ──────────────────────────────────────────────────────────────


def approve_and_place(
    explained_cart: dict,
    client:         "MCPClient",
    approval:       bool = False,
) -> dict:
    """
    Place the Instamart order — IF AND ONLY IF approval=True is passed explicitly.

    Args:
        explained_cart: output of explain_cart()
        client:         MCPClient with the draft cart already populated
        approval:       must be True for the order to be placed;
                        False (default) returns a PENDING status and waits

    Returns:
        {status: "AWAITING_APPROVAL" | "ORDER_PLACED", ...}

    INVARIANT: this function is the ONLY place in the codebase that may call
               client.approve_and_place_order().  All other code must call
               client.instamart_place_order() (which raises RuntimeError).
    """
    if not approval:
        return {
            "status":  "AWAITING_APPROVAL",
            "message": (
                "Draft cart is ready for review. "
                "Pass approval=True to place the order."
            ),
            "cart": explained_cart,
        }

    # Human explicitly approved — forward to the client's approval-gate method
    print(
        f"  [APPROVAL GATE] User approved order for {explained_cart.get('forecast_date')}. "
        f"Placing simulated order …",
        file=sys.stderr,
    )
    order_result = client.approve_and_place_order()

    return {
        "status": "ORDER_PLACED",
        "order":  order_result,
        "cart":   explained_cart,
    }
