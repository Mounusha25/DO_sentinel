"""Zero-shot Chronos-Bolt vs the tuned gradient boosting, on identical origins and targets.

    .venv-ai/bin/python scripts/run_chronos.py [--model amazon/chronos-bolt-small]

Origins: every second whole hour in the last 364 days (4 folds of 91 days, as elsewhere).
Chronos sees the previous 512 hourly DO values (NaN for gaps) and nothing else; no training on
this project's data. The gradient boosting (local per-site, history + saturation features)
is retrained walk-forward per fold exactly as in run_improve.py. Baselines come from baselines.py.
Writes results/chronos_rows.parquet (one row per site, origin, horizon).
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
from dosentinel.features import base_features, horizon_features, load_site_frame, saturation_features, train_origin_mask
from dosentinel.foundation import HORIZONS_H, build_contexts, hourly_series, targets_at
from dosentinel.models import fit_predict_quantiles

CTX = 512
QL = [0.1, 0.5, 0.9]


def chronos_forecasts(pipe, hv: pd.Series, origins: pd.DatetimeIndex, batch: int = 256) -> pd.DataFrame:
    ctx = build_contexts(hv, origins, CTX)
    outs = []
    for i in range(0, len(ctx), batch):
        q, _ = pipe.predict_quantiles(torch.from_numpy(ctx[i:i + batch]), prediction_length=max(HORIZONS_H),
                                      quantile_levels=QL)
        outs.append(q.numpy())
    q = np.concatenate(outs)  # (n, 24, 3)
    cols = {}
    for h in HORIZONS_H:
        for j, name in enumerate(("q10", "q50", "q90")):
            cols[(h, name)] = q[:, h - 1, j]
    return pd.DataFrame(cols, index=origins)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/sites.yaml")
    ap.add_argument("--clean", default="data/clean")
    ap.add_argument("--out", default="results")
    ap.add_argument("--model", default="amazon/chronos-bolt-small")
    ap.add_argument("--stride", type=int, default=6)
    ap.add_argument("--every-hours", type=int, default=2)
    args = ap.parse_args()

    from chronos import BaseChronosPipeline
    dev = "mps" if torch.backends.mps.is_available() else "cpu"
    pipe = BaseChronosPipeline.from_pretrained(args.model, device_map=dev, torch_dtype=torch.float32)

    cfg = yaml.safe_load(Path(args.config).read_text())
    frames = {s["id"]: load_site_frame(s["id"], args.clean) for s in cfg["sites"]}
    end = min(fr.index.max() for fr in frames.values())
    folds = walk_forward_folds(pd.date_range(end=end, periods=2, freq="15min"), n_folds=4)
    rows = []
    t_all = time.time()
    for s in cfg["sites"]:
        f = frames[s["id"]]
        v, idx = f["do"], f["do"].index
        hv = hourly_series(v)
        lo, hi = folds[0][2], min(end, folds[-1][3]) - pd.Timedelta(hours=24)
        origins = hv.index[(hv.index > lo) & (hv.index <= hi) & (hv.index.hour % args.every_hours == 0)]
        fold_of = np.zeros(len(origins), int)
        for k, _, t0, t1 in folds:
            fold_of[(origins > t0) & (origins <= t1)] = k

        t0_ = time.time()
        ch = chronos_forecasts(pipe, hv, origins)
        tg = targets_at(hv, origins)
        t_ch = time.time() - t0_

        X = base_features(f)
        for h in HORIZONS_H:
            hs = h * 4
            Zs = pd.concat([horizon_features(X, v, hs), saturation_features(f, hs)], axis=1)
            delta = (v.shift(-hs) - v).to_numpy()
            fc = forecast_frame(v, hs)
            g = pd.DataFrame({"fold": fold_of, "y_true": tg[h].to_numpy()}, index=origins)
            g["y_origin"] = v.reindex(origins).to_numpy()
            for q in ("q10", "q50", "q90"):
                g[f"ch_{q}"] = ch[(h, q)].to_numpy()
                g[f"gbm_{q}"] = np.nan
            for fold, _, t0, _t1 in folds:
                rws = origins[fold_of == fold]
                if len(rws) == 0:
                    continue
                sel = np.flatnonzero(train_origin_mask(idx, hs, t0) & ~np.isnan(delta))[::args.stride]
                p = fit_predict_quantiles(Zs.iloc[sel], delta[sel], Zs.loc[rws])
                base = v.reindex(rws).to_numpy()
                for q in ("q10", "q50", "q90"):
                    g.loc[rws, f"gbm_{q}"] = base + p[q].to_numpy()
            for m in BASELINES:
                g[m] = fc[m].reindex(origins).to_numpy()
            rows.append(g.reset_index(names="origin").assign(site=s["id"], role=s["role"], horizon_h=h))
        print(f"{s['id']} ({s['role']}): {len(origins)} origins, chronos {t_ch:.0f}s "
              f"(total {time.time() - t_all:.0f}s)", flush=True)

    out = Path(args.out)
    out.mkdir(exist_ok=True)
    pd.concat(rows).to_parquet(out / "chronos_rows.parquet", index=False)


if __name__ == "__main__":
    main()
