import numpy as np
import pandas as pd
import pytest

from dosentinel.foundation import build_contexts, hourly_series, targets_at


def _v(n_hours=24 * 12):
    idx = pd.date_range("2026-01-01", periods=n_hours * 4, freq="15min", tz="UTC")
    return pd.Series(np.arange(len(idx), dtype=float), index=idx)


def test_hourly_series_takes_instantaneous_values_at_the_hour():
    hv = hourly_series(_v())
    assert (hv.index.minute == 0).all()
    assert hv.iloc[1] == 4.0 and hv.iloc[2] == 8.0  # 15-min steps: hour k is at step 4k


def test_context_ends_at_origin_and_has_no_future():
    hv = hourly_series(_v())
    origins = hv.index[100:103]
    ctx = build_contexts(hv, origins, 48)
    assert ctx.shape == (3, 48)
    for row, o in zip(ctx, origins):
        assert row[-1] == hv.loc[o]
        assert row[0] == hv.loc[o - pd.Timedelta(hours=47)]
        assert np.nanmax(row) <= hv.loc[o]  # series is increasing: nothing after the origin


def test_context_pads_with_nan_before_series_start_and_keeps_gaps():
    hv = hourly_series(_v())
    hv.iloc[50] = np.nan
    ctx = build_contexts(hv, hv.index[[10, 60]], 24)
    assert np.isnan(ctx[0, :13]).all() and not np.isnan(ctx[0, 13:]).any()  # 24 entries, origin at position 10
    assert np.isnan(ctx[1, -11])  # hour 50 sits 10 steps before origin 60


def test_context_unchanged_by_future_values():
    hv = hourly_series(_v())
    o = hv.index[[120]]
    a = build_contexts(hv, o, 96)
    hv2 = hv.copy()
    hv2.iloc[121:] = 1e9
    assert np.array_equal(a, build_contexts(hv2, o, 96), equal_nan=True)


def test_targets_align_with_horizons():
    hv = hourly_series(_v())
    o = hv.index[[10]]
    t = targets_at(hv, o, (1, 6))
    assert t.iloc[0, 0] == hv.iloc[11] and t.iloc[0, 1] == hv.iloc[16]


def test_non_hourly_origin_is_rejected():
    hv = hourly_series(_v())
    with pytest.raises(ValueError):
        build_contexts(hv, pd.DatetimeIndex([hv.index[5] + pd.Timedelta("15min")]), 24)


def _hourly_frames(n_hours=24 * 40):
    from dosentinel.foundation import hourly_covariate_frame
    idx = pd.date_range("2026-03-01", periods=n_hours * 4, freq="15min", tz="UTC")
    rng = np.random.default_rng(0)
    f = pd.DataFrame({"do": 8 + np.sin(np.arange(len(idx)) / 15) + rng.normal(0, 0.05, len(idx)),
                      "temp": 15.0, "spcond": 300.0, "turb": np.abs(rng.normal(5, 1, len(idx))),
                      "q": np.abs(rng.normal(30, 3, len(idx)))}, index=idx)
    hrs = pd.date_range(idx[0] - pd.Timedelta(days=2), idx[-1] + pd.Timedelta(days=3), freq="1h", tz="UTC")
    cols = {}
    for p in ("obs", "fc1"):
        cols.update({f"{p}_temperature_2m": 10.0, f"{p}_shortwave_radiation": 200.0, f"{p}_precipitation": 0.0})
    W = pd.DataFrame(cols, index=hrs)
    return f, W, hourly_covariate_frame


def test_chronos2_item_context_ends_at_origin_and_future_is_the_forecast():
    from dosentinel.foundation import CTX, PRED, chronos2_item
    f, W, hourly = _hourly_frames()
    origin = f.index[-1].floor("h")
    H = hourly(f, W)
    item = chronos2_item(H, origin)
    assert len(item["target"]) == CTX and item["target"][-1] == pytest.approx(H.loc[origin, "do"])
    assert all(len(v) == CTX for v in item["past_covariates"].values())
    assert all(len(v) == PRED for v in item["future_covariates"].values())
    assert item["past_covariates"]["air"][-1] == 10.0  # observed proxy at the origin
    # future covariates are strictly after the origin: change the hour after, the item changes; the origin, it does not
    W2 = W.copy()
    W2.loc[origin + pd.Timedelta(hours=1), "fc1_temperature_2m"] = 99.0
    assert chronos2_item(hourly(f, W2), origin)["future_covariates"]["air"][0] == 99.0


def test_chronos2_item_pads_short_history_and_rejects_missing_forecast():
    from dosentinel.foundation import CTX, chronos2_item
    f, W, hourly = _hourly_frames(n_hours=24 * 5)  # 5 days: far less than 512 h
    origin = f.index[-1].floor("h")
    item = chronos2_item(hourly(f, W), origin)
    assert len(item["target"]) == CTX and np.isnan(item["target"][:300]).all()
    W_gap = W.copy()
    W_gap.loc[origin + pd.Timedelta(hours=1): origin + pd.Timedelta(hours=24), "fc1_precipitation"] = np.nan
    assert chronos2_item(hourly(f, W_gap), origin) is None


def test_chronos2_item_matches_the_evaluation_script():
    pytest.importorskip("torch")
    import importlib.util
    import pathlib
    spec = importlib.util.spec_from_file_location("run_chronos2", pathlib.Path(__file__).parents[1] / "scripts/run_chronos2.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from dosentinel.foundation import chronos2_item
    f, W, hourly = _hourly_frames(n_hours=24 * 30)
    H = hourly(f, W)
    origin = f.index[-1].floor("h") - pd.Timedelta(hours=30)
    items, _ = mod.make_items(H, pd.DatetimeIndex([origin]), "wx")
    mine = chronos2_item(H, origin)
    np.testing.assert_array_equal(items[0]["target"], mine["target"])
    for k in ("water_temp", "spcond", "turb", "disch", "air", "sun", "rain"):
        np.testing.assert_array_equal(items[0]["past_covariates"][k], mine["past_covariates"][k])
    for k in ("air", "sun", "rain"):
        np.testing.assert_array_equal(items[0]["future_covariates"][k], mine["future_covariates"][k])
