"""Frozen pipeline on sites that played no part in any decision: the untouched evaluation.

    python scripts/run_holdout_eval.py [--config config/holdout_sites.yaml]

The sites are those pre-registered in config/holdout_sites.yaml (rule and sites committed before any
of their data was pulled). Nothing is tuned: the features, hyper-parameters, calibration windows and
decision rules are those of the 14 development sites, applied to each new site's own history, with the
same walk-forward folds (8 x 91 days) and the same scoring.

Per site:
  forecasts   MAE skill against the best simple baseline, with a site-week cluster bootstrap CI, per horizon;
  intervals   80% coverage raw and after conformal calibration on the previous fold (folds 2-8);
  alerts      (tier A sites only) the budget-fit procedure of run_budget_fit.py: cutoffs fitted on year 1 to
              0.5 alerts per week, frozen, scored on year 2, for the margin rule, the classifier, the
              interval rule and a classifier pooled from the 14 development sites.

Run validate_sites.py first so data/clean holds the new sites. Weather and the pretrained model are not
used (the weather archive for these sites was not pulled). Writes results/holdout_*.csv.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).parent))
import run_budget_fit as bf  # noqa: E402
import run_gbm  # noqa: E402

from dosentinel.baselines import BASELINES  # noqa: E402
from dosentinel.calibration import cqr_correction  # noqa: E402
from dosentinel.decision import Costs  # noqa: E402
from dosentinel.evaluate import walk_forward_folds  # noqa: E402
from dosentinel.features import load_site_frame  # noqa: E402
from dosentinel.stats import skill_ci  # noqa: E402
from dosentinel.tracking import track  # noqa: E402

NAMES = list(BASELINES)


def forecast_tables(sid: str, role: str, clean: str, stride: int, folds: int) -> tuple[list[dict], list[dict]]:
    df = run_gbm.run_site(sid, clean, stride, folds)
    df = df.dropna(subset=["gbm_q50", "gbm_q10", "gbm_q90"])
    df["week"] = df.index.tz_convert("UTC").tz_localize(None).to_period("W").start_time
    skill_rows, cov_rows = [], []
    for h, g in df.groupby("horizon_h"):
        best = min(NAMES, key=lambda m: (g[m] - g["y_true"]).abs().sum())
        a = (g["gbm_q50"] - g["y_true"]).abs().groupby(g["week"]).sum()
        b = (g[best] - g["y_true"]).abs().groupby(g["week"]).sum()
        pt, lo, hi = skill_ci(a.to_numpy(), b.to_numpy(), seed=1)
        skill_rows.append({"site": sid, "role": role, "horizon_h": int(h), "best_baseline": best, "skill": pt,
                           "ci_lo": lo, "ci_hi": hi, "rows": len(g)})
        # conformal margin for fold k from fold k-1 (its targets are known by the start of fold k)
        inside_raw, inside_cal, width, n = [], [], [], 0
        folds_present = sorted(g["fold"].unique())
        for k in folds_present[1:]:
            prev, cur = g[g["fold"] == k - 1], g[g["fold"] == k]
            if len(prev) < 50 or cur.empty:
                continue
            m = cqr_correction(prev["y_true"].to_numpy(), prev["gbm_q10"].to_numpy(), prev["gbm_q90"].to_numpy(), 0.2)
            lo_c = np.minimum(cur["gbm_q10"] - m, cur["gbm_q50"])
            hi_c = np.maximum(cur["gbm_q90"] + m, cur["gbm_q50"])
            inside_raw += list((cur["y_true"] >= cur["gbm_q10"]) & (cur["y_true"] <= cur["gbm_q90"]))
            inside_cal += list((cur["y_true"] >= lo_c) & (cur["y_true"] <= hi_c))
            width += list(hi_c - lo_c)
            n += len(cur)
        cov_rows.append({"site": sid, "role": role, "horizon_h": int(h), "n": n,
                         "coverage_raw": float(np.mean(inside_raw)) if n else np.nan,
                         "coverage_conformal": float(np.mean(inside_cal)) if n else np.nan,
                         "mean_width": float(np.mean(width)) if n else np.nan})
    return skill_rows, cov_rows


def alert_table(new_ids: list[str], dev_ids: list[str], clean: str, thr: float, folds_n: int, stride: int,
                budget: float, jobs: int) -> pd.DataFrame:
    """The run_budget_fit procedure for the new sites; the pooled classifier trains on dev sites only."""
    costs = Costs(10.0, 1.0)
    # every site gets its own folds, ending at its own last timestamp
    inputs = {}
    for sid in [*dev_ids, *new_ids]:
        f = load_site_frame(sid, clean)
        fo = walk_forward_folds(f.index, n_folds=folds_n)
        inputs[sid] = (bf.site_inputs(sid, clean, thr, fo, stride), fo)
    pool = {sid: inputs[sid][0] for sid in dev_ids}
    out = []
    for sid in new_ids:
        d, fo = inputs[sid]
        pooled_inputs = {**pool, sid: d}
        s = bf.scores_for_site(sid, pooled_inputs, thr, fo, stride)
        v = d["v"]
        half = folds_n // 2
        cal, test = s[s["fold"] <= half], s[s["fold"] > half]
        for rule in bf.RULES:
            c = bf.fit_cutoff(v, thr, cal.index, cal[rule].to_numpy(), cal["suspect"].to_numpy(), budget, costs)
            for period, part in (("calibration", cal), ("test", test)):
                flag = (np.zeros(len(part), bool) if c is None else
                        (part[rule].to_numpy() > c) & ~part["suspect"].to_numpy() & np.isfinite(part[rule].to_numpy()))
                r = bf.evaluate_period(v, thr, part.index, flag, costs)
                out.append({"site": sid, "rule": rule, "period": period, "cutoff": c, **r})
        print(f"{sid}: alerts done", flush=True)
    return pd.DataFrame(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/holdout_sites.yaml")
    ap.add_argument("--dev-config", default="config/sites.yaml")
    ap.add_argument("--clean", default="data/clean")
    ap.add_argument("--out", default="results")
    ap.add_argument("--threshold", type=float, default=5.0)
    ap.add_argument("--budget", type=float, default=0.5)
    ap.add_argument("--folds", type=int, default=8)
    ap.add_argument("--stride", type=int, default=4)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--skip-alerts", action="store_true")
    args = ap.parse_args()

    sites = yaml.safe_load(Path(args.config).read_text())["sites"]
    dev = [s["id"] for s in yaml.safe_load(Path(args.dev_config).read_text())["sites"]]
    out = Path(args.out)
    skill, cov = [], []
    for s in sites:
        a, b = forecast_tables(s["id"], s.get("tier", "?"), args.clean, args.stride, args.folds)
        skill += a
        cov += b
        print(f"{s['id']} (tier {s.get('tier')}): forecasts done", flush=True)
    sk, cv = pd.DataFrame(skill), pd.DataFrame(cov)
    sk.to_csv(out / "holdout_skill.csv", index=False)
    cv.to_csv(out / "holdout_coverage.csv", index=False)
    pd.set_option("display.width", 200)
    print(sk.pivot_table(index="site", columns="horizon_h", values="skill").round(3).to_string())
    print(cv.pivot_table(index="site", columns="horizon_h", values="coverage_conformal").round(3).to_string())

    metrics = {}
    for h, g in sk.groupby("horizon_h"):
        metrics[f"skill_{h}h_mean"] = g["skill"].mean()
        metrics[f"sites_better_{h}h"] = float((g["skill"] > 0).sum())
    for h, g in cv.groupby("horizon_h"):
        metrics[f"coverage_{h}h_mean"] = g["coverage_conformal"].mean()
    arts = [out / "holdout_skill.csv", out / "holdout_coverage.csv"]
    if not args.skip_alerts:
        tier_a = [s["id"] for s in sites if s.get("tier") == "A"]
        if tier_a:
            al = alert_table(tier_a, dev, args.clean, args.threshold, args.folds, args.stride, args.budget, args.jobs)
            al.to_csv(out / "holdout_alerts.csv", index=False)
            arts.append(out / "holdout_alerts.csv")
            t = al[al.period == "test"]
            print(t.pivot_table(index="site", columns="rule", values="cost_per_week").round(2).to_string())
            print(t.pivot_table(index="site", columns="rule", values="recall_anticipated").round(2).to_string())
            print(t.pivot_table(index="site", columns="rule", values="alerts_per_week").round(2).to_string())
    track("holdout", params={"sites": [s["id"] for s in sites], "folds": args.folds, "stride": args.stride,
                             "config": args.config, "tuned": "nothing"}, metrics=metrics, artifacts=arts,
          run_name="run_holdout_eval")


if __name__ == "__main__":
    main()
