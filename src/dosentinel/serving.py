"""Train per-site model bundles and produce a forecast + fault report from recent history.

The bundle reuses exactly the feature, model, calibration and detector code that the
walk-forward evaluation used, so serving cannot drift from the evaluated pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor

from .baselines import STEPS_PER_DAY, persistence_diel_delta
from .calibration import PlattScaler, cqr_correction
from .decision import Costs
from .detect import (
    interval_score,
    persistence_residual_online,
    persistence_residual_score,
    robust_scale,
    rules_flag,
    run_length,
    threshold_at_rate,
)
from .evaluate import clean_values
from .features import PARAM_COLS, base_features, horizon_features, train_origin_mask
from .models import PARAMS
from .validate import RULES, flag_series, to_grid

HORIZONS_H = [1, 3, 6, 12, 24]
QUANTILES = (0.1, 0.5, 0.9)
FAULT_FPR = 0.001
MIN_CAL_CLASS = 15
STEP = pd.Timedelta("15min")
MIN_HISTORY_DAYS = 16  # robust-scale window (14 d) plus a margin


@dataclass
class SiteBundle:
    site_id: str
    role: str
    threshold_mgL: float
    trained_through: pd.Timestamp
    calibrated_from: pd.Timestamp
    calibrated_through: pd.Timestamp
    quantile_models: dict = field(default_factory=dict)  # h_steps -> {q: model}
    horizon_columns: dict = field(default_factory=dict)  # h_steps -> list[str]
    cqr_margin: dict = field(default_factory=dict)  # h_steps -> float
    clf: object = None  # classifier, a constant float, or None
    clf_columns: list = field(default_factory=list)
    platt: PlattScaler | None = None
    cal_events: int = 0
    detector_thresholds: dict = field(default_factory=dict)
    metrics: dict = field(default_factory=dict)


def clean_frame_from_raw(raw: dict[str, pd.Series], window_start: pd.Timestamp | None = None) -> pd.DataFrame:
    """Raw value series by parameter code -> flag-masked frame (do,temp,spcond,turb,q) on a
    regular 15-minute grid. Applies the same rules the training data went through.

    `window_start`: if given, the frame is padded back to that time with missing values, so a
    sensor that only resumed reporting recently is treated as sparse history rather than as a
    short history. The end is deliberately not padded (USGS reporting lags the clock)."""
    if "00300" not in raw or raw["00300"].dropna().empty:
        raise ValueError("no dissolved-oxygen (00300) observations supplied")
    grid = to_grid(raw["00300"].dropna().sort_index())
    idx = grid.index
    cols = {}
    for code, name in PARAM_COLS.items():
        s = raw.get(code)
        if s is None or s.dropna().empty:
            cols[name] = pd.Series(np.nan, index=idx)
            continue
        fl = flag_series(s.dropna().sort_index(), RULES[code])
        cols[name] = clean_values(fl).reindex(idx)
    out = pd.DataFrame(cols, index=idx)
    if window_start is not None:
        start = pd.Timestamp(window_start).ceil("15min")
        if start < out.index[0]:
            out = out.reindex(pd.date_range(start, out.index[-1], freq="15min"))
    return out


def train_bundle(frame: pd.DataFrame, site_id: str, role: str = "", threshold: float = 5.0,
                 cal_days: int = 365, stride: int = 4, params: dict | None = None) -> SiteBundle:
    p = {**PARAMS, **(params or {})}
    v, idx = frame["do"], frame.index
    X = base_features(frame)
    end = idx.max()
    cal_lo = end - pd.Timedelta(days=cal_days)
    b = SiteBundle(site_id, role, threshold, trained_through=cal_lo, calibrated_from=cal_lo, calibrated_through=end)

    # ---- point/interval models per horizon, conformal margin on the calibration window
    for hh in HORIZONS_H:
        h = hh * 4
        Z = horizon_features(X, v, h)
        delta = (v.shift(-h) - v).to_numpy()
        tr = np.flatnonzero(train_origin_mask(idx, h, cal_lo) & ~np.isnan(delta))[::stride]
        models = {q: HistGradientBoostingRegressor(loss="quantile", quantile=q, **p)
                  .fit(Z.iloc[tr], delta[tr]) for q in QUANTILES}
        cal = (idx > cal_lo) & train_origin_mask(idx, h, end) & ~np.isnan(delta)
        Zc = Z.loc[cal]
        lo = v.loc[cal].to_numpy() + models[0.1].predict(Zc)
        hi = v.loc[cal].to_numpy() + models[0.9].predict(Zc)
        y = (v.shift(-h)).loc[cal].to_numpy()
        b.quantile_models[h], b.horizon_columns[h] = models, list(Z.columns)
        b.cqr_margin[h] = cqr_correction(y, np.minimum(lo, hi), np.maximum(lo, hi), alpha=0.2)
        med = v.loc[cal].to_numpy() + models[0.5].predict(Zc)
        b.metrics.setdefault("reference_mae_h", {})[hh] = float(np.nanmean(np.abs(med - y)))

    # ---- direct "any DO below threshold within 24 h" classifier + Platt scaling
    H = STEPS_PER_DAY
    rev = v[::-1]
    fmin = rev.rolling(H, min_periods=1).min()[::-1].shift(-1)
    fcnt = rev.notna().astype(float).rolling(H, min_periods=1).sum()[::-1].shift(-1)
    y_in = pd.Series(np.nan, index=idx)
    y_in[(fmin >= threshold) & (fcnt >= H // 2)] = 0.0
    y_in[fmin < threshold] = 1.0
    lab = y_in.to_numpy()
    tr = np.flatnonzero(train_origin_mask(idx, H, cal_lo) & ~np.isnan(lab))[::stride]
    b.clf_columns = list(X.columns)
    ytr = lab[tr].astype(int)
    b.clf = float(ytr.mean()) if ytr.min() == ytr.max() else HistGradientBoostingClassifier(**p).fit(X.iloc[tr], ytr)
    cal = (idx > cal_lo) & train_origin_mask(idx, H, end) & ~np.isnan(lab)
    raw = _proba(b.clf, X.loc[cal])
    ycal = lab[cal].astype(int)
    b.cal_events = int(ycal.sum())
    if ycal.sum() >= MIN_CAL_CLASS and (1 - ycal).sum() >= MIN_CAL_CLASS:
        b.platt = PlattScaler().fit(raw, ycal)

    # ---- fault-detector thresholds from the clean calibration window
    cal_rows = idx > cal_lo
    b.detector_thresholds["run_length"] = threshold_at_rate(run_length(v)[cal_rows], FAULT_FPR)
    b.detector_thresholds["persistence"] = threshold_at_rate(persistence_residual_score(v)[cal_rows], FAULT_FPR)
    Z1 = horizon_features(X, v, 4)
    q = {k: m.predict(Z1.loc[cal_rows, b.horizon_columns[4]]) for k, m in b.quantile_models[4].items()}
    base = v[cal_rows].to_numpy()
    s = interval_score(v.shift(-4)[cal_rows], pd.Series(base + q[0.1], index=v.index[cal_rows]),
                       pd.Series(base + q[0.9], index=v.index[cal_rows]))
    b.detector_thresholds["interval_1h"] = threshold_at_rate(s, FAULT_FPR)
    return b


def _proba(clf, X: pd.DataFrame) -> np.ndarray:
    return np.full(len(X), clf) if isinstance(clf, float) else clf.predict_proba(X)[:, 1]


def forecast(bundle: SiteBundle, frame: pd.DataFrame, cost_ratio: float = 10.0) -> dict:
    """Forecast, calibrated probability, fault report and action for the latest grid step."""
    days = (frame.index.max() - frame.index.min()).total_seconds() / 86400
    if days < MIN_HISTORY_DAYS:
        raise ValueError(f"need at least {MIN_HISTORY_DAYS} days of history, got {days:.1f}")
    v = frame["do"]
    X = base_features(frame)
    t = frame.index[-1]
    now = v.iloc[-1]
    out: dict = {"site_id": bundle.site_id, "as_of": t.isoformat(),
                 "latest_do_mgL": None if np.isnan(now) else float(now),
                 "observed_fraction_last_24h": float(v.iloc[-96:].notna().mean())}

    # ---- fault report (causal; looks at the last 2 hours)
    recent = slice(-8, None)
    rl = run_length(v)
    rules = rules_flag(v, RULES["00300"])
    raw_res = v - persistence_diel_delta(v, 4).shift(4)
    pers = persistence_residual_online(v, bundle.detector_thresholds["persistence"], robust_scale(raw_res),
                                       external=(rl >= bundle.detector_thresholds["run_length"]) | rules)
    Z1 = horizon_features(X, v, 4)
    mdl = bundle.quantile_models[4]
    cols = bundle.horizon_columns[4]
    origin_rows = Z1.iloc[-12:-4]
    base = v.iloc[-12:-4].to_numpy()
    q10 = pd.Series(base + mdl[0.1].predict(origin_rows[cols]), index=v.index[-8:])  # forecast for last 8 steps
    q90 = pd.Series(base + mdl[0.9].predict(origin_rows[cols]), index=v.index[-8:])
    isc = interval_score(v.iloc[recent], q10, q90)
    faults = {
        "rules": bool(rules.iloc[recent].any()),
        "flatline": bool((rl.iloc[recent] >= bundle.detector_thresholds["run_length"]).any()),
        "jump_persistence": bool(pers["flag"].iloc[recent].any()),
        "jump_interval_1h": bool((isc > bundle.detector_thresholds["interval_1h"]).any()),
        "latest_value_missing": bool(np.isnan(now)),
    }
    faults["any"] = any(faults.values())
    out["fault_flags"] = faults

    if np.isnan(now):
        out.update(forecasts=[], p_low_do_within_24h=None, action="maintenance",
                   reason="latest DO value is missing or was masked by a quality rule")
        return out

    # ---- forecasts with conformal 80% intervals
    fc = []
    for hh in HORIZONS_H:
        h = hh * 4
        Zr = horizon_features(X, v, h).iloc[[-1]][bundle.horizon_columns[h]]
        d = np.sort([bundle.quantile_models[h][q].predict(Zr)[0] for q in QUANTILES])
        m = bundle.cqr_margin[h]
        fc.append({"horizon_h": hh, "target_time": (t + hh * 4 * STEP).isoformat(),
                   "median_mgL": float(now + d[1]),
                   # a negative conformal margin must not push the interval past the median
                   "low_mgL": float(max(0.0, min(now + d[0] - m, now + d[1]))),
                   "high_mgL": float(max(now + d[2] + m, now + d[1]))})
    out["forecasts"] = fc

    # ---- probability of a low-DO episode in the next 24 h, and the action
    p_raw = float(_proba(bundle.clf, X.iloc[[-1]][bundle.clf_columns])[0])
    p = float(bundle.platt.transform(np.array([p_raw]))[0]) if bundle.platt else p_raw
    cutoff = Costs(cost_ratio, 1.0).cutoff
    out["p_low_do_within_24h"] = p
    out["probability_calibrated"] = bundle.platt is not None
    out["threshold_mgL"] = bundle.threshold_mgL
    out["alert_cutoff"] = cutoff
    if faults["any"]:
        out["action"], out["reason"] = "maintenance", "possible sensor fault; ecological alerts suppressed"
    elif p > cutoff:
        out["action"], out["reason"] = "warning", f"P(DO < {bundle.threshold_mgL:g} mg/L within 24 h) exceeds {cutoff:.3f}"
    else:
        out["action"], out["reason"] = "none", "no action"
    return out
