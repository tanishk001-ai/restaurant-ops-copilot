"""
Backtest evaluation — train/test split, MAE and MAPE per dish for both models.

Default split:
  Train : 2025-01-01 → 2026-03-31  (~15 months)
  Test  : 2026-04-01 → 2026-05-28  (~58 days)

Usage:
    python -m forecasting.evaluate                         # XGBoost only (fast)
    python -m forecasting.evaluate --models xgb,prophet   # both (slow ~5 min)
    python -m forecasting.evaluate --train-cutoff 2026-03-01
"""

from __future__ import annotations

import argparse
import os
from datetime import date

import numpy as np
import pandas as pd

from data_gen.constants import BASE_DEMAND, MENU_ITEMS
from forecasting.data import get_item_series, load_daily_demand, load_items
from forecasting.xgb import predict_xgb_range, train_xgb

# Dishes considered "high-volume" for the acceptance criterion
_HIGH_VOLUME_THRESHOLD = 25   # BASE_DEMAND >= this
_HIGH_VOLUME_SLUGS = {
    item["slug"] for item in MENU_ITEMS if BASE_DEMAND.get(item["slug"], 0) >= _HIGH_VOLUME_THRESHOLD
}

DEFAULT_TRAIN_CUTOFF = date(2026, 4, 1)
DEFAULT_TEST_END     = date(2026, 5, 28)


# ─── Metrics ──────────────────────────────────────────────────────────────────


def _mae(actual: np.ndarray, predicted: np.ndarray) -> float:
    return float(np.mean(np.abs(actual - predicted)))


def _mape(actual: np.ndarray, predicted: np.ndarray) -> float:
    mask = actual > 0
    if mask.sum() == 0:
        return float("nan")
    return float(np.mean(np.abs((actual[mask] - predicted[mask]) / actual[mask])) * 100)


# ─── Per-dish evaluation ───────────────────────────────────────────────────────


def _eval_xgb(series: pd.Series, train_cutoff: date, test_end: date) -> dict:
    model = train_xgb(series, train_cutoff=train_cutoff)
    preds = predict_xgb_range(model, series, train_cutoff, test_end)
    actual = series.reindex(preds.index).fillna(0).values
    return {
        "mae":  _mae(actual, preds.values),
        "mape": _mape(actual, preds.values),
        "n_test_days": len(preds),
    }


def _eval_prophet(series: pd.Series, train_cutoff: date, test_end: date) -> dict | None:
    from forecasting.baseline import PROPHET_AVAILABLE, predict_prophet_range, train_prophet
    if not PROPHET_AVAILABLE:
        return None
    train_series = series[series.index < pd.Timestamp(train_cutoff)]
    model = train_prophet(train_series)
    if model is None:
        return None
    preds = predict_prophet_range(model, train_cutoff, test_end)
    actual = series.reindex(preds.index).fillna(0).values
    return {
        "mae":  _mae(actual, preds.values),
        "mape": _mape(actual, preds.values),
        "n_test_days": len(preds),
    }


# ─── Report ───────────────────────────────────────────────────────────────────


def _volume_label(item_name: str, slug_map: dict[int, str]) -> str:
    """Map item_name back to its BASE_DEMAND bucket."""
    for slug, bd in BASE_DEMAND.items():
        # match by checking if the slug prefix is in the item_name (loose match)
        if bd >= _HIGH_VOLUME_THRESHOLD and slug in slug_map.values():
            pass
    return ""


def _print_table(rows: list[dict], models: list[str]) -> None:
    w = [34, 5, 9, 10]
    has_p = "prophet" in models

    hdr = f"{'Dish':<{w[0]}} {'Vol':>{w[1]}}  {'XGB MAE':>{w[2]}} {'XGB MAPE':>{w[3]}}"
    if has_p:
        hdr += f"  {'PRO MAE':>9} {'PRO MAPE':>10}"
    sep = "─" * len(hdr)

    print(sep)
    print(hdr)
    print(sep)

    for r in rows:
        vol = "HIGH" if r["high_volume"] else "low"
        line = (
            f"{r['name']:<{w[0]}} "
            f"{vol:>{w[1]}}  "
            f"{r['xgb_mae']:>{w[2]}.1f} "
            f"{r['xgb_mape']:>{w[3]}.1f}%"
        )
        if has_p:
            if r.get("pro_mae") is not None:
                line += f"  {r['pro_mae']:>9.1f} {r['pro_mape']:>9.1f}%"
            else:
                line += f"  {'n/a':>9} {'n/a':>10}"
        print(line)

    print(sep)


