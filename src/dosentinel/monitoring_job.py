"""One cycle of the scheduled monitoring job: issue forecasts, score matured ones, check drift.

The job is what turns the forecast log into the forward holdout PROBLEM.md describes. Each cycle:

1. fetches the latest data for every site and issues a forecast (logged, with the model that
   answered and why weather was or was not used);
2. scores every logged forecast whose target time has passed against the observed DO, and appends
   the newly matured ones to a cumulative scored table (never rescored, never dropped);
3. applies the retrain rule per site and checks input drift against the training-period reference;
4. writes a JSON and a Markdown report.

All state lives in one directory (log, scored table, reports), so a CI job can keep it on a branch.
Network access is injected (`fetch`, `weather`) so the cycle is testable without USGS.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pandas as pd

from .drift import input_drift, log_drift
from .monitoring import input_health, log_forecast, read_log, reference_maes, score_log, site_status
from .serving import SiteBundle, forecast

SCORED_KEY = ["site_id", "issued_at", "horizon_h"]
SCORED_COLUMNS = [*SCORED_KEY, "as_of", "target_time", "median_mgL", "low_mgL", "high_mgL", "p_low_do_within_24h",
                  "action", "forecast_model", "observed", "abs_error", "inside"]


def merge_scored(existing: pd.DataFrame | None, new: pd.DataFrame) -> pd.DataFrame:
    """Append matured forecasts to the cumulative table; a (site, issue time, horizon) is scored once."""
    new = new.dropna(subset=["abs_error"])
    new = new[[c for c in SCORED_COLUMNS if c in new.columns]]
    if existing is None or existing.empty:
        return new.reset_index(drop=True)
    for df in (existing, new):
        df["issued_at"] = pd.to_datetime(df["issued_at"], utc=True)
    both = pd.concat([existing, new], ignore_index=True)
    return both.drop_duplicates(SCORED_KEY, keep="first").reset_index(drop=True)


def holdout_summary(scored: pd.DataFrame) -> list[dict]:
    """Forward-holdout accuracy per forecaster and horizon (n, MAE, 80% coverage)."""
    if scored is None or scored.empty:
        return []
    out = []
    for (model, h), g in scored.groupby(["forecast_model", "horizon_h"]):
        out.append({"forecast_model": model, "horizon_h": int(h), "n": int(len(g)),
                    "mae": round(float(g["abs_error"].mean()), 4), "coverage": round(float(g["inside"].mean()), 4)})
    return out


def run_cycle(bundles: dict[str, SiteBundle], fetch: Callable[[str], pd.DataFrame],
              state: Path, reference: dict | None = None,
              weather: Callable[[SiteBundle], tuple[pd.DataFrame | None, str]] | None = None,
              now: pd.Timestamp | None = None, issue: bool = True, cost_ratio: float = 10.0) -> dict:
    """Run one cycle over `bundles` (site id -> bundle); returns the report dict (also written to disk)."""
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    state.mkdir(parents=True, exist_ok=True)
    log_path, scored_path = state / "forecasts.jsonl", state / "scored.csv"
    frames: dict[str, pd.DataFrame] = {}
    sites: dict[str, dict] = {sid: {"site_id": sid} for sid in bundles}

    for sid, b in bundles.items():
        try:
            frames[sid] = fetch(sid)
        except Exception as e:  # one site's outage must not stop the others
            sites[sid]["error"] = f"{type(e).__name__}: {str(e)[:160]}"
            continue
        if not issue:
            continue
        W, wstatus = weather(b) if weather is not None else (None, "not_requested")
        try:
            rep = forecast(b, frames[sid], cost_ratio, weather=W)
        except ValueError as e:
            sites[sid]["error"] = f"forecast: {e}"
            continue
        rep["weather_status"] = wstatus
        n = log_forecast(rep, log_path, issued_at=now)
        sites[sid].update(issued=n, forecast_model=rep.get("forecast_model"), weather_status=wstatus,
                          action=rep.get("action"), as_of=rep.get("as_of"))

    log = read_log(log_path)
    scored_all = pd.read_csv(scored_path) if scored_path.exists() else None
    for sid, b in bundles.items():
        if sid not in frames:
            continue
        observed = frames[sid]["do"].dropna()
        mine = log[log["site_id"] == sid] if len(log) else log
        matured = score_log(mine, observed)
        scored_all = merge_scored(scored_all, matured)
        sites[sid]["retrain"] = site_status(b, log, frames[sid]["do"], now=now)
        if reference and sid in reference:
            d = input_drift(frames[sid], reference[sid]["features"])
            sites[sid]["input_drift"] = {"worst": d["worst"], "n_major": d["n_major"], "alert": d["alert"],
                                         "features": {k: d[k]["status"] for k in ("do", "temp", "spcond", "turb", "q")}}
        sites[sid]["output_drift"] = log_drift(log, sid)
        sites[sid]["input_health"] = {"do_observed_7d": input_health(frames[sid])["parameters"]["do"]["observed_7d"]}
        sites[sid]["reference_mae"] = reference_maes(b.metrics).get("gbm", {})

    if scored_all is not None:
        scored_all.to_csv(scored_path, index=False)
    summary = holdout_summary(scored_all if scored_all is not None else pd.DataFrame())
    report = {
        "generated": now.isoformat(timespec="seconds"),
        "sites_checked": len(frames), "sites_failed": [s for s, v in sites.items() if "error" in v],
        "retrain_recommended": sorted(s for s, v in sites.items() if v.get("retrain", {}).get("retrain")),
        "input_drift_alerts": sorted(s for s, v in sites.items() if v.get("input_drift", {}).get("alert")),
        "forward_holdout": summary,
        "forward_holdout_rows": int(0 if scored_all is None else len(scored_all)),
        "sites": sites,
    }
    (state / "report.json").write_text(json.dumps(report, indent=2, default=str))
    (state / "report.md").write_text(render_markdown(report))
    return report


def _drift_cell(d: dict | None) -> str:
    if not d:
        return "-"
    return d["worst"] + (f" ({d['n_major']} major)" if d.get("n_major") else "")


def render_markdown(r: dict) -> str:
    lines = [f"# Monitoring report, {r['generated']}", "",
             f"* Sites checked: {r['sites_checked']} (failed: {', '.join(r['sites_failed']) or 'none'})",
             f"* Retrain recommended: {', '.join(r['retrain_recommended']) or 'none'}",
             f"* Input drift alerts (two or more inputs far outside their normal year-to-year range): "
             f"{', '.join(r['input_drift_alerts']) or 'none'}",
             f"* Matured forecasts scored so far (forward holdout): {r['forward_holdout_rows']}", ""]
    if r["forward_holdout"]:
        lines += ["## Forward holdout", "", "| Forecaster | Horizon | n | MAE (mg/L) | 80% coverage |", "|---|---|---|---|---|"]
        lines += [f"| {x['forecast_model']} | {x['horizon_h']} h | {x['n']} | {x['mae']:.3f} | {x['coverage']:.2f} |"
                  for x in r["forward_holdout"]]
        lines.append("")
    lines += ["## Sites", "", "| Site | Model | Weather | Action | Input drift | Output drift | Retrain |", "|---|---|---|---|---|---|---|"]
    for sid, s in sorted(r["sites"].items()):
        if "error" in s and "retrain" not in s:
            lines.append(f"| {sid} | - | - | - | - | - | error: {s['error']} |")
            continue
        rt = s.get("retrain", {})
        lines.append(f"| {sid} | {s.get('forecast_model', '-')} | {s.get('weather_status', '-')} | {s.get('action', '-')} | "
                     f"{_drift_cell(s.get('input_drift'))} | {s.get('output_drift', {}).get('status', '-')} | "
                     f"{'yes: ' + '; '.join(rt.get('reasons', [])) if rt.get('retrain') else 'no'} |")
    return "\n".join(lines) + "\n"

