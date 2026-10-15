"""Live weather for serving, in the same column layout the training weather table uses.

Open-Meteo's forecast endpoint returns the recent past (best available analysis) followed by
the latest forecast. Both are mapped onto the training schema (``obs_*`` and ``fc1_*``):

* ``obs_*``  past hours, used for rain that has already fallen and for the Chronos context;
* ``fc1_*``  future hours. Training used the forecast issued a day earlier; here it is the
  latest run, which is at least as fresh. The model sees a slightly better forecast than it was
  trained on, never a worse one.

No API key is needed. A failure here must never fail a request: callers fall back to the
no-weather model and say so.
"""
from __future__ import annotations

import pandas as pd
import requests

URL = "https://api.open-meteo.com/v1/forecast"
URL_KEYED = "https://customer-api.open-meteo.com/v1/forecast"  # used when an API key is configured
VARS = ("precipitation", "temperature_2m", "shortwave_radiation")


def parse_hourly(payload: dict) -> pd.DataFrame:
    """Open-Meteo JSON -> hourly UTC frame with obs_* and fc1_* columns (identical values)."""
    hourly = payload.get("hourly")
    if not hourly or "time" not in hourly:
        raise ValueError("response has no hourly block")
    idx = pd.to_datetime(hourly["time"], utc=True)
    out = pd.DataFrame(index=pd.DatetimeIndex(idx, name="time"))
    for v in VARS:
        if v not in hourly:
            raise ValueError(f"response is missing {v}")
        s = pd.Series(hourly[v], index=idx, dtype=float)
        out[f"obs_{v}"] = s
        out[f"fc1_{v}"] = s
    return out.sort_index()


class WeatherUnavailable(Exception):
    """The weather provider answered with an error; the message carries the HTTP status and reason."""


def fetch_live_weather(lat: float, lon: float, past_days: int = 8, forecast_days: int = 2,
                       timeout: float = 4.0, session: requests.Session | None = None,
                       api_key: str | None = None) -> pd.DataFrame:
    """Hourly weather from `past_days` ago to `forecast_days` ahead. Raises on any failure.

    With `api_key` the commercial endpoint is used, which is not subject to the shared-IP
    limits of the free one (cloud hosts share an address, and the free tier throttles it)."""
    params = {"latitude": lat, "longitude": lon, "hourly": ",".join(VARS), "timezone": "UTC",
              "past_days": past_days, "forecast_days": forecast_days}
    if api_key:
        params["apikey"] = api_key
    r = (session or requests).get(URL_KEYED if api_key else URL, params=params, timeout=timeout)
    if getattr(r, "status_code", 200) != 200:
        try:
            reason = str(r.json().get("reason", ""))[:100]
        except Exception:
            reason = ""
        raise WeatherUnavailable(f"HTTP {r.status_code} {reason}".strip())
    return parse_hourly(r.json())
