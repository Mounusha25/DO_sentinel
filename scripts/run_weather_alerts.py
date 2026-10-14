"""Does weather improve the low-DO probability and alerts? (realistic archived forecasts)

    python scripts/run_weather_alerts.py

Direct classifier for "any DO below threshold within 24 h", with and without weather features
(rain over the previous 1-168 h, forecast rain / air temperature / sunlight for the next 6 h
and 24 h, issued >= 24 h earlier). Same rows, folds (last 364 days) and training window (from
the first day archived forecasts exist); calibration window is the 90 days before each fold.
Scores: Brier (cluster bootstrap) and anticipated recall at fixed alerts-per-week budgets,
against the same naive margin rule. Writes results/weather_alerts_*.csv.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from dosentinel.calibration import PlattScaler
from dosentinel.decision import episodes_from_series, evaluate_alerts
from dosentinel.direct import H, MIN_CAL_CLASS, within_label
from dosentinel.evaluate import walk_forward_folds
from dosentinel.features import base_features, load_site_frame, train_origin_mask
from dosentinel.models import fit_predict_proba
from dosentinel.weather import load_weather, weather_features

FC_START = pd.Timestamp("2024-01-20", tz="UTC")
CUTOFFS = np.round(np.r_[0.01, 0.02, 0.03, 0.05, 0.07, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9], 3)
MARGINS = np.round(np.arange(0.0, 3.01, 0.25), 2)
BUDGETS = (0.25, 0.5, 1.0, 2.0)


def site_probs(f: pd.DataFrame, W: pd.DataFrame, thr: float, folds, stride: int, cal_days: int,
               source: str = "fc1", train_start: pd.Timestamp = FC_START) -> pd.DataFrame:
    v, idx = f["do"], f["do"].index
    X = base_features(f)
    Wf = pd.concat([weather_features(idx, W, 24, source).add_suffix("_6h"),
                    weather_features(idx, W, 96, source).add_suffix("_24h")], axis=1)
    Xon = pd.concat([X, Wf], axis=1)
    y = within_label(v, thr).to_numpy()
    parts = []
    for fold, _, t0, t1 in folds:
        cal_lo = t0 - pd.Timedelta(days=cal_days)
        tr = np.flatnonzero(train_origin_mask(idx, H, cal_lo) & ~np.isnan(y) & (idx >= train_start))[::stride]
        cal = (idx > cal_lo) & train_origin_mask(idx, H, t0) & ~np.isnan(y)
        test = (idx > t0) & (idx <= t1)
        ev = idx[cal | test]
        is_cal = ev <= t0
        d = pd.DataFrame({"y": pd.Series(y, index=idx).loc[ev].to_numpy()}, index=ev)
        for name, F in (("off", X), ("on", Xon)):
            raw = fit_predict_proba(F.iloc[tr], y[tr].astype(int), F.loc[ev])
            ok = is_cal & d["y"].notna().to_numpy()
            lab = d.loc[ok, "y"].to_numpy()
            scaled = lab.sum() >= MIN_CAL_CLASS and (1 - lab).sum() >= MIN_CAL_CLASS
            d[f"p_{name}_raw"] = raw
            d[f"p_{name}"] = PlattScaler().fit(raw[ok], lab).transform(raw) if scaled else raw
        # reference: rate of a low value in the next 24 h given whether DO is low now (training window)
        now = v.loc[ev].to_numpy()
        ytr = y[tr]
        low_tr = v.to_numpy()[tr] < thr
        r_low = ytr[low_tr].mean() if low_tr.any() else ytr.mean()
        r_hi = ytr[~low_tr & ~np.isnan(v.to_numpy()[tr])].mean()
        d["p_state"] = np.where(np.isnan(now), ytr.mean(), np.where(now < thr, r_low, r_hi))
        t = d[~is_cal].copy()
        t["fold"] = fold
        parts.append(t)
    return pd.concat(parts)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/sites.yaml")
    ap.add_argument("--clean", default="data/clean")
    ap.add_argument("--out", default="results")
    ap.add_argument("--threshold", type=float, default=5.0)
    ap.add_argument("--folds", type=int, default=4)
    ap.add_argument("--cal-days", type=int, default=90)
    ap.add_argument("--stride", type=int, default=4)
    ap.add_argument("--source", choices=["fc1", "obs"], default="fc1",
                    help="obs = perfect-forecast upper bound (actual future weather); never deployable")
    ap.add_argument("--train-start", default="2024-01-20",
                    help="first training day; use 2021-10-14 with --source obs for the full history")
    ap.add_argument("--tag", default="", help="suffix for output files")
    args = ap.parse_args()
    train_start = pd.Timestamp(args.train_start, tz="UTC")
    thr = args.threshold
    cfg = yaml.safe_load(Path(args.config).read_text())
    brier_rows, curve_rows = [], []
    for site in cfg["sites"]:
        sid = site["id"]
        f = load_site_frame(sid, args.clean)
        v = f["do"]
        folds = walk_forward_folds(v.index, n_folds=args.folds)
        P = site_probs(f, load_weather(sid), thr, folds, args.stride, args.cal_days, args.source, train_start)

        # ---- probability quality (rows with an observed outcome), weekly sums for the bootstrap
        B = P.dropna(subset=["y", "p_off", "p_on"])
        wk = B.index.tz_localize(None).to_period("W").start_time
        e = pd.DataFrame({"off": (B.p_off - B.y) ** 2, "on": (B.p_on - B.y) ** 2, "state": (B.p_state - B.y) ** 2,
                          "off_raw": (B.p_off_raw - B.y) ** 2, "on_raw": (B.p_on_raw - B.y) ** 2, "week": wk})
        g = e.groupby("week").sum()
        g["n"] = e.groupby("week").size()
        g["events"] = B.groupby(wk).y.sum()
        brier_rows.append(g.reset_index().assign(site=sid, role=site["role"]))

        # ---- alerts at a fixed budget
        tl = v.index[(v.index > folds[0][2]) & (v.index <= v.index.max() - pd.Timedelta("24h"))]
        flags = pd.read_parquet(f"{args.clean}/{sid}_00300.parquet")
        bad = flags[["out_of_range", "flatline", "spike"]].any(axis=1)
        suspect = (bad.rolling(8, min_periods=1).max().astype(bool) | v.isna()).reindex(tl).to_numpy()
        episodes = [x for x in episodes_from_series(v.loc[tl[0]: tl[-1] + pd.Timedelta("24h")], thr) if x[0] < len(tl)]
        v_now = v.reindex(tl).to_numpy()
        not_low = v_now >= thr

        def ev(alert):
            return evaluate_alerts(episodes, alert & ~suspect, horizon_steps=H, not_low=not_low)

        base = {"site": sid, "role": site["role"], "episodes": len(episodes)}
        for name in ("off", "on"):
            p = P[f"p_{name}"].reindex(tl).fillna(0.0).to_numpy()
            for c in CUTOFFS:
                curve_rows.append({**base, "family": f"model_{name}", "param": f"c={c}", **ev(p > c)})
        for m in MARGINS:
            curve_rows.append({**base, "family": "naive", "param": f"m={m}", **ev(v_now < thr + m)})
        print(f"{sid}: {len(episodes)} episodes, {int(B.y.sum())} positive origins", flush=True)

    out = Path(args.out)
    out.mkdir(exist_ok=True)
    pd.concat(brier_rows).to_csv(out / f"weather_alerts_brier_weekly{args.tag}.csv", index=False)
    curves = pd.DataFrame(curve_rows)
    curves.to_csv(out / f"weather_alerts_curves{args.tag}.csv", index=False)
    summ = []
    for (site, fam), gg in curves.groupby(["site", "family"]):
        gg = gg.dropna(subset=["recall_anticipated"])
        for Bg in BUDGETS:
            ok = gg[gg.alerts_per_week <= Bg]
            r = (ok.sort_values(["recall_anticipated", "cluster_precision"], ascending=False).iloc[0]
                 if len(ok) else None)
            summ.append({"site": site, "family": fam, "budget": Bg,
                         "recall": 0.0 if r is None else r["recall_anticipated"],
                         "precision": np.nan if r is None else r["cluster_precision"],
                         "episodes": int(gg.episodes.iloc[0])})
    pd.DataFrame(summ).to_csv(out / f"weather_alerts_budget{args.tag}.csv", index=False)


if __name__ == "__main__":
    main()
