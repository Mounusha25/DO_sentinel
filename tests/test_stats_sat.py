import numpy as np
import pandas as pd

from dosentinel.features import do_saturation, saturation_features
from dosentinel.stats import mean_diff_ci, sign_test_p, skill_ci


def test_saturation_matches_reference_values():
    # APHA / Benson-Krause freshwater at 1 atm: 0 C 14.6, 10 C 11.3, 20 C 9.09, 30 C 7.56 mg/L
    got = do_saturation(np.array([0.0, 10.0, 20.0, 30.0]))
    assert np.allclose(got, [14.62, 11.29, 9.09, 7.56], atol=0.05)


def test_saturation_features_are_causal():
    n = 96 * 12
    idx = pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC")
    rng = np.random.default_rng(0)
    f = pd.DataFrame({"do": 8 + rng.normal(0, 0.2, n), "temp": 15 + rng.normal(0, 0.3, n)}, index=idx)
    cut = idx[96 * 9]
    g = f.copy()
    g.loc[g.index > cut] = 99.0
    a, b = saturation_features(f, 24), saturation_features(g, 24)
    pd.testing.assert_frame_equal(a[a.index <= cut], b[b.index <= cut])


def test_skill_ci_covers_truth_and_orders():
    rng = np.random.default_rng(1)
    ref = rng.gamma(4, 1, 400)
    mod = ref * 0.8 * rng.normal(1, 0.05, 400)  # true skill about 0.20
    p, lo, hi = skill_ci(mod, ref)
    assert lo < p < hi and lo > 0.1 and hi < 0.3


def test_skill_ci_wide_for_few_clusters_and_zero_skill_straddles_zero():
    rng = np.random.default_rng(2)
    ref = rng.gamma(4, 1, 14)
    mod = ref * rng.normal(1, 0.15, 14)
    p, lo, hi = skill_ci(mod, ref)
    assert lo < 0 < hi


def test_sign_test_and_mean_diff():
    assert sign_test_p(5, 14) > 0.4
    assert sign_test_p(14, 14) < 0.001
    m, lo, hi = mean_diff_ci(np.array([0.2, 0.3, 0.25, 0.1, 0.35]))
    assert lo < m < hi and lo > 0
