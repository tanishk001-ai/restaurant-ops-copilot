"""
FastAPI backend — Restaurant Ops Copilot.

Endpoints
─────────
GET  /                — dashboard HTML
GET  /health          — DB connectivity check
GET  /forecast        — tomorrow's dish-level XGBoost predictions
GET  /forecast/calendar — N-day lifecycle-aware forecast (own_history / category_trend)
GET  /inventory       — current stock with is_low flag per material
POST /draft-order     — forecast → BOM → shortfall → explained draft cart
POST /approve-order   — human approval gate; places simulated order when approval=True
GET  /ask             — NL-ops natural-language query (requires GEMINI_API_KEY)
GET  /spike-check     — mid-day actual-vs-forecast pace check

Session state
─────────────
/draft-order stores the MCPClient instance and explained cart in module-level
_state so /approve-order can use the same in-memory cart.  Single-restaurant
demo — one concurrent session is expected in Phase 5.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta
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


@app.get("/forecast/calendar")
async def get_forecast_calendar(
    restaurant_id: int = Query(1),
    days: Optional[int] = Query(None, description="Horizon length; defaults to FORECAST_HORIZON_DAYS"),
) -> dict:
    """
    Multi-day demand forecast, lifecycle-aware.

    Uses the restaurant's own per-dish XGBoost history when it has >= 90 days
    of order history ("own_history" mode); otherwise falls back to a
    category-trend average across established restaurants sharing the same
    cuisine ("category_trend" mode). `mode` and `message` are included so the
    dashboard can badge which one produced the forecast.
    """
    from data_gen.constants import FESTIVAL_DATES
    from forecasting.xgb import MATURITY_THRESHOLD_DAYS, get_restaurant_data_maturity

    horizon = days or int(os.getenv("FORECAST_HORIZON_DAYS", "7"))
    db = _db_url()

    maturity = get_restaurant_data_maturity(restaurant_id, database_url=db)
    model_version = "xgb_v1" if maturity == "established" else "category_trend_v1"

    c = _conn()
    cur = c.cursor()
    cur.execute(
        "SELECT MIN(ordered_at), MAX(ordered_at) FROM orders WHERE restaurant_id = %s",
        (restaurant_id,),
    )
    min_ts, max_ts = cur.fetchone()
    c.close()
    data_days = (max_ts.date() - min_ts.date()).days + 1 if min_ts else 0

    if maturity == "established":
        message = "Based on your restaurant's history"
    else:
        message = f"Using category trends — {data_days} day{'s' if data_days != 1 else ''} of data collected"

    start = date.today() + timedelta(days=1)
    end   = start + timedelta(days=horizon - 1)

    # Batch-check the whole range in one query, and if anything is missing,
    # backfill it in one shot — see run_forecast_range() for why this must
    # not be a per-day loop (25 dishes x 30 days of fresh training = ~3 min).
    c = _conn()
    cur = c.cursor()
    cur.execute(
        "SELECT COUNT(DISTINCT forecast_date) FROM forecasts WHERE restaurant_id = %s "
        "AND model_version = %s AND forecast_date BETWEEN %s AND %s",
        (restaurant_id, model_version, start, end),
    )
    covered_days = cur.fetchone()[0]
    c.close()

    if covered_days < horizon:
        from forecasting.run import run_forecast_range
        run_forecast_range(start, end, database_url=db, restaurant_id=restaurant_id)

    calendar: list[dict] = []

    for offset in range(horizon):
        fd = start + timedelta(days=offset)

        c = _conn()
        cur = c.cursor()
        cur.execute(
            """
            SELECT f.item_id, mi.name, mi.category, f.predicted_qty::float
            FROM   forecasts f
            JOIN   menu_items mi ON f.item_id = mi.id
            WHERE  f.restaurant_id = %s AND f.forecast_date = %s AND f.model_version = %s
            ORDER  BY f.predicted_qty DESC
            """,
            (restaurant_id, fd, model_version),
        )
        rows = cur.fetchall()
        c.close()

        festival = FESTIVAL_DATES.get(fd)
        predictions = [
            {"item_id": r[0], "item_name": r[1], "category": r[2], "predicted_qty": round(r[3], 2)}
            for r in rows
        ]

        calendar.append({
            "date":                str(fd),
            "total_predicted_qty": round(sum(p["predicted_qty"] for p in predictions), 2),
            "is_weekend":          fd.weekday() >= 5,
            "is_festival":         festival is not None,
            "festival_name":       festival[0] if festival else None,
            "predictions":         predictions,
        })

    return {
        "restaurant_id": restaurant_id,
        "start_date":    str(start),
        "days":          horizon,
        "mode":          "own_history" if maturity == "established" else "category_trend",
        "data_days":     data_days,
        "maturity_threshold_days": MATURITY_THRESHOLD_DAYS,
        "message":       message,
        "forecast":      calendar,
    }


@app.get("/inventory")
async def get_inventory() -> dict:
    """
    Return current stock for all 21 raw materials.
    is_low = True when current_qty ≤ reorder_point.
    days_until_stockout = current_qty ÷ (avg daily consumption, derived from
    order history × BOM) — None when there's no consumption history for a
    material (e.g. never used in any ordered dish).
    """
    from procurement.bom import explode_to_ingredients, load_avg_daily_demand

    db = _db_url()
    avg_daily_dish_qty = load_avg_daily_demand(restaurant_id=1, database_url=db)
    avg_daily_consumption = explode_to_ingredients(avg_daily_dish_qty, restaurant_id=1, database_url=db)

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

    items = []
    for r in rows:
        raw_material, current_qty, unit, reorder_point, product_name = r
        rate = avg_daily_consumption.get(raw_material, 0.0)
        days_until_stockout = round(current_qty / rate, 1) if rate > 0 else None
        items.append({
            "raw_material":         raw_material,
            "current_qty":          round(current_qty, 2),
            "unit":                 unit,
            "reorder_point":        round(reorder_point, 2),
            "product_name":         product_name,
            "is_low":               current_qty <= reorder_point,
            "days_until_stockout":  days_until_stockout,
        })

    return {"items": items}


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
        from procurement.cart import estimate_savings, run_procurement_pipeline

        client = get_client(database_url=db)
        result = run_procurement_pipeline(
            fd, client=client, database_url=db, verbose=False
        )
        explained = explain_cart(
            result["cart"], result["shortfalls"], result["needs"], fd
        )
        savings = estimate_savings(result["cart"], result["forecast"], database_url=db)

        _state.client          = client
        _state.pipeline_result = result
        _state.explained_cart  = explained

        cart = explained
        return {
            "status":        "ok",
            "forecast_date": str(fd),
            "cart":          cart,
            "savings":       savings,
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


@app.get("/spike-check")
async def spike_check(restaurant_id: int = Query(1)) -> dict:
    """
    Compare today's actual order pace so far against the pace implied by
    today's forecast (predicted_qty × fraction of the day elapsed).

    ratio = total actual so far / total expected so far, aggregated across
    dishes. is_spiking = ratio >= SPIKE_THRESHOLD (1.3x).  affected_dishes
    lists individual dishes whose own ratio also clears the threshold (with
    a floor on expected_qty so a sliver of forecast right after midnight
    can't produce a noisy, meaningless ratio).
    """
    SPIKE_THRESHOLD = 1.3
    MIN_EXPECTED    = 1.0   # units — floor to avoid noise early in the day

    db = _db_url()

    c = _conn()
    cur = c.cursor()
    cur.execute("SELECT CURRENT_DATE, NOW()")
    today, now = cur.fetchone()
    c.close()
    fraction_elapsed = max(
        (now.hour * 60 + now.minute + now.second / 60) / 1440, 0.001
    )

    c = _conn()
    cur = c.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM forecasts WHERE restaurant_id = %s "
        "AND forecast_date = %s AND model_version = 'xgb_v1'",
        (restaurant_id, today),
    )
    missing = cur.fetchone()[0] == 0
    c.close()

    if missing:
        from forecasting.run import run_forecast
        run_forecast(today, models=["xgb"], database_url=db, restaurant_id=restaurant_id)

    c = _conn()
    cur = c.cursor()
    cur.execute(
        """
        SELECT f.item_id, mi.name, f.predicted_qty::float
        FROM   forecasts f
        JOIN   menu_items mi ON f.item_id = mi.id
        WHERE  f.restaurant_id = %s AND f.forecast_date = %s AND f.model_version = 'xgb_v1'
        """,
        (restaurant_id, today),
    )
    forecast_rows = cur.fetchall()

    cur.execute(
        "SELECT item_id, SUM(qty)::float FROM orders "
        "WHERE restaurant_id = %s AND ordered_at >= CURRENT_DATE AND ordered_at <= NOW() "
        "GROUP BY item_id",
        (restaurant_id,),
    )
    actual_by_item = {r[0]: r[1] for r in cur.fetchall()}
    c.close()

    total_actual   = 0.0
    total_expected = 0.0
    affected: list[dict] = []

    for item_id, item_name, predicted_qty in forecast_rows:
        expected_so_far = max(predicted_qty * fraction_elapsed, 0.0)
        actual_so_far   = actual_by_item.get(item_id, 0.0)

        total_actual   += actual_so_far
        total_expected += expected_so_far

        if expected_so_far >= MIN_EXPECTED:
            dish_ratio = actual_so_far / expected_so_far
            if dish_ratio >= SPIKE_THRESHOLD:
                affected.append({
                    "item_name": item_name,
                    "actual":    round(actual_so_far, 1),
                    "expected":  round(expected_so_far, 1),
                    "ratio":     round(dish_ratio, 2),
                })

    affected.sort(key=lambda d: d["ratio"], reverse=True)

    ratio      = round(total_actual / total_expected, 2) if total_expected > 0 else 0.0
    is_spiking = ratio >= SPIKE_THRESHOLD

    if is_spiking:
        top = ", ".join(d["item_name"] for d in affected[:3])
        message = (
            f"Demand is running {ratio}x above forecast pace today"
            + (f" — driven by {top}" if top else "") + "."
        )
    else:
        message = f"Demand tracking normal — {ratio}x expected forecast pace."

    return {
        "is_spiking":              is_spiking,
        "ratio":                   ratio,
        "fraction_of_day_elapsed": round(fraction_elapsed, 3),
        "affected_dishes":         affected[:5],
        "message":                 message,
    }


@app.get("/ask")
async def ask_question(q: str = Query(..., description="Natural-language ops question")) -> dict:
    """
    Answer a natural-language question about restaurant operations.
    Requires GEMINI_API_KEY — returns a friendly message if not configured.
    """
    if not q.strip():
        raise HTTPException(status_code=400, detail="q cannot be empty")

    if not os.environ.get("GEMINI_API_KEY"):
        return {
            "question": q,
            "answer":   (
                "NL-ops requires GEMINI_API_KEY. "
                "Add it to your .env file and restart the server."
            ),
            "sql":      "",
            "raw_rows": [],
        }

    try:
        from agent.nl_ops import ask
        return ask(q, database_url=_db_url())
    except Exception as exc:
        from google.genai.errors import APIError

        # Most common questions (revenue, stock levels, top dishes, forecast)
        # are answered by agent.nl_ops's fast path and never reach here at
        # all — this only runs for novel questions that fall through to the
        # LLM. Degrade gracefully for the two failure modes that are the
        # LLM call's own fault, not the user's; anything else is a real bug.
        if isinstance(exc, APIError) and exc.code == 429:
            return {
                "question": q,
                "answer": (
                    "The AI service's free-tier quota is exhausted for today. "
                    "Try one of the suggested questions above — those are "
                    "answered directly without the AI service and always work."
                ),
                "sql":      "",
                "raw_rows": [],
            }

        # Walk the cause chain — network blips (DNS failures, connection
        # resets, timeouts) surface as an OSError somewhere underneath the
        # SDK's own exception type.
        root = exc
        while root.__cause__ is not None:
            root = root.__cause__
        if isinstance(root, OSError):
            return {
                "question": q,
                "answer": (
                    "Couldn't reach the AI service just now (temporary network issue). "
                    "Please try again in a moment."
                ),
                "sql":      "",
                "raw_rows": [],
            }
        raise HTTPException(status_code=500, detail=str(exc))
