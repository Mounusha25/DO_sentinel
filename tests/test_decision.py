import numpy as np
import pandas as pd

from dosentinel.calibration import PlattScaler, brier, cqr_correction, reliability_table
from dosentinel.decision import Costs, alert_clusters, chance_baseline, decide, episodes_from_series, evaluate_alerts


def _v(vals):
    return pd.Series(vals, dtype=float)


def test_cutoff_from_costs():
    assert abs(Costs(miss=10, false_alarm=1).cutoff - 1 / 11) < 1e-12
    assert Costs(miss=1, false_alarm=1).cutoff == 0.5


def test_episodes_merge_short_gaps_and_drop_blips():
    v = _v([8] * 3 + [4] * 5 + [6] * 2 + [4] * 5 + [8] * 3 + [4] * 2 + [8] * 3)
    eps = episodes_from_series(v, 5.0, merge_gap=2, min_len=4)
    assert eps == [(3, 14)]  # two runs merged across a 2-step bounce; 2-step blip dropped


def test_nan_is_not_low():
    v = _v([8, np.nan, 4, 4, 4, 4, np.nan, 8])
    assert episodes_from_series(v, 5.0, merge_gap=0, min_len=4) == [(2, 5)]


def test_alert_clusters_respect_cooldown():
    a = np.zeros(100, bool)
    a[[10, 11, 12, 20, 70]] = True
    assert alert_clusters(a, cooldown=10) == [(10, 20), (70, 70)]


def test_evaluate_alerts_lead_miss_and_false_alarm():
    n = 400
    alert = np.zeros(n, bool)
    episodes = [(200, 230), (300, 320)]
    alert[170] = True          # 30 steps (7.5 h) before episode 1
    alert[10] = True           # lone alert with nothing following within 96 steps
    r = evaluate_alerts(episodes, alert, horizon_steps=96, cooldown=24)
    assert r["events"] == 2 and r["detected"] == 1 and r["anticipated"] == 1
    assert r["median_lead_h"] == 7.5
    assert r["clusters"] == 2 and r["false_alarms"] == 1
    assert r["cost_per_week"] == (10 * 1 + 1 * 1) / (n / 672)


def test_late_alert_counts_as_detected_not_anticipated():
    alert = np.zeros(300, bool)
    alert[210] = True
    r = evaluate_alerts([(200, 230)], alert)
    assert r["detected"] == 1 and r["anticipated"] == 0 and np.isnan(r["median_lead_h"])


def test_maintenance_suppresses_warning():
    out = decide(np.array([0.9, 0.9, 0.01]), np.array([False, True, True]), cutoff=0.1)
    assert out.tolist() == ["warning", "maintenance", "maintenance"]


def test_cqr_restores_coverage_on_overconfident_intervals():
    rng = np.random.default_rng(0)
    y = rng.normal(0, 1, 5000)
    lo, hi = np.full(5000, -0.5), np.full(5000, 0.5)  # too narrow for 80%
    q = cqr_correction(y[:2500], lo[:2500], hi[:2500], alpha=0.2)
    cover = ((y[2500:] >= lo[2500:] - q) & (y[2500:] <= hi[2500:] + q)).mean()
    assert q > 0 and 0.77 < cover < 0.83


def test_platt_fixes_overconfident_probabilities():
    rng = np.random.default_rng(0)
    true_p = rng.uniform(0.02, 0.5, 6000)
    y = rng.random(6000) < true_p
    logit = np.log(true_p / (1 - true_p))
    overconf = 1 / (1 + np.exp(-2.0 * logit))  # logit stretched 2x: overconfident
    cal = PlattScaler().fit(overconf[:3000], y[:3000]).transform(overconf[3000:])
    assert brier(cal, y[3000:]) < brier(overconf[3000:], y[3000:])
    t = reliability_table(cal, y[3000:])
    t = t[t["n"] >= 100]
    assert len(t) >= 2 and (abs(t["mean_pred"] - t["observed"]) < 0.08).all()


def test_alert_at_already_low_origin_is_detected_but_not_anticipated():
    n = 300
    alert = np.zeros(n, bool)
    alert[190] = True
    not_low = np.ones(n, bool)
    not_low[190] = False  # DO was already below threshold (e.g. a dropped short dip)
    r = evaluate_alerts([(200, 230)], alert, not_low=not_low)
    assert r["detected"] == 1 and r["anticipated"] == 0
    assert r["cost_per_week"] == 10 / (n / 672)  # un-anticipated episode costs a miss


def test_chance_baseline_is_high_when_episodes_are_frequent():
    n = 96 * 100
    episodes = [(i, i + 8) for i in range(30, n - 100, 96)]  # one per day
    alert = np.zeros(n, bool)
    alert[1000:1010] = True
    c = chance_baseline(episodes, alert, trials=50)
    assert c["chance_cluster_precision"] > 0.9  # almost any alert lands near an episode


def test_prob_below_matches_the_normal_cdf_and_is_monotone():
    import numpy as np

    from dosentinel.blend import prob_below
    # an interval centred on the threshold puts half the mass below it
    assert abs(float(prob_below(4.0, 5.0, 6.0, 5.0)) - 0.5) < 1e-12
    # the 10th percentile sitting exactly on the threshold means 10% below it
    assert abs(float(prob_below(5.0, 6.0, 7.0, 5.0)) - 0.1) < 1e-9
    # a wider lower tail (lower q10, same median and q90) can only raise the probability
    x = prob_below(np.array([4.0, 3.0, 2.0]), 6.0, 8.0, 5.0)
    assert (np.diff(x) >= 0).all()
