"""Does weather information improve DO forecasts? Two experiments.

    python scripts/run_weather.py --mode oracle      # perfect-forecast upper bound, full history
    python scripts/run_weather.py --mode realistic   # archived day-ahead forecasts, shorter history

In both, the "off" model has the current best feature set (history + saturation features) and
"on" adds weather features; they share rows, folds and training window, so the difference is
weather alone. `realistic` also scores "off_long": the off model trained on the full history,
the stronger reference available without weather.

Writes results/weather_{mode}_weekly.csv (summed absolute errors per site, horizon, week and
wet/dry status of the target window, for cluster bootstrap CIs).
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
from dosentinel.features import base_features, horizon_features, load_site_frame, saturation_features, train_origin_mask
from dosentinel.models import fit_predict_quantiles
from dosentinel.weather import load_weather, weather_features

FC_START = pd.Timestamp("2024-01-20", tz="UTC")  # first day with archived precipitation forecasts
WET_MM = 1.0


def fit_med(X, y, Xte) -> np.ndarray:
    return fit_predict_quantiles(X, y, Xte, quantiles=(0.5,))["q50"].to_numpy()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["oracle", "realistic"], required=True)
    ap.add_argument("--config", default="config/sites.yaml")
    ap.add_argument("--clean", default="data/clean")
    ap.add_argument("--out", default="results")
    ap.add_argument("--horizons", type=int, nargs="+", default=[6, 12, 24])
    ap.add_argument("--folds", type=int, default=None)
    ap.add_argument("--stride", type=int, default=6)
    ap.add_argument("--ablate", action="store_true",
                    help="also fit rain-only and atmosphere-only (air temperature, sunlight) variants")
    args = ap.parse_args()
    source = "obs" if args.mode == "oracle" else "fc1"
    n_folds = args.folds or (8 if args.mode == "oracle" else 4)

    cfg = yaml.safe_load(Path(args.config).read_text())
    frames = {s["id"]: load_site_frame(s["id"], args.clean) for s in cfg["sites"]}
    end = min(fr.index.max() for fr in frames.values())
    folds = walk_forward_folds(pd.date_range(end=end, periods=2, freq="15min"), n_folds=n_folds)
    variants = ["off", "on"] + (["off_long"] if args.mode == "realistic" else [])
    if args.ablate:
        variants += ["on_rain", "on_atmos"]
    weekly = []
    t_all = time.time()
    for s in cfg["sites"]:
        f = frames[s["id"]]
        v, idx = f["do"], f["do"].index
        X = base_features(f)
        W = load_weather(s["id"])
        for hh in args.horizons:
            h = hh * 4
            Zb = horizon_features(X, v, h)
            Zs = pd.concat([Zb, saturation_features(f, h)], axis=1)
            Wf = weather_features(idx, W, h, source)
            Zw = pd.concat([Zs, Wf], axis=1)
            wet_actual = weather_features(idx, W, h, "obs")["rain_next"] > WET_MM
            delta = (v.shift(-h) - v).to_numpy()
            df = evaluation_table(v, folds=folds, horizons_h=[hh])
            if args.mode == "realistic":
                df = df[df.index >= FC_START + pd.Timedelta(days=1)]
            if df.empty:
                continue
            for c in variants:
                df[c] = np.nan
            offc, onc = list(Zs.columns), list(Zw.columns)
            for fold, _, t0, _ in folds:
                rows = df.index[df["fold"] == fold]
                if len(rows) == 0:
                    continue
                ok = train_origin_mask(idx, h, t0) & ~np.isnan(delta)
                tr = ok & (idx >= FC_START) if args.mode == "realistic" else ok
                sel = np.flatnonzero(tr)[::args.stride]
                base = v.loc[rows].to_numpy()
                df.loc[rows, "off"] = base + fit_med(Zs.iloc[sel][offc], delta[sel], Zs.loc[rows, offc])
                df.loc[rows, "on"] = base + fit_med(Zw.iloc[sel][onc], delta[sel], Zw.loc[rows, onc])
                if args.ablate:
                    rain_c = offc + [c for c in Wf.columns if c.startswith("rain_")]
                    atmos_c = offc + [c for c in Wf.columns if c.startswith(("air_temp", "sun_"))]
                    df.loc[rows, "on_rain"] = base + fit_med(Zw.iloc[sel][rain_c], delta[sel], Zw.loc[rows, rain_c])
                    df.loc[rows, "on_atmos"] = base + fit_med(Zw.iloc[sel][atmos_c], delta[sel], Zw.loc[rows, atmos_c])
                if args.mode == "realistic":
                    sel_l = np.flatnonzero(ok)[::args.stride]
                    df.loc[rows, "off_long"] = base + fit_med(Zs.iloc[sel_l][offc], delta[sel_l], Zs.loc[rows, offc])
            df = df.dropna(subset=["y_true", *BASELINES, *variants])
            err = pd.DataFrame({m: (df[m] - df["y_true"]).abs() for m in list(BASELINES) + variants})
            err["week"] = df.index.tz_localize(None).to_period("W").start_time
            err["wet"] = wet_actual.reindex(df.index).fillna(False).to_numpy()
            g = err.groupby(["week", "wet"]).sum()
            g["n"] = err.groupby(["week", "wet"]).size()
            weekly.append(g.reset_index().assign(site=s["id"], role=s["role"], horizon_h=hh))
        print(f"{s['id']} done ({time.time() - t_all:.0f}s elapsed)", flush=True)

    out = Path(args.out)
    out.mkdir(exist_ok=True)
    pd.concat(weekly).to_csv(out / f"weather_{args.mode}{'_ablate' if args.ablate else ''}_weekly.csv", index=False)


if __name__ == "__main__":
    main()
