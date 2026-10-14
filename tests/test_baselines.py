import numpy as np
import pandas as pd
import pytest

from dosentinel.baselines import BASELINES, STEPS_PER_DAY, forecast_frame, persistence, seasonal_naive
from dosentinel.evaluate import event_table, evaluation_table, walk_forward_folds


def _v(n=STEPS_PER_DAY * 12, seed=0):
    idx = pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC")
    rng = np.random.default_rng(seed)
    base = 8 + 2 * np.sin(2 * np.pi * np.arange(n) / STEPS_PER_DAY)
    return pd.Series(base + rng.normal(0, 0.1, n), index=idx)


@pytest.mark.parametrize("name", list(BASELINES))
@pytest.mark.parametrize("h", [1, 16, 96])
def test_no_leakage_from_after_origin(name, h):
    """A forecast made at origin t must not change if everything after t is replaced."""
    v = _v()
    cut = v.index[STEPS_PER_DAY * 9]
    corrupted = v.copy()
    corrupted[corrupted.index > cut] = 1e6
    a = BASELINES[name](v, h)
    b = BASELINES[name](corrupted, h)
    pd.testing.assert_series_equal(a[a.index <= cut], b[b.index <= cut])


def test_persistence_and_seasonal_naive_values():
    v = _v()
    h = 8
    t = v.index[STEPS_PER_DAY * 3]
    assert persistence(v, h)[t] == v[t]
    assert seasonal_naive(v, h)[t] == v[t + pd.Timedelta("15min") * h - pd.Timedelta("1D")]


def test_perfect_diurnal_cycle_is_predicted_by_seasonal_methods():
    idx = pd.date_range("2026-01-01", periods=STEPS_PER_DAY * 10, freq="15min", tz="UTC")
    v = pd.Series(8 + 2 * np.sin(2 * np.pi * np.arange(len(idx)) / STEPS_PER_DAY), index=idx)
    f = forecast_frame(v, 24).dropna()
    assert np.allclose(f["seasonal_naive"], f["y_true"])
    assert np.allclose(f["seasonal_mean_7d"], f["y_true"])
    assert (f["persistence"] - f["y_true"]).abs().mean() > 0.1


def test_horizon_beyond_one_day_is_rejected():
    with pytest.raises(ValueError):
        seasonal_naive(_v(), STEPS_PER_DAY + 1)


def test_all_methods_scored_on_identical_origins_and_missing_dropped():
    v = _v()
    v.iloc[STEPS_PER_DAY * 6: STEPS_PER_DAY * 6 + 20] = np.nan
    folds = walk_forward_folds(v.index, n_folds=2, fold_days=3)
    df = evaluation_table(v, horizons_h=[3], folds=folds)
    assert df[["y_true", *BASELINES]].notna().all().all()
    assert not df.set_index("fold", append=True).index.duplicated().any()
    # origins whose target falls inside the NaN block must be absent
    gap = v.index[STEPS_PER_DAY * 6: STEPS_PER_DAY * 6 + 20]
    assert not (df.index + pd.Timedelta("3h")).isin(gap).any()


def test_folds_are_ordered_and_non_overlapping():
    idx = pd.date_range("2024-01-01", "2026-01-01", freq="15min", tz="UTC")
    folds = walk_forward_folds(idx, n_folds=4, fold_days=91)
    for (_, _, s1, e1), (_, _, s2, e2) in zip(folds, folds[1:]):
        assert e1 == s2 and s1 < e1 < e2


def test_event_table_counts():
    idx = pd.date_range("2026-01-01", periods=4, freq="15min", tz="UTC")
    df = pd.DataFrame({"horizon_h": 1, "y_true": [4.0, 4.0, 6.0, 6.0],
                       "y_origin": [4.0, 6.0, 6.0, 4.0],
                       "persistence": [4.0, 6.0, 4.0, 6.0]}, index=idx)
    r = event_table(df, ["persistence"]).iloc[0]
    assert (r.tp, r.fp, r.fn) == (1, 1, 1)
    assert r.precision == 0.5 and r.recall == 0.5
    # one onset (row 2: origin 6 -> target 4); persistence predicts 6, so it misses it
    assert r.onsets == 1 and r.onset_tp == 0 and r.onset_recall == 0.0
    # one onset-style false alarm (row 3: origin 6, target 6, predicted 4)
    assert r.onset_fp == 1


def test_persistence_has_zero_onset_recall_by_construction():
    v = _v()
    v = v - 3  # push the series across the threshold so onsets exist
    folds = walk_forward_folds(v.index, n_folds=1, fold_days=8)
    df = evaluation_table(v, horizons_h=[1], folds=folds)
    r = event_table(df, ["persistence"], threshold=6.0).iloc[0]
    assert r.onsets > 0 and r.onset_tp == 0
