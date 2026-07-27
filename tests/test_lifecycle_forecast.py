"""
Restaurant-lifecycle-aware forecasting tests.

Spice Junction (restaurant_id=1) has 17+ months of order history →
'established' → own-history XGBoost model, unaffected by this feature.

A synthetic second restaurant (same cuisine, only 20 days of order history)
is inserted directly via SQL to confirm it correctly falls back to the
category-trend model instead of per-dish XGBoost.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta

import psycopg2
import psycopg2.extras
import pytest
from fastapi.testclient import TestClient

TOMORROW = date.today() + timedelta(days=1)

NEW_RESTAURANT_MENU = [
    ("Butter Chicken",   320.0, "main_course"),
    ("Dal Makhani",      220.0, "main_course"),
    ("Butter Naan",       45.0, "bread"),
    ("Chicken Biryani",  280.0, "biryani"),
    ("Masala Chai",       40.0, "beverage"),
]
NEW_RESTAURANT_HISTORY_DAYS = 20


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def new_restaurant(seeded_db) -> int:
    """
    Insert a second restaurant, same cuisine as Spice Junction, with only
    NEW_RESTAURANT_HISTORY_DAYS days of order history — too little for its
    own XGBoost model, but enough to be a real, queryable restaurant.
    """
    conn = psycopg2.connect(seeded_db)
    cur = conn.cursor()

    cur.execute(
        "INSERT INTO restaurants (name, locality, cuisine) VALUES (%s, %s, %s) RETURNING id",
        ("Curry Corner", "Koramangala", "North Indian"),
    )
    restaurant_id: int = cur.fetchone()[0]

    item_ids: list[int] = []
    for name, price, category in NEW_RESTAURANT_MENU:
        cur.execute(
            "INSERT INTO menu_items (restaurant_id, name, price, category) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (restaurant_id, name, price, category),
        )
        item_ids.append(cur.fetchone()[0])

    end   = date.today() - timedelta(days=1)
    start = end - timedelta(days=NEW_RESTAURANT_HISTORY_DAYS - 1)

    order_rows = []
    day = start
    while day <= end:
        noon = datetime(day.year, day.month, day.day, 12, 0, 0)
        for item_id in item_ids:
            order_rows.append((restaurant_id, item_id, 8, noon))
        day += timedelta(days=1)

    psycopg2.extras.execute_values(
        cur,
        "INSERT INTO orders (restaurant_id, item_id, qty, ordered_at) VALUES %s",
        order_rows,
    )
    conn.commit()
    conn.close()

    return restaurant_id


@pytest.fixture(scope="module")
def client(seeded_db, new_restaurant):
    os.environ["DATABASE_URL"] = seeded_db
    from api.main import app, _reset_state
    _reset_state()
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


# ── get_restaurant_data_maturity() ──────────────────────────────────────────────


def test_established_restaurant_maturity(seeded_db):
    """Spice Junction (17+ months of history) is 'established'."""
    from forecasting.xgb import get_restaurant_data_maturity

    assert get_restaurant_data_maturity(1, seeded_db) == "established"


def test_new_restaurant_maturity(seeded_db, new_restaurant):
    """A restaurant with only 20 days of history is 'new'."""
    from forecasting.xgb import get_restaurant_data_maturity

    assert get_restaurant_data_maturity(new_restaurant, seeded_db) == "new"


# ── run_forecast() model selection ──────────────────────────────────────────────


def test_established_restaurant_forecast_uses_xgb(seeded_db):
    """Spice Junction's forecast is untouched — still per-dish XGBoost."""
    from forecasting.run import run_forecast

    predictions = run_forecast(TOMORROW, models=["xgb"], database_url=seeded_db, restaurant_id=1)

    assert len(predictions) == 25
    assert all(p["model_version"] == "xgb_v1" for p in predictions)
    assert all(p["predicted_qty"] > 0 for p in predictions)


def test_new_restaurant_forecast_uses_category_trend(seeded_db, new_restaurant):
    """The new restaurant falls back to the category-trend model."""
    from forecasting.run import run_forecast

    predictions = run_forecast(
        TOMORROW, models=["xgb"], database_url=seeded_db, restaurant_id=new_restaurant
    )

    assert len(predictions) == len(NEW_RESTAURANT_MENU)
    assert all(p["model_version"] == "category_trend_v1" for p in predictions)
    assert all(p["predicted_qty"] > 0 for p in predictions), (
        "Category-trend fallback should produce positive predictions "
        "using Spice Junction (same cuisine, established) as the peer restaurant"
    )

    conn = psycopg2.connect(seeded_db)
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM forecasts WHERE restaurant_id = %s "
        "AND forecast_date = %s AND model_version = 'category_trend_v1'",
        (new_restaurant, TOMORROW),
    )
    count = cur.fetchone()[0]
    conn.close()
    assert count == len(NEW_RESTAURANT_MENU)


# ── GET /forecast/calendar ──────────────────────────────────────────────────────


def test_calendar_established_restaurant_own_history(client):
    r = client.get("/forecast/calendar?restaurant_id=1&days=2")
    assert r.status_code == 200
    data = r.json()

    assert data["mode"] == "own_history"
    assert data["message"] == "Based on your restaurant's history"
    assert data["data_days"] >= 90
    assert len(data["forecast"]) == 2
    assert len(data["forecast"][0]["predictions"]) == 25


def test_calendar_new_restaurant_category_trend(client, new_restaurant):
    r = client.get(f"/forecast/calendar?restaurant_id={new_restaurant}&days=2")
    assert r.status_code == 200
    data = r.json()

    assert data["mode"] == "category_trend"
    assert "category trends" in data["message"].lower()
    assert str(NEW_RESTAURANT_HISTORY_DAYS) in data["message"]
    assert data["data_days"] == NEW_RESTAURANT_HISTORY_DAYS
    assert len(data["forecast"]) == 2
    assert len(data["forecast"][0]["predictions"]) == len(NEW_RESTAURANT_MENU)
