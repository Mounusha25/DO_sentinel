"""Alerting on the forecast's lower bound instead of a classifier probability.

    python scripts/run_alert_bound.py

A probability classifier did not clearly beat "alert when DO < threshold + margin". A different
use of the same forecasts: alert when the lowest 10th-percentile forecast over the next 24 h
(the lower end of the 80% interval, at 1/3/6/12/24 h) falls below threshold + offset. Families,
all evaluated on the same 4-hourly origins of the last year (results/chronos2_rows.parquet):

  naive        current DO < thr + m
  bound_gbm    min over horizons of the weather GBM's q10 < thr + m
  bound_blend  min over horizons of the Chronos-2 + GBM blend's q10 < thr + m
  *_cal        the same with the walk-forward conformalized lower bound (what the API serves);
               needs an earlier fold to calibrate on, so every family is scored on folds 2-4 only
  normal_*     P(DO < thr) at the worst horizon, from the conformalized 80% interval read as a
               normal (blend.prob_below), against the cost-derived cutoff c = 1 / (1 + miss:FA).
               The API uses it only where a site has no trained classifier. "gbm" is the no-weather
               GBM, "gbm_wx" the weather one.
  model_prob   the direct "any low DO within 24 h" classifier (calibrated, 365-day window)
               with P > c, on the same folds and origins; swept over c instead of m

Each family is swept over m and compared at fixed alerts-per-week budgets (best anticipated
recall without exceeding the budget; m chosen per site on the test window, optimistic for every
family equally). Episode scoring is `dosentinel.decision.evaluate_alerts`. Writes
results/alert_bound_summary.csv and results/alert_bound_paired.csv.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, "src")
sys.path.insert(0, str(Path(__file__).parent))
from run_blend import calibrated, quantiles  # noqa: E402

from dosentinel.blend import blend_quantiles, prob_below  # noqa: E402
from dosentinel.decision import episodes_from_series, evaluate_alerts  # noqa: E402
from dosentinel.direct import H, site_probability  # noqa: E402
from dosentinel.evaluate import walk_forward_folds  # noqa: E402
from dosentinel.features import load_site_frame  # noqa: E402
from dosentinel.stats import mean_diff_ci, sign_test_p  # noqa: E402

HORIZONS = (1, 3, 6, 12, 24)
MARGINS = np.round(np.arange(-1.0, 3.01, 0.25), 2)
CUTOFFS = np.round(np.r_[0.01, 1 / 11, 0.02, 0.03, 0.05, 0.07, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9], 3)
BUDGETS = (0.25, 0.5, 1.0, 2.0)


def origin_table(d: pd.DataFrame, thr: float = 5.0) -> pd.DataFrame:
    """One row per origin: current DO, the min-over-horizons lower bound of each forecaster
    (raw and conformalized) and the max-over-horizons normal-approximation P(DO < thr)."""
    cols: dict[str, list] = {k: [] for k in ("gbm", "blend", "gbm_cal", "blend_cal")}
    probs: dict[str, list] = {k: [] for k in ("gbm_wx", "blend", "gbm")}
    y_origin = None
    for h, g in d.groupby("horizon_h"):
        g = g.reset_index(drop=True)
        gbm, ch = quantiles(g, "gbmwx"), quantiles(g, "wx")
        bl = blend_quantiles(gbm, ch)
        for name, q in (("gbm", gbm), ("blend", bl)):
            cols[name].append(pd.Series(q["q10"].to_numpy(), index=g["origin"]))
            cq = calibrated(g, q, "expanding", h)
            cols[name + "_cal"].append(pd.Series(cq["q10"].to_numpy(), index=g["origin"]))
            if name == "gbm":
                probs["gbm_wx"].append(pd.Series(prob_below(cq["q10"], cq["q50"], cq["q90"], thr), index=g["origin"]))
            else:
                probs["blend"].append(pd.Series(prob_below(cq["q10"], cq["q50"], cq["q90"], thr), index=g["origin"]))
        nw = calibrated(g, quantiles(g, "gbm"), "expanding", h)  # no-weather GBM, conformalized
        probs["gbm"].append(pd.Series(prob_below(nw["q10"], nw["q50"], nw["q90"], thr), index=g["origin"]))
        y_origin = pd.Series(g["y_origin"].to_numpy(), index=g["origin"])
    t = pd.DataFrame({"y_origin": y_origin})
    for name, parts in cols.items():
        t[f"bound_{name}"] = pd.concat(parts, axis=1).min(axis=1, skipna=False)
    for name, parts in probs.items():
        t[f"normal_{name}"] = pd.concat(parts, axis=1).max(axis=1, skipna=False)
    return t


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", default="results/chronos2_rows.parquet")
    ap.add_argument("--clean", default="data/clean")
    ap.add_argument("--out", default="results")
    ap.add_argument("--threshold", type=float, default=5.0)
    args = ap.parse_args()
    thr = args.threshold
    raw = pd.read_parquet(args.rows)
    summ, curves = [], []
    # the folds run_chronos2.py used: four 91-day windows ending at the earliest site's last timestamp
    end = min(load_site_frame(sid, args.clean).index.max() for sid in raw["site"].unique())
    folds = walk_forward_folds(pd.date_range(end=end, periods=2, freq="15min"), n_folds=4)
    for sid, d in raw.groupby("site"):
        t = origin_table(d, thr).dropna()
        v = load_site_frame(sid, args.clean)["do"]
        lo, hi = t.index.min(), t.index.max()
        tl = v.index[(v.index >= lo) & (v.index <= hi)]
        if len(tl) == 0 or t.empty:
            continue
        pos = tl.get_indexer(t.index)
        keep = pos >= 0
        pos = pos[keep]
        t = t[keep]
        episodes = [e for e in episodes_from_series(v.loc[tl[0]: tl[-1] + pd.Timedelta("24h")], thr) if e[0] < len(tl)]
        not_low = (v.reindex(tl).to_numpy() >= thr)
        for fam, col in (("naive", "y_origin"), ("bound_gbm", "bound_gbm"), ("bound_blend", "bound_blend"),
                         ("bound_gbm_cal", "bound_gbm_cal"), ("bound_blend_cal", "bound_blend_cal")):
            for m in MARGINS:
                alert = np.zeros(len(tl), bool)
                alert[pos] = t[col].to_numpy() < thr + m
                r = evaluate_alerts(episodes, alert, horizon_steps=H, not_low=not_low)
                curves.append({"site": sid, "family": fam, "margin": m, "episodes": len(episodes), **r})
        for fam in ("normal_gbm", "normal_gbm_wx", "normal_blend"):
            for c in CUTOFFS:
                alert = np.zeros(len(tl), bool)
                alert[pos] = t[fam].to_numpy() > c
                r = evaluate_alerts(episodes, alert, horizon_steps=H, not_low=not_low)
                curves.append({"site": sid, "family": fam, "margin": c, "episodes": len(episodes), **r})
        P = site_probability(load_site_frame(sid, args.clean), thr, folds, 4, 365)
        prob = P["p"].reindex(t.index).fillna(0.0).to_numpy()
        for c in CUTOFFS:
            alert = np.zeros(len(tl), bool)
            alert[pos] = prob > c
            r = evaluate_alerts(episodes, alert, horizon_steps=H, not_low=not_low)
            curves.append({"site": sid, "family": "model_prob", "margin": c, "episodes": len(episodes), **r})
    curves = pd.DataFrame(curves)
    curves.to_csv(Path(args.out) / "alert_bound_curves.csv", index=False)
    for (sid, fam), g in curves.groupby(["site", "family"]):
        g = g.dropna(subset=["recall_anticipated"])
        for B in BUDGETS:
            ok = g[g["alerts_per_week"] <= B]
            if ok.empty:
                summ.append({"site": sid, "family": fam, "budget": B, "recall": 0.0, "precision": np.nan,
                             "alerts_per_week": 0.0, "margin": np.nan})
                continue
            b = ok.sort_values(["recall_anticipated", "cluster_precision"], ascending=False).iloc[0]
            summ.append({"site": sid, "family": fam, "budget": B, "recall": b["recall_anticipated"],
                         "precision": b["cluster_precision"], "alerts_per_week": b["alerts_per_week"],
                         "margin": b["margin"]})
    s = pd.DataFrame(summ)
    out = Path(args.out)
    s.to_csv(out / "alert_bound_summary.csv", index=False)

    rows = []
    wide = s.pivot_table(index=["site", "budget"], columns="family", values="recall").reset_index()
    for B, g in wide.groupby("budget"):
        for fam in ("bound_gbm", "bound_blend", "bound_gbm_cal", "bound_blend_cal", "normal_gbm", "normal_gbm_wx",
                    "normal_blend", "model_prob"):
            diff = (g[fam] - g["naive"]).to_numpy()
            m, lo, hi = mean_diff_ci(diff)
            wins, losses = int((diff > 1e-9).sum()), int((diff < -1e-9).sum())
            rows.append({"budget": B, "family": fam, "mean_recall": g[fam].mean(), "naive_recall": g["naive"].mean(),
                         "paired_diff": m, "ci_lo": lo, "ci_hi": hi, "wins": wins, "losses": losses,
                         "sign_test_p": sign_test_p(wins, wins + losses), "n_sites": len(g)})
    p = pd.DataFrame(rows)
    p.to_csv(out / "alert_bound_paired.csv", index=False)

    # untuned comparison: the parameter each rule would have by construction, no test-window sweep
    fixed = {"naive": 0.5, "bound_gbm_cal": 0.0, "bound_blend_cal": 0.0, "normal_gbm": 1 / 11,
             "normal_gbm_wx": 1 / 11, "normal_blend": 1 / 11, "model_prob": 1 / 11}
    rows = []
    for fam, m in fixed.items():
        g = curves[(curves.family == fam) & np.isclose(curves.margin, m, atol=1e-3)]
        rows.append(g.assign(rule=fam))
    f = pd.concat(rows)
    cost = f.pivot_table(index="site", columns="rule", values="cost_per_week")
    tab = f.groupby("rule").agg(recall=("recall_anticipated", "mean"), precision=("cluster_precision", "mean"),
                                alerts_per_week=("alerts_per_week", "mean"), cost_per_week=("cost_per_week", "mean"),
                                median_lead_h=("median_lead_h", "median"), sites=("site", "nunique"))
    tab["cheaper_than_naive_sites"] = [int((cost[r] < cost["naive"]).sum()) if r != "naive" else np.nan for r in tab.index]
    for r in tab.index:
        if r != "naive":
            m_, lo_, hi_ = mean_diff_ci((cost["naive"] - cost[r]).to_numpy())
            tab.loc[r, ["cost_saving_vs_naive", "saving_ci_lo", "saving_ci_hi"]] = (m_, lo_, hi_)
    tab.to_csv(out / "alert_bound_fixed.csv")
    pd.set_option("display.width", 200)
    print(p.round(3).to_string(index=False))
    print()
    print(tab.round(3).to_string())


if __name__ == "__main__":
    main()
