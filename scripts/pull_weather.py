"""Pull hourly weather per site from Open-Meteo into data/weather/{site}.parquet.

    python scripts/pull_weather.py

Columns (UTC hourly, accumulations are for the hour ending at the timestamp):
  obs_*  : ERA5 archive (precipitation mm, temperature_2m C, shortwave_radiation W/m2).
           Used for rainfall that has already fallen (a proxy for radar/gauge QPE) and, in the
           perfect-forecast upper bound, for the future window.
  fc1_*  : the GFS forecast for that valid hour issued ~1 day earlier ("previous day 1").
           For any horizon <= 24 h this forecast already existed at the forecast origin.
Archived previous-day precipitation/radiation start 2024-01-19, so fc1_* is NaN before then.
Sites are located from data/site_coords.json (public USGS station coordinates).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd
import requests

START, END = "2021-10-01", "2026-10-13"
VARS = ["precipitation", "temperature_2m", "shortwave_radiation"]


def get(url: str, params: dict, tries: int = 4) -> dict:
    for k in range(tries):
        r = requests.get(url, params=params, timeout=180)
        if r.status_code == 200:
            return r.json()["hourly"]
        time.sleep(5 * (k + 1))
    raise RuntimeError(f"{url} failed: {r.status_code} {r.text[:150]}")


def main() -> None:
    coords = json.loads(Path("data/site_coords.json").read_text())
    out = Path("data/weather")
    out.mkdir(parents=True, exist_ok=True)
    for sid, c in coords.items():
        path = out / f"{sid}.parquet"
        if path.exists():
            print(sid, "cached")
            continue
        base = {"latitude": c["lat"], "longitude": c["lon"], "start_date": START, "end_date": END, "timezone": "UTC"}
        obs = get("https://archive-api.open-meteo.com/v1/archive", {**base, "hourly": ",".join(VARS)})
        fc = get("https://previous-runs-api.open-meteo.com/v1/forecast",
                 {**base, "models": "gfs_global", "hourly": ",".join(f"{v}_previous_day1" for v in VARS)})
        o = pd.DataFrame(obs).set_index("time").add_prefix("obs_")
        f = pd.DataFrame(fc).set_index("time")
        f.columns = ["fc1_" + c.replace("_previous_day1", "") for c in f.columns]
        df = o.join(f, how="outer")
        df.index = pd.to_datetime(df.index, utc=True)
        df.index.name = "time"
        df.to_parquet(path)
        print(f"{sid}: {len(df)} rows; fc1 precip non-null from {df['fc1_precipitation'].dropna().index.min()}", flush=True)
        time.sleep(1.0)


if __name__ == "__main__":
    main()
