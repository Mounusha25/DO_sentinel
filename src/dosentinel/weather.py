"""Leakage-aware weather features at 15-minute forecast origins.

Hourly accumulations are for the hour *ending* at the timestamp. At origin t only hours with
timestamp <= floor(t) have happened. Two kinds of features:

* past (observed proxy, valid at or before t): rainfall over the previous 1/6/24/72/168 h,
  current air temperature. Always from ``obs_*`` (ERA5; a stand-in for radar/gauge rainfall).
* future (the window (t, t+h]): rainfall total and peak hour, air temperature and sunlight at
  the target time. ``source="fc1"`` uses the forecast issued ~24 h before each valid hour, which
  existed at t for every h <= 24 h. ``source="obs"`` uses what actually happened: a perfect-
  forecast *upper bound*, never deployable.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

PAST_HOURS = (1, 6, 24, 72, 168)


def load_weather(site_id: str, root: str = "data/weather") -> pd.DataFrame:
    return pd.read_parquet(Path(root) / f"{site_id}.parquet").sort_index()


def _at(series: pd.Series, times: pd.DatetimeIndex) -> np.ndarray:
    return series.reindex(times).to_numpy()


def weather_features(idx: pd.DatetimeIndex, W: pd.DataFrame, h: int, source: str = "fc1") -> pd.DataFrame:
    """Weather features for origins `idx` (15-min grid, UTC) and horizon h in 15-min steps."""
    if source not in ("fc1", "obs"):
        raise ValueError("source must be 'fc1' or 'obs'")
    hh = max(1, h // 4)
    now = idx.floor("h")
    target = (idx + h * pd.Timedelta("15min")).floor("h")

    p_obs = W["obs_precipitation"]
    c_obs = p_obs.fillna(0.0).cumsum()
    out = pd.DataFrame(index=idx)
    for w in PAST_HOURS:
        out[f"rain_past_{w}h"] = _at(c_obs, now) - _at(c_obs, now - pd.Timedelta(hours=w))
    out["air_temp_now"] = _at(W["obs_temperature_2m"], now)

    p = W[f"{source}_precipitation"]
    c = p.fillna(0.0).cumsum()
    avail = p.notna().astype(float)
    rain_next = _at(c, target) - _at(c, now)
    # unknown where the forecast archive has no value for the target hour
    out["rain_next"] = np.where(~np.isnan(_at(p, target)), rain_next, np.nan)
    out["rain_next_peak_1h"] = _at(p.rolling(hh, min_periods=1).max(), target)
    t_f = W[f"{source}_temperature_2m"]
    out["air_temp_target"] = _at(t_f, target)
    out["air_temp_change"] = out["air_temp_target"] - out["air_temp_now"]
    sw = W[f"{source}_shortwave_radiation"]
    out["sun_target"] = _at(sw, target)
    out["sun_mean_window"] = _at(sw.rolling(hh, min_periods=1).mean(), target)
    out["rain_x_prior24h"] = out["rain_next"] * np.log1p(out["rain_past_24h"])
    return out
