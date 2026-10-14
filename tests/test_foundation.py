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
