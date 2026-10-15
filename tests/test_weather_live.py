import numpy as np
import pandas as pd
import pytest

from dosentinel import weather_live
from dosentinel.weather import weather_features

PAYLOAD = {"hourly": {
    "time": ["2026-10-14T00:00", "2026-10-14T01:00", "2026-10-14T02:00"],
    "precipitation": [0.0, 1.2, None],
    "temperature_2m": [10.0, 9.5, 9.0],
    "shortwave_radiation": [0, 0, 15.0],
}}


def test_parse_hourly_maps_onto_training_schema():
    W = weather_live.parse_hourly(PAYLOAD)
    assert str(W.index.tz) == "UTC" and W.index.name == "time"
    assert set(W.columns) == {f"{p}_{v}" for p in ("obs", "fc1") for v in weather_live.VARS}
    assert W["obs_precipitation"].iloc[1] == 1.2 and np.isnan(W["fc1_precipitation"].iloc[2])


def test_parse_rejects_bad_payloads():
    with pytest.raises(ValueError):
        weather_live.parse_hourly({})
    with pytest.raises(ValueError):
        weather_live.parse_hourly({"hourly": {"time": ["2026-10-14T00:00"], "precipitation": [0.0]}})


def test_live_table_feeds_weather_features():
    n = 24 * 12
    t = pd.date_range("2026-10-01", periods=n, freq="1h", tz="UTC")
    payload = {"hourly": {"time": [x.strftime("%Y-%m-%dT%H:%M") for x in t], "precipitation": [0.1] * n,
                          "temperature_2m": [12.0] * n, "shortwave_radiation": [100.0] * n}}
    W = weather_live.parse_hourly(payload)
    X = weather_features(pd.DatetimeIndex([t[200]]), W, 96)  # 96 steps = 24 h
    assert X["rain_past_24h"].iloc[0] == pytest.approx(2.4) and X["rain_next"].iloc[0] == pytest.approx(2.4)


def test_fetch_uses_session_and_raises_on_http_error():
    class R:
        def __init__(self, code): self.status_code = code
        def raise_for_status(self):
            if self.status_code != 200:
                raise RuntimeError("HTTP 500")
        def json(self): return PAYLOAD

    class S:
        def __init__(self, code): self.code, self.seen = code, None
        def get(self, url, params, timeout):
            self.seen = (url, params)
            return R(self.code)

    s = S(200)
    W = weather_live.fetch_live_weather(38.8, -77.2, session=s)
    assert len(W) == 3 and s.seen[1]["timezone"] == "UTC" and s.seen[1]["past_days"] == 8
    with pytest.raises(RuntimeError):
        weather_live.fetch_live_weather(38.8, -77.2, session=S(500))
