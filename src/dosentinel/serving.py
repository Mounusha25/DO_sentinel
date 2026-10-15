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
from .blend import apply_margin, blend_quantiles, conformal_margin, prob_below
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
from .features import PARAM_COLS, base_features, horizon_features, saturation_features, train_origin_mask
from .models import PARAMS
from .validate import RULES, flag_series, to_grid
from .weather import weather_features

HORIZONS_H = [1, 3, 6, 12, 24]
QUANTILES = (0.1, 0.5, 0.9)
FAULT_FPR = 0.001
MIN_CAL_CLASS = 15
STEP = pd.Timedelta("15min")
MIN_HISTORY_DAYS = 16  # robust-scale window (14 d) plus a margin
# Day-ahead forecast archives start here; weather models are trained on origins from this date on.
WX_FROM = pd.Timestamp("2024-01-20", tz="UTC")


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
    # Optional weather-aware models (same quantiles, trained from WX_FROM). Empty on older bundles.
    wx_quantile_models: dict = field(default_factory=dict)  # h_steps -> {q: model}
    wx_horizon_columns: dict = field(default_factory=dict)
    wx_cqr_margin: dict = field(default_factory=dict)
    # Conformal margin of the 50/50 blend of the weather GBM and Chronos-2, fitted on the
    # calibration window (needs the cached Chronos-2 forecasts for that window).
    blend_margin: dict = field(default_factory=dict)  # h_steps -> float
    lat: float | None = None  # station coordinates, used to fetch live weather
    lon: float | None = None


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


def wx_matrix(frame: pd.DataFrame, X: pd.DataFrame, v: pd.Series, W: pd.DataFrame, h: int,
              rows: pd.DatetimeIndex | None = None) -> pd.DataFrame:
    """Feature matrix of the weather-aware model for horizon h (steps): the base horizon
    features, saturation features and weather features, exactly as in the walk-forward runs."""
    Z = pd.concat([horizon_features(X, v, h), saturation_features(frame, h)], axis=1)
    idx = frame.index if rows is None else rows
    if rows is not None:
        Z = Z.loc[rows]
    return pd.concat([Z, weather_features(idx, W, h, "fc1")], axis=1)


def train_bundle(frame: pd.DataFrame, site_id: str, role: str = "", threshold: float = 5.0,
                 cal_days: int = 365, stride: int = 4, params: dict | None = None,
                 weather: pd.DataFrame | None = None, chronos_cal: pd.DataFrame | None = None) -> SiteBundle:
    """`weather`: hourly obs_*/fc1_* table for this site; adds weather-aware models.
    `chronos_cal`: cached Chronos-2 "wx" quantiles (columns wx_q{10,50,90}_{h}, index = hourly
    origins) over the calibration window; adds the blend margin. Needs `weather`."""
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

        if weather is not None:
            _fit_weather_models(b, frame, X, v, weather, h, hh, delta, cal_lo, end, stride, p, chronos_cal)

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


def _quantile_frame(base: np.ndarray, models: dict, Z: pd.DataFrame, index) -> pd.DataFrame:
    d = np.sort(np.column_stack([models[q].predict(Z) for q in QUANTILES]), axis=1)
    return pd.DataFrame(base[:, None] + d, index=index, columns=["q10", "q50", "q90"])


def _fit_weather_models(b: SiteBundle, frame, X, v, W, h, hh, delta, cal_lo, end, stride, p, chronos_cal) -> None:
    idx = frame.index
    Zw = wx_matrix(frame, X, v, W, h)
    tr = np.flatnonzero(train_origin_mask(idx, h, cal_lo) & ~np.isnan(delta) & (idx >= WX_FROM))[::stride]
    if len(tr) < 500:  # too little archived-forecast history to fit; leave the no-weather model in charge
        return
    models = {q: HistGradientBoostingRegressor(loss="quantile", quantile=q, **p).fit(Zw.iloc[tr], delta[tr])
              for q in QUANTILES}
    cal = (idx > cal_lo) & train_origin_mask(idx, h, end) & ~np.isnan(delta)
    qc = _quantile_frame(v.loc[cal].to_numpy(), models, Zw.loc[cal], idx[cal])
    y = v.shift(-h).loc[cal].to_numpy()
    b.wx_quantile_models[h], b.wx_horizon_columns[h] = models, list(Zw.columns)
    b.wx_cqr_margin[h] = conformal_margin(y, qc)
    b.metrics.setdefault("wx_reference_mae_h", {})[hh] = float(np.nanmean(np.abs(qc["q50"].to_numpy() - y)))

    cols = [f"wx_{k}_{hh}" for k in ("q10", "q50", "q90")]
    if chronos_cal is None or not set(cols) <= set(chronos_cal.columns):
        return
    orig = qc.index.intersection(chronos_cal.index)
    if len(orig) < 200:
        return
    ch = chronos_cal.loc[orig, cols].set_axis(["q10", "q50", "q90"], axis=1)
    bl = blend_quantiles(qc.loc[orig], ch)
    yo = v.shift(-h).loc[orig].to_numpy()
    b.blend_margin[h] = conformal_margin(yo, bl)
    b.metrics.setdefault("blend_reference_mae_h", {})[hh] = float(np.nanmean(np.abs(bl["q50"].to_numpy() - yo)))


