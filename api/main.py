"""
FastAPI backend — Restaurant Ops Copilot.

Endpoints
─────────
GET  /                — dashboard HTML
GET  /health          — DB connectivity check
GET  /forecast        — tomorrow's dish-level XGBoost predictions
GET  /inventory       — current stock with is_low flag per material
POST /draft-order     — forecast → BOM → shortfall → explained draft cart
POST /approve-order   — human approval gate; places simulated order when approval=True
GET  /ask             — NL-ops natural-language query (requires ANTHROPIC_API_KEY)

Session state
─────────────
/draft-order stores the MCPClient instance and explained cart in module-level
_state so /approve-order can use the same in-memory cart.  Single-restaurant
demo — one concurrent session is expected in Phase 5.
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import psycopg2
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ── App setup ──────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Restaurant Ops Copilot",
    description="AI-powered demand forecasting and procurement for Spice Junction",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve frontend/ as /static  (index.html served explicitly at /)
_FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
if _FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_FRONTEND_DIR)), name="static")

# ── DB helper ──────────────────────────────────────────────────────────────────

_DEFAULT_DB = "postgresql://copilot:copilot@localhost:5432/restaurant_ops"


def _db_url() -> str:
    return os.getenv("DATABASE_URL", _DEFAULT_DB)


def _conn():
    return psycopg2.connect(_db_url())


# ── Session state (single-user demo) ──────────────────────────────────────────

class _State:
    """Holds the in-flight draft cart between /draft-order and /approve-order."""
    client          = None   # MCPClient — holds the in-memory cart
    pipeline_result = None   # full dict from run_procurement_pipeline()
    explained_cart  = None   # dict from explain_cart()


_state = _State()


def _reset_state() -> None:
    """Used by tests to clear state between runs."""
    _state.client          = None
    _state.pipeline_result = None
    _state.explained_cart  = None


# ── Pydantic models ────────────────────────────────────────────────────────────

class DraftOrderRequest(BaseModel):
    forecast_date: Optional[str] = None   # ISO date string; defaults to tomorrow


class ApproveOrderRequest(BaseModel):
    approval: bool = False


# ── Routes ─────────────────────────────────────────────────────────────────────


@app.get("/", response_class=FileResponse)
async def dashboard() -> FileResponse:
    """Serve the single-page dashboard."""
    index = _FRONTEND_DIR / "index.html"
    if not index.exists():
        return JSONResponse({"error": "frontend/index.html not found"}, status_code=404)
    return FileResponse(str(index))


@app.get("/health")
async def health() -> JSONResponse:
    """
    Liveness + readiness check.

    Returns 200 {"status":"ok", "orders":<n>, "forecasts":<n>} when the DB
    is reachable and the orders table has been seeded.
    Returns 503 {"status":"error", "detail":"…"} otherwise.

    Consumed by Railway healthcheck and deploy_smoke_test.sh.
    """
    try:
        c = _conn()
        cur = c.cursor()
        cur.execute("SELECT COUNT(*) FROM orders")
        orders: int = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM forecasts")
        forecasts: int = cur.fetchone()[0]
        c.close()
        return JSONResponse({"status": "ok", "orders": orders, "forecasts": forecasts})
    except Exception as exc:
        return JSONResponse(
            {"status": "error", "detail": str(exc)},
            status_code=503,
        )


@app.get("/forecast")
async def get_forecast(forecast_date: Optional[str] = Query(None)) -> dict:
    """
    Return dish-level XGBoost predictions for the given date (default: tomorrow).
    Auto-generates the forecast if it is not yet in the DB.
    """
    fd: date = (
        date.fromisoformat(forecast_date)
        if forecast_date
        else date.today() + timedelta(days=1)
    )

    # Auto-generate if missing
    c = _conn()
    cur = c.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM forecasts WHERE forecast_date = %s AND model_version = 'xgb_v1'",
        (fd,),
    )
    if cur.fetchone()[0] == 0:
        c.close()
        from forecasting.run import run_forecast
        run_forecast(fd, models=["xgb"], database_url=_db_url())
        c = _conn()
        cur = c.cursor()

    cur.execute(
        """
        SELECT f.item_id, mi.name, mi.category, f.predicted_qty::float
        FROM   forecasts f
        JOIN   menu_items mi ON f.item_id = mi.id
        WHERE  f.forecast_date = %s AND f.model_version = 'xgb_v1'
        ORDER  BY f.predicted_qty DESC
        """,
        (fd,),
    )
    rows = cur.fetchall()
    c.close()

    return {
        "date": str(fd),
        "predictions": [
            {"item_id": r[0], "item_name": r[1], "category": r[2], "predicted_qty": round(r[3], 2)}
            for r in rows
        ],
    }


@app.get("/inventory")
async def get_inventory() -> dict:
    """
    Return current stock for all 21 raw materials.
    is_low = True when current_qty ≤ reorder_point.
    """
    c = _conn()
    cur = c.cursor()
    cur.execute(
        """
        SELECT i.raw_material,
               i.current_qty::float,
               i.unit,
               i.reorder_point::float,
               COALESCE(cat.product_name, i.raw_material) AS product_name
        FROM   inventory i
        LEFT JOIN raw_material_catalog cat ON cat.name = i.raw_material
        WHERE  i.restaurant_id = 1
        ORDER  BY i.raw_material
        """,
    )
    rows = cur.fetchall()
    c.close()

    return {
        "items": [
            {
                "raw_material":  r[0],
                "current_qty":   round(r[1], 2),
                "unit":          r[2],
                "reorder_point": round(r[3], 2),
                "product_name":  r[4],
                "is_low":        r[1] <= r[3],
            }
            for r in rows
        ],
    }


@app.post("/draft-order")
async def draft_order(req: DraftOrderRequest = Body(default_factory=DraftOrderRequest)) -> dict:
    """
    Run the full forecast → BOM explosion → shortfall → draft cart pipeline.
    Stores the MCPClient instance in session state so /approve-order can use it.
    """
    fd: date = (
        date.fromisoformat(req.forecast_date)
        if req.forecast_date
        else date.today() + timedelta(days=1)
    )
    db = _db_url()

    # Ensure forecast exists
    c = _conn()
    cur = c.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM forecasts WHERE forecast_date = %s AND model_version = 'xgb_v1'",
        (fd,),
    )
    needs_forecast = cur.fetchone()[0] == 0
    c.close()

    if needs_forecast:
        from forecasting.run import run_forecast
        run_forecast(fd, models=["xgb"], database_url=db)

    try:
        from agent.approval import explain_cart
        from mcp_client.client import get_client
        from procurement.cart import run_procurement_pipeline

        client = get_client(database_url=db)
        result = run_procurement_pipeline(
            fd, client=client, database_url=db, verbose=False
        )
        explained = explain_cart(
            result["cart"], result["shortfalls"], result["needs"], fd
        )

        _state.client          = client
        _state.pipeline_result = result
        _state.explained_cart  = explained

        cart = explained
        return {
            "status":        "ok",
            "forecast_date": str(fd),
            "cart":          cart,
            "message": (
                f"Draft ready: {cart['total_items']} products, "
                f"{cart['total_packs']} packs, ₹{cart['total_cost']:,.2f}"
            ),
        }

    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/approve-order")
async def approve_order(req: ApproveOrderRequest) -> dict:
    """
    Human approval gate.
    approval=False  → returns AWAITING_APPROVAL with cart (no order placed)
    approval=True   → places simulated COD order, clears session state
    """
    if _state.client is None or _state.explained_cart is None:
        raise HTTPException(
            status_code=400,
            detail="No active draft order. Call POST /draft-order first.",
        )

    from agent.approval import approve_and_place

    result = approve_and_place(
        _state.explained_cart, _state.client, approval=req.approval
    )

    if result["status"] == "ORDER_PLACED":
        _reset_state()

    return result


@app.get("/ask")
async def ask_question(q: str = Query(..., description="Natural-language ops question")) -> dict:
    """
    Answer a natural-language question about restaurant operations.
    Requires ANTHROPIC_API_KEY — returns a friendly message if not configured.
    """
    if not q.strip():
        raise HTTPException(status_code=400, detail="q cannot be empty")

    if not os.environ.get("ANTHROPIC_API_KEY"):
        return {
            "question": q,
            "answer":   (
                "NL-ops requires ANTHROPIC_API_KEY. "
                "Add it to your .env file and restart the server."
            ),
            "sql":      "",
            "raw_rows": [],
        }

    try:
        from agent.nl_ops import ask
        return ask(q, database_url=_db_url())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
