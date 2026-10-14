"""Baseline DO forecasters on a regular 15-minute grid.

Every forecaster takes the series ``v`` (NaN where missing or flagged) and a horizon
``h`` in steps, and returns a Series indexed by *origin time* t: the forecast of
v[t + h] using only values at or before t. Every lookup is a non-negative shift, so
nothing after the origin can leak in; tests/test_baselines.py enforces this.
"""
from __future__ import annotations

import pandas as pd

STEPS_PER_DAY = 96
HORIZONS_H = [1, 3, 6, 12, 24]


def _check(h: int) -> None:
    if not 1 <= h <= STEPS_PER_DAY:
        raise ValueError(f"horizon must be 1..{STEPS_PER_DAY} steps for seasonal baselines, got {h}")


def persistence(v: pd.Series, h: int) -> pd.Series:
    """Last observed value, carried forward."""
    _check(h)
    return v.copy()


def seasonal_naive(v: pd.Series, h: int) -> pd.Series:
    """Value at the same time of day, one day before the target time."""
    _check(h)
    return v.shift(STEPS_PER_DAY - h)


def seasonal_mean(v: pd.Series, h: int, days: int = 7, min_days: int = 3) -> pd.Series:
    """Mean of the same time of day over the previous `days` days (at least `min_days`)."""
    _check(h)
    lagged = pd.concat([v.shift(STEPS_PER_DAY * k - h) for k in range(1, days + 1)], axis=1)
    return lagged.mean(axis=1).where(lagged.notna().sum(axis=1) >= min_days)


def persistence_diel_delta(v: pd.Series, h: int) -> pd.Series:
    """Current level plus the change yesterday saw over the same window.

    yhat(t+h) = v[t] + (v[t+h-1d] - v[t-1d]). Combines the latest observation with
    the previous day's diel shape; the strongest simple baseline here.
    """
    _check(h)
    return v + (v.shift(STEPS_PER_DAY - h) - v.shift(STEPS_PER_DAY))


BASELINES = {
    "persistence": persistence,
    "seasonal_naive": seasonal_naive,
    "seasonal_mean_7d": seasonal_mean,
    "persistence_diel_delta": persistence_diel_delta,
}


def forecast_frame(v: pd.Series, h: int, methods: dict | None = None) -> pd.DataFrame:
    """Targets and forecasts for horizon h, one row per origin time."""
    out = pd.DataFrame({"y_true": v.shift(-h), "y_origin": v})
    for name, fn in (methods or BASELINES).items():
        out[name] = fn(v, h)
    return out
