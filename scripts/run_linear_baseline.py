"""A well-specified linear model (direct multi-horizon ridge) against the gradient boosting.

    python scripts/run_linear_baseline.py [--stride 4]

Same features, folds, training windows and scored origins as run_gbm.py, so the numbers are
comparable row for row. It answers: is the boosting's gain over the simple baselines more than a
linear model with the same lags, covariates and calendar terms would give? Writes
results/linear_overall.csv and results/linear_vs_gbm.csv.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from sklearn.impute import SimpleImputer
from sklearn.linear_model import RidgeCV
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import FunctionTransformer, RobustScaler

from dosentinel.baselines import BASELINES, HORIZONS_H
from dosentinel.evaluate import evaluation_table, skill_table, storm_state, walk_forward_folds
from dosentinel.features import base_features, horizon_features, load_site_frame, train_origin_mask
from dosentinel.tracking import track


def _inf_to_nan(a):
    a = np.asarray(a, float)
    return np.where(np.isfinite(a), a, np.nan)


def _clip(a):
    return np.clip(a, -10.0, 10.0)  # a linear model is not robust to the heavy-tailed ratio features


def make_model() -> object:
    return make_pipeline(FunctionTransformer(_inf_to_nan), SimpleImputer(strategy="median", add_indicator=True),
                         RobustScaler(), FunctionTransformer(_clip), RidgeCV(alphas=np.logspace(-2, 3, 8)))


def run_site(site_id: str, clean_dir: str, stride: int, n_folds: int = 4) -> pd.DataFrame:
    f = load_site_frame(site_id, clean_dir)
    v = f["do"]
    folds = walk_forward_folds(v.index, n_folds=n_folds)
    df = evaluation_table(v, storm=storm_state(f["q"], v.index), folds=folds)
    X = base_features(f)
    df["linear_q50"] = np.nan
    for hh in HORIZONS_H:
        h = hh * 4
        Z = horizon_features(X, v, h)
        delta = v.shift(-h) - v
        for fold, _, t0, _ in folds:
            tr_idx = np.flatnonzero(train_origin_mask(Z.index, h, t0) & delta.notna().to_numpy())[::stride]
            sel = (df["horizon_h"] == hh) & (df["fold"] == fold)
            Xte = Z.loc[df.index[sel]]
            m = make_model().fit(Z.iloc[tr_idx], delta.iloc[tr_idx].to_numpy())
            df.loc[sel, "linear_q50"] = v.loc[Xte.index].to_numpy() + m.predict(Xte)
    return df


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/sites.yaml")
    ap.add_argument("--clean", default="data/clean")
    ap.add_argument("--out", default="results")
    ap.add_argument("--stride", type=int, default=4)
    ap.add_argument("--folds", type=int, default=4)
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text())
    out = Path(args.out)
    names = list(BASELINES)
    rows = []
    for site in cfg["sites"]:
        t = time.time()
        df = run_site(site["id"], args.clean, args.stride, args.folds)
        sk = skill_table(df, names + ["linear_q50"], ["horizon_h"])
        best = sk[sk.method.isin(names)].groupby("horizon_h")["mae"].min()
        sk["mae_skill_vs_best_baseline"] = 1 - sk["mae"].values / best.reindex(sk["horizon_h"]).values
        rows.append(sk.assign(site=site["id"], role=site["role"]))
        print(f"{site['id']} ({site['role']}): {len(df)} rows, {time.time() - t:.0f}s", flush=True)
    lin = pd.concat(rows)
    lin.to_csv(out / "linear_overall.csv", index=False)

    gbm = pd.read_csv(out / "gbm_overall.csv")
    a = lin[lin.method == "linear_q50"].set_index(["site", "horizon_h"])
    b = gbm[gbm.method == "gbm_q50"].set_index(["site", "horizon_h"])
    j = a[["mae", "mae_skill_vs_best_baseline"]].join(b[["mae", "mae_skill_vs_best_baseline"]], lsuffix="_linear", rsuffix="_gbm")
    j["gbm_vs_linear"] = 1 - j["mae_gbm"] / j["mae_linear"]
    j.reset_index().to_csv(out / "linear_vs_gbm.csv", index=False)
    s = j.groupby("horizon_h").agg(linear_skill=("mae_skill_vs_best_baseline_linear", "mean"),
                                   gbm_skill=("mae_skill_vs_best_baseline_gbm", "mean"),
                                   gbm_vs_linear=("gbm_vs_linear", "mean"),
                                   sites_gbm_better=("gbm_vs_linear", lambda x: int((x > 0).sum())),
                                   n_sites=("gbm_vs_linear", "size"))
    print(s.round(3).to_string())
    metrics = {}
    for h, r in s.iterrows():
        metrics.update({f"linear_skill_{h}h": r.linear_skill, f"gbm_skill_{h}h": r.gbm_skill,
                        f"gbm_vs_linear_{h}h": r.gbm_vs_linear, f"sites_gbm_better_{h}h": r.sites_gbm_better})
    track("baselines", params={"model": "ridge (RobustScaler, clip +-10, RidgeCV)", "stride": args.stride,
                               "folds": args.folds}, metrics=metrics,
          artifacts=[out / "linear_overall.csv", out / "linear_vs_gbm.csv"], run_name="run_linear_baseline")


if __name__ == "__main__":
    main()
