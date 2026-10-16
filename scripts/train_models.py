"""Train and save one serving bundle per site in config/sites.yaml.

    python scripts/train_models.py [--out models]

Each bundle is trained on data before the final calibration year and calibrated on that year
(conformal margins, probability scaling, fault thresholds), then saved with joblib.
Re-run on a schedule to refresh; the bundle records its training and calibration dates.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import joblib
import pandas as pd
import yaml

from dosentinel.features import load_site_frame
from dosentinel.serving import train_bundle
from dosentinel.tracking import track
from dosentinel.weather import load_weather


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/sites.yaml")
    ap.add_argument("--clean", default="data/clean")
    ap.add_argument("--out", default="models")
    ap.add_argument("--cal-days", type=int, default=365)
    ap.add_argument("--stride", type=int, default=4)
    ap.add_argument("--weather", default="data/weather", help="weather tables; adds weather-aware models ('' to skip)")
    ap.add_argument("--chronos-cache", default="data/chronos2_cache",
                    help="cached Chronos-2 'wx' forecasts for the calibration window; adds the blend margin ('' to skip)")
    ap.add_argument("--coords", default="data/site_coords.json")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(exist_ok=True)
    cfg = yaml.safe_load(Path(args.config).read_text())
    coords = json.loads(Path(args.coords).read_text()) if args.coords and Path(args.coords).exists() else {}
    # one small file lets the API request weather for every site in a single call
    (out / "site_coordinates.json").write_text(json.dumps(
        {s["id"]: {"lat": coords[s["id"]]["lat"], "lon": coords[s["id"]]["lon"]}
         for s in cfg["sites"] if s["id"] in coords}, indent=0))
    for s in cfg["sites"]:
        t0 = time.time()
        frame = load_site_frame(s["id"], args.clean)
        wx = load_weather(s["id"], args.weather) if args.weather and (Path(args.weather) / f"{s['id']}.parquet").exists() else None
        cp = Path(args.chronos_cache) / f"{s['id']}_wx.parquet" if args.chronos_cache else None
        cal = pd.read_parquet(cp) if wx is not None and cp is not None and cp.exists() else None
        b = train_bundle(frame, s["id"], role=s["role"], cal_days=args.cal_days, stride=args.stride,
                         weather=wx, chronos_cal=cal)
        b.lat, b.lon = coords.get(s["id"], {}).get("lat"), coords.get(s["id"], {}).get("lon")
        path = out / f"{s['id']}.joblib"
        joblib.dump(b, path, compress=3)
        m = {f"reference_mae_{h}h": v for h, v in b.metrics.get("reference_mae_h", {}).items()}
        m.update({f"wx_reference_mae_{h}h": v for h, v in b.metrics.get("wx_reference_mae_h", {}).items()})
        m.update({f"blend_reference_mae_{h}h": v for h, v in b.metrics.get("blend_reference_mae_h", {}).items()})
        m.update({"cal_events": b.cal_events, "weather_models": len(b.wx_quantile_models),
                  "blend_margins": len(b.blend_margin), "probability_scaled": float(b.platt is not None)})
        track("training", params={"site": s["id"], "role": s["role"], "cal_days": args.cal_days, "stride": args.stride,
                                  "trained_through": b.trained_through.date(), "classifier": type(b.clf).__name__},
              metrics=m, run_name=s["id"])
        print(f"{s['id']} ({s['role']}): trained through {b.trained_through:%Y-%m-%d}, "
              f"{b.cal_events} calibration events, probability scaled={b.platt is not None}, "
              f"weather models={len(b.wx_quantile_models)}/5, blend margins={len(b.blend_margin)}/5, "
              f"{path.stat().st_size / 1e6:.1f} MB, {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
