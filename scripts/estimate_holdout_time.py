"""How long until the forward holdout can say anything about alerting?

    python scripts/estimate_holdout_time.py

Low-DO episodes are rare, and an alert is only scored per episode, so what limits the forward holdout
is the number of *episodes* it accumulates, not the number of forecasts. This counts episodes per
site-month over the last two years (with the same episode definition as the scoring), then converts
that into the months needed to estimate an anticipated-recall figure to a given precision, pooled over
the 14 sites and for a typical single site. Writes results/forward_holdout_time.csv.
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from dosentinel.decision import episodes_from_series
from dosentinel.features import load_site_frame


def episodes_needed(half_width: float, p: float = 0.8, z: float = 1.96) -> int:
    """Episodes needed so a recall near `p` has a 95% interval of +/- `half_width` (normal approximation)."""
    return math.ceil(z**2 * p * (1 - p) / half_width**2)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/sites.yaml")
    ap.add_argument("--clean", default="data/clean")
    ap.add_argument("--out", default="results")
    ap.add_argument("--threshold", type=float, default=5.0)
    ap.add_argument("--years", type=float, default=2.0)
    args = ap.parse_args()

    rows = []
    for s in yaml.safe_load(Path(args.config).read_text())["sites"]:
        v = load_site_frame(s["id"], args.clean)["do"]
        v = v[v.index > v.index.max() - pd.Timedelta(days=round(365 * args.years))]
        eps = episodes_from_series(v, args.threshold)
        # an episode is a run below the threshold; separate episodes less than a day apart are one
        # *alerting* opportunity (an alert cluster covers them), so also count day-separated ones
        starts = np.array([e[0] for e in eps])
        days = len(v) / 96
        independent = int(1 + (np.diff(starts) > 96).sum()) if len(starts) else 0
        rows.append({"site": s["id"], "role": s["role"], "episodes": len(eps), "episodes_day_apart": independent,
                     "per_year": len(eps) / (days / 365), "independent_per_year": independent / (days / 365)})
    df = pd.DataFrame(rows)
    out = Path(args.out)
    df.to_csv(out / "forward_holdout_time.csv", index=False)

    pooled_month = df["independent_per_year"].sum() / 12
    median_site_month = df["independent_per_year"].median() / 12
    print(df.round(1).to_string(index=False))
    print(f"\npooled independent episodes per month, 14 sites: {pooled_month:.1f}")
    print(f"median single site: {median_site_month:.2f} per month "
          f"({df.independent_per_year.min() / 12:.2f} to {df.independent_per_year.max() / 12:.2f})")
    print("\nmonths until a recall near 0.8 is known to +/- the half width (pooled, then median single site):")
    for hw in (0.20, 0.15, 0.10):
        n = episodes_needed(hw)
        print(f"  +/-{hw:.2f}: {n:>3} episodes -> {n / pooled_month:5.1f} months pooled, "
              f"{n / median_site_month:6.0f} months for one site")
    # a paired comparison between two rules needs more: roughly 2x the episodes for the same half width
    n_pair = 2 * episodes_needed(0.10)
    print(f"  paired comparison of two rules to +/-0.10: about {n_pair} episodes -> {n_pair / pooled_month:.1f} months pooled")


if __name__ == "__main__":
    main()
