"""
XGBoost forecasting model with lag features — one model per dish.

Features used:
  Calendar   : day_of_week, month, day_of_year, is_weekend, is_festival
  Lags       : lag_7  (same weekday last week)
               lag_14 (same weekday two weeks ago)
               lag_28 (four-week anchor — captures monthly seasonality)
  Rolling    : rolling_7_mean   (short-term momentum)
               rolling_28_mean  (longer-term baseline)

All lag/rolling features are computed with a shift so they never include the
target day itself (no look-ahead leakage at training time).

Restaurant lifecycle:
  New restaurants (< 90 days of order history) don't have enough data for
  per-dish lag/rolling features to mean anything. get_restaurant_data_maturity()
  detects this, and predict_category_trend() provides a simpler fallback —
  a day-of-week + festival multiplier averaged across established restaurants
  in the same cuisine category — until the restaurant crosses the threshold.
"""

from __future__ import annotations

import os
from datetime import date, timedelta

import numpy as np
import pandas as pd
import psycopg2
from xgboost import XGBRegressor

from data_gen.constants import FESTIVAL_DATES

FEATURE_COLS: list[str] = [
    "day_of_week",
    "month",
    "day_of_year",
    "is_weekend",
    "is_festival",
    "lag_7",
    "lag_14",
    "lag_28",
    "rolling_7_mean",
    "rolling_28_mean",
]

_FESTIVAL_SET: set[date] = set(FESTIVAL_DATES.keys())

DEFAULT_DATABASE_URL = "postgresql://copilot:copilot@localhost:5432/restaurant_ops"
MATURITY_THRESHOLD_DAYS = 90


def _get_conn(database_url: str | None = None):
    return psycopg2.connect(database_url or os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL))


# ─── Feature engineering ───────────────────────────────────────────────────────


def _make_feature_df(series: pd.Series) -> pd.DataFrame:
    """
    Build a full feature DataFrame from a daily quantity series.
    Rows with NaN lag values (first 28 days) are dropped.
    """
    idx = pd.DatetimeIndex(series.index)
    df = pd.DataFrame({"qty": series.values}, index=idx)

    df["day_of_week"]  = idx.dayofweek
    df["month"]        = idx.month
    df["day_of_year"]  = idx.day_of_year
    df["is_weekend"]   = (idx.dayofweek >= 5).astype(int)
    df["is_festival"]  = [int(d.date() in _FESTIVAL_SET) for d in idx]

    # Lag features — shift(N) ensures we only see past values
    df["lag_7"]          = df["qty"].shift(7)
    df["lag_14"]         = df["qty"].shift(14)
    df["lag_28"]         = df["qty"].shift(28)
    df["rolling_7_mean"] = df["qty"].shift(1).rolling(7).mean()
    df["rolling_28_mean"]= df["qty"].shift(1).rolling(28).mean()

    return df.dropna()


# ─── Training ──────────────────────────────────────────────────────────────────


def train_xgb(series: pd.Series, train_cutoff: date | None = None) -> XGBRegressor:
    """
    Fit an XGBRegressor on the series.
    If train_cutoff is given, only data strictly before that date is used.
    """
    df = _make_feature_df(series)

    if train_cutoff is not None:
        df = df[df.index < pd.Timestamp(train_cutoff)]

    if df.empty:
        raise ValueError("No training data after feature engineering + cutoff filter")

    model = XGBRegressor(
        n_estimators=300,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        min_child_weight=5,     # regularises leaf splits for count data
        objective="count:poisson",
        random_state=42,
        verbosity=0,
    )
    model.fit(df[FEATURE_COLS], df["qty"])
    return model


# ─── Prediction ────────────────────────────────────────────────────────────────


def predict_xgb_single(
    model: XGBRegressor, series: pd.Series, target_date: date
) -> float:
    """
    Predict demand for a single future date.
    All lag features are drawn from actual historical values in `series`.
    """
    ts = pd.Timestamp(target_date)

    def _qty_at(d: date) -> float:
        t = pd.Timestamp(d)
        return float(series[t]) if t in series.index else 0.0

    lag_7  = _qty_at(target_date - timedelta(days=7))
    lag_14 = _qty_at(target_date - timedelta(days=14))
    lag_28 = _qty_at(target_date - timedelta(days=28))
    roll_7 = np.mean([_qty_at(target_date - timedelta(days=i)) for i in range(1, 8)])
    roll_28= np.mean([_qty_at(target_date - timedelta(days=i)) for i in range(1, 29)])

    row = pd.DataFrame([{
        "day_of_week":    ts.dayofweek,
        "month":          ts.month,
        "day_of_year":    ts.day_of_year,
        "is_weekend":     int(ts.dayofweek >= 5),
        "is_festival":    int(target_date in _FESTIVAL_SET),
        "lag_7":          lag_7,
        "lag_14":         lag_14,
        "lag_28":         lag_28,
        "rolling_7_mean": roll_7,
        "rolling_28_mean":roll_28,
    }])

    pred = float(model.predict(row[FEATURE_COLS])[0])
    return max(0.0, pred)


