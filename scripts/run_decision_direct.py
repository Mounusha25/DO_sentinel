"""Alert layer built on a direct "any low DO within the next 24 h" classifier.

    python scripts/run_decision_direct.py [--folds 8] [--cal-days 365]

run_decision.py alerts on the maximum of several per-horizon probabilities compared with a
cutoff that is only optimal for one calibrated probability. Here the classifier is trained
and calibrated on the quantity the alert actually needs, so the cost-derived cutoff applies
to it directly. Same folds, episodes, scoring and chance baseline as run_decision.py.
Writes results/decision_alerts_direct.csv and results/decision_direct_probability.csv.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from dosentinel.calibration import brier
from dosentinel.decision import Costs, chance_baseline, decide, episodes_from_series, evaluate_alerts
from dosentinel.direct import H, site_probability, within_label  # noqa: F401
from dosentinel.evaluate import walk_forward_folds
from dosentinel.features import load_site_frame


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/sites.yaml")
    ap.add_argument("--clean", default="data/clean")
    ap.add_argument("--out", default="results")
    ap.add_argument("--threshold", type=float, default=5.0)
    ap.add_argument("--stride", type=int, default=4)
    ap.add_argument("--folds", type=int, default=8)
    ap.add_argument("--cal-days", type=int, default=365)
    args = ap.parse_args()
    thr = args.threshold

    cfg = yaml.safe_load(Path(args.config).read_text())
    out = Path(args.out)
    out.mkdir(exist_ok=True)
    alert_rows, prob_rows = [], []
    for site in cfg["sites"]:
        sid, tag = site["id"], dict(site=site["id"], role=site["role"])
        f = load_site_frame(sid, args.clean)
        v = f["do"]
        folds = walk_forward_folds(v.index, n_folds=args.folds)
        P = site_probability(f, thr, folds, args.stride, args.cal_days)

        tl = v.index[(v.index > folds[0][2]) & (v.index <= v.index.max() - pd.Timedelta("24h"))]
        p = P["p"].reindex(tl).fillna(0.0).to_numpy()
        flags = pd.read_parquet(f"{args.clean}/{sid}_00300.parquet")
        bad = flags[["out_of_range", "flatline", "spike"]].any(axis=1)
        suspect = (bad.rolling(8, min_periods=1).max().astype(bool) | v.isna()).reindex(tl).to_numpy()
        v_ext = v.loc[tl[0]: tl[-1] + pd.Timedelta("24h")]
        episodes = [e for e in episodes_from_series(v_ext, thr) if e[0] < len(tl)]
        v_now = v.reindex(tl).to_numpy()
        not_low = v_now >= thr

        # probability quality on the alert event itself, against a state-only reference
        ok = P["y"].notna()
        b = P[ok]
        cur_low = v.reindex(b.index).to_numpy() < thr
        known = ~np.isnan(v.reindex(b.index).to_numpy())
        ref = np.where(~known, b["train_rate"], np.where(cur_low, b.loc[b.index, "y"][cur_low].mean() if cur_low.any() else b["train_rate"].mean(),
                                                          b.loc[b.index, "y"][~cur_low & known].mean()))
        prob_rows.append({**tag, "n": int(ok.sum()), "event_rate": float(b["y"].mean()),
                          "brier_model": brier(b["p"], b["y"]), "brier_state_only": brier(ref, b["y"]),
                          "platt_applied_share": float(P.groupby("fold").platt_applied.first().mean())})

        def score(rule: str, alert: np.ndarray, ratio: float, *, _episodes=episodes, _not_low=not_low, _tag=tag,
                  **extra) -> None:
            kw = {"horizon_steps": H, "costs": Costs(ratio, 1.0), "not_low": _not_low}
            alert_rows.append({**_tag, "rule": rule, "miss_to_fa_ratio": ratio, **extra,
                               **evaluate_alerts(_episodes, alert, **kw), **chance_baseline(_episodes, alert, **kw)})

        for ratio in (3, 10, 30):
            c = Costs(ratio, 1.0)
            score("direct_probability", decide(p, suspect, c.cutoff) == "warning", ratio, cutoff=round(c.cutoff, 3))
        for cut in (0.05, 0.1, 0.2, 0.3, 0.5, 0.7):  # descriptive only: chosen on test
            score("direct_cutoff_sweep", decide(p, suspect, cut) == "warning", 10, cutoff=cut)
        for m in (0.0, 0.5, 1.0):
            score("current_value", (v_now < thr + m) & ~suspect, 10, margin=m)
        print(f"{sid} ({site['role']}): {len(episodes)} episodes", flush=True)

    pd.DataFrame(alert_rows).to_csv(out / "decision_alerts_direct.csv", index=False)
    pd.DataFrame(prob_rows).to_csv(out / "decision_direct_probability.csv", index=False)


if __name__ == "__main__":
    main()
