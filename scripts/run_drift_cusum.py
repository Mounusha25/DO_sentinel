"""Slow-drift detection with change-detection statistics, and the minimum detectable drift.

    python scripts/run_drift_cusum.py [--reps 18] [--far 0.05]

Windowed residual means (run_drift.py) have a built-in problem: with a false-flag rate of 0.67 per
week, 49% of any 7-day window contains a false flag, so "detection" in a week is mostly chance. Here:

  * the false-alarm rate is a budget, set low (default one false alarm per 20 weeks) and fixed on the
    clean calibration year for every detector, so detection rates are comparable and chance is small;
  * the statistics are the standard ones for small persistent shifts: two-sided CUSUM (Page) and EWMA
    on hourly standardized residuals of the long-lag-anchored model, against the 72 h window mean;
  * drifts (ramp from 0 to +/- magnitude, then snap back) are injected into the held-out year at
    0.25 to 3 mg/L over 3, 7 and 14 days, 18 repetitions per site, plus zero-magnitude "null" windows
    scored identically, so every detection rate is shown next to its chance level;
  * the result is a minimum detectable drift: the smallest magnitude detected in at least 80% of
    drifts of a given duration, with a bootstrap interval over sites.

Writes results/drift_cusum_*.csv.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from run_drift import ECHO_DAYS
from sklearn.ensemble import HistGradientBoostingRegressor

from dosentinel.faults import inject_drifts
from dosentinel.features import base_features, load_site_frame, long_lag_features
from dosentinel.models import PARAMS
from dosentinel.tracking import track

MAGS = ((0.25, 1.5), (0.5, 2.0), (1.0, 3.0))  # two magnitudes per repetition, cycled
DURATIONS = (3, 7, 14)
STEPS_WEEK = 7 * 24  # hourly
CUSUM_K = 0.5  # reference value, in residual standard deviations
EWMA_LAMBDA = 0.03
CLIP = 4.0  # standard deviations; residual spikes beyond this are clipped before accumulating


BASE_LAG_H, BASE_WINDOW_H = 15 * 24, 60 * 24  # baseline = median of hours t-75d .. t-15d: causal, and slower
                                               # than any injected drift, so a drift is not absorbed into it


def hourly(res: pd.Series) -> pd.Series:
    return res.resample("1h").mean()


def standardized(res_h: pd.Series, floor: float) -> pd.Series:
    """(residual - lagged median) / lagged robust scale: causal, so the level and the spread adapt to a
    model that ages, and the thresholds are in units that transfer between years."""
    base = lagged_baseline(res_h)
    c = res_h - base
    scale = 1.4826 * c.abs().shift(BASE_LAG_H).rolling(BASE_WINDOW_H, min_periods=7 * 24).median()
    return (c / scale.clip(lower=floor)).clip(-CLIP, CLIP)  # winsorised: one storm must not be an alarm


def lagged_baseline(res_h: pd.Series) -> pd.Series:
    """Trailing median of the residual, ending 15 days ago. Removes slow level shifts between years
    (model ageing, a sensor swap) that would otherwise accumulate in a CUSUM, without seeing a drift
    shorter than the lag."""
    return res_h.shift(BASE_LAG_H).rolling(BASE_WINDOW_H, min_periods=7 * 24).median()


def cusum_episodes(z: np.ndarray, k: float, h: float) -> np.ndarray:
    """Two-sided Page CUSUM on hourly z; returns a boolean alarm flag per hour (re-arms after an alarm)."""
    hi = lo = 0.0
    out = np.zeros(len(z), bool)
    for i, x in enumerate(z):
        if not np.isfinite(x):
            continue
        hi, lo = max(0.0, hi + x - k), max(0.0, lo - x - k)
        if hi > h or lo > h:
            out[i] = True
            hi = lo = 0.0
    return out


def ewma_flags(z: np.ndarray, lam: float, thr: float) -> np.ndarray:
    out = np.zeros(len(z), bool)
    e = 0.0
    for i, x in enumerate(z):
        if np.isfinite(x):
            e = lam * x + (1 - lam) * e
        out[i] = abs(e) > thr
    return out


def episodes_per_week(flag: np.ndarray, mask: np.ndarray, gap: int = 24) -> float:
    """Alarm episodes per week over the masked hours (alarms within `gap` hours are one episode)."""
    idx = np.flatnonzero(flag & mask)
    if idx.size == 0:
        return 0.0
    n = 1 + int((np.diff(idx) > gap).sum())
    return n / max(mask.sum() / STEPS_WEEK, 1e-9)


def bisect_threshold(make_flag, lo: float, hi: float, mask: np.ndarray, far: float, iters: int = 14) -> float:
    """Smallest threshold in [lo, hi] whose episode rate is at or below `far` (rate falls as it rises)."""
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        if episodes_per_week(make_flag(mid), mask) <= far:
            hi = mid
        else:
            lo = mid
    return hi


def fit_threshold(make_flag, grid: np.ndarray, mask: np.ndarray, far: float) -> float:
    """Smallest threshold on the calibration year whose episode rate is at or below `far` per week."""
    for t in grid:
        if episodes_per_week(make_flag(t), mask) <= far:
            return float(t)
    return float(grid[-1])


def run_site(sid: str, role: str, clean: str, fars, n_reps: int, seed: int) -> list[dict]:
    f = load_site_frame(sid, clean)
    v = f["do"]
    idx = v.index
    end = idx.max()
    t2, t1 = end - pd.Timedelta(days=365), end - pd.Timedelta(days=730)
    start_pos = int(idx.searchsorted(t2)) + 1

    X = base_features(f)
    cov_cols = [c for c in X.columns if not c.startswith("do")]
    LL = long_lag_features(v)
    ok = (idx <= t1) & v.notna().to_numpy() & LL["ll_anchor"].notna().to_numpy()
    sel = np.flatnonzero(ok)[::4]
    F_train = pd.concat([X[cov_cols], LL], axis=1)
    model_rows = idx > t1
    # fit once: the training data are clean, so the model is the same for every repetition
    model = HistGradientBoostingRegressor(loss="quantile", quantile=0.5, **PARAMS).fit(F_train.iloc[sel], v.to_numpy()[sel])
    q = pd.Series(model.predict(F_train.loc[model_rows]), index=F_train.index[model_rows])
    res_clean = hourly(v - q.reindex(idx))
    cal_h = (res_clean.index > t1) & (res_clean.index <= t2)
    floor = 0.05  # mg/L: below the sensor's 0.1 mg/L resolution, so no division by a vanishing spread

    def standardize(res_h: pd.Series) -> np.ndarray:
        return standardized(res_h, floor).to_numpy()

    z_cal = standardize(res_clean)
    cal_mask = np.asarray(cal_h) & np.isfinite(z_cal)
    detectors = {}
    for far in fars:
        h = fit_threshold(lambda t, z=z_cal: cusum_episodes(z, CUSUM_K, t), np.arange(1.0, 80.0, 0.5), cal_mask, far)
        e = fit_threshold(lambda t, z=z_cal: ewma_flags(z, EWMA_LAMBDA, t), np.arange(0.2, 6.0, 0.05), cal_mask, far)
        w = fit_threshold(lambda t, z=z_cal: _window_flags(z, 72, t), np.arange(0.3, 8.0, 0.05), cal_mask, far)
        detectors[far] = {"cusum": h, "ewma": e, "window72": w}

    rows: list[dict] = []
    for rep in range(n_reps):
        for kind, mags in (("drift", MAGS[rep % 3]), ("null", (0.0,))):
            if kind == "null" and rep % 3:  # one null repetition per three drift repetitions keeps cost down
                continue
            rng = np.random.default_rng(seed * 1000 + rep * 2 + (kind == "null"))
            v_c, events, _ = inject_drifts(v, rng, magnitudes=mags, durations_days=DURATIONS,
                                           per_cell=2 if kind == "null" else 1, start_pos=start_pos)
            fc = f.copy()
            fc["do"] = v_c
            Xc = base_features(fc)
            LLc = long_lag_features(v_c)
            Fc = pd.concat([Xc[cov_cols], LLc], axis=1).loc[model_rows]
            pred = pd.Series(model.predict(Fc), index=Fc.index)
            z = standardize(hourly(v_c - pred.reindex(idx)))
            hours = hourly(v_c).index
            near = np.zeros(len(hours), bool)
            for e in events:
                s_h = max(0, int((idx[e.start] - hours[0]) / pd.Timedelta("1h")))
                e_h = int((idx[min(e.end, len(idx) - 1)] - hours[0]) / pd.Timedelta("1h"))
                near[max(0, s_h - 24): e_h + ECHO_DAYS * 24] = True
            strict = (hours > t2) & ~near & np.isfinite(z)
            makers = {"cusum": lambda t, z=z: cusum_episodes(z, CUSUM_K, t),
                      "ewma": lambda t, z=z: ewma_flags(z, EWMA_LAMBDA, t),
                      "window72": lambda t, z=z: _window_flags(z, 72, t)}
            spans = {"cusum": (0.5, 200.0), "ewma": (0.05, 8.0), "window72": (0.05, 10.0)}
            for far, th in detectors.items():
                for ttype in ("deployable", "equal_footing"):
                    for name, make in makers.items():
                        t = th[name] if ttype == "deployable" else bisect_threshold(make, *spans[name], strict, far)
                        fl = make(t)
                        fp = episodes_per_week(fl, strict)
                        for e in events:
                            s_h = int((idx[e.start] - hours[0]) / pd.Timedelta("1h"))
                            e_h = int((idx[min(e.end, len(idx) - 1)] - hours[0]) / pd.Timedelta("1h"))
                            hit = np.flatnonzero(fl[s_h:e_h])
                            rows.append({"site": sid, "role": role, "rep": rep, "kind": kind, "far_per_week": far,
                                         "threshold_type": ttype, "detector": name, "magnitude": abs(e.magnitude),
                                         "duration_days": (e.end - e.start) / 96, "detected": bool(hit.size),
                                         "delay_h": float(hit[0]) if hit.size else np.nan, "clean_far_per_week": fp})
    return rows


def _window_flags(z: np.ndarray, hours: int, thr: float) -> np.ndarray:
    m = pd.Series(z).rolling(hours, min_periods=hours // 2).mean().to_numpy()
    return np.abs(m) > thr


def summarize(df: pd.DataFrame, target: float = 0.8) -> tuple[pd.DataFrame, pd.DataFrame]:
    d = df[df.kind == "drift"]
    n = df[df.kind == "null"]
    curve = (d.groupby(["far_per_week", "threshold_type", "detector", "duration_days", "magnitude"])
             .agg(detection=("detected", "mean"), median_delay_h=("delay_h", "median"), events=("detected", "size"))
             .reset_index())
    chance = (n.groupby(["far_per_week", "threshold_type", "detector", "duration_days"]).detected.mean()
              .rename("chance").reset_index())
    curve = curve.merge(chance, on=["far_per_week", "threshold_type", "detector", "duration_days"], how="left")
    rng = np.random.default_rng(0)
    rows = []
    for (far, ttype, det, dur), g in d.groupby(["far_per_week", "threshold_type", "detector", "duration_days"]):
        sites = g.site.unique()
        mags = sorted(g.magnitude.unique())

        def mdd(sample_sites, g=g, mags=mags):
            gg = pd.concat([g[g.site == s] for s in sample_sites])
            r = gg.groupby("magnitude").detected.mean()
            ok = [m for m in mags if m in r.index and r[m] >= target and (r[[x for x in r.index if x >= m]] >= target).all()]
            return min(ok) if ok else np.nan

        point = mdd(list(sites))
        boots = [mdd(list(rng.choice(sites, len(sites)))) for _ in range(200)]
        b = np.array(boots, float)
        rows.append({"far_per_week": far, "threshold_type": ttype, "detector": det, "duration_days": dur,
                     "min_detectable_mgL": point,
                     "ci_lo": np.nanquantile(b, 0.1) if np.isfinite(b).any() else np.nan,
                     "ci_hi": np.nanquantile(b, 0.9) if np.isfinite(b).any() else np.nan,
                     "share_of_bootstraps_detectable": float(np.isfinite(b).mean())})
    return curve, pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/sites.yaml")
    ap.add_argument("--clean", default="data/clean")
    ap.add_argument("--out", default="results")
    ap.add_argument("--reps", type=int, default=18)
    ap.add_argument("--far", type=float, nargs="+", default=[0.05, 0.2])
    args = ap.parse_args()
    cfg = yaml.safe_load(Path(args.config).read_text())
    rows = []
    for i, s in enumerate(cfg["sites"]):
        rows += run_site(s["id"], s["role"], args.clean, args.far, args.reps, seed=31 + i)
        print(f"{s['id']} ({s['role']}) done", flush=True)
    df = pd.DataFrame(rows)
    out = Path(args.out)
    df.to_csv(out / "drift_cusum_events.csv", index=False)
    curve, mdd = summarize(df)
    curve.to_csv(out / "drift_cusum_curve.csv", index=False)
    mdd.to_csv(out / "drift_cusum_mdd.csv", index=False)
    fp = df.drop_duplicates(["site", "rep", "far_per_week", "threshold_type", "detector"]) \
        .groupby(["far_per_week", "threshold_type", "detector"]).clean_far_per_week.mean()
    pd.set_option("display.width", 200)
    print("realized false-alarm episodes per site-week (clean test regions):\n", fp.round(3).to_string())
    print(mdd.round(2).to_string(index=False))
    track("drift-cusum", params={"far_per_week": args.far, "reps": args.reps, "k": CUSUM_K, "ewma_lambda": EWMA_LAMBDA,
                                 "durations_days": DURATIONS},
          metrics={f"mdd_{r.threshold_type}_{r.detector}_{r.duration_days:g}d_far{r.far_per_week}": r.min_detectable_mgL
                   for r in mdd.itertuples()},
          artifacts=[out / "drift_cusum_mdd.csv", out / "drift_cusum_curve.csv"], run_name="run_drift_cusum")


if __name__ == "__main__":
    main()
