"""
Prophet baseline — one model per dish.

Prophet handles weekly seasonality, yearly seasonality, and trend out of the
box. We use multiplicative mode since restaurant demand scales with the baseline
rather than adding a fixed seasonal offset.
"""

from __future__ import annotations

import logging
from datetime import date

import pandas as pd

# Prophet imports Stan at load time — suppress its verbose logging
logging.getLogger("prophet").setLevel(logging.WARNING)
logging.getLogger("cmdstanpy").setLevel(logging.WARNING)

try:
    from prophet import Prophet
    PROPHET_AVAILABLE = True
except Exception:   # catches ImportError and Stan compilation failures
    PROPHET_AVAILABLE = False


def train_prophet(series: pd.Series) -> object | None:
    """
    Fit a Prophet model on a complete daily demand series.
    Returns None if Prophet is unavailable.
    """
    if not PROPHET_AVAILABLE:
        return None

    df = pd.DataFrame({"ds": series.index, "y": series.values.astype(float)})
    df = df[df["y"] >= 0]          # Prophet requires non-negative y

    m = Prophet(
        daily_seasonality=False,
        weekly_seasonality=True,
        yearly_seasonality=True,
        seasonality_mode="multiplicative",
        changepoint_prior_scale=0.1,   # modest flexibility — avoids overfitting
        seasonality_prior_scale=10.0,
    )
    m.fit(df)
    return m


def predict_prophet_single(model: object | None, target_date: date) -> float:
    """Predict demand for a single date. Returns 0.0 if model is None."""
    if model is None:
        return 0.0
    future = pd.DataFrame({"ds": [pd.Timestamp(target_date)]})
    fc = model.predict(future)
    return max(0.0, float(fc["yhat"].iloc[0]))


def predict_prophet_range(
    model: object | None, start_date: date, end_date: date
) -> pd.Series:
    """
    Predict demand for every day in [start_date, end_date].
    Returns an empty Series if model is None.
    """
    if model is None:
        return pd.Series(dtype=float)
    date_range = pd.date_range(start_date, end_date, freq="D")
    future = pd.DataFrame({"ds": date_range})
    fc = model.predict(future)
    result = fc.set_index("ds")["yhat"]
    return result.clip(lower=0.0)
