"""Drift-detection benchmark with long-lag-anchored detectors.

    python scripts/run_drift.py [--reps 5] [--fpr 0.001] [--control]

Drift-only faults (magnitude 0.5/1/2 mg/L x duration 3/7/14 days, ramping then snapping back)
are injected into the final year of each site. The proposed detectors predict DO from today's
covariates plus DO values from 15-21 days ago (a level anchor that drifts shorter than 15 days
have not yet contaminated) and flag a sustained residual over 1, 3 and 7 days. Comparators:
the 1-hour forecast interval and a covariate-only model with no DO anchor.

Thresholds: "deployable" = set on the clean calibration year; "oracle" = set on the test
year's own normal steps (equal-footing capability). Writes results/drift_*.csv.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from dosentinel.detect import interval_score, threshold_at_rate
from dosentinel.faults import inject_drifts
from dosentinel.features import (base_features, horizon_features, load_site_frame, long_lag_features,
                                 train_origin_mask)
from dosentinel.models import fit_predict_quantiles

WINDOWS_H = (24, 72, 168)
DETECTORS = [f"ll_{w}h" for w in WINDOWS_H] + ["cov_72h", "interval_1h", "ll_combined"]
ECHO_DAYS = 22  # lag features carry a drift into forecasts for up to 21 days after it ends


def _windowed(resid: pd.Series, hours: int) -> pd.Series:
    n = hours * 4
    return resid.rolling(n, min_periods=n // 2).mean()


def run_site(sid: str, role: str, clean_dir: str, fpr: float, n_reps: int, per_cell: int, seed: int,
             magnitudes=(0.5, 1.0, 2.0)):
    f = load_site_frame(sid, clean_dir)
    v = f["do"]
    idx = v.index
    end = idx.max()
    t2, t1 = end - pd.Timedelta(days=365), end - pd.Timedelta(days=730)
    cal = (idx > t1) & (idx <= t2)
    test = idx > t2
    start_pos = int(idx.searchsorted(t2)) + 1

    X = base_features(f)
    cov_cols = [c for c in X.columns if not c.startswith("do")]
    LL = long_lag_features(v)
    feats_ll = pd.concat([X[cov_cols], LL], axis=1)
    ok = ((idx <= t1) & v.notna().to_numpy() & LL["ll_anchor"].notna().to_numpy())
    sel_ll = np.flatnonzero(ok)[::4]
    sel_cov = np.flatnonzero((idx <= t1) & v.notna().to_numpy())[::4]
    delta = (v.shift(-4) - v).to_numpy()
    sel_i = np.flatnonzero(train_origin_mask(idx, 4, t1) & ~np.isnan(delta))[::4]
    Z4_clean = horizon_features(X, v, 4)
    rows = idx > t1

    ev_rows, fp_rows, noise = [], [], {}
    thr_dep: dict[str, float] = {}
    for rep in range(n_reps):
        rng = np.random.default_rng(seed * 100 + rep)
        v_c, events, _ = inject_drifts(v, rng, magnitudes=magnitudes, per_cell=per_cell, start_pos=start_pos)
        fc = f.copy()
        fc["do"] = v_c
        Xc = base_features(fc)
        LLc = long_lag_features(v_c)

        p_ll = fit_predict_quantiles(feats_ll.iloc[sel_ll], v.to_numpy()[sel_ll],
                                     pd.concat([Xc[cov_cols], LLc], axis=1).loc[rows], quantiles=(0.5,))["q50"]
        p_cov = fit_predict_quantiles(X[cov_cols].iloc[sel_cov], v.to_numpy()[sel_cov],
                                      Xc.loc[rows, cov_cols], quantiles=(0.5,))["q50"]
        q = fit_predict_quantiles(Z4_clean.iloc[sel_i], delta[sel_i], horizon_features(Xc, v_c, 4).loc[rows])
        base = v_c.loc[rows]
        q10 = (base + q["q10"]).shift(4).reindex(idx)
        q90 = (base + q["q90"]).shift(4).reindex(idx)

        res_ll = (v_c - p_ll.reindex(idx))
        res_cov = (v_c - p_cov.reindex(idx))
        S = pd.DataFrame(index=idx)
        for w in WINDOWS_H:
            r = _windowed(res_ll, w)
            med = r[cal].median()
            sc = 1.4826 * (r[cal] - med).abs().median()
            S[f"ll_{w}h"] = (r - med).abs() / max(sc, 0.05)
            if w == 72:
                noise["resid72_scale_mgL"] = float(sc)
        r = _windowed(res_cov, 72)
        med = r[cal].median()
        S["cov_72h"] = (r - med).abs() / max(1.4826 * (r[cal] - med).abs().median(), 0.05)
        S["interval_1h"] = interval_score(v_c, q10, q90)
        S["ll_combined"] = pd.concat(
            [S[f"ll_{w}h"] / max(threshold_at_rate(S.loc[cal, f"ll_{w}h"], fpr / len(WINDOWS_H)), 1e-6)
             for w in WINDOWS_H], axis=1).max(axis=1)

        near = np.zeros(len(idx), bool)
        for e in events:
            near[max(0, e.start - 96): e.end + ECHO_DAYS * 96] = True
        strict = test & ~near & v_c.notna().to_numpy()
        weeks = strict.sum() / 672
        if rep == 0:
            for d in DETECTORS:
                thr_dep[d] = (1.0 if d == "ll_combined" else threshold_at_rate(S.loc[cal, d], fpr))
        for d in DETECTORS:
            for kind, thr in (("deployable", thr_dep[d]),
                              ("oracle", 1.0 if d == "ll_combined" and False else threshold_at_rate(S.loc[strict, d], fpr))):
                fl = (S[d] > thr).to_numpy()
                fp_rows.append({"site": sid, "role": role, "rep": rep, "detector": d, "threshold_type": kind,
                                "false_flags_per_week": float(fl[strict].sum() / weeks)})
                for e in events:
                    hit = np.flatnonzero(fl[e.start:e.end])
                    ev_rows.append({"site": sid, "role": role, "rep": rep, "detector": d, "threshold_type": kind,
                                    "magnitude": abs(e.magnitude), "duration_days": (e.end - e.start) / 96,
                                    "detected": bool(hit.size),
                                    "delay_h": float(hit[0] * 0.25) if hit.size else np.nan})
    n = pd.DataFrame(ev_rows)
    return n, pd.DataFrame(fp_rows), noise


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/sites.yaml")
    ap.add_argument("--clean", default="data/clean")
    ap.add_argument("--out", default="results")
    ap.add_argument("--fpr", type=float, default=0.001)
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--per-cell", type=int, default=1)
    ap.add_argument("--control", action="store_true", help="no faults; measure false flags only")
    ap.add_argument("--null", action="store_true",
                    help="zero-magnitude 'drifts': same windows, no perturbation; detection here is pure chance")
    args = ap.parse_args()
    cfg = yaml.safe_load(Path(args.config).read_text())
    out = Path(args.out)
    out.mkdir(exist_ok=True)
    ev, fp, nz = [], [], []
    for i, s in enumerate(cfg["sites"]):
        e, p, noise = run_site(s["id"], s["role"], args.clean, args.fpr,
                               1 if args.control else args.reps, 0 if args.control else args.per_cell, seed=7 + i,
                               magnitudes=(0.0,) if args.null else (0.5, 1.0, 2.0))
        ev.append(e)
        fp.append(p)
        nz.append({"site": s["id"], "role": s["role"], **noise})
        print(f"{s['id']} ({s['role']}): {len(e) // max(1, len(DETECTORS) * 2)} drift events", flush=True)
    sfx = "_control" if args.control else ("_null" if args.null else "")
    pd.concat(ev).to_csv(out / f"drift_events{sfx}.csv", index=False)
    pd.concat(fp).to_csv(out / f"drift_false_flags{sfx}.csv", index=False)
    pd.DataFrame(nz).to_csv(out / f"drift_noise{sfx}.csv", index=False)


if __name__ == "__main__":
    main()
