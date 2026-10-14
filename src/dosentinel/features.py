"""Leakage-safe features for direct multi-horizon DO forecasting.

Every feature at origin t is built from trailing windows and non-negative shifts, so it
uses only data at or before t. The only forward-looking quantities are calendar terms
for the *target* time (t + h), which are known in advance.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .baselines import STEPS_PER_DAY
from .evaluate import clean_values

DAY = STEPS_PER_DAY
MONTH = 30 * DAY
STEP = pd.Timedelta("15min")

PARAM_COLS = {"00300": "do", "00010": "temp", "00095": "spcond", "63680": "turb", "00060": "q"}


def load_site_frame(site_id: str, clean_dir: str = "data/clean") -> pd.DataFrame:
    """Flag-masked series for one site, aligned on the DO grid (NaN where absent)."""
    do = clean_values(pd.read_parquet(f"{clean_dir}/{site_id}_00300.parquet")).rename("do")
    frame = pd.DataFrame(do)
    for code, name in PARAM_COLS.items():
        if name == "do":
            continue
        path = Path(clean_dir) / f"{site_id}_{code}.parquet"
        # A site may not report a covariate at all (no file); treat it as all-missing so the
        # models see NaN rather than the site being dropped.
        if path.exists():
            frame[name] = clean_values(pd.read_parquet(path)).reindex(frame.index)
        else:
            frame[name] = np.nan
    return frame


def _cyc(values: np.ndarray, period: float, prefix: str) -> dict[str, np.ndarray]:
    ang = 2 * np.pi * values / period
    return {f"{prefix}_sin": np.sin(ang), f"{prefix}_cos": np.cos(ang)}


def _calendar(index: pd.DatetimeIndex, prefix: str) -> dict[str, np.ndarray]:
    hour = index.hour.values + index.minute.values / 60
    doy = index.dayofyear.values
    return {**_cyc(hour, 24, f"{prefix}_hour"), **_cyc(doy, 365.25, f"{prefix}_doy")}


def base_features(f: pd.DataFrame) -> pd.DataFrame:
    """Horizon-independent features at each origin. `f` has columns do,temp,spcond,turb,q."""
    do, temp, spc, turb, q = f["do"], f["temp"], f["spcond"], f["turb"], f["q"]
    X = pd.DataFrame(index=f.index)
    X["do"] = do
    for k in (4, 8, 12, 24, 48, 96):  # 1h .. 24h
        X[f"do_d{k}"] = do - do.shift(k)
    for name, w in (("6h", 24), ("24h", 96)):
        r = do.rolling(w, min_periods=w // 2)
        X[f"do_mean_{name}"], X[f"do_std_{name}"] = r.mean(), r.std()
    X["do_min_24h"] = do.rolling(DAY, min_periods=DAY // 2).min()
    X["do_max_24h"] = do.rolling(DAY, min_periods=DAY // 2).max()

    X["temp"] = temp
    X["temp_d24"] = temp - temp.shift(24)
    X["temp_mean_24h"] = temp.rolling(DAY, min_periods=DAY // 2).mean()

    X["spc"] = spc
    X["spc_d24"] = spc - spc.shift(24)
    X["spc_rel_30d"] = spc / spc.rolling(MONTH, min_periods=DAY).median()

    X["turb_log"] = np.log1p(turb.clip(lower=0))
    X["turb_d24"] = X["turb_log"] - X["turb_log"].shift(24)

    lq = np.log1p(q.clip(lower=0))
    X["q_log"] = lq
    X["q_d4"] = lq - lq.shift(4)
    X["q_d24"] = lq - lq.shift(24)
    X["q_max_24h"] = lq.rolling(DAY, min_periods=24).max()
    X["q_above_min_24h"] = lq - lq.rolling(DAY, min_periods=24).min()
    X["q_rel_30d"] = q / q.rolling(MONTH, min_periods=DAY).median()  # storm index

    for k, v in _calendar(f.index, "org").items():
        X[k] = v
    return X


def horizon_features(X: pd.DataFrame, v: pd.Series, h: int) -> pd.DataFrame:
    """Add features specific to horizon h (in steps), 1 <= h <= 96."""
    if not 1 <= h <= DAY:
        raise ValueError(f"horizon must be 1..{DAY} steps, got {h}")
    Z = X.copy()
    for k, a in _calendar(X.index + h * STEP, "tgt").items():
        Z[k] = a
    Z["do_yday_target"] = v.shift(DAY - h)
    Z["do_yday_delta"] = v.shift(DAY - h) - v.shift(DAY)
    lagged = pd.concat([v.shift(DAY * k - h) for k in range(1, 8)], axis=1)
    Z["do_mean7d_target"] = lagged.mean(axis=1).where(lagged.notna().sum(axis=1) >= 3)
    return Z


def train_origin_mask(index: pd.DatetimeIndex, h: int, test_start: pd.Timestamp) -> np.ndarray:
    """Origins whose *target time* falls on or before the start of the test window."""
    return np.asarray(index + h * STEP <= test_start)


# Features whose absolute scale is site-specific (conductance level, discharge magnitude).
# Dropped for models trained on other sites; relative versions (spc_rel_30d, q_rel_30d,
# q_d4, q_d24, q_above_min_24h) remain.
SITE_SPECIFIC = ["spc", "spc_d24", "q_log", "q_max_24h"]


LONG_LAG_DAYS = 15  # longer than the longest injected drift (14 d), so anchors are unaffected


def long_lag_features(v: pd.Series, lag_days: int = LONG_LAG_DAYS) -> pd.DataFrame:
    """DO reference values from `lag_days` ago and before: a level anchor that a drift
    shorter than `lag_days` has not yet contaminated. All lookups are non-negative shifts."""
    d = lag_days * DAY
    lagged = v.shift(d)
    week = lagged.rolling(7 * DAY, min_periods=DAY)
    day = lagged.rolling(DAY, min_periods=DAY // 2)
    return pd.DataFrame({
        "ll_anchor": lagged, "ll_mean7": week.mean(), "ll_std7": week.std(),
        "ll_min1d": day.min(), "ll_max1d": day.max(),
    })


def do_saturation(temp_c) -> np.ndarray | pd.Series:
    """Freshwater DO saturation (mg/L) at 1 atm from water temperature, APHA (1985).

    Ignores elevation/pressure, so absolute percent saturation is biased at high-altitude
    sites; that bias is constant per site and absorbed by site-specific models."""
    tk = np.asarray(temp_c, dtype=float) + 273.15
    ln = (-139.34411 + 1.575701e5 / tk - 6.642308e7 / tk**2 + 1.243800e10 / tk**3 - 8.621949e11 / tk**4)
    out = np.exp(ln)
    return pd.Series(out, index=temp_c.index) if isinstance(temp_c, pd.Series) else out


def saturation_features(f: pd.DataFrame, h: int) -> pd.DataFrame:
    """Saturation-based features at each origin for horizon h (steps). Past data only; the
    temperature at the target time is *estimated* with persistence + yesterday's diel change."""
    if not 1 <= h <= DAY:
        raise ValueError(f"horizon must be 1..{DAY} steps, got {h}")
    temp, do = f["temp"], f["do"]
    sat = do_saturation(temp)
    pct = do / sat
    out = pd.DataFrame(index=f.index)
    out["do_sat"] = sat
    out["do_pct"] = pct
    out["sat_deficit"] = sat - do
    out["do_pct_mean_24h"] = pct.rolling(DAY, min_periods=DAY // 2).mean()
    out["pct_yday_target"] = pct.shift(DAY - h)
    temp_hat = temp + temp.shift(DAY - h) - temp.shift(DAY)
    out["sat_target_hat"] = do_saturation(temp_hat)
    return out
