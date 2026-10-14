"""Alert rules compared at a matched alert budget, including hybrids.

    python scripts/run_alert_budget.py [--folds 8] [--cal-days 365]

A single cost number mixes alert volume with accuracy. Here every rule family is swept over
its parameter and compared at fixed alerts-per-week budgets: the best anticipated recall each
family can reach without exceeding the budget (parameters chosen per site on the test window,
which is optimistic for every family equally).

Families:
  model     alert when calibrated P(DO < thr within 24 h) > c
  naive     alert when current DO < thr + margin
  hybrid    naive margin AND P > c_low   (the model vetoes the naive rule's false alarms)
Writes results/alert_budget_curves.csv and results/alert_budget_summary.csv.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from dosentinel.decision import episodes_from_series, evaluate_alerts
from dosentinel.direct import H, site_probability
from dosentinel.evaluate import walk_forward_folds
from dosentinel.features import load_site_frame

CUTOFFS = np.round(np.r_[0.01, 0.02, 0.03, 0.05, 0.07, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9], 3)
MARGINS = np.round(np.arange(0.0, 3.01, 0.25), 2)
HYBRID_CUTS = (0.03, 0.1, 0.3)
BUDGETS = (0.25, 0.5, 1.0, 2.0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/sites.yaml")
    ap.add_argument("--clean", default="data/clean")
    ap.add_argument("--out", default="results")
    ap.add_argument("--threshold", type=float, default=5.0)
    ap.add_argument("--folds", type=int, default=8)
    ap.add_argument("--cal-days", type=int, default=365)
    ap.add_argument("--stride", type=int, default=4)
    args = ap.parse_args()
    thr = args.threshold

    cfg = yaml.safe_load(Path(args.config).read_text())
    rows = []
    for site in cfg["sites"]:
        sid = site["id"]
        f = load_site_frame(sid, args.clean)
        v = f["do"]
        folds = walk_forward_folds(v.index, n_folds=args.folds)
        P = site_probability(f, thr, folds, args.stride, args.cal_days)
        tl = v.index[(v.index > folds[0][2]) & (v.index <= v.index.max() - pd.Timedelta("24h"))]
        p = P["p"].reindex(tl).fillna(0.0).to_numpy()
        flags = pd.read_parquet(f"{args.clean}/{sid}_00300.parquet")
        bad = flags[["out_of_range", "flatline", "spike"]].any(axis=1)
        suspect = (bad.rolling(8, min_periods=1).max().astype(bool) | v.isna()).reindex(tl).to_numpy()
        episodes = [e for e in episodes_from_series(v.loc[tl[0]: tl[-1] + pd.Timedelta("24h")], thr) if e[0] < len(tl)]
        v_now = v.reindex(tl).to_numpy()
        not_low = v_now >= thr

        def ev(alert: np.ndarray) -> dict:
            return evaluate_alerts(episodes, alert & ~suspect, horizon_steps=H, not_low=not_low)

        base = {"site": sid, "role": site["role"], "episodes": len(episodes)}
        for c in CUTOFFS:
            rows.append({**base, "family": "model", "param": f"c={c}", **ev(p > c)})
        for m in MARGINS:
            rows.append({**base, "family": "naive", "param": f"m={m}", **ev(v_now < thr + m)})
        for cl in HYBRID_CUTS:
            for m in MARGINS:
                rows.append({**base, "family": f"hybrid_c{cl}", "param": f"m={m}", **ev((v_now < thr + m) & (p > cl))})
        print(f"{sid} ({site['role']}): {len(episodes)} episodes", flush=True)

    out = Path(args.out)
    out.mkdir(exist_ok=True)
    curves = pd.DataFrame(rows)
    curves.to_csv(out / "alert_budget_curves.csv", index=False)

    # best anticipated recall per site, family and budget (ties: higher precision)
    summ = []
    for (site, role, fam), g in curves.groupby(["site", "role", "family"]):
        g = g.dropna(subset=["recall_anticipated"])
        for B in BUDGETS:
            ok = g[g["alerts_per_week"] <= B]
            if ok.empty:
                summ.append({"site": site, "role": role, "family": fam, "budget": B, "recall": 0.0,
                             "precision": np.nan, "alerts_per_week": 0.0, "median_lead_h": np.nan, "param": "none"})
                continue
            best = ok.sort_values(["recall_anticipated", "cluster_precision"], ascending=False).iloc[0]
            summ.append({"site": site, "role": role, "family": fam, "budget": B,
                         "recall": best["recall_anticipated"], "precision": best["cluster_precision"],
                         "alerts_per_week": best["alerts_per_week"], "median_lead_h": best["median_lead_h"],
                         "param": best["param"]})
    pd.DataFrame(summ).to_csv(out / "alert_budget_summary.csv", index=False)


if __name__ == "__main__":
    main()
