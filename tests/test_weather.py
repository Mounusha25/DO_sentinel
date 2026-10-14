import numpy as np
import pandas as pd
import pytest

from dosentinel.weather import weather_features


def _W(n_hours=24 * 20, seed=0):
    idx = pd.date_range("2026-01-01", periods=n_hours, freq="1h", tz="UTC")
    rng = np.random.default_rng(seed)
    p = np.where(rng.random(n_hours) < 0.2, rng.exponential(2.0, n_hours), 0.0)
    W = pd.DataFrame({"obs_precipitation": p, "obs_temperature_2m": 10 + rng.normal(0, 1, n_hours),
                      "obs_shortwave_radiation": rng.uniform(0, 600, n_hours)}, index=idx)
    # the forecast differs from what happened
    W["fc1_precipitation"] = np.clip(p + rng.normal(0, 0.5, n_hours), 0, None)
    W["fc1_temperature_2m"] = W["obs_temperature_2m"] + rng.normal(0, 1, n_hours)
    W["fc1_shortwave_radiation"] = W["obs_shortwave_radiation"] * 0.9
    return W


def _idx(W, start_h=24 * 8, n=200):
    return pd.date_range(W.index[start_h], periods=n, freq="15min", tz="UTC")


def test_past_rain_equals_hand_sum():
    W = _W()
    t = W.index[24 * 10] + pd.Timedelta("30min")  # mid-hour origin
    f = weather_features(pd.DatetimeIndex([t]), W, 24, "fc1")
    now = t.floor("h")
    expect = W["obs_precipitation"].loc[now - pd.Timedelta(hours=5): now].sum()  # 6 h ending at floor(t)
    assert f["rain_past_6h"].iloc[0] == pytest.approx(expect)


def test_future_window_uses_target_hours_and_source():
    W = _W()
    t = W.index[24 * 10]
    h = 24  # 6 hours ahead
    f_fc = weather_features(pd.DatetimeIndex([t]), W, h, "fc1")
    f_ob = weather_features(pd.DatetimeIndex([t]), W, h, "obs")
    hours = W.loc[t + pd.Timedelta(hours=1): t + pd.Timedelta(hours=6)]
    assert f_ob["rain_next"].iloc[0] == pytest.approx(hours["obs_precipitation"].sum())
    assert f_fc["rain_next"].iloc[0] == pytest.approx(hours["fc1_precipitation"].sum())
    assert f_fc["rain_next"].iloc[0] != pytest.approx(f_ob["rain_next"].iloc[0])


def test_past_features_unchanged_by_future_observations():
    W = _W()
    idx = _idx(W)
    cut = idx[-1]
    W2 = W.copy()
    later = W2.index > cut.floor("h")
    W2.loc[later, ["obs_precipitation", "obs_temperature_2m", "obs_shortwave_radiation"]] = 999.0
    a = weather_features(idx, W, 24, "fc1")
    b = weather_features(idx, W2, 24, "fc1")
    past = [c for c in a.columns if c.startswith("rain_past") or c == "air_temp_now"]
    pd.testing.assert_frame_equal(a[past], b[past])


def test_fc1_future_features_do_not_use_actual_weather():
    W = _W()
    idx = _idx(W)
    W2 = W.copy()
    W2[["obs_precipitation", "obs_temperature_2m", "obs_shortwave_radiation"]] += 50.0  # change *actuals*
    a = weather_features(idx, W, 24, "fc1")
    b = weather_features(idx, W2, 24, "fc1")
    fut = ["rain_next", "rain_next_peak_1h", "air_temp_target", "sun_target", "sun_mean_window"]
    pd.testing.assert_frame_equal(a[fut], b[fut])


def test_missing_forecast_gives_nan_not_zero():
    W = _W()
    W.loc[W.index[:24 * 12], "fc1_precipitation"] = np.nan
    f = weather_features(pd.DatetimeIndex([W.index[24 * 5]]), W, 24, "fc1")
    assert np.isnan(f["rain_next"].iloc[0])
