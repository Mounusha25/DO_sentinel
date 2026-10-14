import numpy as np
import pandas as pd

from dosentinel.detect import (interval_score, persistence_residual_online,
                               persistence_residual_score, robust_scale, rules_flag, run_length,
                               threshold_at_rate)
from dosentinel.faults import DEFAULT_COUNTS, inject_drifts, inject_faults
from dosentinel.validate import Rule


def _v(n=96 * 120, seed=0):
    idx = pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC")
    rng = np.random.default_rng(seed)
    base = 8 + 1.5 * np.sin(2 * np.pi * np.arange(n) / 96) + rng.normal(0, 0.05, n)
    return pd.Series(np.round(base, 1), index=idx, name="do")


def test_injection_is_labelled_non_overlapping_and_preserves_nans():
    v = _v()
    v.iloc[500:520] = np.nan
    vc, events, label = inject_faults(v, np.random.default_rng(1))
    assert len(events) > 0
    # outside every event the series is untouched (NaNs included)
    outside = label == -1
    pd.testing.assert_series_equal(vc[outside], v[outside])
    # events do not overlap and carry consistent labels
    spans = sorted((e.start, e.end) for e in events)
    assert all(a_end <= b_start for (_, a_end), (b_start, _) in zip(spans, spans[1:]))
    for e in events:
        assert (label[e.start:e.end] == e.id).all()
    assert vc.dropna().min() >= 0.0
    assert np.isnan(vc.iloc[505])  # a pre-existing gap stays a gap


def test_injection_respects_start_position_and_counts():
    v = _v()
    _, events, _ = inject_faults(v, np.random.default_rng(2), start_pos=96 * 60)
    assert all(e.start >= 96 * 60 for e in events)
    kinds = pd.Series([e.kind for e in events]).value_counts()
    assert set(kinds.index) <= set(DEFAULT_COUNTS)


def test_flatline_event_is_constant_and_drift_ramps():
    v = _v()
    vc, events, _ = inject_faults(v, np.random.default_rng(3))
    flat = next(e for e in events if e.kind == "flatline")
    assert vc.iloc[flat.start:flat.end].nunique() == 1
    drift = next(e for e in events if e.kind == "drift")
    delta = (vc - v).iloc[drift.start:drift.end].to_numpy()
    assert abs(delta[-1]) > abs(delta[0]) and np.sign(delta[-1]) == np.sign(drift.magnitude)


def test_run_length_counts_and_resets():
    v = pd.Series([1, 1, 1, 2, 2, np.nan, 2.0])
    assert run_length(v).tolist() == [1, 2, 3, 1, 2, 0, 1]


def test_rules_flatline_flag_is_causal():
    v = pd.Series([5, 6, 7, 7, 7, 7, 7, 8.0])
    f = rules_flag(v, Rule(lo=0, hi=20, max_step=10, flat_steps=4))
    assert f.tolist() == [False, False, False, False, False, True, True, False]


def test_scores_use_only_past_data():
    v = _v()
    cut = v.index[96 * 80]
    w = v.copy()
    w[w.index > cut] = 99.0
    a, b = persistence_residual_score(v), persistence_residual_score(w)
    pd.testing.assert_series_equal(a[a.index <= cut], b[b.index <= cut])


def test_interval_score_zero_inside_and_scaled_outside():
    v = pd.Series([5.0, 9.0, 1.0])
    s = interval_score(v, pd.Series([4.0] * 3), pd.Series([6.0] * 3))
    assert s.tolist() == [0.0, 1.5, 1.5]


def test_threshold_at_rate_controls_exceedance():
    s = pd.Series(np.random.default_rng(0).exponential(size=20000))
    t = threshold_at_rate(s, 0.01)
    assert abs((s > t).mean() - 0.01) < 0.002


def test_inject_drifts_grid_spacing_and_ramp():
    v = _v(n=96 * 400)
    vc, events, label = inject_drifts(v, np.random.default_rng(0), per_cell=1, start_pos=96 * 10)
    assert 4 <= len(events) <= 9
    spans = sorted((e.start, e.end) for e in events)
    assert all(b_start - a_end >= 22 * 96 for (_, a_end), (b_start, _) in zip(spans, spans[1:]))
    for e in events:
        assert e.kind == "drift" and e.start >= 96 * 10
        delta = (vc - v).iloc[e.start:e.end].to_numpy()
        assert np.sign(delta[-1]) == np.sign(e.magnitude)
        assert abs(abs(delta[-1]) - abs(e.magnitude)) < 0.11  # ramps to full magnitude (rounding/clip)
    assert (vc[label == -1] == v[label == -1]).all()


def test_long_lag_features_unaffected_by_recent_data():
    from dosentinel.features import LONG_LAG_DAYS, long_lag_features
    v = _v(n=96 * 60)
    w = v.copy()
    cut = v.index[96 * 50]
    w[w.index > cut - pd.Timedelta(days=LONG_LAG_DAYS - 1)] = 99.0  # corrupt the last ~14 days
    a, b = long_lag_features(v), long_lag_features(w)
    pd.testing.assert_frame_equal(a[a.index <= cut], b[b.index <= cut])


def _online(v, thr=6.0, **kw):
    from dosentinel.baselines import persistence_diel_delta
    raw = v - persistence_diel_delta(v, 4).shift(4)
    return persistence_residual_online(v, thr, robust_scale(raw), **kw)


def test_online_detector_flags_spike_but_not_its_next_day_echo():
    v = _v(n=96 * 40)
    s = 96 * 30
    v.iloc[s] += 4.0
    plain = persistence_residual_score(v)
    assert plain.iloc[s + 96] > 6 and plain.iloc[s] > 6  # the echo exists without suppression
    out = _online(v)
    assert out["flag"].iloc[s]
    assert not out["flag"].iloc[s + 96] and not out["flag"].iloc[s + 100]
    assert out["flag"].sum() <= 3


def test_online_detector_accepts_persistent_regime_change():
    v = _v(n=96 * 40)
    v.iloc[96 * 25:] += 3.0  # permanent level shift (a real event, not a fault)
    out = _online(v, max_mask_run=16)
    late = out["flag"].iloc[96 * 25 + 40: 96 * 25 + 96 * 3]
    assert late.mean() < 0.1  # after the mask cap the new level is accepted


def test_online_detector_is_causal():
    v = _v(n=96 * 40)
    cut = 96 * 30
    w = v.copy()
    w.iloc[cut + 1:] = 99.0
    a, b = _online(v), _online(w)
    pd.testing.assert_frame_equal(a.iloc[: cut + 1], b.iloc[: cut + 1])
