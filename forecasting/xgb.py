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
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
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
