"""Alert rules with cutoffs fitted on the first year to a fixed alert budget, scored on the second.

    python scripts/run_budget_fit.py [--budget 0.5] [--folds 8]

The earlier budget comparisons (run_alert_budget.py, run_alert_bound.py) chose each rule's parameter
on the test window itself, which flatters every rule. Here, per site and rule:

  1. folds 1-4 (first year) are the calibration period: pick the most sensitive cutoff whose alert
     rate does not exceed the budget (alert clusters per week, cooldown 6 h);
  2. freeze it; apply it unchanged to folds 5-8 (second year);
  3. report what actually happened out of sample: alerts per week (does the budget hold?),
     anticipated recall, cluster precision, and cost per week at 10:1.

Rules (score above cutoff means alert; origins are 4-hourly):
  naive         current DO below threshold + margin
  classifier    calibrated direct P(DO < threshold within 24 h)
  interval      worst-horizon z-score of the threshold under the conformalized 80% interval, read as a normal
                (monotone in P(DO < threshold); the z-scale keeps ranking where the probability saturates at 1)
  average       mean of the classifier's and the interval rule's log-odds (logistic scale: logit p = 1.702 z)
  pooled        classifier trained on the other 13 sites only (usable where a site has no events of its own)

Sensitivity of the alert count to the cooldown (6 h vs 24 h) is reported too. Writes
results/budget_fit_*.csv.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from joblib import Parallel, delayed

from dosentinel.baselines import HORIZONS_H
from dosentinel.blend import Z90
from dosentinel.calibration import cqr_correction
from dosentinel.decision import Costs, alert_clusters, episodes_from_series, evaluate_alerts
from dosentinel.direct import H, site_probability, within_label
from dosentinel.evaluate import walk_forward_folds
from dosentinel.features import (
    SITE_SPECIFIC,
    base_features,
    horizon_features,
    load_site_frame,
    train_origin_mask,
)
from dosentinel.models import fit_predict_proba, fit_predict_quantiles
from dosentinel.tracking import track

RULES = ("naive", "classifier", "interval", "average", "pooled")
STEP_WEEK = 7 * 96


def site_inputs(sid: str, clean: str, thr: float, folds, stride: int) -> dict:
    f = load_site_frame(sid, clean)
    v = f["do"]
    idx = v.index
    X = base_features(f)
    origins = idx[(idx > folds[0][2]) & (idx <= idx.max() - pd.Timedelta("24h"))][::16]
    return {"sid": sid, "f": f, "v": v, "idx": idx, "X": X, "origins": origins,
            "y": within_label(v, thr).to_numpy()}


def interval_z(d: dict, thr: float, folds, stride: int) -> pd.Series:
    """max over horizons of the z-score of `thr` under the conformalized quantile forecast
    ((thr - median) / sd, sd from the 80% interval); a monotone transform of P(DO < thr)."""
    v, idx, X, origins = d["v"], d["idx"], d["X"], d["origins"]
    per_h = []
    for hh in HORIZONS_H:
        h = hh * 4
        Z = horizon_features(X, v, h)
        delta = (v.shift(-h) - v).to_numpy()
        fold_q = {}
        for fold, _, t0, t1 in folds:
            rows = origins[(origins > t0) & (origins <= t1)]
            if len(rows) == 0:
                continue
            tr = np.flatnonzero(train_origin_mask(idx, h, t0) & ~np.isnan(delta))[::stride]
            p = fit_predict_quantiles(Z.iloc[tr], delta[tr], Z.loc[rows])
            base = v.loc[rows].to_numpy()
            fold_q[fold] = (rows, pd.DataFrame({k: base + p[k].to_numpy() for k in ("q10", "q50", "q90")}, index=rows))
        out = []
        for fold, (rows, q) in fold_q.items():
            m = 0.0
            if fold - 1 in fold_q:  # margin from the previous fold's own errors, all known at this fold's start
                prow, pq = fold_q[fold - 1]
                y = v.shift(-h).reindex(prow).to_numpy()
                ok = ~np.isnan(y) & pq["q50"].notna().to_numpy()
                if ok.sum() > 50:
                    m = cqr_correction(y[ok], pq["q10"].to_numpy()[ok], pq["q90"].to_numpy()[ok], alpha=0.2)
            lo = np.minimum(q["q10"] - m, q["q50"])
            hi = np.maximum(q["q90"] + m, q["q50"])
            sd = np.maximum((hi - lo) / (2 * Z90), 1e-3)
            out.append(pd.Series(((thr - q["q50"]) / sd).to_numpy(), index=rows))
        per_h.append(pd.concat(out))
    return pd.concat(per_h, axis=1).max(axis=1, skipna=True)


def pooled_prob(sid: str, inputs: dict[str, dict], thr: float, folds, stride: int = 16) -> pd.Series:
    """Classifier trained on all *other* sites (site-specific scale features dropped)."""
    me = inputs[sid]
    cols = [c for c in me["X"].columns if c not in SITE_SPECIFIC]
    out = []
    for _fold, _, t0, t1 in folds:
        rows = me["origins"][(me["origins"] > t0) & (me["origins"] <= t1)]
        if len(rows) == 0:
            continue
        Xs, ys = [], []
        for other, d in inputs.items():
            if other == sid:
                continue
            ok = np.flatnonzero(train_origin_mask(d["idx"], H, t0) & ~np.isnan(d["y"]))[::stride]
            Xs.append(d["X"].iloc[ok][cols])
            ys.append(d["y"][ok].astype(int))
        p = fit_predict_proba(pd.concat(Xs), np.concatenate(ys), me["X"].loc[rows, cols])
        out.append(pd.Series(p, index=rows))
    return pd.concat(out)


def scores_for_site(sid, inputs, thr, folds, stride) -> pd.DataFrame:
    d = inputs[sid]
    org = d["origins"]
    p_cls = site_probability(d["f"], thr, folds, stride, 365)["p"].reindex(org).fillna(0.0)
    z_int = interval_z(d, thr, folds, stride).reindex(org)
    p_pool = pooled_prob(sid, inputs, thr, folds).reindex(org)
    s = pd.DataFrame({"naive": -d["v"].reindex(org).to_numpy(), "classifier": p_cls.to_numpy(),
                      "interval": z_int.to_numpy(), "pooled": p_pool.to_numpy()}, index=org)
    logit = np.log(np.clip(p_cls.to_numpy(), 1e-4, 1 - 1e-4) / (1 - np.clip(p_cls.to_numpy(), 1e-4, 1 - 1e-4)))
    s["average"] = 0.5 * (logit + 1.702 * s["interval"].to_numpy())
    s["suspect"] = d["v"].reindex(org).isna().to_numpy()
    s["do_now"] = d["v"].reindex(org).to_numpy()
    s["fold"] = np.searchsorted([f[3] for f in folds], org.to_numpy(), side="left") + 1
    return s


def grid(score: np.ndarray, n: int = 60) -> np.ndarray:
    x = score[np.isfinite(score)]
    return np.unique(np.quantile(x, np.linspace(0.0, 1.0, n))) if len(x) else np.array([0.0])


def alerts_array(n_grid: int, pos: np.ndarray, flag: np.ndarray) -> np.ndarray:
    a = np.zeros(n_grid, bool)
    a[pos] = flag
    return a


def evaluate_period(v: pd.Series, thr: float, org: pd.DatetimeIndex, flag: np.ndarray, costs: Costs,
                    cooldown: int = 24) -> dict:
    tl = v.index[(v.index >= org.min()) & (v.index <= org.max())]
    pos = tl.get_indexer(org)
    ok = pos >= 0
    episodes = [e for e in episodes_from_series(v.loc[tl[0]: tl[-1] + pd.Timedelta("24h")], thr) if e[0] < len(tl)]
    not_low = v.reindex(tl).to_numpy() >= thr
    a = alerts_array(len(tl), pos[ok], flag[ok])
    r = evaluate_alerts(episodes, a, horizon_steps=H, cooldown=cooldown, costs=costs, not_low=not_low)
    r["alerts_per_week_24h_cooldown"] = len(alert_clusters(a, 96)) / (len(tl) / STEP_WEEK)
    r["episodes"] = len(episodes)
    return r


def fit_cutoff(v, thr, org, score, suspect, budget, costs) -> float | None:
    """Most sensitive cutoff on the calibration period with alerts per week <= budget."""
    best = None
    for c in sorted(grid(score), reverse=True):  # high cutoff = few alerts; walk down until the budget breaks
        flag = (score > c) & ~suspect & np.isfinite(score)
        r = evaluate_period(v, thr, org, flag, costs)
        if r["alerts_per_week"] <= budget:
            best = float(c)
        else:
            break
    return best


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/sites.yaml")
    ap.add_argument("--clean", default="data/clean")
    ap.add_argument("--out", default="results")
    ap.add_argument("--threshold", type=float, default=5.0)
    ap.add_argument("--budget", type=float, default=0.5)
    ap.add_argument("--folds", type=int, default=8)
    ap.add_argument("--stride", type=int, default=4)
    ap.add_argument("--jobs", type=int, default=4)
    args = ap.parse_args()
    thr, costs = args.threshold, Costs(10.0, 1.0)

    ids = [s["id"] for s in yaml.safe_load(Path(args.config).read_text())["sites"]]
    ref = load_site_frame(ids[0], args.clean)
    folds = walk_forward_folds(ref.index, n_folds=args.folds)
    inputs = {sid: site_inputs(sid, args.clean, thr, folds, args.stride) for sid in ids}
    scores = Parallel(n_jobs=args.jobs)(delayed(scores_for_site)(sid, inputs, thr, folds, args.stride) for sid in ids)
    half = args.folds // 2

    rows, cuts = [], []
    for sid, s in zip(ids, scores):
        v = inputs[sid]["v"]
        cal = s[s["fold"] <= half]
        test = s[s["fold"] > half]
        for rule in RULES:
            c = fit_cutoff(v, thr, cal.index, cal[rule].to_numpy(), cal["suspect"].to_numpy(), args.budget, costs)
            cuts.append({"site": sid, "rule": rule, "cutoff": c})
            for period, part in (("calibration", cal), ("test", test)):
                if c is None:
                    flag = np.zeros(len(part), bool)
                else:
                    flag = (part[rule].to_numpy() > c) & ~part["suspect"].to_numpy() & np.isfinite(part[rule].to_numpy())
                r = evaluate_period(v, thr, part.index, flag, costs)
                rows.append({"site": sid, "rule": rule, "period": period, "cutoff": c, **r})
        print(f"{sid}: done", flush=True)
    res = pd.DataFrame(rows)
    out = Path(args.out)
    res.to_csv(out / "budget_fit_results.csv", index=False)
    pd.DataFrame(cuts).to_csv(out / "budget_fit_cutoffs.csv", index=False)
    pd.concat(scores, keys=ids, names=["site", "origin"]).to_parquet(out / "budget_fit_scores.parquet")

    t = res[res.period == "test"]
    tab = t.groupby("rule").agg(alerts_per_week=("alerts_per_week", "mean"),
                                alerts_per_week_24h_cooldown=("alerts_per_week_24h_cooldown", "mean"),
                                recall=("recall_anticipated", "mean"), detected=("recall", "mean"),
                                precision=("cluster_precision", "mean"),
                                cost_per_week=("cost_per_week", "mean"), sites=("site", "nunique"))
    pd.set_option("display.width", 200)
    print(tab.round(3).to_string())
    track("alerting-budget-fit", params={"budget_alerts_per_week": args.budget, "calibration": "folds 1-4",
                                         "test": "folds 5-8", "cooldown_h": 6, "origins": "4-hourly"},
          metrics={f"{k}_{r}": tab.loc[r, k] for r in tab.index for k in ("alerts_per_week", "recall", "cost_per_week")},
          artifacts=[out / "budget_fit_results.csv"], run_name="run_budget_fit")


if __name__ == "__main__":
    main()
