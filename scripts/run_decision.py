"""Calibrated forecasts and alert decisions, evaluated walk-forward.

    python scripts/run_decision.py [--threshold 5.0] [--stride 4]

Per fold the timeline is: train | 90-day calibration | 91-day test. Models are trained on
origins whose targets end before calibration starts; conformal intervals and probability
scaling are fit on the calibration window; nothing touches the test window until scoring.
Writes results/decision_*.csv.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from dosentinel.baselines import HORIZONS_H
from dosentinel.calibration import PlattScaler, brier, cqr_correction, reliability_table
from dosentinel.decision import Costs, chance_baseline, decide, episodes_from_series, evaluate_alerts
from dosentinel.evaluate import walk_forward_folds
from dosentinel.features import base_features, horizon_features, load_site_frame, train_origin_mask
from dosentinel.models import fit_predict_proba, fit_predict_quantiles

CAL_DAYS = 90
MIN_CAL_CLASS = 15  # need this many events and non-events to fit the probability scaler
HORIZON_STEPS = 96


def site_predictions(site_id: str, clean_dir: str, thr: float, stride: int, n_folds: int = 4,
                     cal_days: int = CAL_DAYS):
    f = load_site_frame(site_id, clean_dir)
    v, idx = f["do"], f["do"].index
    X = base_features(f)
    folds = walk_forward_folds(idx, n_folds=n_folds)
    rows, diag = [], []
    for hh in HORIZONS_H:
        h = hh * 4
        Z = horizon_features(X, v, h)
        y = v.shift(-h)
        delta = (y - v).to_numpy()
        label = (y < thr).to_numpy().astype(int)
        y_ok = y.notna().to_numpy()
        for fold, _, t0, t1 in folds:
            cal_lo = t0 - pd.Timedelta(days=cal_days)
            tr = train_origin_mask(idx, h, cal_lo)
            tq = np.flatnonzero(tr & ~np.isnan(delta))[::stride]
            tc = np.flatnonzero(tr & y_ok)[::stride]
            is_cal_row = (idx > cal_lo) & train_origin_mask(idx, h, t0)
            is_test_row = (idx > t0) & (idx <= t1)
            ev = idx[is_cal_row | is_test_row]
            Zev = Z.loc[ev]
            quant = fit_predict_quantiles(Z.iloc[tq], delta[tq], Zev)
            p_raw = fit_predict_proba(Z.iloc[tc], label[tc], Zev)

            base = v.loc[ev].to_numpy()
            out = pd.DataFrame({"y_true": y.loc[ev].to_numpy(), "y_origin": base,
                                "q10": base + quant["q10"].to_numpy(),
                                "q50": base + quant["q50"].to_numpy(),
                                "q90": base + quant["q90"].to_numpy(), "p_raw": p_raw}, index=ev)
            cal = out.index <= t0
            ok_q = cal & out[["y_true", "q10", "q90"]].notna().all(axis=1).to_numpy()
            Q = cqr_correction(out.loc[ok_q, "y_true"].to_numpy(), out.loc[ok_q, "q10"].to_numpy(),
                               out.loc[ok_q, "q90"].to_numpy(), alpha=0.2)
            out["q10c"], out["q90c"] = out["q10"] - Q, out["q90"] + Q

            ok_p = cal & out[["y_true", "p_raw"]].notna().all(axis=1).to_numpy()
            lab = (out.loc[ok_p, "y_true"] < thr).astype(int).to_numpy()
            pos, neg = int(lab.sum()), int((1 - lab).sum())
            scaled = pos >= MIN_CAL_CLASS and neg >= MIN_CAL_CLASS
            out["p_cal"] = (PlattScaler().fit(out.loc[ok_p, "p_raw"].to_numpy(), lab)
                            .transform(out["p_raw"].to_numpy()) if scaled else out["p_raw"])
            test = out[~cal].copy()
            test["horizon_h"], test["fold"] = hh, fold
            test["base_rate"] = float(label[tc].mean()) if len(tc) else np.nan
            # Reference forecaster: P(low at t+h) conditioned only on whether DO is low now,
            # with rates learned on the training window. Tests whether the model adds
            # information beyond "it is low now".
            cur_low = (v.to_numpy()[tc] < thr)
            have = ~np.isnan(v.to_numpy()[tc])
            r_low = label[tc][have & cur_low].mean() if (have & cur_low).any() else test["base_rate"].iloc[0]
            r_hi = label[tc][have & ~cur_low].mean() if (have & ~cur_low).any() else test["base_rate"].iloc[0]
            now = test["y_origin"]
            test["p_state"] = np.where(now.isna(), test["base_rate"], np.where(now < thr, r_low, r_hi))
            rows.append(test)
            diag.append({"horizon_h": hh, "fold": fold, "cqr_margin": Q, "cal_events": pos,
                         "cal_nonevents": neg, "platt_applied": scaled})
    return f, pd.concat(rows), pd.DataFrame(diag), folds


def calibration_summary(P: pd.DataFrame, thr: float) -> pd.DataFrame:
    rows = []
    for hh, g in P.groupby("horizon_h"):
        a = g.dropna(subset=["y_true", "q10", "q90"])
        b = g.dropna(subset=["y_true", "p_raw", "p_cal"])
        lab = (b["y_true"] < thr).astype(int).to_numpy()
        ref = np.full(len(b), b["base_rate"].mean())
        bs = {k: brier(b[k].to_numpy(), lab) for k in ("p_raw", "p_cal", "p_state")}
        b0 = brier(ref, lab)
        rows.append({
            "horizon_h": hh,
            "coverage_raw": float(((a.y_true >= a.q10) & (a.y_true <= a.q90)).mean()),
            "coverage_conformal": float(((a.y_true >= a.q10c) & (a.y_true <= a.q90c)).mean()),
            "width_raw": float((a.q90 - a.q10).mean()), "width_conformal": float((a.q90c - a.q10c).mean()),
            "event_rate": float(lab.mean()),
            "brier_base_rate": b0, "brier_state": bs["p_state"],
            "brier_raw": bs["p_raw"], "brier_cal": bs["p_cal"],
            "bss_vs_state_raw": 1 - bs["p_raw"] / bs["p_state"],
            "bss_vs_state_cal": 1 - bs["p_cal"] / bs["p_state"],
        })
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/sites.yaml")
    ap.add_argument("--clean", default="data/clean")
    ap.add_argument("--out", default="results")
    ap.add_argument("--threshold", type=float, default=5.0)
    ap.add_argument("--stride", type=int, default=4)
    ap.add_argument("--folds", type=int, default=4, help="number of 91-day test folds")
    ap.add_argument("--cal-days", type=int, default=CAL_DAYS,
                    help="length of the calibration window before each test fold (365 covers every season)")
    ap.add_argument("--tag", default="", help="suffix for output files, e.g. cal365")
    args = ap.parse_args()
    thr = args.threshold

    cfg = yaml.safe_load(Path(args.config).read_text())
    out = Path(args.out)
    out.mkdir(exist_ok=True)
    cal_rows, rel_rows, alert_rows, diag_rows = [], [], [], []
    for site in cfg["sites"]:
        sid, tag = site["id"], dict(site=site["id"], role=site["role"])
        f, P, diag, folds = site_predictions(sid, args.clean, thr, args.stride, args.folds, args.cal_days)
        diag_rows.append(diag.assign(**tag))
        cal_rows.append(calibration_summary(P, thr).assign(**tag))
        b = P.dropna(subset=["y_true", "p_cal"])
        rel_rows.append(reliability_table(b["p_cal"].to_numpy(), (b["y_true"] < thr).to_numpy()).assign(**tag))

        # ---- decision timeline: every 15-min origin in the test span, with 24 h of lookahead
        v = f["do"]
        tl = v.index[(v.index > folds[0][2]) & (v.index <= v.index.max() - pd.Timedelta("24h"))]
        p_within = (P.reset_index(names="t").pivot_table(index="t", columns="horizon_h", values="p_cal")
                    .max(axis=1).reindex(tl).fillna(0.0).to_numpy())
        flags = pd.read_parquet(f"{args.clean}/{sid}_00300.parquet")
        bad = flags[["out_of_range", "flatline", "spike"]].any(axis=1)
        suspect = (bad.rolling(8, min_periods=1).max().astype(bool) | v.isna()).reindex(tl).to_numpy()
        v_ext = v.loc[tl[0]: tl[-1] + pd.Timedelta("24h")]
        episodes = [e for e in episodes_from_series(v_ext, thr) if e[0] < len(tl)]
        v_now = v.reindex(tl).to_numpy()
        not_low = v_now >= thr  # NaN compares False: no anticipation credit on missing data

        def score(rule: str, alert: np.ndarray, ratio: float, chance: bool = False, *,
                  _episodes=episodes, _not_low=not_low, _tag=tag, **extra) -> None:
            kw = {"horizon_steps": HORIZON_STEPS, "costs": Costs(ratio, 1.0), "not_low": _not_low}
            row = {**_tag, "rule": rule, "miss_to_fa_ratio": ratio, **extra, **evaluate_alerts(_episodes, alert, **kw)}
            if chance:
                row.update(chance_baseline(_episodes, alert, **kw))
            alert_rows.append(row)

        for ratio in (3, 10, 30):
            c = Costs(ratio, 1.0)
            act = decide(p_within, suspect, c.cutoff)
            score("gbm_probability", act == "warning", ratio, chance=True, cutoff=round(c.cutoff, 3),
                  maintenance_steps=int((act == "maintenance").sum()),
                  suppressed_warning_steps=int(((p_within > c.cutoff) & suspect).sum()))
        score("gbm_probability_no_suppression", p_within > Costs(10, 1).cutoff, 10)
        # Descriptive only: cutoffs chosen on the test window are optimistic.
        for cut in (0.05, 0.1, 0.2, 0.3, 0.5, 0.7):
            score("gbm_cutoff_sweep", decide(p_within, suspect, cut) == "warning", 10, chance=True, cutoff=cut)
        for m in (0.0, 0.5, 1.0):
            score("current_value", (v_now < thr + m) & ~suspect, 10, chance=True, margin=m)
        print(f"{sid} ({site['role']}): {len(episodes)} episodes over {len(tl) / 96:.0f} days", flush=True)

    suffix = f"_{args.tag}" if args.tag else ""
    cal = pd.concat(cal_rows)
    cal.to_csv(out / f"decision_calibration{suffix}.csv", index=False)
    pd.concat(rel_rows).to_csv(out / f"decision_reliability{suffix}.csv", index=False)
    pd.concat(diag_rows).to_csv(out / f"decision_fold_diagnostics{suffix}.csv", index=False)
    pd.DataFrame(alert_rows).to_csv(out / f"decision_alerts{suffix}.csv", index=False)


if __name__ == "__main__":
    main()
