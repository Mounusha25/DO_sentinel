"""Direct "any DO below threshold within the next 24 h" probability, walk-forward and calibrated."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .calibration import PlattScaler
from .features import base_features, train_origin_mask
from .models import fit_predict_proba

H = 96  # 24 h in steps
MIN_OBSERVED = 48  # need half the lookahead observed to call a window "no low DO"
MIN_CAL_CLASS = 15


def within_label(v: pd.Series, thr: float) -> pd.Series:
    """1 if any observed value in (t, t+24h] is below thr; 0 if none and >= half observed; else NaN."""
    rev = v[::-1]
    fmin = rev.rolling(H, min_periods=1).min()[::-1].shift(-1)
    fcnt = rev.notna().astype(float).rolling(H, min_periods=1).sum()[::-1].shift(-1)
    lab = pd.Series(np.nan, index=v.index)
    lab[(fmin >= thr) & (fcnt >= MIN_OBSERVED)] = 0.0
    lab[fmin < thr] = 1.0
    return lab


def site_probability(f: pd.DataFrame, thr: float, folds, stride: int, cal_days: int) -> pd.DataFrame:
    v, idx = f["do"], f["do"].index
    X = base_features(f)
    y = within_label(v, thr).to_numpy()
    out = []
    for fold, _, t0, t1 in folds:
        cal_lo = t0 - pd.Timedelta(days=cal_days)
        tr = np.flatnonzero(train_origin_mask(idx, H, cal_lo) & ~np.isnan(y))[::stride]
        cal = (idx > cal_lo) & train_origin_mask(idx, H, t0) & ~np.isnan(y)
        test = (idx > t0) & (idx <= t1)
        ev = idx[cal | test]
        p_raw = fit_predict_proba(X.iloc[tr], y[tr].astype(int), X.loc[ev])
        d = pd.DataFrame({"p_raw": p_raw, "y": pd.Series(y, index=idx).loc[ev].to_numpy()}, index=ev)
        is_cal = d.index <= t0
        ok = is_cal & d["y"].notna().to_numpy()
        pos, neg = int(d.loc[ok, "y"].sum()), int((1 - d.loc[ok, "y"]).sum())
        scaled = pos >= MIN_CAL_CLASS and neg >= MIN_CAL_CLASS
        d["p"] = (PlattScaler().fit(d.loc[ok, "p_raw"].to_numpy(), d.loc[ok, "y"].to_numpy())
                  .transform(d["p_raw"].to_numpy()) if scaled else d["p_raw"])
        t = d[~is_cal].copy()
        t["fold"], t["platt_applied"] = fold, scaled
        t["train_rate"] = float(y[tr].mean()) if len(tr) else np.nan
        out.append(t)
    return pd.concat(out)