def _print_summary(rows: list[dict], models: list[str]) -> None:
    hv = [r for r in rows if r["high_volume"]]
    xgb_mapes = [r["xgb_mape"] for r in hv if not np.isnan(r["xgb_mape"])]

    print(f"\nHigh-volume dishes ({len(hv)}): XGB MAPE = {np.mean(xgb_mapes):.1f}% "
          f"[min {min(xgb_mapes):.1f}%  max {max(xgb_mapes):.1f}%]")

    passed = all(m < 25.0 for m in xgb_mapes)
    status = "PASS" if passed else "FAIL"
    print(f"Acceptance criterion  MAPE < 25 %  →  {status}")

    if "prophet" in models:
        pro_mapes = [r["pro_mape"] for r in hv
                     if r.get("pro_mape") is not None and not np.isnan(r["pro_mape"])]
        if pro_mapes:
            print(f"High-volume dishes ({len(hv)}): PRO MAPE = {np.mean(pro_mapes):.1f}% "
                  f"[min {min(pro_mapes):.1f}%  max {max(pro_mapes):.1f}%]")


# ─── Main ─────────────────────────────────────────────────────────────────────


def run_backtest(
    models: list[str] = ("xgb",),
    train_cutoff: date = DEFAULT_TRAIN_CUTOFF,
    test_end: date = DEFAULT_TEST_END,
    database_url: str | None = None,
    verbose: bool = True,
) -> list[dict]:
    """
    Run backtest for each dish. Returns list of per-dish metric dicts.
    """
    demand = load_daily_demand(database_url)
    items  = load_items(database_url)

    # Build slug lookup: item_id → slug
    slug_by_name = {item["name"]: item["slug"] for item in MENU_ITEMS}

    if verbose:
        print(f"\n=== Forecasting Backtest ===")
        print(f"Train : {demand['date'].min().date()} → {(pd.Timestamp(train_cutoff) - pd.Timedelta(days=1)).date()}")
        print(f"Test  : {train_cutoff} → {test_end}  ({(date.toordinal(test_end) - date.toordinal(train_cutoff) + 1)} days)")
        print(f"Models: {', '.join(models)}\n")

    results = []
    total = len(items)
    for i, (item_id, item_name) in enumerate(items.items(), 1):
        slug = slug_by_name.get(item_name, "")
        is_hv = BASE_DEMAND.get(slug, 0) >= _HIGH_VOLUME_THRESHOLD

        if verbose:
            tag = "HIGH" if is_hv else "low "
            print(f"  [{i:02d}/{total}] {tag}  {item_name} …", end=" ", flush=True)

        series = get_item_series(demand, item_id)

        row: dict = {
            "item_id":     item_id,
            "name":        item_name,
            "slug":        slug,
            "high_volume": is_hv,
        }

        if "xgb" in models:
            m = _eval_xgb(series, train_cutoff, test_end)
            row["xgb_mae"]  = m["mae"]
            row["xgb_mape"] = m["mape"]

        if "prophet" in models:
            pm = _eval_prophet(series, train_cutoff, test_end)
            if pm:
                row["pro_mae"]  = pm["mae"]
                row["pro_mape"] = pm["mape"]
            else:
                row["pro_mae"]  = None
                row["pro_mape"] = None

        results.append(row)

        if verbose:
            parts = [f"XGB {row.get('xgb_mape', float('nan')):.1f}%"]
            if "prophet" in models and row.get("pro_mape") is not None:
                parts.append(f"PRO {row['pro_mape']:.1f}%")
            print("  ".join(parts))

    if verbose:
        print()
        _print_table(results, list(models))
        _print_summary(results, list(models))

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backtest forecasting models")
    parser.add_argument("--models", default="xgb",
                        help="Comma-separated list: xgb,prophet  (default: xgb)")
    parser.add_argument("--train-cutoff", default=str(DEFAULT_TRAIN_CUTOFF),
                        help="Train/test split date (default: 2026-04-01)")
    parser.add_argument("--test-end", default=str(DEFAULT_TEST_END),
                        help="Last test date (default: 2026-05-28)")
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args()

    run_backtest(
        models=[m.strip() for m in args.models.split(",")],
        train_cutoff=date.fromisoformat(args.train_cutoff),
        test_end=date.fromisoformat(args.test_end),
        database_url=args.database_url,
    )
