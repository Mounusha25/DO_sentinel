"""Walk-forward evaluation of quantile gradient boosting against the baselines.

    python scripts/run_gbm.py [--stride 4]

Per site, horizon and fold: train on origins whose target time precedes the test window
(expanding window), predict the 10/50/90th percentile of DO, and score on exactly the
origins the baselines were scored on. Writes results/gbm_*.csv.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from dosentinel.baselines import BASELINES, HORIZONS_H
from dosentinel.evaluate import evaluation_table, event_table, skill_table, storm_state, walk_forward_folds
from dosentinel.features import base_features, horizon_features, load_site_frame, train_origin_mask
from dosentinel.models import fit_predict_quantiles

GBM = ["gbm_q10", "gbm_q50", "gbm_q90"]


def pinball(y: pd.Series, p: pd.Series, q: float) -> float:
    d = y - p
    return float(np.maximum(q * d, (q - 1) * d).mean())


def run_site(site_id: str, clean_dir: str, stride: int, n_folds: int = 4) -> pd.DataFrame:
    f = load_site_frame(site_id, clean_dir)
    v = f["do"]
    folds = walk_forward_folds(v.index, n_folds=n_folds)
    df = evaluation_table(v, storm=storm_state(f["q"], v.index), folds=folds)
    X = base_features(f)
    for c in GBM:
        df[c] = np.nan
    for hh in HORIZONS_H:
        h = hh * 4
        Z = horizon_features(X, v, h)
        delta = v.shift(-h) - v
        for fold, _, t0, _ in folds:
            tr = train_origin_mask(Z.index, h, t0) & delta.notna().to_numpy()
            tr_idx = np.flatnonzero(tr)[::stride]
            sel = (df["horizon_h"] == hh) & (df["fold"] == fold)
            Xte = Z.loc[df.index[sel]]
            p = fit_predict_quantiles(Z.iloc[tr_idx], delta.iloc[tr_idx].to_numpy(), Xte)
            base = v.loc[Xte.index].to_numpy()
            for q in ("q10", "q50", "q90"):
                df.loc[sel, f"gbm_{q}"] = base + p[q].to_numpy()
    return df


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/sites.yaml")
    ap.add_argument("--clean", default="data/clean")
    ap.add_argument("--out", default="results")
    ap.add_argument("--stride", type=int, default=4, help="use every k-th training origin")
    ap.add_argument("--folds", type=int, default=4, help="number of 91-day test folds")
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text())
    out = Path(args.out)
    out.mkdir(exist_ok=True)
    names = list(BASELINES)
    methods = names + ["gbm_q50"]
    overall, slices, events, cal = [], [], [], []
    for site in cfg["sites"]:
        t = time.time()
        df = run_site(site["id"], args.clean, args.stride, args.folds)
        print(f"{site['id']} ({site['role']}): {len(df)} scored rows, {time.time() - t:.0f}s", flush=True)
        tag = dict(site=site["id"], role=site["role"])
        sk = skill_table(df, methods, ["horizon_h"])
        best = sk[sk.method.isin(names)].groupby("horizon_h")["mae"].min()
        sk["mae_skill_vs_best_baseline"] = 1 - sk["mae"].values / best.reindex(sk["horizon_h"]).values
        overall.append(sk.assign(**tag))
        for c in ("season", "storm", "fold"):
            s = skill_table(df, methods, ["horizon_h", c]).rename(columns={c: "value"}).assign(slice=c)
            bb = s[s.method.isin(names)].groupby(["horizon_h", "value"])["mae"].min()
            key = pd.MultiIndex.from_frame(s[["horizon_h", "value"]])
            s["mae_skill_vs_best_baseline"] = 1 - s["mae"].values / bb.reindex(key).values
            slices.append(s.assign(**tag))
        events.append(event_table(df, names + ["gbm_q50", "gbm_q10"]).assign(**tag))
        for hh, g in df.groupby("horizon_h"):
            cal.append({**tag, "horizon_h": hh,
                        "coverage_80": float(((g.y_true >= g.gbm_q10) & (g.y_true <= g.gbm_q90)).mean()),
                        "width_80": float((g.gbm_q90 - g.gbm_q10).mean()),
                        "pinball_q10": pinball(g.y_true, g.gbm_q10, .1),
                        "pinball_q50": pinball(g.y_true, g.gbm_q50, .5),
                        "pinball_q90": pinball(g.y_true, g.gbm_q90, .9)})
    pd.concat(overall).to_csv(out / "gbm_overall.csv", index=False)
    pd.concat(slices).to_csv(out / "gbm_slices.csv", index=False)
    pd.concat(events).to_csv(out / "gbm_events.csv", index=False)
    pd.DataFrame(cal).to_csv(out / "gbm_calibration.csv", index=False)


if __name__ == "__main__":
    main()
