"""Conformal calibration of the Chronos-2 + gradient-boosting blend, walk-forward.

    python scripts/run_blend.py [--rows results/chronos2_rows.parquet]

Reads the per-origin quantile forecasts written by run_chronos2.py. For each of folds 2-4 the
80% interval is widened by a CQR margin fitted *only* on origins whose targets precede the fold
(per site and horizon): either all earlier folds ("expanding") or the single previous fold
("last_fold"). Fold 1 has nothing to calibrate on and is excluded from every interval number,
so raw and calibrated rows are identical. Point skill is reported on the same rows.

Writes results/blend_intervals.csv, results/blend_skill.csv, results/blend_weekly.csv.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, "src")
from dosentinel.baselines import BASELINES as _BASELINES  # noqa: E402
from dosentinel.blend import apply_margin, blend_quantiles, conformal_margin, interval_score  # noqa: E402
from dosentinel.stats import skill_ci  # noqa: E402
from dosentinel.tracking import track  # noqa: E402

BASELINES = list(_BASELINES)
MEMBERS = {"gbm_wx": "gbmwx", "chronos2_wx": "wx", "gbm_nowx": "gbm", "chronos2_wq": "wq"}
ALPHA = 0.2


def quantiles(d: pd.DataFrame, prefix: str) -> pd.DataFrame:
    q = pd.DataFrame({k: d[f"{prefix}_{k}"].to_numpy() for k in ("q10", "q50", "q90")}, index=d.index)
    return pd.DataFrame(np.sort(q.to_numpy(), axis=1), index=d.index, columns=q.columns)


def calibrated(d: pd.DataFrame, q: pd.DataFrame, mode: str, h: int) -> pd.DataFrame:
    """Per-site margins from strictly earlier folds, applied to folds 2-4."""
    out = q.copy()
    out[:] = np.nan
    for _site, g in d.groupby("site"):
        for k in sorted(g["fold"].unique()):
            if k == g["fold"].min():
                continue
            test = g.index[g["fold"] == k]
            start = g.loc[test, "origin"].min()
            known = g["origin"] + pd.Timedelta(hours=h) <= start
            cal = g.index[known & ((g["fold"] < k) if mode == "expanding" else (g["fold"] == k - 1))]
            m = conformal_margin(g.loc[cal, "y_true"].to_numpy(), q.loc[cal], ALPHA)
            out.loc[test] = apply_margin(q.loc[test], m).to_numpy()
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", default="results/chronos2_rows.parquet")
    ap.add_argument("--out", default="results")
    args = ap.parse_args()
    raw = pd.read_parquet(args.rows)
    need = ["y_true", "gbmwx_q50", "wx_q50", "gbm_q50", "wq_q50"]
    raw = raw.dropna(subset=need).reset_index(drop=True)

    interval_rows, skill_rows, weekly = [], [], []
    for h, d in raw.groupby("horizon_h"):
        d = d.reset_index(drop=True)
        qs = {name: quantiles(d, p) for name, p in MEMBERS.items()}
        qs["blend_wx"] = blend_quantiles(qs["gbm_wx"], qs["chronos2_wx"])
        qs["blend_wq_wx"] = blend_quantiles(qs["gbm_wx"], qs["chronos2_wq"])
        evaluated = d["fold"] > d.groupby("site")["fold"].transform("min")
        y = d["y_true"].to_numpy()

        for name, q in qs.items():
            variants = {"raw": q.where(evaluated)}
            for mode in ("expanding", "last_fold"):
                variants[mode] = calibrated(d, q, mode, h)
            for var, qq in variants.items():
                ok = evaluated.to_numpy() & qq["q50"].notna().to_numpy()
                lo, hi = qq["q10"].to_numpy()[ok], qq["q90"].to_numpy()[ok]
                inside = (y[ok] >= lo) & (y[ok] <= hi)
                site_cov = pd.Series(inside).groupby(d["site"].to_numpy()[ok]).mean()
                interval_rows.append({
                    "horizon_h": h, "model": name, "interval": var, "n": int(ok.sum()),
                    "coverage": float(inside.mean()), "site_cov_min": float(site_cov.min()),
                    "site_cov_max": float(site_cov.max()), "mean_width": float((hi - lo).mean()),
                    "interval_score": float(interval_score(y[ok], lo, hi, ALPHA).mean())})

        # point skill vs best simple baseline per site, on the evaluated rows
        e = d[evaluated].copy()
        for name, q in qs.items():
            e[f"err_{name}"] = (q.loc[e.index, "q50"] - e["y_true"]).abs()
        for m in BASELINES:
            e[f"err_{m}"] = (e[m] - e["y_true"]).abs()
        best = e.groupby("site")[[f"err_{m}" for m in BASELINES]].sum().idxmin(axis=1).str.removeprefix("err_")
        errs = np.column_stack([e[f"err_{m}"].to_numpy() for m in BASELINES])
        pick = e["site"].map(best).map({m: i for i, m in enumerate(BASELINES)}).to_numpy()
        e["err_best"] = errs[np.arange(len(e)), pick]
        e["week"] = e["origin"].dt.strftime("%G-%V")
        wk = e.groupby(["site", "week"])[["err_best", *[f"err_{n}" for n in qs]]].sum().reset_index().assign(horizon_h=h)
        weekly.append(wk)
        for name in qs:
            a = skill_ci(wk[f"err_{name}"].to_numpy(), wk["err_best"].to_numpy(), seed=1)
            ss = wk.groupby("site")[[f"err_{name}", "err_best"]].sum()
            b = skill_ci(ss[f"err_{name}"].to_numpy(), ss["err_best"].to_numpy(), seed=2)
            skill_rows.append({"horizon_h": h, "model": name, "skill": a[0], "ci_lo": a[1], "ci_hi": a[2],
                               "site_ci_lo": b[1], "site_ci_hi": b[2],
                               "sites_better": int((ss[f"err_{name}"] < ss["err_best"]).sum()), "n_sites": len(ss)})

    out = Path(args.out)
    iv, sk = pd.DataFrame(interval_rows), pd.DataFrame(skill_rows)
    iv.to_csv(out / "blend_intervals.csv", index=False)
    sk.to_csv(out / "blend_skill.csv", index=False)
    pd.concat(weekly).to_csv(out / "blend_weekly.csv", index=False)
    pd.set_option("display.width", 200)
    print(sk.round(3).to_string(index=False))
    print(iv[iv.model.isin(["gbm_wx", "chronos2_wx", "blend_wx"])].round(3).to_string(index=False))
    metrics = {}
    for _, r in sk[sk.model.isin(["gbm_wx", "chronos2_wx", "blend_wx"])].iterrows():
        metrics[f"skill_{r.model}_{int(r.horizon_h)}h"] = r.skill
    for _, r in iv[(iv.model == "blend_wx") & (iv.interval == "expanding")].iterrows():
        metrics[f"coverage_blend_{int(r.horizon_h)}h"] = r.coverage
        metrics[f"interval_score_blend_{int(r.horizon_h)}h"] = r.interval_score
    track("blend-conformal", params={"alpha": ALPHA, "blend_weight": 0.5, "calibration": "expanding, earlier folds only",
                                     "rows": args.rows}, metrics=metrics,
          artifacts=[out / "blend_skill.csv", out / "blend_intervals.csv"], run_name="run_blend")


if __name__ == "__main__":
    main()
