"""Unit tests for the statistics inside the drift and alert-budget experiment scripts."""
import importlib.util
import pathlib
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).parents[1]


def _load(name: str):
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def cu():
    return _load("run_drift_cusum")


@pytest.fixture(scope="module")
def bf():
    return _load("run_budget_fit")


def test_cusum_is_quiet_on_noise_and_alarms_on_a_persistent_shift(cu):
    rng = np.random.default_rng(0)
    z = rng.normal(0, 1, 24 * 200)
    assert cu.cusum_episodes(z, k=0.5, h=12.0).sum() == 0
    shifted = z.copy()
    shifted[24 * 100:] += 1.5
    fl = cu.cusum_episodes(shifted, k=0.5, h=12.0)
    assert fl[: 24 * 100].sum() == 0 and fl[24 * 100:].any()
    first = int(np.flatnonzero(fl)[0]) - 24 * 100
    assert first < 24 * 5  # a 1.5 sigma shift is caught within days


def test_cusum_ignores_missing_hours_and_rearms(cu):
    z = np.full(500, 2.0)
    z[100:120] = np.nan
    fl = cu.cusum_episodes(z, k=0.5, h=10.0)
    assert fl.sum() >= 2  # alarms repeatedly: re-armed after each alarm
    assert not fl[100:120].any()  # nothing accumulates through a gap


def test_ewma_flags_a_level_shift(cu):
    rng = np.random.default_rng(1)
    z = rng.normal(0, 1, 24 * 120)
    z[24 * 60:] += 2.0
    fl = cu.ewma_flags(z, lam=0.03, thr=1.0)
    assert fl[: 24 * 60].sum() == 0 and fl[24 * 70:].all()


def test_episode_rate_merges_alarms_within_the_gap(cu):
    flag = np.zeros(24 * 28, bool)
    flag[[10, 11, 12, 40, 41, 300]] = True  # 10-12 and 40-41 are within 24 h of each other? 12 -> 40 is 28 h
    mask = np.ones_like(flag)
    assert cu.episodes_per_week(flag, mask, gap=24) == pytest.approx(3 / 4)
    assert cu.episodes_per_week(flag, mask, gap=48) == pytest.approx(2 / 4)  # merged at a wider gap
    assert cu.episodes_per_week(flag, np.zeros_like(flag), gap=24) == 0.0


def test_bisect_threshold_meets_the_rate_and_is_tight(cu):
    rng = np.random.default_rng(2)
    z = np.clip(rng.normal(0, 1, 24 * 400), -4, 4)
    mask = np.isfinite(z)
    far = 0.1

    def make(t):
        return cu.ewma_flags(z, 0.03, t)

    t = cu.bisect_threshold(make, 0.05, 8.0, mask, far)
    assert cu.episodes_per_week(make(t), mask) <= far
    assert cu.episodes_per_week(make(t * 0.8), mask) > far or t * 0.8 < 0.06  # a slightly lower one breaks it


def test_baseline_follows_slow_shifts_but_not_a_short_drift(cu):
    n = 24 * 300
    idx = pd.date_range("2025-01-01", periods=n, freq="1h", tz="UTC")
    r = pd.Series(0.0, index=idx)
    r.iloc[24 * 150: 24 * 160] = 2.0  # a 10-day drift: shorter than the 15-day lag
    z = cu.standardized(r + np.random.default_rng(3).normal(0, 0.1, n), floor=0.05)
    assert z.iloc[24 * 150: 24 * 160].mean() > 3.0  # still clearly visible to the detector
    level = pd.Series(np.r_[np.zeros(24 * 100), np.full(n - 24 * 100, 5.0)], index=idx)
    z2 = cu.standardized(level + np.random.default_rng(4).normal(0, 0.1, n), floor=0.05)
    assert abs(z2.iloc[-24 * 10:].mean()) < 1.0  # a permanent level shift is absorbed after the lag + window


def test_standardized_is_winsorised(cu):
    idx = pd.date_range("2025-01-01", periods=24 * 120, freq="1h", tz="UTC")
    r = pd.Series(np.random.default_rng(5).normal(0, 0.3, len(idx)), index=idx)
    r.iloc[24 * 100] = 50.0
    assert cu.standardized(r, floor=0.05).max() <= cu.CLIP + 1e-9


def test_fit_cutoff_is_the_most_sensitive_within_budget(bf):
    from dosentinel.decision import Costs
    idx = pd.date_range("2026-01-01", periods=96 * 120, freq="15min", tz="UTC")
    v = pd.Series(8.0, index=idx)
    for s in range(96 * 5, len(idx) - 96 * 2, 96 * 7):  # a 6 h dip to 3 mg/L every week
        v.iloc[s + 40: s + 64] = 3.0
    org = idx[(idx > idx[96 * 2]) & (idx <= idx[-1] - pd.Timedelta("24h"))][::16]
    score = (-v.reindex(org)).to_numpy()  # naive rule: alert when DO is low
    susp = np.zeros(len(org), bool)
    c = bf.fit_cutoff(v, 5.0, org, score, susp, budget=0.5, costs=Costs(10, 1))
    assert c is not None
    r = bf.evaluate_period(v, 5.0, org, (score > c) & ~susp, Costs(10, 1))
    assert r["alerts_per_week"] <= 0.5
    # one grid step more sensitive must break the budget (or there is no such step)
    higher = [g for g in sorted(bf.grid(score)) if g < c]
    if higher:
        nxt = higher[-1]
        assert bf.evaluate_period(v, 5.0, org, (score > nxt) & ~susp, Costs(10, 1))["alerts_per_week"] > 0.5


def test_evaluate_period_reports_the_cooldown_sensitivity(bf):
    from dosentinel.decision import Costs
    idx = pd.date_range("2026-01-01", periods=96 * 60, freq="15min", tz="UTC")
    v = pd.Series(8.0, index=idx)
    org = idx[::16]
    flag = np.zeros(len(org), bool)
    flag[::12] = True  # an alert every 2 days: far apart, so the cooldown cannot merge them
    r = bf.evaluate_period(v, 5.0, org, flag, Costs(10, 1))
    assert r["alerts_per_week"] > 0 and r["alerts_per_week_24h_cooldown"] <= r["alerts_per_week"]
    assert r["episodes"] == 0
    assert r["share_time_alerting"] == pytest.approx(flag.mean())
    # an alert that is always on is one cluster, and the share of time shows it
    always = bf.evaluate_period(v, 5.0, org, np.ones(len(org), bool), Costs(10, 1))
    assert always["share_time_alerting"] == 1.0 and always["clusters"] == 1
