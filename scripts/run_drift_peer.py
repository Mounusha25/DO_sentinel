"""Does a nearby sensor help detect slow drift? (cross-site consistency)

    python scripts/run_drift_peer.py [--reps 5] [--null] [--max-km 70]

Slow drift is invisible to one sensor because the forecast-residual noise is as large as the
drift. A neighbouring station on a similar water body sees the same weather, so its *concurrent*
DO should explain much of the target's residual noise. Here the same drift-only faults as
run_drift.py are injected into one site of each close pair, and the target's DO is predicted
from its own covariates plus the (uncorrupted) peer's concurrent DO, with and without the
15-day-lag anchors. Windowed residuals are thresholded exactly as in run_drift.py; the
comparator "ll_72h" is the single-sensor detector from that script on the same events.

Sites closer than --max-km are paired; each member is used as the target in turn.
Writes results/drift_peer_*.csv (suffix _null for the zero-magnitude chance control).
"""
from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from run_drift import ECHO_DAYS, WINDOWS_H, _windowed

from dosentinel.detect import threshold_at_rate
from dosentinel.faults import inject_drifts
from dosentinel.features import base_features, load_site_frame, long_lag_features
from dosentinel.models import fit_predict_quantiles

DETECTORS = [f"peer_{w}h" for w in WINDOWS_H] + ["peer_ll_72h", "ll_72h"]


def haversine_km(a: dict, b: dict) -> float:
    la1, lo1, la2, lo2 = (np.radians(x) for x in (a["lat"], a["lon"], b["lat"], b["lon"]))
    d = np.sin((la2 - la1) / 2) ** 2 + np.cos(la1) * np.cos(la2) * np.sin((lo2 - lo1) / 2) ** 2
    return float(6371 * 2 * np.arcsin(np.sqrt(d)))


def peer_features(peer_do: pd.Series, idx: pd.DatetimeIndex) -> pd.DataFrame:
    p = peer_do.reindex(idx)
    return pd.DataFrame({"peer_do": p, "peer_mean_24h": p.rolling(96, min_periods=48).mean(),
                         "peer_d4": p - p.shift(4), "peer_d24": p - p.shift(24)})


def score(res: pd.Series, hours: int, cal: pd.Series) -> pd.Series:
    r = _windowed(res, hours)
    med = r[cal].median()
    sc = 1.4826 * (r[cal] - med).abs().median()
    return (r - med).abs() / max(sc, 0.05)


