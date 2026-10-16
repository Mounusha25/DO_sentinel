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
        def json(self): return PAYLOAD if self.status_code == 200 else {"error": True, "reason": "Too many requests"}

    class S:
        def __init__(self, code): self.code, self.seen = code, None
        def get(self, url, params, timeout):
            self.seen = (url, params)
            return R(self.code)

    s = S(200)
    W = weather_live.fetch_live_weather(38.8, -77.2, session=s)
    assert len(W) == 3 and s.seen[1]["timezone"] == "UTC" and s.seen[1]["past_days"] == 8
    with pytest.raises(weather_live.WeatherUnavailable, match="HTTP 429 Too many requests"):
        weather_live.fetch_live_weather(38.8, -77.2, session=S(429))
    keyed = S(200)
    weather_live.fetch_live_weather(38.8, -77.2, session=keyed, api_key="k")
    assert keyed.seen[0] == weather_live.URL_KEYED and keyed.seen[1]["apikey"] == "k"


def _block(n=3, base=10.0):
    t = pd.date_range("2026-10-01", periods=n, freq="1h", tz="UTC")
    return {"hourly": {"time": [x.strftime("%Y-%m-%dT%H:%M") for x in t], "precipitation": [0.1] * n,
                       "temperature_2m": [base] * n, "shortwave_radiation": [100.0] * n}}


def test_fetch_many_sends_one_request_and_keeps_site_order():
    class R:
        status_code = 200

        def json(self):
            return [_block(base=10.0), _block(base=20.0)]

    class S:
        calls = []

        def get(self, url, params, timeout):
            S.calls.append(params)
            return R()

    out = weather_live.fetch_many({"A": (38.0, -77.0), "B": (40.0, -80.0)}, session=S())
    assert len(S.calls) == 1 and S.calls[0]["latitude"] == "38.0000,40.0000"
    assert out["A"]["obs_temperature_2m"].iloc[0] == 10.0 and out["B"]["obs_temperature_2m"].iloc[0] == 20.0
    assert weather_live.fetch_many({}) == {}


def test_fetch_many_rejects_a_short_answer_and_http_errors():
    class R:
        def __init__(self, code, body):
            self.status_code, self._b = code, body

        def json(self):
            return self._b

    class S:
        def __init__(self, r):
            self.r = r

        def get(self, url, params, timeout):
            return self.r

    with pytest.raises(ValueError):
        weather_live.fetch_many({"A": (1, 2), "B": (3, 4)}, session=S(R(200, [_block()])))
    with pytest.raises(weather_live.WeatherUnavailable, match="HTTP 429"):
        weather_live.fetch_many({"A": (1, 2)}, session=S(R(429, {"reason": "Daily API request limit exceeded"})))


def test_snapshot_round_trip_preserves_the_training_schema():
    frames = {"A": weather_live.parse_hourly(PAYLOAD)}
    snap = weather_live.to_snapshot(frames, pd.Timestamp("2026-10-15T12:00:00Z"))
    back, generated = weather_live.from_snapshot(snap)
    assert generated == pd.Timestamp("2026-10-15T12:00:00Z")
    assert list(back["A"].columns) == list(frames["A"].columns)
    pd.testing.assert_frame_equal(back["A"].fillna(-1), frames["A"].fillna(-1), check_freq=False, check_names=False)
