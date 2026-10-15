"""Precompute everything the standalone dashboard shows, from the trained bundles and results/.

    python scripts/build_dashboard_data.py

Replay: real forecasts from models/*.joblib at every 6th hour over the last 30 days, for all
sites, plus hourly observed DO around that window. Forecasts use the weather-aware model with the
archived day-ahead weather forecast (what a live request would have had); `p` is the probability
that drives the action: the classifier's, or the forecast interval's where a site has no trained
classifier. Evidence: tables read from results/.
Writes dashboard/data.json.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import yaml
from joblib import Parallel, delayed

sys.path.insert(0, "src")
from dosentinel.features import load_site_frame  # noqa: E402
from dosentinel.serving import forecast  # noqa: E402
from dosentinel.stats import skill_ci  # noqa: E402
from dosentinel.weather import load_weather  # noqa: E402

R = Path("results")
OUT = Path("dashboard")
OUT.mkdir(parents=True, exist_ok=True)
HOURS = [1, 3, 6, 12, 24]
FLAG_KEYS = ["rules", "flatline", "jump_persistence", "jump_interval_1h", "latest_value_missing"]
NAMES = {
    "USGS-03293500": "Middle Fork Beargrass Creek, Louisville KY",
    "USGS-02344630": "Line Creek below Lake McIntosh, GA",
    "USGS-08065350": "Trinity River near Crockett, TX",
    "USGS-02336313": "Woodall Creek, Atlanta GA",
    "USGS-02110400": "Buck Creek near Longs, SC",
    "USGS-10133650": "East Canyon Creek, Park City UT",
    "USGS-01654500": "Long Branch near Annandale, VA",
    "USGS-05576195": "Sugar Creek near Chatham, IL",
    "USGS-02110704": "Waccamaw River at Conway, SC",
    "USGS-08068000": "West Fork San Jacinto River, Conroe TX",
    "USGS-07143672": "Little Arkansas River near Halstead, KS",
    "USGS-03290500": "Kentucky River at Lock 2, Lockport KY",
    "USGS-01184000": "Connecticut River at Thompsonville, CT",
    "USGS-08211200": "Nueces River at Bluntzer, TX",
}
REGIME = {"flashy": "Flashy stream", "flashy_urban": "Flashy urban stream", "regulated": "Regulated, below a lake",
          "moderate": "Moderate flow", "steady": "Steady river", "slow_river": "Large slow river",
          "no_discharge": "Blackwater river (no discharge gauge)"}


def replay_site(sid: str, asofs: list[str]) -> dict:
    b = joblib.load(f"models/{sid}.joblib")
    f = load_site_frame(sid, "data/clean")
    W = load_weather(sid) if b.wx_quantile_models else None
    out, used = [], set()
    t0 = time.time()
    for a in asofs:
        t = pd.Timestamp(a)
        rep = forecast(b, f.loc[:t].iloc[-96 * 40:], 10.0, weather=W)
        used.add(rep.get("forecast_model"))
        fl = rep["fault_flags"]
        p = rep["p_low_do_within_24h"]
        if rep.get("alert_rule") == "forecast_interval":
            p = rep["p_low_do_forecast_interval"]
        row = {"p": None if p is None else round(p, 4),
               "do": None if rep["latest_do_mgL"] is None else round(rep["latest_do_mgL"], 2),
               "obs": round(rep["observed_fraction_last_24h"], 3),
               "fl": int(sum((1 << i) for i, k in enumerate(FLAG_KEYS) if fl.get(k))),
               "f": [[round(x["low_mgL"], 2), round(x["median_mgL"], 2), round(x["high_mgL"], 2)] for x in rep["forecasts"]]}
        out.append(row)
    return {"site": sid, "rows": out, "seconds": round(time.time() - t0, 1), "models": sorted(m for m in used if m),
            "rule": "forecast_interval" if isinstance(b.clf, float) else "probability",
            "trained_through": b.trained_through.isoformat(), "calibrated_through": b.calibrated_through.isoformat()}


def main() -> None:
    cfg = yaml.safe_load(Path("config/sites.yaml").read_text())["sites"]
    coords = json.loads(Path("data/site_coords.json").read_text())
    end = min(load_site_frame(s["id"], "data/clean").index.max() for s in cfg).floor("6h")
    asofs = [t.isoformat() for t in pd.date_range(end=end - pd.Timedelta("24h"), periods=30 * 4, freq="6h", tz="UTC")]
    print(f"replay window {asofs[0]} .. {asofs[-1]} ({len(asofs)} times x {len(cfg)} sites)", flush=True)
    t0 = time.time()
    res = Parallel(n_jobs=7)(delayed(replay_site)(s["id"], asofs) for s in cfg)
    print(f"forecasts done in {time.time() - t0:.0f}s; per-site seconds {[r['seconds'] for r in res]}", flush=True)
    by = {r["site"]: r for r in res}

    sites = []
    t_lo = pd.Timestamp(asofs[0]) - pd.Timedelta(hours=96)
    t_hi = pd.Timestamp(asofs[-1]) + pd.Timedelta(hours=30)
    hours = pd.date_range(t_lo.ceil("h"), t_hi.floor("h"), freq="1h", tz="UTC")
    for s in cfg:
        sid = s["id"]
        f = load_site_frame(sid, "data/clean")
        do = f["do"][f.index.minute == 0].reindex(hours)
        dm = f["do"].resample("1D").min().dropna()
        sites.append({
            "id": sid, "name": NAMES[sid], "short": NAMES[sid].split(",")[0], "state": s["state"],
            "lat": coords[sid]["lat"], "lon": coords[sid]["lon"], "regime": REGIME.get(s["role"], s["role"]),
            "area": s["drainage_sqmi"], "low_day_share": round(float((dm < 5).mean()), 3),
            "trained_through": by[sid]["trained_through"], "calibrated_through": by[sid]["calibrated_through"],
            "alert_rule": by[sid]["rule"],
            "series": [None if np.isnan(v) else round(float(v), 2) for v in do.to_numpy()],
            "replay": by[sid]["rows"]})

    # ---------------- evidence, read from the saved results
    d = pd.read_parquet(R / "chronos2_rows.parquet").dropna(
        subset=["y_true", "wx_q50", "gbm_q50", "gbmwx_q50", "persistence", "seasonal_naive", "persistence_diel_delta"])
    d["blend"] = 0.5 * (d.wx_q50 + d.gbmwx_q50)
    d["week"] = d.origin.dt.tz_localize(None).dt.to_period("W").dt.start_time
    B = ["persistence", "seasonal_naive", "seasonal_mean_7d", "persistence_diel_delta"]
    skill = []
    for h, g in d.groupby("horizon_h"):
        tot = g.groupby("site").apply(lambda x: pd.Series({b: (x[b] - x.y_true).abs().sum() for b in B}), include_groups=False)
        best = tot.idxmin(axis=1)
        g = g.assign(best=[abs(r[best[r.site]] - r.y_true) for _, r in g.iterrows()],
                     a_gbm=(g.gbm_q50 - g.y_true).abs(), a_gbmwx=(g.gbmwx_q50 - g.y_true).abs(),
                     a_blend=(g.blend - g.y_true).abs())
        wk = g.groupby(["site", "week"])[["best", "a_gbm", "a_gbmwx", "a_blend"]].sum()
        ci = skill_ci(wk["a_gbm"].to_numpy(), wk["best"].to_numpy(), seed=1)
        civ = skill_ci(wk["a_blend"].to_numpy(), wk["best"].to_numpy(), seed=2)
        n = len(g)
        skill.append({"h": int(h), "mae_baseline": float(g.best.sum() / n), "mae_gbm": float(g.a_gbm.sum() / n),
                      "mae_gbm_wx": float(g.a_gbmwx.sum() / n), "mae_blend": float(g.a_blend.sum() / n),
                      "skill_gbm": ci[0], "skill_gbm_lo": ci[1], "skill_gbm_hi": ci[2],
                      "skill_blend": civ[0], "skill_blend_lo": civ[1], "skill_blend_hi": civ[2]})
    cov = pd.read_csv(R / "decision_calibration_cal365.csv").groupby("horizon_h")[["coverage_raw", "coverage_conformal"]].mean()
    ab = pd.read_csv(R / "alert_budget_summary.csv")
    abm = ab[ab.family.isin(["model", "naive"])].pivot_table(index="budget", columns="family", values="recall", aggfunc="mean")
    ev, nu = pd.read_csv(R / "drift_events.csv"), pd.read_csv(R / "drift_events_null.csv")
    def pick(t: pd.DataFrame) -> pd.Series:
        return t[(t.detector == "interval_1h") & (t.threshold_type == "oracle")].groupby("duration_days").detected.mean()

    fe = pd.read_csv(R / "faults_events.csv")
    fk = fe[fe.detector.isin(["interval_1h", "run_length"])].groupby(["detector", "kind"]).detected.mean().unstack()
    wk_ = pd.read_csv(R / "weather_realistic_ablate_weekly.csv")
    g24 = wk_[wk_.horizon_h == 24]
    weather = {k: float(1 - g24[c].sum() / g24["off"].sum()) for k, c in (("rain", "on_rain"), ("atmos", "on_atmos"), ("all", "on"))}
    evidence = {
        "skill": skill,
        "coverage": [{"h": int(h), "raw": float(r.coverage_raw), "conformal": float(r.coverage_conformal)} for h, r in cov.iterrows()],
        "alerts": [{"budget": float(b), "model": float(r["model"]), "naive": float(r["naive"])} for b, r in abm.iterrows()],
        "drift": [{"days": float(k), "real": float(real), "null": float(pick(nu).get(k, np.nan))} for k, real in pick(ev).items()],
        "faults": {"spike": float(fk.loc["interval_1h", "spike"]), "stuck_low": float(fk.loc["interval_1h", "stuck_low"]),
                   "offset": float(fk.loc["interval_1h", "offset"]), "flatline": float(fk.loc["run_length", "flatline"]),
                   "drift": float(fk.loc["interval_1h", "drift"])},
        "weather24": weather,
    }
    models = sorted({m for r in res for m in r["models"]})
    data = {"asofs": asofs, "forecast_models": models, "horizons": HOURS, "flag_keys": FLAG_KEYS, "series_start": hours[0].isoformat(),
            "sites": sites, "evidence": evidence, "generated": pd.Timestamp.now(tz="UTC").isoformat(timespec="seconds")}
    p = OUT / "data.json"
    p.write_text(json.dumps(data, separators=(",", ":")))
    print(f"wrote {p} ({p.stat().st_size / 1e3:.0f} KB)")


if __name__ == "__main__":
    main()