def run_pair(target: str, peer: str, role: str, clean_dir: str, fpr: float, n_reps: int, seed: int, magnitudes):
    f = load_site_frame(target, clean_dir)
    v = f["do"]
    idx = v.index
    P = peer_features(load_site_frame(peer, clean_dir)["do"], idx)
    end = idx.max()
    t2, t1 = end - pd.Timedelta(days=365), end - pd.Timedelta(days=730)
    cal = (idx > t1) & (idx <= t2)
    test = idx > t2
    start_pos = int(idx.searchsorted(t2)) + 1
    X = base_features(f)
    cov_cols = [c for c in X.columns if not c.startswith("do")]
    LL = long_lag_features(v)
    rows = idx > t1
    have = v.notna().to_numpy() & P["peer_do"].notna().to_numpy()
    sel_p = np.flatnonzero((idx <= t1) & have)[::4]
    sel_pl = np.flatnonzero((idx <= t1) & have & LL["ll_anchor"].notna().to_numpy())[::4]
    sel_l = np.flatnonzero((idx <= t1) & v.notna().to_numpy() & LL["ll_anchor"].notna().to_numpy())[::4]
    F_p = pd.concat([X[cov_cols], P], axis=1)
    F_pl = pd.concat([X[cov_cols], P, LL], axis=1)
    F_l = pd.concat([X[cov_cols], LL], axis=1)
    y = v.to_numpy()

    ev_rows, fp_rows = [], []
    thr: dict[str, float] = {}
    for rep in range(n_reps):
        rng = np.random.default_rng(seed * 100 + rep)
        v_c, events, _ = inject_drifts(v, rng, magnitudes=magnitudes, per_cell=1, start_pos=start_pos)
        fc = f.copy()
        fc["do"] = v_c
        Xc = base_features(fc)
        LLc = long_lag_features(v_c)
        Cc = Xc[cov_cols]
        preds = {
            "peer": fit_predict_quantiles(F_p.iloc[sel_p], y[sel_p], pd.concat([Cc, P], axis=1).loc[rows],
                                          quantiles=(0.5,))["q50"],
            "peer_ll": fit_predict_quantiles(F_pl.iloc[sel_pl], y[sel_pl], pd.concat([Cc, P, LLc], axis=1).loc[rows],
                                             quantiles=(0.5,))["q50"],
            "ll": fit_predict_quantiles(F_l.iloc[sel_l], y[sel_l], pd.concat([Cc, LLc], axis=1).loc[rows],
                                        quantiles=(0.5,))["q50"],
        }
        res = {k: v_c - p.reindex(idx) for k, p in preds.items()}
        S = pd.DataFrame(index=idx)
        for w in WINDOWS_H:
            S[f"peer_{w}h"] = score(res["peer"], w, cal)
        S["peer_ll_72h"] = score(res["peer_ll"], 72, cal)
        S["ll_72h"] = score(res["ll"], 72, cal)

        near = np.zeros(len(idx), bool)
        for e in events:
            near[max(0, e.start - 96): e.end + ECHO_DAYS * 96] = True
        strict = test & ~near & v_c.notna().to_numpy() & P["peer_do"].notna().to_numpy()
        weeks = max(strict.sum() / 672, 1e-9)
        if rep == 0:
            thr = {d: threshold_at_rate(S.loc[cal, d], fpr) for d in DETECTORS}
        for d in DETECTORS:
            for kind, th in (("deployable", thr[d]), ("oracle", threshold_at_rate(S.loc[strict, d], fpr))):
                fl = (S[d] > th).to_numpy()
                fp_rows.append({"site": target, "peer": peer, "role": role, "rep": rep, "detector": d,
                                "threshold_type": kind, "false_flags_per_week": float(fl[strict].sum() / weeks)})
                for e in events:
                    hit = np.flatnonzero(fl[e.start:e.end])
                    ev_rows.append({"site": target, "peer": peer, "role": role, "rep": rep, "detector": d,
                                    "threshold_type": kind, "magnitude": abs(e.magnitude),
                                    "duration_days": (e.end - e.start) / 96, "detected": bool(hit.size)})
    return pd.DataFrame(ev_rows), pd.DataFrame(fp_rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/sites.yaml")
    ap.add_argument("--clean", default="data/clean")
    ap.add_argument("--coords", default="data/site_coords.json")
    ap.add_argument("--out", default="results")
    ap.add_argument("--fpr", type=float, default=0.001)
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--max-km", type=float, default=70.0)
    ap.add_argument("--null", action="store_true", help="zero-magnitude drifts: detection is pure chance")
    args = ap.parse_args()
    cfg = yaml.safe_load(Path(args.config).read_text())
    roles = {s["id"]: s["role"] for s in cfg["sites"]}
    coords = json.loads(Path(args.coords).read_text())
    pairs = [(a, b) for a, b in itertools.combinations(roles, 2) if haversine_km(coords[a], coords[b]) <= args.max_km]
    ev, fp = [], []
    for i, (a, b) in enumerate(pairs):
        for k, (t, p) in enumerate(((a, b), (b, a))):
            e, f = run_pair(t, p, roles[t], args.clean, args.fpr, args.reps, seed=11 + 2 * i + k,
                            magnitudes=(0.0,) if args.null else (0.5, 1.0, 2.0))
            ev.append(e)
            fp.append(f)
            print(f"target {t} <- peer {p} ({haversine_km(coords[a], coords[b]):.0f} km): {len(e) // (len(DETECTORS) * 2)} events",
                  flush=True)
    sfx = "_null" if args.null else ""
    out = Path(args.out)
    pd.concat(ev).to_csv(out / f"drift_peer_events{sfx}.csv", index=False)
    pd.concat(fp).to_csv(out / f"drift_peer_false_flags{sfx}.csv", index=False)


if __name__ == "__main__":
    main()
