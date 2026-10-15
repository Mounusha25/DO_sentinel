"""Check whether each site's model needs retraining, and optionally retrain the ones that do.

    python scripts/retrain.py --log logs/forecasts.jsonl            # report only
    python scripts/retrain.py --log logs/forecasts.jsonl --apply    # retrain flagged sites into --out

The rule is the one in `monitoring.RetrainRule` (live error above 1.25x its evaluation level
with at least 200 matured forecasts, coverage below 0.65, more than 30% of DO missing over a
week, or a model older than a year). Run `pull_data.py` and `validate_sites.py` first so
`data/clean` holds the latest observations; this script does not fetch data itself.

Retrained bundles are written to --out (default `models_new/`), never over `models/`. Promote
them after looking at the report. The blend margin needs Chronos-2 forecasts for the new
calibration window (`run_chronos2.py`); without them a retrained bundle serves the weather
GBM only, and the report says so.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import joblib
import pandas as pd
import yaml

sys.path.insert(0, "src")
from dosentinel.features import load_site_frame  # noqa: E402
from dosentinel.monitoring import read_log, site_status  # noqa: E402
from dosentinel.serving import train_bundle  # noqa: E402
from dosentinel.weather import load_weather  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/sites.yaml")
    ap.add_argument("--models", default="models")
    ap.add_argument("--clean", default="data/clean")
    ap.add_argument("--log", default="", help="forecast log written by the API (DOSENTINEL_LOG)")
    ap.add_argument("--apply", action="store_true", help="retrain flagged sites")
    ap.add_argument("--out", default="models_new")
    ap.add_argument("--weather", default="data/weather")
    ap.add_argument("--chronos-cache", default="data/chronos2_cache")
    ap.add_argument("--coords", default="data/site_coords.json")
    ap.add_argument("--json", default="", help="also write the report to this path")
    args = ap.parse_args()

    log = read_log(args.log) if args.log else read_log("/nonexistent")
    cfg = yaml.safe_load(Path(args.config).read_text())
    coords = json.loads(Path(args.coords).read_text()) if Path(args.coords).exists() else {}
    report = []
    for s in cfg["sites"]:
        path = Path(args.models) / f"{s['id']}.joblib"
        if not path.exists():
            report.append({"site_id": s["id"], "retrain": True, "reasons": ["no bundle"], "model_age_days": None})
            continue
        bundle = joblib.load(path)
        frame = load_site_frame(s["id"], args.clean)
        st = site_status(bundle, log, frame["do"])
        st["retrained"] = False
        if args.apply and st["retrain"]:
            wx_path = Path(args.weather) / f"{s['id']}.parquet"
            wx = load_weather(s["id"], args.weather) if wx_path.exists() else None
            cp = Path(args.chronos_cache) / f"{s['id']}_wx.parquet"
            cal = pd.read_parquet(cp) if wx is not None and cp.exists() else None
            new = train_bundle(frame, s["id"], role=s["role"], weather=wx, chronos_cal=cal)
            new.lat, new.lon = coords.get(s["id"], {}).get("lat"), coords.get(s["id"], {}).get("lon")
            out = Path(args.out)
            out.mkdir(exist_ok=True)
            joblib.dump(new, out / path.name, compress=3)
            st["retrained"] = True
            st["has_blend"] = bool(new.blend_margin)
            if wx is not None and not new.blend_margin:
                st["note"] = "no Chronos-2 cache covering the new calibration window; serves weather GBM only"
        report.append(st)

    for r in report:
        flag = "RETRAIN" if r["retrain"] else "ok     "
        why = "; ".join(r["reasons"]) or "-"
        print(f"{flag} {r['site_id']}  age={r['model_age_days']}d  logged={r.get('logged_forecasts', 0)}  {why}"
              + ("  -> retrained" if r.get("retrained") else ""))
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
