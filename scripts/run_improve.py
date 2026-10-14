"""Point-forecast improvement experiments, scored on identical origins with weekly error totals.

    python scripts/run_improve.py [--horizons 1 6 24] [--stride 6]

Variants (median forecast of DO, mg/L), all walk-forward over the same 8 test folds:
  v0_local              current per-site model
  v1a_local_sat         + saturation features (percent saturation, deficit, temperature-based)
  v1b_local_sat_target  predicts the change in percent saturation, converted back with the
                        temperature expected at the target time
  v4_global             one model for all sites (history up to each fold) with saturation and
                        static site features (log drainage area, flashiness, discharge available)
  v3_blend              mean of v0_local and v4_global
Baselines come from baselines.py. Writes results/improve_weekly.csv: per site, horizon, week
the row count and summed absolute error for every method, for cluster bootstrap CIs.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from dosentinel.baselines import BASELINES
from dosentinel.evaluate import evaluation_table, walk_forward_folds
from dosentinel.features import (
    base_features,
    do_saturation,
    horizon_features,
    load_site_frame,
    saturation_features,
    train_origin_mask,
)
from dosentinel.models import fit_predict_quantiles

STEP = pd.Timedelta("15min")
METHODS = list(BASELINES) + ["v0_local", "v1a_local_sat", "v1b_local_sat_target", "v4_global", "v3_blend"]


def fit_med(X: pd.DataFrame, y: np.ndarray, Xte: pd.DataFrame) -> np.ndarray:
    return fit_predict_quantiles(X, y, Xte, quantiles=(0.5,))["q50"].to_numpy()


def static_for(site: dict) -> dict:
    h = site.get("history", {})
    rb = h.get("richards_baker_2y")
    return {"log_area": float(np.log(site["drainage_sqmi"])), "rb_flash": np.nan if rb is None else float(rb),
            "has_discharge": float(h.get("discharge_observed", 0.0) > 0.5)}


def build(site: dict, clean: str, hh: int):
    f = load_site_frame(site["id"], clean)
    X = base_features(f)
    h = hh * 4
    Zb = horizon_features(X, f["do"], h)
    S = saturation_features(f, h)
    st = pd.DataFrame({k: v for k, v in static_for(site).items()}, index=f.index)
    return f, Zb, pd.concat([Zb, S], axis=1), pd.concat([Zb, S, st], axis=1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/sites.yaml")
    ap.add_argument("--clean", default="data/clean")
    ap.add_argument("--out", default="results")
    ap.add_argument("--horizons", type=int, nargs="+", default=[1, 6, 24])
    ap.add_argument("--stride", type=int, default=6)
    ap.add_argument("--folds", type=int, default=8)
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text())
    sites = cfg["sites"]
    frames = {s["id"]: load_site_frame(s["id"], args.clean) for s in sites}
    end = min(fr.index.max() for fr in frames.values())
    folds = walk_forward_folds(pd.date_range(end=end, periods=2, freq="15min"), n_folds=args.folds)
    weekly, coverage = [], []

    for hh in args.horizons:
        h = hh * 4
        t_h = time.time()
        # ---- pass 1: strided training rows from every site, to fit the global model per fold
        sub = []
        for s in sites:
            f, Zb, Zs, Zg = build(s, args.clean, hh)
            v = f["do"]
            delta = (v.shift(-h) - v).to_numpy()
            sel = np.arange(0, len(v), args.stride)
            keep = ~np.isnan(delta[sel])
            sel = sel[keep]
            sub.append((Zg.iloc[sel], delta[sel], v.index[sel]))
        gmodels = {}
        for fold, _, t0, _ in folds:
            parts = [(Z[ix + h * STEP <= t0], d[ix + h * STEP <= t0]) for Z, d, ix in sub]
            gmodels[fold] = (pd.concat([p[0] for p in parts]), np.concatenate([p[1] for p in parts]))
        del sub

        # ---- pass 2: per site evaluation rows, local variants, global predictions
        for s in sites:
            f, Zb, Zs, Zg = build(s, args.clean, hh)
            v, idx = f["do"], f["do"].index
            temp = f["temp"]
            df = evaluation_table(v, folds=folds, horizons_h=[hh])
            if df.empty:
                continue
            colsS, colsG = list(Zs.columns), list(Zg.columns)
            delta = (v.shift(-h) - v).to_numpy()
            sat_tgt = pd.Series(do_saturation(temp.shift(-h)), index=idx)  # true sat at target time
            pct_now = Zs["do_pct"]
            delta_pct = ((v.shift(-h) / sat_tgt) - pct_now).to_numpy()
            for c in ("v0_local", "v1a_local_sat", "v1b_local_sat_target", "v4_global"):
                df[c] = np.nan
            for fold, _, t0, _ in folds:
                rows = df.index[df["fold"] == fold]
                if len(rows) == 0:
                    continue
                tr = train_origin_mask(idx, h, t0)
                sel0 = np.flatnonzero(tr & ~np.isnan(delta))[::args.stride]
                selb = np.flatnonzero(tr & ~np.isnan(delta_pct))[::args.stride]
                base = v.loc[rows].to_numpy()
                df.loc[rows, "v0_local"] = base + fit_med(Zb.iloc[sel0], delta[sel0], Zb.loc[rows])
                df.loc[rows, "v1a_local_sat"] = base + fit_med(Zs.iloc[sel0][colsS], delta[sel0], Zs.loc[rows, colsS])
                dp = fit_med(Zs.iloc[selb][colsS], delta_pct[selb], Zs.loc[rows, colsS])
                df.loc[rows, "v1b_local_sat_target"] = (pct_now.loc[rows].to_numpy() + dp) * Zs.loc[rows, "sat_target_hat"].to_numpy()
                Xg, yg = gmodels[fold]
                df.loc[rows, "v4_global"] = base + fit_med(Xg, yg, Zg.loc[rows, colsG])
            df["v3_blend"] = 0.5 * (df["v0_local"] + df["v4_global"])
            n0 = len(df)
            df = df.dropna(subset=["y_true", *METHODS])
            coverage.append({"site": s["id"], "horizon_h": hh, "rows_before": n0, "rows_kept": len(df)})
            err = pd.DataFrame({m: (df[m] - df["y_true"]).abs() for m in METHODS})
            err["week"] = df.index.tz_convert("UTC").tz_localize(None).to_period("W").start_time
            g = err.groupby("week").agg(["sum"])
            g.columns = [c[0] for c in g.columns]
            g["n"] = err.groupby("week").size()
            g = g.reset_index().assign(site=s["id"], role=s["role"], horizon_h=hh)
            weekly.append(g)
        print(f"horizon {hh} h done in {time.time() - t_h:.0f}s", flush=True)

    out = Path(args.out)
    out.mkdir(exist_ok=True)
    pd.concat(weekly).to_csv(out / "improve_weekly.csv", index=False)
    pd.DataFrame(coverage).to_csv(out / "improve_coverage.csv", index=False)


if __name__ == "__main__":
    main()
