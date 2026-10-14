"""Walk-forward baseline evaluation on the sites in config/sites.yaml.

    python scripts/run_baselines.py

Reads data/clean/*.parquet (from validate_sites.py), writes results/baselines_*.csv.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
import yaml

from dosentinel.baselines import BASELINES
from dosentinel.features import load_site_frame
from dosentinel.evaluate import (clean_values, evaluation_table, event_table, skill_table,
                                 storm_state, walk_forward_folds)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/sites.yaml")
    ap.add_argument("--clean", default="data/clean")
    ap.add_argument("--out", default="results")
    ap.add_argument("--threshold", type=float, default=5.0)
    ap.add_argument("--folds", type=int, default=4, help="number of 91-day test folds")
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text())
    names = list(BASELINES)
    out = Path(args.out)
    out.mkdir(exist_ok=True)
    overall, slices, events = [], [], []
    for site in cfg["sites"]:
        sid = site["id"]
        v = clean_values(pd.read_parquet(f"{args.clean}/{sid}_00300.parquet"))
        q = load_site_frame(sid, args.clean)["q"]
        df = evaluation_table(v, storm=storm_state(q, v.index), folds=walk_forward_folds(v.index, n_folds=args.folds))
        for tab, rows in ((overall, skill_table(df, names, ["horizon_h"])),
                          (slices, pd.concat([skill_table(df, names, ["horizon_h", c]).assign(slice=c)
                                              .rename(columns={c: "value"}) for c in ("season", "storm", "fold")]))):
            tab.append(rows.assign(site=sid, role=site["role"]))
        events.append(event_table(df, names, args.threshold).assign(site=sid, role=site["role"]))

    pd.concat(overall).to_csv(out / "baselines_overall.csv", index=False)
    pd.concat(slices).to_csv(out / "baselines_slices.csv", index=False)
    pd.concat(events).to_csv(out / "baselines_events.csv", index=False)

    pd.set_option("display.width", 200)
    o = pd.concat(overall)
    print("MAE (mg/L) by site / horizon\n")
    print(o.pivot_table(index=["role", "horizon_h"], columns="method", values="mae").round(3).to_string())
    print("\nMAE skill of seasonal methods vs persistence (positive = better)\n")
    print(o[o.method != "persistence"].pivot_table(index=["role", "horizon_h"], columns="method",
          values="mae_skill_vs_persistence").round(3).to_string())
    e = pd.concat(events)
    print(f"\nLow-DO onset detection (target < {args.threshold} mg/L while origin >= it)\n")
    print(e[e.horizon_h.isin([1, 6, 24])][["role", "horizon_h", "method", "onsets", "onset_precision",
                                           "onset_recall"]].round(3).to_string(index=False))


if __name__ == "__main__":
    main()
