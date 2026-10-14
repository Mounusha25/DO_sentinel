import numpy as np
import pandas as pd

from dosentinel.validate import RULES, Rule, flag_series, summarize, to_grid

RULE = Rule(lo=0.0, hi=20.0, max_step=2.0, flat_steps=4)


def _series(values, start="2026-01-01T00:00:00Z", freq="15min"):
    idx = pd.date_range(start, periods=len(values), freq=freq, tz="UTC")
    return pd.Series(values, index=idx, dtype=float)


def test_gap_is_missing_not_spike():
    s = _series([8, 8.1, 8.2, 8.3, 8.4]).drop(pd.Timestamp("2026-01-01T00:30:00Z"))
    f = flag_series(s, RULE)
    assert len(f) == 5
    assert f["missing"].tolist() == [False, False, True, False, False]
    assert not f["spike"].any()


def test_out_of_range_and_spike():
    f = flag_series(_series([8, 8, 25, 8, 8]), RULE)
    assert f["out_of_range"].tolist() == [False, False, True, False, False]
    assert f["spike"].tolist() == [False, False, True, True, False]  # up and back down


def test_flatline_marks_whole_run_only_when_long_enough():
    f = flag_series(_series([5, 5, 5, 6, 7, 7, 7, 7, 8]), RULE)
    assert f["flatline"].tolist() == [False] * 4 + [True] * 4 + [False]


def test_nan_runs_are_not_flatlines():
    f = flag_series(_series([5, np.nan, np.nan, np.nan, np.nan, 6]), RULE)
    assert not f["flatline"].any()


def test_off_grid_timestamps_snap_to_nearest_step():
    idx = pd.to_datetime(["2026-01-01T00:01:00Z", "2026-01-01T00:14:00Z", "2026-01-01T00:31:00Z"])
    g = to_grid(pd.Series([1.0, 2.0, 3.0], index=idx))
    assert g.index[0] == pd.Timestamp("2026-01-01T00:15:00Z")
    assert g.tolist()[0] == 2.0  # 00:14 is nearest to the 00:15 step


def test_summarize_fractions_use_observed_denominator():
    f = flag_series(_series([8, np.nan, 25, 8]), RULE)
    s = summarize(f)
    assert s["observed_frac"] == 0.75
    assert abs(s["out_of_range_frac"] - 1 / 3) < 1e-9


def test_rules_cover_all_configured_parameters():
    assert set(RULES) == {"00300", "00010", "00095", "63680", "00060"}