def _proba(clf, X: pd.DataFrame) -> np.ndarray:
    return np.full(len(X), clf) if isinstance(clf, float) else clf.predict_proba(X)[:, 1]


def _weather_forecasts(bundle: SiteBundle, frame: pd.DataFrame, W: pd.DataFrame, foundation) -> tuple[list, str]:
    """Interval forecasts from the weather-aware model, or its blend with Chronos-2.

    Issued from the last whole hour, because the pretrained model is evaluated on hourly origins.
    Returns ([], "") when the inputs do not allow it, so the caller falls back to the base model."""
    t = frame.index[-1].floor("h")
    f = frame.loc[:t]
    v = f["do"]
    if f.index[-1] != t or np.isnan(v.iloc[-1]) or not bundle.wx_quantile_models:
        return [], ""
    X = base_features(f)
    now = float(v.iloc[-1])
    ch = foundation.predict(f, W) if foundation is not None and bundle.blend_margin else None
    rows, used_blend = [], ch is not None
    for hh in HORIZONS_H:
        h = hh * 4
        if h not in bundle.wx_quantile_models:
            return [], ""
        Zr = wx_matrix(f, X, v, W, h, rows=f.index[-1:])[bundle.wx_horizon_columns[h]]
        g = _quantile_frame(np.array([now]), bundle.wx_quantile_models[h], Zr, f.index[-1:])
        if ch is not None and h in bundle.blend_margin:
            c = pd.DataFrame([ch[hh]], columns=["q10", "q50", "q90"], index=g.index)
            q = apply_margin(blend_quantiles(g, c), bundle.blend_margin[h])
        else:
            used_blend = False
            q = apply_margin(g, bundle.wx_cqr_margin[h])
        rows.append({"horizon_h": hh, "target_time": (t + hh * 4 * STEP).isoformat(),
                     "median_mgL": float(q["q50"].iloc[0]), "low_mgL": float(max(0.0, q["q10"].iloc[0])),
                     "high_mgL": float(q["q90"].iloc[0])})
    return rows, ("gbm_weather+chronos2" if used_blend else "gbm_weather")


def forecast(bundle: SiteBundle, frame: pd.DataFrame, cost_ratio: float = 10.0,
             weather: pd.DataFrame | None = None, foundation=None) -> dict:
    """Forecast, calibrated probability, fault report and action for the latest grid step.

    With `weather` (live table in the training schema) and a bundle that has weather models, the
    interval forecasts come from the weather-aware model; with `foundation` as well and a fitted
    blend margin, from the conformalized blend. Faults and probability are unchanged. Any missing
    piece falls back to the base model, and `forecast_model` says which one answered."""
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
    out["forecast_model"] = "gbm"
    if weather is not None:
        try:
            wfc, name = _weather_forecasts(bundle, frame, weather, foundation)
        except Exception as e:  # a weather or foundation failure must degrade, never fail the request
            wfc, name = [], ""
            out["forecast_model_note"] = f"weather model skipped: {type(e).__name__}"
        if wfc:
            fc, out["forecast_model"] = wfc, name
            out["forecast_issued_from"] = frame.index[-1].floor("h").isoformat()
    out["forecasts"] = fc

    # ---- probability of a low-DO episode in the next 24 h, and the action
    p_raw = float(_proba(bundle.clf, X.iloc[[-1]][bundle.clf_columns])[0])
    p = float(bundle.platt.transform(np.array([p_raw]))[0]) if bundle.platt else p_raw
    cutoff = Costs(cost_ratio, 1.0).cutoff
    # Where the classifier is a real model its calibrated probability decides (it was the cheaper
    # rule at 9 of 14 sites). A constant classifier means the training data held no low-DO events,
    # so its probability can never cross a cutoff (USGS-01184000 scored 5.4 cost/week against 0.4-0.6
    # for the interval rule and 1.1 for "DO < threshold + 0.5"); there the alert reads the interval
    # forecast: P(DO < threshold) at the worst horizon, taking the conformalized 80% interval as a
    # normal around its median, against the same cost-derived cutoff. See scripts/run_alert_bound.py.
    exceed = float(max(prob_below(x["low_mgL"], x["median_mgL"], x["high_mgL"], bundle.threshold_mgL) for x in fc))
    rule = "forecast_interval" if isinstance(bundle.clf, float) else "probability"
    trigger = exceed if rule == "forecast_interval" else p
    out["p_low_do_within_24h"] = p
    out["probability_calibrated"] = bundle.platt is not None and rule == "probability"
    out["p_low_do_forecast_interval"] = exceed
    out["threshold_mgL"] = bundle.threshold_mgL
    out["alert_cutoff"] = cutoff
    out["alert_rule"] = rule
    if faults["any"]:
        out["action"], out["reason"] = "maintenance", "possible sensor fault; ecological alerts suppressed"
    elif trigger > cutoff and rule == "forecast_interval":
        out["action"] = "warning"
        out["reason"] = (f"forecast interval puts P(DO < {bundle.threshold_mgL:g} mg/L) at {exceed:.2f} "
                         f"within 24 h, above the cost-derived cutoff {cutoff:.3f} (no probability model for this site)")
    elif trigger > cutoff:
        out["action"], out["reason"] = "warning", f"P(DO < {bundle.threshold_mgL:g} mg/L within 24 h) exceeds {cutoff:.3f}"
    else:
        out["action"], out["reason"] = "none", "no action"
    return out
