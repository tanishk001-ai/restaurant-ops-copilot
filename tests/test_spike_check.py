"""
Mid-day demand spike check tests.

GET /spike-check compares today's actual order pace (orders table, up to
NOW()) against the pace implied by today's forecast (predicted_qty x
fraction of the day elapsed). The seed data records one row per dish per
day at a fixed noon timestamp — not a realistic intra-day trickle — so
these tests clear today's orders first for a deterministic baseline, then
manually seed a large burst of "extra" orders to simulate a real spike.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta

import psycopg2
import pytest
from fastapi.testclient import TestClient

TODAY = date.today()


@pytest.fixture(scope="module")
def client(seeded_db):
    os.environ["DATABASE_URL"] = seeded_db
    from api.main import app, _reset_state
    _reset_state()
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


def _delete_todays_orders(seeded_db: str) -> None:
    conn = psycopg2.connect(seeded_db)
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM orders WHERE restaurant_id = 1 AND ordered_at >= CURRENT_DATE"
    )
    conn.commit()
    conn.close()


@pytest.fixture(autouse=True, scope="module")
def clear_todays_orders(seeded_db):
    """
    The seed script writes one noon-timestamped row per dish for every day
    up to and including today. That makes the spike-check baseline
    unpredictable depending on what time of day the suite runs — clear it
    so both tests below start from a known, empty "today".

    Teardown also deletes anything dated "today" — this module runs
    directly against the shared dev DATABASE_URL (not an isolated test DB;
    see conftest.seeded_db), and test_seeded_spike_flips_is_spiking_true
    deliberately inserts a 5000-unit burst there. Once real time moves
    past "today", an unclean burst stops looking like test fixture data
    and becomes a permanent, silent outlier in that dish's order history —
    it'll train straight into the forecast model and produce wildly wrong
    predictions with no obvious cause. Clean up regardless of pass/fail.
    """
    _delete_todays_orders(seeded_db)
    yield
    _delete_todays_orders(seeded_db)


def test_no_spike_with_no_orders_today(client):
    """With zero orders recorded today, the pace ratio must be well under the spike threshold."""
    r = client.get("/spike-check")
    assert r.status_code == 200
    data = r.json()

    assert data["is_spiking"] is False
    assert data["ratio"] < 1.3
    assert data["affected_dishes"] == []


def test_seeded_spike_flips_is_spiking_true(client, seeded_db):
    """
    Manually seed a large burst of extra orders for today for one dish —
    far more than any plausible forecast pace, regardless of what fraction
    of the day has elapsed when this test runs — and confirm the endpoint
    correctly flips is_spiking to True and names the dish.
    """
    conn = psycopg2.connect(seeded_db)
    cur = conn.cursor()

    cur.execute(
        "SELECT id, name FROM menu_items WHERE restaurant_id = 1 AND name = 'Chicken Biryani'"
    )
    item_id, item_name = cur.fetchone()

    # is_spiking is driven by the AGGREGATE ratio across all ~25 dishes, whose
    # combined full-day forecast runs roughly 700-1250 units — so the burst
    # has to beat that whole-restaurant total, not just one dish's own
    # forecast, to guarantee a spike regardless of what fraction of the day
    # has elapsed (worst case: this runs at 23:59, i.e. ~100% of the day
    # already "expected") when the suite happens to run.
    cur.execute(
        "INSERT INTO orders (restaurant_id, item_id, qty, ordered_at) VALUES (%s, %s, %s, NOW())",
        (1, item_id, 5000),
    )
    conn.commit()
    conn.close()

    r = client.get("/spike-check")
    assert r.status_code == 200
    data = r.json()

    assert data["is_spiking"] is True
    assert data["ratio"] >= 1.3
    affected_names = [d["item_name"] for d in data["affected_dishes"]]
    assert item_name in affected_names, (
        f"Expected {item_name!r} in affected_dishes, got {affected_names}"
    )
