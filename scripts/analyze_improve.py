"""Summarize results/improve_weekly.csv with cluster-bootstrap confidence intervals.

    python scripts/analyze_improve.py

For each horizon: MAE skill of every variant against (a) the best baseline per site and
(b) the current local model v0, pooled over sites. Intervals resample site-weeks (short-range
dependence) and, separately, whole sites (heterogeneity); the site-level interval is the more
conservative one with only 14 sites.
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

sys.path.insert(0, "src")
from dosentinel.stats import sign_test_p, skill_ci  # noqa: E402

BASELINES = ["persistence", "seasonal_naive", "seasonal_mean_7d", "persistence_diel_delta"]
VARIANTS = ["v0_local", "v1a_local_sat", "v1b_local_sat_target", "v4_global", "v3_blend"]


def main(path: str = "results/improve_weekly.csv") -> None:
    w = pd.read_csv(path)
    cov = pd.read_csv(path.replace("weekly", "coverage"))
    print("rows kept / before:", f"{cov.rows_kept.sum() / cov.rows_before.sum():.4f}",
          "| sites losing >1% rows:", cov[cov.rows_kept < 0.99 * cov.rows_before].site.nunique())
    pd.set_option("display.width", 200)
    for hh, g in w.groupby("horizon_h"):
        # best baseline per site (by total error over the whole test period)
        tot = g.groupby("site")[BASELINES].sum()
        best = tot.idxmin(axis=1)
        g = g.assign(best_baseline=g.apply(lambda r: r[best[r["site"]]], axis=1))
        print(f"\n=== horizon {hh} h ===  (site-weeks: {len(g)}, sites: {g.site.nunique()})")
        rows = []
        for m in VARIANTS:
            a = skill_ci(g[m].to_numpy(), g["best_baseline"].to_numpy(), seed=1)
            sa = g.groupby("site")[[m, "best_baseline"]].sum()
            b = skill_ci(sa[m].to_numpy(), sa["best_baseline"].to_numpy(), seed=2)
            c = skill_ci(g[m].to_numpy(), g["v0_local"].to_numpy(), seed=3) if m != "v0_local" else (np.nan,) * 3
            sc = g.groupby("site")[[m, "v0_local"]].sum()
            d = skill_ci(sc[m].to_numpy(), sc["v0_local"].to_numpy(), seed=4) if m != "v0_local" else (np.nan,) * 3
            per_site_better = int(((1 - sc[m] / sc["v0_local"]) > 0).sum()) if m != "v0_local" else None
            rows.append({"variant": m,
                         "skill_vs_best_baseline": f"{a[0]:+.3f} [{a[1]:+.3f},{a[2]:+.3f}]",
                         "  (site-level CI)": f"[{b[1]:+.3f},{b[2]:+.3f}]",
                         "skill_vs_v0": "" if m == "v0_local" else f"{c[0]:+.3f} [{c[1]:+.3f},{c[2]:+.3f}]",
                         "  (site-level CI) ": "" if m == "v0_local" else f"[{d[1]:+.3f},{d[2]:+.3f}]",
                         "sites_better_than_v0": "" if per_site_better is None else f"{per_site_better}/{len(sc)}"})
        print(pd.DataFrame(rows).to_string(index=False))
    print("\nNote: v4_global trains on all sites' history up to each fold (including the site itself); "
          "it is a production global model, not a transfer test.")


if __name__ == "__main__":
    main(*sys.argv[1:])
