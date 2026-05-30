"""
Phase 2 acceptance tests — forecasting engine.

Tests:
  1. run.py --date tomorrow writes 25 rows to forecasts table
  2. All predicted quantities are positive
  3. XGBoost backtest MAPE < 25% on every high-volume dish
  4. XGBoost backtest MAPE < 25% on average across all dishes

Requires a running Postgres with seeded data (seeded_db fixture).
"""

from __future__ import annotations

import os
from datetime import date, timedelta

import psycopg2
import pytest

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://copilot:copilot@localhost:5432/restaurant_ops",
)

TOMORROW = date.today() + timedelta(days=1)
HIGH_VOLUME_MAPE_THRESHOLD = 25.0  # percent


# ── helpers ────────────────────────────────────────────────────────────────────

def _db_conn():
    return psycopg2.connect(DATABASE_URL)


# ── run.py tests ───────────────────────────────────────────────────────────────

def test_run_writes_forecasts_to_db(seeded_db):
    """run_forecast() writes exactly 25 rows (one per dish) for tomorrow."""
    from forecasting.run import run_forecast

    predictions = run_forecast(TOMORROW, models=["xgb"], database_url=seeded_db)

    assert len(predictions) == 25, f"Expected 25 predictions, got {len(predictions)}"

    conn = _db_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM forecasts WHERE forecast_date = %s AND model_version = 'xgb_v1'",
        (TOMORROW,),
    )
    count = cur.fetchone()[0]
    conn.close()
    assert count == 25, f"Expected 25 DB rows, found {count}"


def test_run_predictions_are_positive(seeded_db):
    """Every predicted quantity is strictly positive."""
    from forecasting.run import run_forecast

    predictions = run_forecast(TOMORROW, models=["xgb"], database_url=seeded_db)
    for p in predictions:
        assert p["predicted_qty"] > 0, (
            f"{p['item_name']}: predicted_qty = {p['predicted_qty']} (must be > 0)"
        )


def test_run_is_idempotent(seeded_db):
    """Running run_forecast twice for the same date doesn't duplicate rows."""
    from forecasting.run import run_forecast

    run_forecast(TOMORROW, models=["xgb"], database_url=seeded_db)
    run_forecast(TOMORROW, models=["xgb"], database_url=seeded_db)

    conn = _db_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM forecasts WHERE forecast_date = %s AND model_version = 'xgb_v1'",
        (TOMORROW,),
    )
    count = cur.fetchone()[0]
    conn.close()
    assert count == 25, f"Idempotency broken: found {count} rows after 2 runs"


def test_run_high_volume_predictions_sensible(seeded_db):
    """
    High-volume dishes (base demand >= 25) should have predicted_qty >= 10
    even on a quiet day — sanity check that the model isn't predicting zeros.
    """
    from data_gen.constants import BASE_DEMAND, MENU_ITEMS
    from forecasting.run import run_forecast

    predictions = run_forecast(TOMORROW, models=["xgb"], database_url=seeded_db)
    slug_by_name = {item["name"]: item["slug"] for item in MENU_ITEMS}

    for p in predictions:
        slug = slug_by_name.get(p["item_name"], "")
        if BASE_DEMAND.get(slug, 0) >= 25:
            assert p["predicted_qty"] >= 10, (
                f"High-volume dish {p['item_name']!r} has suspiciously low "
                f"prediction: {p['predicted_qty']:.1f}"
            )


# ── backtest / evaluate.py tests ───────────────────────────────────────────────

@pytest.fixture(scope="module")
def backtest_results(seeded_db):
    """Run the backtest once per module — it takes ~5 s."""
    from forecasting.evaluate import DEFAULT_TEST_END, DEFAULT_TRAIN_CUTOFF, run_backtest

    return run_backtest(
        models=["xgb"],
        train_cutoff=DEFAULT_TRAIN_CUTOFF,
        test_end=DEFAULT_TEST_END,
        database_url=seeded_db,
        verbose=False,
    )


def test_backtest_returns_all_dishes(backtest_results):
    """Backtest covers all 25 dishes."""
    assert len(backtest_results) == 25


def test_backtest_high_volume_mape_under_threshold(backtest_results):
    """
    Every individual high-volume dish (base demand >= 25) achieves
    XGBoost MAPE < 25 %.
    """
    from data_gen.constants import BASE_DEMAND

    failures = []
    for row in backtest_results:
        if row["high_volume"]:
            mape = row["xgb_mape"]
            if mape >= HIGH_VOLUME_MAPE_THRESHOLD:
                failures.append(f"  {row['name']}: MAPE = {mape:.1f}%")

    assert not failures, (
        f"High-volume dishes exceeded MAPE threshold:\n" + "\n".join(failures)
    )


def test_backtest_avg_mape_under_threshold(backtest_results):
    """Average XGBoost MAPE across all 25 dishes is < 25 %."""
    import numpy as np

    mapes = [r["xgb_mape"] for r in backtest_results]
    avg = float(np.mean(mapes))
    assert avg < HIGH_VOLUME_MAPE_THRESHOLD, (
        f"Average MAPE across all dishes = {avg:.1f}% (threshold {HIGH_VOLUME_MAPE_THRESHOLD}%)"
    )


def test_backtest_xgb_metrics_present(backtest_results):
    """Each result row contains xgb_mae and xgb_mape."""
    for row in backtest_results:
        assert "xgb_mae"  in row and row["xgb_mae"]  >= 0
        assert "xgb_mape" in row and row["xgb_mape"] >= 0