def predict_xgb_range(
    model: XGBRegressor,
    series: pd.Series,
    start_date: date,
    end_date: date,
) -> pd.Series:
    """
    Predict for every day in [start_date, end_date].
    Uses the full series (training + test actuals) to compute lag features —
    valid because in production we always have yesterday's actual sales.
    """
    df = _make_feature_df(series)

    mask = (df.index >= pd.Timestamp(start_date)) & (df.index <= pd.Timestamp(end_date))
    test_df = df[mask]

    if test_df.empty:
        return pd.Series(dtype=float)

    preds = np.maximum(0, model.predict(test_df[FEATURE_COLS]))
    return pd.Series(preds.astype(float), index=test_df.index)


# ─── Restaurant lifecycle / category-trend fallback ────────────────────────────


def get_restaurant_data_maturity(
    restaurant_id: int,
    database_url: str | None = None,
    threshold_days: int = MATURITY_THRESHOLD_DAYS,
) -> str:
    """
    Return 'new' if restaurant_id has fewer than threshold_days of order
    history, else 'established'. Used to decide whether a restaurant has
    enough of its own history for per-dish XGBoost lag features to be
    meaningful, or whether it should fall back to the category-trend model.
    """
    conn = _get_conn(database_url)
    cur = conn.cursor()
    cur.execute(
        "SELECT MIN(ordered_at), MAX(ordered_at) FROM orders WHERE restaurant_id = %s",
        (restaurant_id,),
    )
    min_ts, max_ts = cur.fetchone()
    conn.close()

    if min_ts is None:
        return "new"

    history_days = (max_ts.date() - min_ts.date()).days + 1
    return "established" if history_days >= threshold_days else "new"


def predict_category_trend(
    restaurant_id: int,
    target_date: date,
    database_url: str | None = None,
    threshold_days: int = MATURITY_THRESHOLD_DAYS,
) -> dict[int, float]:
    """
    Fallback forecast for restaurants without enough history of their own.

    Averages a day-of-week demand curve (qty per active menu item per day)
    across "established" restaurants sharing the same cuisine, applies the
    same festival multiplier used elsewhere, and scales the result by this
    restaurant's own menu — i.e. every one of its active items gets the same
    per-item prediction for target_date.
    """
    conn = _get_conn(database_url)
    cur = conn.cursor()

    cur.execute("SELECT cuisine FROM restaurants WHERE id = %s", (restaurant_id,))
    row = cur.fetchone()
    if row is None:
        conn.close()
        raise ValueError(f"Unknown restaurant_id={restaurant_id}")
    cuisine = row[0]

    cur.execute(
        "SELECT id FROM restaurants WHERE cuisine = %s AND id != %s",
        (cuisine, restaurant_id),
    )
    peer_ids = [r[0] for r in cur.fetchall()]
    established_peers = [
        rid for rid in peer_ids
        if get_restaurant_data_maturity(rid, database_url, threshold_days) == "established"
    ]

    cur.execute(
        "SELECT COUNT(*) FROM menu_items WHERE restaurant_id = %s AND active = TRUE",
        (restaurant_id,),
    )
    target_item_count = cur.fetchone()[0]

    # day-of-week bucket sums: per-peer average qty-per-item-per-day, then
    # averaged again across peers so no single restaurant dominates
    dow_sums   = np.zeros(7)
    dow_counts = np.zeros(7)

    for rid in established_peers:
        cur.execute(
            "SELECT COUNT(*) FROM menu_items WHERE restaurant_id = %s AND active = TRUE",
            (rid,),
        )
        peer_item_count = cur.fetchone()[0] or 1

        cur.execute(
            """
            SELECT DATE(ordered_at) AS day, SUM(qty) AS total_qty
            FROM   orders
            WHERE  restaurant_id = %s
            GROUP  BY DATE(ordered_at)
            """,
            (rid,),
        )
        for day, total_qty in cur.fetchall():
            dow = pd.Timestamp(day).dayofweek
            dow_sums[dow]   += float(total_qty) / peer_item_count
            dow_counts[dow] += 1

    conn.close()

    if not established_peers or target_item_count == 0 or dow_counts.sum() == 0:
        # No peers to learn from — flat, conservative default
        return {}

    dow_avg = np.divide(dow_sums, np.maximum(dow_counts, 1))
    overall_avg = dow_avg.mean() or 1.0
    dow_multiplier = dow_avg / overall_avg

    target_dow = pd.Timestamp(target_date).dayofweek
    festival_multiplier = (
        FESTIVAL_DATES[target_date][1] if target_date in _FESTIVAL_SET else 1.0
    )
    predicted_per_item = max(
        0.0, overall_avg * dow_multiplier[target_dow] * festival_multiplier
    )

    conn = _get_conn(database_url)
    cur = conn.cursor()
    cur.execute(
        "SELECT id FROM menu_items WHERE restaurant_id = %s AND active = TRUE",
        (restaurant_id,),
    )
    item_ids = [r[0] for r in cur.fetchall()]
    conn.close()

    return {item_id: predicted_per_item for item_id in item_ids}
