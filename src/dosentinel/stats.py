"""Cluster-bootstrap confidence intervals for skill scores built from summed errors."""
from __future__ import annotations

import numpy as np


def skill_ci(err_model: np.ndarray, err_ref: np.ndarray, n_boot: int = 4000, seed: int = 0,
             alpha: float = 0.05) -> tuple[float, float, float]:
    """Point estimate and percentile CI of 1 - sum(err_model) / sum(err_ref).

    Inputs are per-cluster *summed* errors (e.g. one entry per site-week, or per site), so
    resampling whole clusters respects within-cluster autocorrelation. Returns (point, lo, hi).
    """
    a = np.asarray(err_model, float)
    b = np.asarray(err_ref, float)
    k = len(a)
    if k == 0 or b.sum() == 0:
        return (np.nan, np.nan, np.nan)
    rng = np.random.default_rng(seed)
    w = rng.multinomial(k, np.full(k, 1.0 / k), size=n_boot)  # (n_boot, k) cluster counts
    num, den = w @ a, w @ b
    ok = den > 0
    boots = 1.0 - num[ok] / den[ok]
    return (1.0 - a.sum() / b.sum(), float(np.quantile(boots, alpha / 2)), float(np.quantile(boots, 1 - alpha / 2)))


def mean_diff_ci(x: np.ndarray, n_boot: int = 4000, seed: int = 0, alpha: float = 0.05):
    """Mean of paired per-cluster differences with a percentile bootstrap CI."""
    x = np.asarray(x, float)
    x = x[~np.isnan(x)]
    if x.size == 0:
        return (np.nan, np.nan, np.nan)
    rng = np.random.default_rng(seed)
    m = rng.choice(x, size=(n_boot, x.size), replace=True).mean(axis=1)
    return (float(x.mean()), float(np.quantile(m, alpha / 2)), float(np.quantile(m, 1 - alpha / 2)))


def sign_test_p(wins: int, n: int) -> float:
    """Two-sided exact binomial sign-test p-value against p = 0.5 (ties excluded by caller)."""
    from math import comb
    if n == 0:
        return float("nan")
    k = min(wins, n - wins)
    return float(min(1.0, 2 * sum(comb(n, i) for i in range(k + 1)) / 2 ** n))
