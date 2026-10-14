"""Classical statistical baseline: Holt-Winters exponential smoothing (additive daily season).

    python scripts/run_stats_baseline.py

At each origin (00:00 and 12:00 UTC from the shared evaluation table in results/chronos_rows.parquet)
the model is fit to the previous 14 days of hourly DO and forecasts 24 hours ahead; short gaps are
interpolated and origins with less than 80% of the context observed are skipped. No covariates.
Writes results/stats_baseline_rows.parquet and prints skill against the other models on identical rows.
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from joblib import Parallel, delayed
from statsmodels.tsa.holtwinters import ExponentialSmoothing

sys.path.insert(0, "src")
from dosentinel.features import load_site_frame  # noqa: E402
from dosentinel.foundation import HORIZONS_H, build_contexts, hourly_series  # noqa: E402
from dosentinel.stats import skill_ci  # noqa: E402

CTX = 336  # 14 days of hourly values


def fit_forecast(ctx: np.ndarray):
    if np.isnan(ctx).mean() > 0.2:
        return None
    s = pd.Series(ctx).interpolate(limit=12, limit_direction="both")
    if s.isna().any():
        return None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            m = ExponentialSmoothing(s.to_numpy(), trend=None, seasonal="add", seasonal_periods=24,
                                     initialization_method="estimated").fit()
            f = m.forecast(24)
        except Exception:
            return None
    return f[[h - 1 for h in HORIZONS_H]]


def main() -> None:
    tab = pd.read_parquet("results/chronos_rows.parquet")
    tab = tab[tab.origin.dt.hour.isin([0, 12])]
    out = []
    for site, g in tab.groupby("site"):
        v = load_site_frame(site, "data/clean")["do"]
        hv = hourly_series(v)
        origins = pd.DatetimeIndex(sorted(g.origin.unique()))
        ctx = build_contexts(hv, origins, CTX)
        res = Parallel(n_jobs=8)(delayed(fit_forecast)(c) for c in ctx)
        rows = []
        for o, r in zip(origins, res):
            if r is None:
                continue
            for h, val in zip(HORIZONS_H, r):
                rows.append({"site": site, "origin": o, "horizon_h": h, "ets": float(val)})
        out.append(pd.DataFrame(rows))
        print(f"{site}: {len(rows) // len(HORIZONS_H)} / {len(origins)} origins fitted", flush=True)
    ets = pd.concat(out)
    d = tab.merge(ets, on=["site", "origin", "horizon_h"], how="inner")
    d.to_parquet("results/stats_baseline_rows.parquet", index=False)

    need = ["y_true", "ets", "gbm_q50", "ch_q50", "persistence", "seasonal_naive", "seasonal_mean_7d",
            "persistence_diel_delta"]
    d = d.dropna(subset=need)
    d["week"] = d.origin.dt.tz_localize(None).dt.to_period("W").dt.start_time
    print(f"\nrows on identical origins: {len(d)}; sites {d.site.nunique()}")
    B = ["persistence", "seasonal_naive", "seasonal_mean_7d", "persistence_diel_delta"]
    lines = []
    for h, g in d.groupby("horizon_h"):
        best = g.groupby("site").apply(lambda x: pd.Series({b: (x[b] - x.y_true).abs().sum() for b in B}),
                                       include_groups=False).idxmin(axis=1)
        g = g.assign(best=[abs(r[best[r.site]] - r.y_true) for _, r in g.iterrows()])
        for c in ("ets", "gbm_q50", "ch_q50"):
            g[f"a_{c}"] = (g[c] - g.y_true).abs()
        wk = g.groupby(["site", "week"])[["best", "a_ets", "a_gbm_q50", "a_ch_q50"]].sum()
        f = lambda a, b, s: skill_ci(wk[a].to_numpy(), wk[b].to_numpy(), seed=s)
        p1 = f("a_ets", "best", 1)
        p2 = f("a_gbm_q50", "best", 2)
        p3 = f("a_ch_q50", "best", 3)
        p4 = f("a_gbm_q50", "a_ets", 4)
        lines.append({"h": h, "n": len(g), "ETS vs best simple baseline": f"{p1[0]:+.3f} [{p1[1]:+.3f},{p1[2]:+.3f}]",
                      "GBM vs best baseline": f"{p2[0]:+.3f}", "Chronos-Bolt vs best baseline": f"{p3[0]:+.3f}",
                      "GBM vs ETS": f"{p4[0]:+.3f} [{p4[1]:+.3f},{p4[2]:+.3f}]"})
        print({k: (round(float(np.mean(np.abs(g[c] - g.y_true))), 3)) for k, c in
               (("MAE ets", "ets"), ("MAE gbm", "gbm_q50"), ("MAE bolt", "ch_q50"),
                ("MAE diel_delta", "persistence_diel_delta"), ("MAE persistence", "persistence"))}, "h=", h)
    pd.set_option("display.width", 220)
    print(pd.DataFrame(lines).to_string(index=False))


if __name__ == "__main__":
    main()
