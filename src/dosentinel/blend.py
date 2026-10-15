"""Blend two quantile forecasters and conformalize the blend.

The blend is an unweighted average of each quantile (so it stays ordered if both inputs are),
and the conformal margin is computed on the blend's own errors, never inherited from its
members: averaging changes both the centre and the spread.
"""
from __future__ import annotations

from math import erf, sqrt

import numpy as np
import pandas as pd

from .calibration import cqr_correction

Q = ("q10", "q50", "q90")


def blend_quantiles(a: pd.DataFrame, b: pd.DataFrame, weight: float = 0.5) -> pd.DataFrame:
    """Weighted mean of two frames with columns q10/q50/q90, sorted row-wise so quantiles never cross."""
    if not 0.0 <= weight <= 1.0:
        raise ValueError("weight must be in [0, 1]")
    arr = weight * a[list(Q)].to_numpy(float) + (1 - weight) * b[list(Q)].to_numpy(float)
    return pd.DataFrame(np.sort(arr, axis=1), index=a.index, columns=list(Q))


def conformal_margin(y: np.ndarray, q: pd.DataFrame, alpha: float = 0.2) -> float:
    """CQR margin for intervals [q10, q90] against targets y; NaN rows are ignored."""
    y = np.asarray(y, float)
    lo, hi = q["q10"].to_numpy(float), q["q90"].to_numpy(float)
    ok = ~(np.isnan(y) | np.isnan(lo) | np.isnan(hi))
    return cqr_correction(y[ok], lo[ok], hi[ok], alpha=alpha)


def apply_margin(q: pd.DataFrame, margin: float) -> pd.DataFrame:
    """Widen [q10, q90] by `margin`, never letting a negative margin cross the median."""
    out = q.copy()
    out["q10"] = np.minimum(q["q10"] - margin, q["q50"])
    out["q90"] = np.maximum(q["q90"] + margin, q["q50"])
    return out


def interval_score(y: np.ndarray, lo: np.ndarray, hi: np.ndarray, alpha: float = 0.2) -> np.ndarray:
    """Winkler score: width plus a 2/alpha penalty for how far the target missed the interval."""
    y, lo, hi = (np.asarray(x, float) for x in (y, lo, hi))
    return (hi - lo) + (2 / alpha) * (np.maximum(lo - y, 0) + np.maximum(y - hi, 0))


Z90 = 1.2815515655446004  # standard-normal 0.9 quantile: q90 - q10 spans 2 * Z90 standard deviations


def prob_below(q10, q50, q90, threshold: float):
    """P(value < threshold) from an 80% interval, treating the forecast as normal around its median.

    The spread is read from the (conformalized) interval, so it is as wide as the interval is;
    the centre is the median. A cheap way to turn q10/q50/q90 into the single probability a
    cost-ratio cutoff needs. Works on scalars and arrays."""
    q10, q50, q90 = (np.asarray(x, float) for x in (q10, q50, q90))
    sd = np.maximum((q90 - q10) / (2 * Z90), 1e-3)
    z = (threshold - q50) / sd
    return 0.5 * (1.0 + np.vectorize(erf)(z / sqrt(2.0)))
