"""Sensor-fault detection benchmark on synthetic injected faults.

    python scripts/run_faults.py [--fpr 0.001]

Per site: models are trained on data up to (end - 2 years); thresholds are set on the clean
year (end - 2y, end - 1y] so each detector flags at most `--fpr` of normal steps; faults are
injected only into the final year and scored with causal detectors. Writes
results/faults_events.csv (one row per fault event x detector) and
results/faults_site_summary.csv (false-flag rate and AUROC per site x detector).
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from sklearn.metrics import roc_auc_score

from dosentinel.baselines import persistence_diel_delta
from dosentinel.detect import (
    interval_score,
    persistence_residual_online,
    persistence_residual_score,
    residual_score,
    robust_scale,
    rules_flag,
    run_length,
    threshold_at_rate,
)
from dosentinel.faults import inject_faults
from dosentinel.features import base_features, horizon_features, load_site_frame, train_origin_mask
from dosentinel.models import fit_predict_quantiles
from dosentinel.validate import RULES, Rule

DETECTORS = ["rules", "rules_sensitive", "persistence_resid", "persistence_resid_es", "interval_1h",
             "run_length", "covariate_resid", "covariate_drift", "combined"]
SCORED = ["persistence_resid", "interval_1h", "run_length", "covariate_resid", "covariate_drift"]
SENSITIVE = Rule(lo=0.0, hi=25.0, max_step=1.0, flat_steps=24)  # 6 h flatline, 1 mg/L step
GRACE = 8  # steps after a fault ends excluded from the normal pool (a flag there is not "detection")
NEAR = 8  # steps around a fault excluded from the "normal" pool


def run_site(sid: str, role: str, clean_dir: str, fpr: float, seed: int, counts: dict | None = None):
    f = load_site_frame(sid, clean_dir)
    v = f["do"]
    idx = v.index
    end = idx.max()
    t2, t1 = end - pd.Timedelta(days=365), end - pd.Timedelta(days=730)
    cal_rows = (idx > t1) & (idx <= t2)
    test_rows = idx > t2
    start_pos = int(idx.searchsorted(t2)) + 1

    # ---- models trained on clean data up to t1 only
    X = base_features(f)
    delta = (v.shift(-4) - v).to_numpy()
    sel = np.flatnonzero(train_origin_mask(idx, 4, t1) & ~np.isnan(delta))[::4]
    cov_cols = [c for c in X.columns if not c.startswith("do")]
    sel6 = np.flatnonzero((idx <= t1) & v.notna().to_numpy())[::4]

    # ---- inject faults into the final year only
    v_c, events, label = inject_faults(v, np.random.default_rng(seed), counts=counts, start_pos=start_pos)
    fc = f.copy()
    fc["do"] = v_c
    Xc = base_features(fc)
    rows = idx > t1
    Zc = horizon_features(Xc, v_c, 4)

    q = fit_predict_quantiles(horizon_features(X, v, 4).iloc[sel], delta[sel], Zc.loc[rows])
    base = v_c.loc[rows]
    q10 = (base + q["q10"]).shift(4).reindex(idx)  # forecast for time t issued at t-4
    q90 = (base + q["q90"]).shift(4).reindex(idx)

    m6 = fit_predict_quantiles(X[cov_cols].iloc[sel6], v.to_numpy()[sel6], Xc.loc[rows, cov_cols], quantiles=(0.5,))
    pred6 = m6["q50"].reindex(idx)
    r6_cal = (v - pred6)[cal_rows].abs().dropna()
    scale6 = float(1.4826 * r6_cal.median()) if len(r6_cal) else 0.5

    S = pd.DataFrame(index=idx)
    S["persistence_resid"] = persistence_residual_score(v_c)
    S["interval_1h"] = interval_score(v_c, q10, q90)
    S["run_length"] = run_length(v_c)
    S["covariate_resid"] = residual_score(v_c, pred6, scale6)
    S["covariate_drift"] = residual_score(v_c, pred6, scale6, smooth=96)
    thr = {c: threshold_at_rate(S.loc[cal_rows, c], fpr) for c in SCORED}
    thr_c = {c: max(threshold_at_rate(S.loc[cal_rows, c], fpr / len(SCORED)), 1e-6) for c in SCORED}
    flags = pd.DataFrame(index=idx)
    for c in SCORED:
        flags[c] = S[c] > thr[c]
    S["combined"] = pd.concat([S[c] / thr_c[c] for c in SCORED], axis=1).max(axis=1)
    flags["combined"] = S["combined"] > 1.0
    flags["rules"] = rules_flag(v_c, RULES["00300"])
    flags["rules_sensitive"] = rules_flag(v_c, SENSITIVE)
    # echo-suppressed persistence detector: flagged steps (its own, plus jump/flatline flags from
    # detectors that do not use 24 h history) are masked out of the history later forecasts use
    raw_resid = v_c - persistence_diel_delta(v_c, 4).shift(4)
    scale_es = robust_scale(raw_resid)
    ext = flags["run_length"] | flags["rules"]
    es = persistence_residual_online(v_c, thr["persistence_resid"], scale_es, external=ext)
    flags["persistence_resid_es"] = es["flag"]

    # ---- evaluate on the test year
    in_test = test_rows
    near = np.zeros(len(idx), bool)
    for e in events:
        near[max(0, e.start - NEAR): e.end + GRACE + NEAR] = True
    normal = in_test & ~near & v_c.notna().to_numpy()
    echo = np.zeros(len(idx), bool)  # lag-24h features carry a fault into the next day's forecasts
    for e in events:
        echo[max(0, e.start - NEAR): e.end + 96 + GRACE + NEAR] = True
    strict = in_test & ~echo & v_c.notna().to_numpy()
    weeks, weeks_strict = normal.sum() / 672, strict.sum() / 672
    ev_rows, site_rows = [], []
    kind_of_step = np.array([None] * len(idx), dtype=object)
    for e in events:
        kind_of_step[e.start:e.end] = e.kind
    for d in DETECTORS:
        fl = flags[d].to_numpy()
        site_rows.append({"site": sid, "role": role, "detector": d,
                          "false_flags_per_week": float(fl[normal].sum() / weeks),
                          "false_flags_per_week_strict": float(fl[strict].sum() / weeks_strict),
                          "false_flag_rate": float(fl[normal].mean()),
                          "threshold": thr.get(d, np.nan)})
        for e in events:
            win = slice(e.start, e.end)  # must be flagged while the fault is active
            hit = np.flatnonzero(fl[win])
            ev_rows.append({"site": sid, "role": role, "detector": d, "kind": e.kind, "severity": e.severity,
                            "magnitude": e.magnitude, "duration_steps": e.end - e.start,
                            "detected": bool(hit.size), "delay_h": float(hit[0] * 0.25) if hit.size else np.nan})
    # "oracle": thresholds set on the test year's own normal steps. This is what each detector
    # could achieve with well-transferred thresholds, and puts them on equal false-flag footing.
    for d in SCORED + ["combined"]:
        t_o = threshold_at_rate(S.loc[strict, d], fpr)
        fl = (S[d] > t_o).to_numpy()
        site_rows.append({"site": sid, "role": role, "detector": d + "@oracle",
                          "false_flags_per_week": float(fl[normal].sum() / weeks),
                          "false_flags_per_week_strict": float(fl[strict].sum() / weeks_strict),
                          "false_flag_rate": float(fl[normal].mean()), "threshold": t_o})
        for e in events:
            hit = np.flatnonzero(fl[e.start: e.end])
            ev_rows.append({"site": sid, "role": role, "detector": d + "@oracle", "kind": e.kind,
                            "severity": e.severity, "magnitude": e.magnitude, "duration_steps": e.end - e.start,
                            "detected": bool(hit.size), "delay_h": float(hit[0] * 0.25) if hit.size else np.nan})
    es0 = persistence_residual_online(v_c, thr["persistence_resid"], scale_es, external=ext)
    t_es = threshold_at_rate(es0.loc[strict, "score"], fpr)
    fl = persistence_residual_online(v_c, t_es, scale_es, external=ext)["flag"].to_numpy()
    site_rows.append({"site": sid, "role": role, "detector": "persistence_resid_es@oracle",
                      "false_flags_per_week": float(fl[normal].sum() / weeks),
                      "false_flags_per_week_strict": float(fl[strict].sum() / weeks_strict),
                      "false_flag_rate": float(fl[normal].mean()), "threshold": t_es})
    for e in events:
        hit = np.flatnonzero(fl[e.start: e.end])
        ev_rows.append({"site": sid, "role": role, "detector": "persistence_resid_es@oracle", "kind": e.kind,
                        "severity": e.severity, "magnitude": e.magnitude, "duration_steps": e.end - e.start,
                        "detected": bool(hit.size), "delay_h": float(hit[0] * 0.25) if hit.size else np.nan})
    # threshold-free separability: AUROC of the continuous scores, per kind
    for d in SCORED + ["combined"]:
        sc = S[d].replace([np.inf, -np.inf], np.nan).fillna(0.0).to_numpy()
        for kind in sorted({e.kind for e in events}):
            fault = in_test & (kind_of_step == kind)
            if fault.sum() == 0 or strict.sum() == 0:
                continue
            y = np.r_[np.ones(fault.sum()), np.zeros(strict.sum())]
            auc = roc_auc_score(y, np.r_[sc[fault], sc[strict]])
            site_rows.append({"site": sid, "role": role, "detector": d, "kind": kind, "auroc": float(auc)})
    return pd.DataFrame(ev_rows), pd.DataFrame(site_rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/sites.yaml")
    ap.add_argument("--clean", default="data/clean")
    ap.add_argument("--out", default="results")
    ap.add_argument("--fpr", type=float, default=0.001, help="allowed false-flag rate per detector on clean data")
    ap.add_argument("--control", action="store_true", help="inject no faults; measures false-flag drift between years")
    args = ap.parse_args()
    cfg = yaml.safe_load(Path(args.config).read_text())
    out = Path(args.out)
    out.mkdir(exist_ok=True)
    ev, st = [], []
    for i, s in enumerate(cfg["sites"]):
        counts = {k: 0 for k in ("spike", "offset", "drift", "flatline", "stuck_low")} if args.control else None
        e, t = run_site(s["id"], s["role"], args.clean, args.fpr, seed=1000 + i, counts=counts)
        ev.append(e)
        st.append(t)
        print(f"{s['id']} ({s['role']}): {e.drop_duplicates(['kind','severity','magnitude','duration_steps']).shape[0]} fault events scored", flush=True)
    sfx = "_control" if args.control else ""
    pd.concat(ev).to_csv(out / f"faults_events{sfx}.csv", index=False)
    pd.concat(st).to_csv(out / f"faults_site_summary{sfx}.csv", index=False)


if __name__ == "__main__":
    main()
