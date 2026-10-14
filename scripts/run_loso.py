"""Leave-one-site-out transfer test.

    python scripts/run_loso.py [--horizons 1 6 24] [--folds 4]

For each held-out site and fold, a model trained ONLY on the other sites' data (targets
before the calibration window, same temporal cutoff as the site-specific model) is
scored on the held-out site's test window and compared with:
  * the best baseline, and
  * a site-specific model trained on that site's own history.
Both models use the same transferable feature set (site-specific absolute scales such as
conductance level and discharge magnitude are removed), so the comparison isolates data
source rather than features. Writes results/loso_*.csv.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor

from dosentinel.baselines import BASELINES, forecast_frame
from dosentinel.evaluate import walk_forward_folds
from dosentinel.features import SITE_SPECIFIC, base_features, horizon_features, load_site_frame, train_origin_mask
from dosentinel.models import PARAMS

CAL_DAYS = 90
THR = 5.0


def fit_reg(X, y):
    m = HistGradientBoostingRegressor(loss="quantile", quantile=0.5, **PARAMS)
    return m.fit(X, y)


def fit_clf(X, y):
    y = np.asarray(y, int)
    if y.min() == y.max():
        return float(y.mean())
    return HistGradientBoostingClassifier(**PARAMS).fit(X, y)


def proba(m, X):
    return np.full(len(X), m) if isinstance(m, float) else m.predict_proba(X)[:, 1]


def brier(p, y):
    return float(np.mean((np.asarray(p, float) - np.asarray(y, float)) ** 2))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/sites.yaml")
    ap.add_argument("--clean", default="data/clean")
    ap.add_argument("--out", default="results")
    ap.add_argument("--horizons", type=int, nargs="+", default=[1, 6, 24])
    ap.add_argument("--folds", type=int, default=4)
    ap.add_argument("--stride", type=int, default=8, help="use every k-th training origin")
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text())
    sites = [s["id"] for s in cfg["sites"]]
    role = {s["id"]: s["role"] for s in cfg["sites"]}
    F = {sid: load_site_frame(sid, args.clean) for sid in sites}
    X = {sid: base_features(F[sid]) for sid in sites}
    end = min(F[sid].index.max() for sid in sites)
    folds = walk_forward_folds(pd.date_range(end=end, periods=2, freq="15min"), n_folds=args.folds)
    rows = []
    for hh in args.horizons:
        h = hh * 4
        Z, y, v = {}, {}, {}
        for sid in sites:
            Z[sid] = horizon_features(X[sid], F[sid]["do"], h).drop(columns=SITE_SPECIFIC)
            v[sid] = F[sid]["do"]
            y[sid] = v[sid].shift(-h)
        for fold, _, t0, t1 in folds:
            cal_lo = t0 - pd.Timedelta(days=CAL_DAYS)
            train = {}
            for sid in sites:
                idx = Z[sid].index
                m = train_origin_mask(idx, h, cal_lo) & y[sid].notna().to_numpy() & v[sid].notna().to_numpy()
                sel = np.flatnonzero(m)[::args.stride]
                yy = y[sid].to_numpy()[sel]
                train[sid] = (Z[sid].iloc[sel], yy - v[sid].to_numpy()[sel], (yy < THR).astype(int))
            for held in sites:
                others = [s for s in sites if s != held]
                Xp = pd.concat([train[s][0] for s in others])
                dp = np.concatenate([train[s][1] for s in others])
                lp = np.concatenate([train[s][2] for s in others])
                Xl, dl, ll = train[held]
                idx = Z[held].index
                te = (idx > t0) & (idx <= t1) & y[held].notna().to_numpy() & v[held].notna().to_numpy()
                if te.sum() == 0 or len(Xl) < 200:
                    continue
                Xt = Z[held].loc[te]
                base = v[held].to_numpy()[te]
                truth = y[held].to_numpy()[te]
                fr = forecast_frame(v[held], h).loc[Xt.index]
                pred_pool = base + fit_reg(Xp, dp).predict(Xt)
                pred_loc = base + fit_reg(Xl, dl).predict(Xt)
                lab = (truth < THR).astype(int)
                cur_low = base < THR
                r_low = ll[(Xl["do"] < THR).to_numpy()].mean() if (Xl["do"] < THR).any() else ll.mean()
                r_hi = ll[(Xl["do"] >= THR).to_numpy()].mean() if (Xl["do"] >= THR).any() else ll.mean()
                p_state = np.where(cur_low, r_low, r_hi)
                row = {"site": held, "role": role[held], "horizon_h": hh, "fold": fold, "n": int(te.sum()),
                       "events": int(lab.sum()),
                       "mae_pooled": float(np.abs(pred_pool - truth).mean()),
                       "mae_local": float(np.abs(pred_loc - truth).mean()),
                       "brier_state": brier(p_state, lab),
                       "brier_pooled": brier(proba(fit_clf(Xp, lp), Xt), lab),
                       "brier_local": brier(proba(fit_clf(Xl, ll), Xt), lab)}
                for name in BASELINES:
                    row[f"mae_{name}"] = float((fr[name] - truth).abs().mean())
                rows.append(row)
            print(f"h={hh}h fold {fold}/{len(folds)} done", flush=True)

    df = pd.DataFrame(rows)
    out = Path(args.out)
    out.mkdir(exist_ok=True)
    df.to_csv(out / "loso_folds.csv", index=False)

    # weight folds by rows so a short fold does not dominate
    def agg(g: pd.DataFrame) -> pd.Series:
        w = g["n"].to_numpy()
        res = {k: float(np.average(g[k], weights=w)) for k in g.columns if k.startswith(("mae_", "brier_"))}
        res["events"], res["n"] = int(g["events"].sum()), int(g["n"].sum())
        res["mae_best_baseline"] = min(v for k, v in res.items() if k.startswith("mae_") and k.split("mae_")[1] in BASELINES)
        return pd.Series(res)

    over = df.groupby(["site", "role", "horizon_h"]).apply(agg, include_groups=False).reset_index()
    over["skill_pooled_vs_baseline"] = 1 - over["mae_pooled"] / over["mae_best_baseline"]
    over["skill_local_vs_baseline"] = 1 - over["mae_local"] / over["mae_best_baseline"]
    over["skill_pooled_vs_local"] = 1 - over["mae_pooled"] / over["mae_local"]
    over["bss_pooled_vs_state"] = 1 - over["brier_pooled"] / over["brier_state"]
    over["bss_local_vs_state"] = 1 - over["brier_local"] / over["brier_state"]
    over.to_csv(out / "loso_overall.csv", index=False)
    pd.set_option("display.width", 220)
    print(over[["site", "role", "horizon_h", "events", "mae_best_baseline", "mae_local", "mae_pooled",
                "skill_local_vs_baseline", "skill_pooled_vs_baseline", "bss_local_vs_state",
                "bss_pooled_vs_state"]].round(3).to_string(index=False))


if __name__ == "__main__":
    main()
