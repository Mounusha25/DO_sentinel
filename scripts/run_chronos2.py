"""Chronos-2 with covariates, zero-shot, against the tuned gradient boosting.

    .venv-ai/bin/python scripts/run_chronos2.py

Variants (all zero-shot, 512 hourly context steps, 24-step forecast):
  uni  target only
  wq   + water temperature, specific conductance, turbidity, discharge (past values)
  wx   + weather: past observed (ERA5 proxy) and future as the forecast issued >= 24 h earlier
Comparators on the same origins: local gradient boosting without weather (full history) and with
weather (history from the first day archived forecasts exist). Last 364 days, origins every
4 hours. Writes results/chronos2_rows.parquet.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import yaml

from dosentinel.baselines import BASELINES, forecast_frame
from dosentinel.evaluate import walk_forward_folds
from dosentinel.features import (base_features, horizon_features, load_site_frame, saturation_features,
                                 train_origin_mask)
from dosentinel.foundation import HORIZONS_H, hourly_series, targets_at
from dosentinel.models import fit_predict_quantiles
from dosentinel.weather import load_weather, weather_features

CTX, PRED = 512, 24
QL = [0.1, 0.5, 0.9]
FC_START = pd.Timestamp("2024-01-20", tz="UTC")


def hourly_frame(f: pd.DataFrame, W: pd.DataFrame) -> pd.DataFrame:
    """Everything on one complete hourly UTC grid: water-quality values at :00 and weather."""
    h = f[f.index.minute == 0]
    full = pd.date_range(min(h.index.min(), W.index.min()), max(h.index.max(), W.index.max()), freq="1h")
    out = pd.DataFrame(index=full)
    for c in ("do", "temp", "spcond", "turb", "q"):
        out[c] = h[c].reindex(full)
    out["q"] = np.log1p(out["q"].clip(lower=0))
    out["turb"] = np.log1p(out["turb"].clip(lower=0))
    for src, name in (("obs", "obs"), ("fc1", "fc1")):
        out[f"{name}_air"] = W[f"{src}_temperature_2m"].reindex(full)
        out[f"{name}_sun"] = W[f"{src}_shortwave_radiation"].reindex(full)
        out[f"{name}_rain"] = W[f"{src}_precipitation"].reindex(full)
    return out


def make_items(H: pd.DataFrame, origins: pd.DatetimeIndex, variant: str):
    pos = H.index.get_indexer(origins)
    arr = {c: H[c].to_numpy(np.float32) for c in H.columns}
    items, keep = [], []
    for k, p in enumerate(pos):
        sl = slice(p - CTX + 1, p + 1)
        if p - CTX + 1 < 0 or p + PRED >= len(H):
            continue
        it = {"target": arr["do"][sl]}
        if variant in ("wq", "wx"):
            pc = {"water_temp": arr["temp"][sl], "spcond": arr["spcond"][sl], "turb": arr["turb"][sl], "disch": arr["q"][sl]}
            if variant == "wx":
                pc.update(air=arr["obs_air"][sl], sun=arr["obs_sun"][sl], rain=arr["obs_rain"][sl])
                fut = {"air": arr["fc1_air"][p + 1: p + 1 + PRED], "sun": arr["fc1_sun"][p + 1: p + 1 + PRED],
                       "rain": arr["fc1_rain"][p + 1: p + 1 + PRED]}
                if any(np.isnan(v).mean() > 0.25 for v in fut.values()):
                    continue
                fut = {k2: np.nan_to_num(v, nan=float(np.nanmean(v)) if np.isfinite(v).any() else 0.0)
                       for k2, v in fut.items()}
                it["future_covariates"] = fut
            it["past_covariates"] = pc
        items.append(it)
        keep.append(k)
    return items, np.array(keep)


def run_variant(pipe, H, origins, variant, batch=64) -> pd.DataFrame:
    items, keep = make_items(H, origins, variant)
    out = np.full((len(origins), PRED, 3), np.nan, np.float32)
    for i in range(0, len(items), batch):
        q, _ = pipe.predict_quantiles(items[i:i + batch], prediction_length=PRED, quantile_levels=QL)
        for j, t in enumerate(q):
            out[keep[i + j]] = t[0].numpy()
    cols = {}
    for h in HORIZONS_H:
        for j, n in enumerate(("q10", "q50", "q90")):
            cols[f"{variant}_{n}_{h}"] = out[:, h - 1, j]
    return pd.DataFrame(cols, index=origins)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/sites.yaml")
    ap.add_argument("--clean", default="data/clean")
    ap.add_argument("--out", default="results")
    ap.add_argument("--every-hours", type=int, default=4)
    ap.add_argument("--stride", type=int, default=6)
    ap.add_argument("--variants", nargs="+", default=["uni", "wq", "wx"])
    args = ap.parse_args()

    from chronos import Chronos2Pipeline
    dev = "mps" if torch.backends.mps.is_available() else "cpu"
    pipe = Chronos2Pipeline.from_pretrained("amazon/chronos-2", device_map=dev)
    cfg = yaml.safe_load(Path(args.config).read_text())
    frames = {s["id"]: load_site_frame(s["id"], args.clean) for s in cfg["sites"]}
    end = min(fr.index.max() for fr in frames.values())
    folds = walk_forward_folds(pd.date_range(end=end, periods=2, freq="15min"), n_folds=4)
    cache = Path("data/chronos2_cache")
    cache.mkdir(parents=True, exist_ok=True)
    rows, t_all = [], time.time()
    for s in cfg["sites"]:
        f = frames[s["id"]]
        v, idx = f["do"], f["do"].index
        W = load_weather(s["id"])
        H = hourly_frame(f, W)
        hv = hourly_series(v)
        lo, hi = folds[0][2], min(end, folds[-1][3]) - pd.Timedelta(hours=24)
        origins = hv.index[(hv.index > lo) & (hv.index <= hi) & (hv.index.hour % args.every_hours == 0)]
        fold_of = np.zeros(len(origins), int)
        for k, _, t0, t1 in folds:
            fold_of[(origins > t0) & (origins <= t1)] = k

        t0_ = time.time()
        parts = []
        for var in args.variants:
            cp = cache / f"{s['id']}_{var}.parquet"
            if cp.exists():
                parts.append(pd.read_parquet(cp))
                continue
            d = run_variant(pipe, H, origins, var)
            d.to_parquet(cp)
            parts.append(d)
        ch = pd.concat(parts, axis=1)
        t_ch = time.time() - t0_
        tg = targets_at(hv, origins)

        X = base_features(f)
        for h in HORIZONS_H:
            hs = h * 4
            Zs = pd.concat([horizon_features(X, v, hs), saturation_features(f, hs)], axis=1)
            Zw = pd.concat([Zs, weather_features(idx, W, hs, "fc1")], axis=1)
            delta = (v.shift(-hs) - v).to_numpy()
            fc = forecast_frame(v, hs)
            g = pd.DataFrame({"fold": fold_of, "y_true": tg[h].to_numpy()}, index=origins)
            g["y_origin"] = v.reindex(origins).to_numpy()
            for c in ch.columns:
                if c.endswith(f"_{h}"):
                    g[c[: -len(f"_{h}")]] = ch[c].to_numpy()
            for q in ("q10", "q50", "q90"):
                g[f"gbm_{q}"] = np.nan
                g[f"gbmwx_{q}"] = np.nan
            for fold, _, t0, t1 in folds:
                rws = origins[fold_of == fold]
                if len(rws) == 0:
                    continue
                ok = train_origin_mask(idx, hs, t0) & ~np.isnan(delta)
                sel = np.flatnonzero(ok)[::args.stride]
                selw = np.flatnonzero(ok & (idx >= FC_START))[::args.stride]
                base = v.reindex(rws).to_numpy()
                p = fit_predict_quantiles(Zs.iloc[sel], delta[sel], Zs.loc[rws])
                pw = fit_predict_quantiles(Zw.iloc[selw], delta[selw], Zw.loc[rws])
                for q in ("q10", "q50", "q90"):
                    g.loc[rws, f"gbm_{q}"] = base + p[q].to_numpy()
                    g.loc[rws, f"gbmwx_{q}"] = base + pw[q].to_numpy()
            for m in BASELINES:
                g[m] = fc[m].reindex(origins).to_numpy()
            rows.append(g.reset_index(names="origin").assign(site=s["id"], role=s["role"], horizon_h=h))
        print(f"{s['id']} ({s['role']}): {len(origins)} origins; chronos-2 {t_ch:.0f}s; total {time.time() - t_all:.0f}s",
              flush=True)

    out = Path(args.out)
    out.mkdir(exist_ok=True)
    pd.concat(rows).to_parquet(out / "chronos2_rows.parquet", index=False)


if __name__ == "__main__":
    main()
