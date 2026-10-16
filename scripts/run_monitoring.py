"""One cycle of the scheduled monitoring job (see dosentinel.monitoring_job).

    USGS_API_KEY=... python scripts/run_monitoring.py --state monitoring_state

Issues a forecast per site from live USGS data, scores matured forecasts against observed DO,
applies the retrain rule, checks input drift against models/drift_reference.json and writes
report.json and report.md into --state. Meant for GitHub Actions (.github/workflows/monitor.yml);
runs locally too. Exit code 1 with --fail-on-retrain when any site needs retraining.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import joblib
import yaml

sys.path.insert(0, "src")
from dosentinel import api  # noqa: E402
from dosentinel.monitoring_job import run_cycle  # noqa: E402
from dosentinel.tracking import track  # noqa: E402
from dosentinel.weather_live import to_snapshot  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/sites.yaml")
    ap.add_argument("--models", default="models")
    ap.add_argument("--state", default="monitoring_state")
    ap.add_argument("--reference", default="models/drift_reference.json")
    ap.add_argument("--sites", nargs="*", help="restrict to these site ids")
    ap.add_argument("--no-issue", action="store_true", help="score and check only; issue no new forecasts")
    ap.add_argument("--fail-on-retrain", action="store_true")
    args = ap.parse_args()

    ids = [s["id"] for s in yaml.safe_load(Path(args.config).read_text())["sites"]]
    if args.sites:
        ids = [i for i in ids if i in args.sites]
    bundles = {sid: joblib.load(Path(args.models) / f"{sid}.joblib") for sid in ids}
    ref = json.loads(Path(args.reference).read_text()) if Path(args.reference).exists() else None
    rep = run_cycle(bundles, api._fetch_live_frame, Path(args.state), reference=ref,
                    weather=api.live_weather, issue=not args.no_issue)
    # publish the weather this cycle fetched, so the API can fall back to it when its own address is
    # throttled; an unsuccessful fetch leaves the previous snapshot in place (the API ignores old ones)
    fresh = {sid: W for sid, (_, W) in api._wx_cache.items() if api._wx_status.get(sid) == "ok"}
    if fresh:
        (Path(args.state) / "weather_snapshot.json").write_text(json.dumps(to_snapshot(fresh), separators=(",", ":")))
        print(f"weather snapshot written for {len(fresh)} sites")
    print((Path(args.state) / "report.md").read_text())
    track("monitoring", params={"sites": len(ids), "issued": not args.no_issue},
          metrics={"sites_failed": len(rep["sites_failed"]), "retrain_recommended": len(rep["retrain_recommended"]),
                   "input_drift_alerts": len(rep["input_drift_alerts"]), "holdout_rows": rep["forward_holdout_rows"]},
          artifacts=[Path(args.state) / "report.json"], run_name="monitoring_cycle")
    if args.fail_on_retrain and rep["retrain_recommended"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
