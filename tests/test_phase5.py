"""
Phase 5 acceptance tests — FastAPI backend.

Verifies all 5 endpoints via FastAPI TestClient (in-process, no network).
The full dashboard flow is tested end-to-end:
  GET /forecast       → predictions for all 25 dishes
  GET /inventory      → 21 materials with is_low flag
  POST /draft-order   → 14 line items, ₹2,710 total
  POST /approve-order → AWAITING_APPROVAL (approval=False)
  POST /approve-order → ORDER_PLACED (approval=True)
  GET  /ask           → graceful response without API key

Browser visual test: run
    uvicorn api.main:app --reload
and open http://localhost:8000 to confirm the dashboard renders.
"""

from __future__ import annotations

import os
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

TOMORROW = date.today() + timedelta(days=1)


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def client(seeded_db):
    """
    1. Seed DB (via conftest seeded_db fixture).
    2. Generate tomorrow's XGBoost forecast so /draft-order finds it.
    3. Return a TestClient bound to the FastAPI app.
    """
    os.environ["DATABASE_URL"] = seeded_db

    from forecasting.run import run_forecast
    run_forecast(TOMORROW, models=["xgb"], database_url=seeded_db)

    from api.main import app, _reset_state
    _reset_state()                        # start each module with clean state

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


@pytest.fixture(autouse=True)
def reset_state_between_tests():
    """Reset session state before every test that might care."""
    from api.main import _reset_state
    _reset_state()
    yield
    _reset_state()


# ── /health ────────────────────────────────────────────────────────────────────


def test_health_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["orders"] > 0


# ── GET / (dashboard HTML) ────────────────────────────────────────────────────


