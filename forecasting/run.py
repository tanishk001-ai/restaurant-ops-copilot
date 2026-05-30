"""
Produce dish-level demand forecasts and write them to the forecasts table.

Usage:
    python -m forecasting.run --date tomorrow
    python -m forecasting.run --date 2026-06-01
    python -m forecasting.run --date 2026-06-01 --model both
    python -m forecasting.run --date 2026-06-01 --database-url postgresql://...
"""

from __future__ import annotations

import argparse
import os
from datetime import date, timedelta

import pandas as pd

from data_gen.constants import BASE_DEMAND, MENU_ITEMS
from forecasting.data import (
    get_item_series,
    load_daily_demand,
    load_items,
    write_forecasts,
)
from forecasting.xgb import predict_xgb_single, train_xgb

DEFAULT_DATABASE_URL = "postgresql://copilot:copilot@localhost:5432/restaurant_ops"


def _resolve_date(date_str: str) -> date:
    if date_str.lower() == "tomorrow":
        return date.today() + timedelta(days=1)
    if date_str.lower() == "today":
        return date.today()
    return date.fromisoformat(date_str)


def _fmt_date(d: date) -> str:
    return d.strftime("%A, %d %b %Y")


def run_forecast(
    target_date: date,
    models: list[str],
    database_url: str | None = None,
) -> list[dict]:
    """
    Fit model(s) on all historical data, predict target_date for every dish,
    write results to the forecasts table, and return the predictions.
    """
    database_url = database_url or os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
    slug_by_name = {item["name"]: item["slug"] for item in MENU_ITEMS}

    print(f"\n{'─'*55}")
    print(f"  Restaurant Ops Copilot — Demand Forecast")
    print(f"  Date: {_fmt_date(target_date)}")
    print(f"  Models: {', '.join(models)}")
    print(f"{'─'*55}")

    print("  Loading order history …", end=" ", flush=True)
    demand = load_daily_demand(database_url)
    items  = load_items(database_url)
    history_days = (demand["date"].max() - demand["date"].min()).days + 1
    print(f"{len(demand):,} rows  ({history_days} days)")

    all_predictions: list[dict] = []

    # ── XGBoost ─────────────────────────────────────────────────────────────
    if "xgb" in models:
        print(f"\n  Fitting XGBoost models …")
        xgb_predictions: list[dict] = []

        for item_id, item_name in items.items():
            slug = slug_by_name.get(item_name, "")
            base = BASE_DEMAND.get(slug, 0)
            series = get_item_series(demand, item_id)

            model = train_xgb(series)   # train on ALL history
            qty = predict_xgb_single(model, series, target_date)

            xgb_predictions.append({
                "item_id":       item_id,
                "item_name":     item_name,
                "forecast_date": target_date,
                "predicted_qty": qty,
                "model_version": "xgb_v1",
                "base_demand":   base,
            })

        # Sort by predicted qty descending for display
        xgb_predictions.sort(key=lambda x: x["predicted_qty"], reverse=True)

        print(f"\n  {'Dish':<32} {'Predicted':>10}  {'Base':>6}")
        print(f"  {'─'*32} {'─'*10}  {'─'*6}")
        for p in xgb_predictions:
            print(f"  {p['item_name']:<32} {p['predicted_qty']:>10.1f}  {p['base_demand']:>6}")

        write_forecasts(xgb_predictions, database_url=database_url)
        all_predictions.extend(xgb_predictions)
        print(f"\n  ✓ {len(xgb_predictions)} XGBoost predictions written to forecasts table")

    # ── Prophet ─────────────────────────────────────────────────────────────
    if "prophet" in models:
        from forecasting.baseline import (
            PROPHET_AVAILABLE,
            predict_prophet_single,
            train_prophet,
        )

        if not PROPHET_AVAILABLE:
            print("  ! Prophet not available — skipping")
        else:
            print(f"\n  Fitting Prophet models (this may take a few minutes) …")
            prophet_predictions: list[dict] = []

            for i, (item_id, item_name) in enumerate(items.items(), 1):
                series = get_item_series(demand, item_id)
                model  = train_prophet(series)
                qty    = predict_prophet_single(model, target_date)
                prophet_predictions.append({
                    "item_id":       item_id,
                    "item_name":     item_name,
                    "forecast_date": target_date,
                    "predicted_qty": qty,
                    "model_version": "prophet_v1",
                })
                print(f"    [{i:02d}/{len(items)}] {item_name:<32}  {qty:.1f}")

            write_forecasts(prophet_predictions, database_url=database_url)
            all_predictions.extend(prophet_predictions)
            print(f"\n  ✓ {len(prophet_predictions)} Prophet predictions written")

    print(f"\n{'─'*55}\n")
    return all_predictions


def main() -> None:
    parser = argparse.ArgumentParser(description="Run demand forecast for a target date")
    parser.add_argument(
        "--date", required=True,
        help='Forecast date: "tomorrow", "today", or YYYY-MM-DD',
    )
    parser.add_argument(
        "--model", default="xgb",
        choices=["xgb", "prophet", "both"],
        help="Model(s) to run (default: xgb)",
    )
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args()

    target_date = _resolve_date(args.date)
    models = ["xgb", "prophet"] if args.model == "both" else [args.model]

    run_forecast(target_date, models=models, database_url=args.database_url)


if __name__ == "__main__":
    main()
