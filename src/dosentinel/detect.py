"""Causal sensor-fault scores for a DO series.

Every score at time t uses only data up to t (a real-time system's view). Higher means more
anomalous. Thresholds are chosen elsewhere, on a clean calibration period, to a fixed
false-flag rate so detectors can be compared at matched false alarms.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .baselines import STEPS_PER_DAY, persistence_diel_delta
from .validate import Rule

EPS = 1e-9


def run_length(v: pd.Series) -> pd.Series:
    """Number of consecutive identical observed values ending at each step (0 if missing)."""
    grp = (v != v.shift()).cumsum()
    rl = v.groupby(grp).cumcount() + 1
    return rl.where(v.notna(), 0).astype(float)


def rules_flag(v: pd.Series, rule: Rule) -> pd.Series:
    """Causal version of the validate.py rules: a flatline is flagged from its n-th step on."""
    oor = (v < rule.lo) | (v > rule.hi)
    spike = v.diff().abs() > rule.max_step
    flat = run_length(v) >= rule.flat_steps
    return (oor | spike | flat).fillna(False)


def robust_scale(x: pd.Series, days: int = 14, floor: float = 0.05) -> pd.Series:
    """Trailing 1.4826 * median(|x|), shifted so the value at t excludes x[t]."""
    med = x.abs().rolling(days * STEPS_PER_DAY, min_periods=STEPS_PER_DAY).median().shift(1)
    return (1.4826 * med).clip(lower=floor)


def persistence_residual_score(v: pd.Series, h: int = 4) -> pd.Series:
    """|v(t) - forecast made h steps earlier by persistence+diel-delta| / trailing robust scale."""
    forecast = persistence_diel_delta(v, h).shift(h)  # forecast for time t, issued at t-h
    r = v - forecast
    return r.abs() / robust_scale(r)


def interval_score(v: pd.Series, q10: pd.Series, q90: pd.Series) -> pd.Series:
    """How far v falls outside [q10, q90], in units of the interval width (0 inside).

    `q10`, `q90` must already be aligned to the target time (forecast issued earlier)."""
    width = (q90 - q10).clip(lower=1e-3)
    return (np.maximum(q10 - v, v - q90) / width).clip(lower=0.0)


def residual_score(v: pd.Series, predicted: pd.Series, scale: float, smooth: int = 1) -> pd.Series:
    """|rolling mean over `smooth` steps of (v - predicted)| / fixed scale."""
    r = v - predicted
    if smooth > 1:
        r = r.rolling(smooth, min_periods=smooth // 2).mean()
    return r.abs() / max(scale, 0.05)


def threshold_at_rate(scores: pd.Series, rate: float) -> float:
    """Smallest threshold such that at most `rate` of finite calibration scores exceed it."""
    s = scores.replace([np.inf, -np.inf], np.nan).dropna().to_numpy()
    if s.size == 0:
        return np.inf
    return float(np.quantile(s, 1.0 - rate, method="higher"))


def persistence_residual_online(v: pd.Series, thr: float, scale: pd.Series, h: int = 4,
                                external: pd.Series | None = None, max_mask_run: int = 48) -> pd.DataFrame:
    """Persistence + diel-delta residual score with echo suppression, computed sequentially.

    Forecast for t (issued at t-h): c[t-h] + c[t-96] - c[t-h-96], where c is the *cleaned*
    history. A step flagged by this detector (score > thr) or by `external` is masked out of
    c, so a fault does not contaminate the forecasts made 24 h later, and those forecasts are
    simply not scored. After `max_mask_run` consecutive masked steps the observed value is
    accepted (a real regime change must not blind the detector forever).

    Returns columns: score (NaN where it cannot be computed) and flag.
    """
    L = STEPS_PER_DAY
    x = v.to_numpy(dtype=float)
    sc = scale.reindex(v.index).to_numpy(dtype=float)
    ext = np.zeros(len(x), bool) if external is None else external.reindex(v.index).fillna(False).to_numpy(bool)
    c = x.copy()
    score = np.full(len(x), np.nan)
    flag = np.zeros(len(x), bool)
    run = 0
    for t in range(L + h, len(x)):
        f = c[t - h] + c[t - L] - c[t - h - L]
        if np.isnan(x[t]):
            continue
        z = np.nan
        if not (np.isnan(f) or np.isnan(sc[t])):
            z = abs(x[t] - f) / sc[t]
            score[t] = z
        bad = ext[t] or (not np.isnan(z) and z > thr)
        flag[t] = bad
        if bad and run < max_mask_run:
            c[t] = np.nan
            run += 1
        else:
            c[t] = x[t]
            if not bad:
                run = 0
    return pd.DataFrame({"score": score, "flag": flag}, index=v.index)