def test_dashboard_serves_html(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Spice Junction" in r.text
    assert "forecast-chart" in r.text
    assert "prepare-btn" in r.text


# ── GET /forecast ─────────────────────────────────────────────────────────────


def test_forecast_returns_all_dishes(client):
    r = client.get("/forecast")
    assert r.status_code == 200
    data = r.json()

    assert "date" in data
    assert data["date"] == str(TOMORROW)
    assert "predictions" in data
    assert len(data["predictions"]) == 25


def test_forecast_sorted_descending(client):
    data = client.get("/forecast").json()
    qtys = [p["predicted_qty"] for p in data["predictions"]]
    assert qtys == sorted(qtys, reverse=True), "Predictions not sorted by qty desc"


def test_forecast_has_required_fields(client):
    preds = client.get("/forecast").json()["predictions"]
    for p in preds:
        assert "item_id"      in p
        assert "item_name"    in p
        assert "category"     in p
        assert "predicted_qty" in p
        assert p["predicted_qty"] > 0


def test_forecast_custom_date(client, seeded_db):
    """Requesting a date without existing forecasts auto-generates them."""
    next_week = str(TOMORROW + timedelta(days=7))
    r = client.get(f"/forecast?forecast_date={next_week}")
    assert r.status_code == 200
    data = r.json()
    assert data["date"] == next_week
    assert len(data["predictions"]) == 25


# ── GET /inventory ────────────────────────────────────────────────────────────


def test_inventory_returns_all_materials(client):
    r = client.get("/inventory")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 21


def test_inventory_has_required_fields(client):
    items = client.get("/inventory").json()["items"]
    for it in items:
        assert "raw_material"  in it
        assert "current_qty"   in it
        assert "unit"          in it
        assert "reorder_point" in it
        assert "is_low"        in it
        assert isinstance(it["is_low"], bool)


def test_inventory_is_low_flag_correct(client):
    """is_low must be True iff current_qty ≤ reorder_point."""
    items = client.get("/inventory").json()["items"]
    for it in items:
        expected = it["current_qty"] <= it["reorder_point"]
        assert it["is_low"] == expected, (
            f"{it['raw_material']}: is_low={it['is_low']} "
            f"but current={it['current_qty']} reorder={it['reorder_point']}"
        )


# ── POST /draft-order ─────────────────────────────────────────────────────────


def test_draft_order_returns_cart(client):
    r = client.post("/draft-order", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert "cart" in body


def test_draft_order_14_line_items(client):
    """The procurement pipeline must produce exactly 14 shortfall items."""
    cart = client.post("/draft-order", json={}).json()["cart"]
    assert cart["total_items"] == 14, (
        f"Expected 14 line items, got {cart['total_items']}"
    )


def test_draft_order_total_cost(client):
    """Total cost must be ₹2,710 (paneer 13×75 + onion 2×38 + …)."""
    cart = client.post("/draft-order", json={}).json()["cart"]
    assert abs(cart["total_cost"] - 2710.0) < 1.0, (
        f"Expected ₹2,710, got ₹{cart['total_cost']}"
    )


def test_draft_order_line_items_have_explanations(client):
    """Every line item must have a non-empty explanation string."""
    cart = client.post("/draft-order", json={}).json()["cart"]
    for li in cart["line_items"]:
        assert "explanation" in li and li["explanation"], (
            f"{li['product_name']} has no explanation"
        )


def test_draft_order_pack_rounding(client):
    """Verify ceiling division for every line item in the returned cart."""
    import math
    cart = client.post("/draft-order", json={}).json()["cart"]
    for li in cart["line_items"]:
        expected = math.ceil(li["shortfall_qty"] / li["pack_size"])
        assert li["packs_needed"] == expected, (
            f"{li['product_name']}: expected {expected} packs, "
            f"got {li['packs_needed']}"
        )


def test_draft_order_total_matches_sum(client):
    """total_cost == Σ subtotals."""
    cart = client.post("/draft-order", json={}).json()["cart"]
    computed = round(sum(li["subtotal"] for li in cart["line_items"]), 2)
    assert abs(cart["total_cost"] - computed) < 0.01


# ── POST /approve-order ───────────────────────────────────────────────────────


def test_approve_without_draft_returns_400(client):
    """Calling /approve-order before /draft-order must return 400."""
    r = client.post("/approve-order", json={"approval": True})
    assert r.status_code == 400


def test_approve_pending(client):
    """approval=False returns AWAITING_APPROVAL and does not place order."""
    client.post("/draft-order", json={})          # populate state
    r = client.post("/approve-order", json={"approval": False})
    assert r.status_code == 200
    assert r.json()["status"] == "AWAITING_APPROVAL"
    # State should still be populated (order not placed)
    from api.main import _state
    assert _state.explained_cart is not None


def test_approve_full_flow(client):
    """
    End-to-end:  draft-order → approve (pending) → approve (confirmed).
    Simulated order must be placed, state cleared, order_id is SIM-*.
    """
    # Step 1: Draft
    r1 = client.post("/draft-order", json={})
    assert r1.status_code == 200
    cart = r1.json()["cart"]
    assert cart["total_items"] == 14

    # Step 2: Pending (approval=False)
    r2 = client.post("/approve-order", json={"approval": False})
    assert r2.json()["status"] == "AWAITING_APPROVAL"

    # Step 3: Confirm (approval=True)
    r3 = client.post("/approve-order", json={"approval": True})
    assert r3.status_code == 200
    body = r3.json()
    assert body["status"] == "ORDER_PLACED"
    assert "order" in body
    order = body["order"]
    assert order["success"] is True
    assert order["order_id"].startswith("SIM-")
    assert "SIMULATED" in order["status"]
    assert order["total"] > 0

    # State must be cleared after placing
    from api.main import _state
    assert _state.client is None
    assert _state.explained_cart is None


# ── GET /ask ──────────────────────────────────────────────────────────────────


def test_ask_no_api_key(client, monkeypatch):
    """Without API key, /ask returns a human-readable message (no error)."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = client.get("/ask?q=what+is+the+current+paneer+stock")
    assert r.status_code == 200
    body = r.json()
    assert "question" in body
    assert "answer"   in body
    assert body["answer"]   # not empty


def test_ask_empty_question(client):
    """Missing q param returns 422 (FastAPI validation)."""
    r = client.get("/ask")
    assert r.status_code == 422
