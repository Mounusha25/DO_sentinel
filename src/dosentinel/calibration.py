"""Calibration utilities: conformalized quantile intervals and probability scaling."""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

EPS = 1e-4


def cqr_correction(y: np.ndarray, lo: np.ndarray, hi: np.ndarray, alpha: float = 0.2) -> float:
    """Conformalized quantile regression margin (Romano et al. 2019).

    Scores are how far each calibration target fell outside [lo, hi] (negative inside).
    Widening the interval by the finite-sample (1 - alpha) quantile of the scores gives
    marginal coverage of 1 - alpha if calibration and test are exchangeable. Time series
    are not exactly exchangeable, so coverage is checked on held-out folds, not assumed.
    """
    s = np.maximum(lo - y, y - hi)
    n = len(s)
    if n == 0:
        return 0.0
    level = min(1.0, np.ceil((n + 1) * (1 - alpha)) / n)
    return float(np.quantile(s, level, method="higher"))


class PlattScaler:
    """Logistic recalibration of probabilities: p' = sigmoid(a * logit(p) + b)."""

    def __init__(self, C: float = 100.0):
        self._lr = LogisticRegression(C=C)
        self.fitted = False

    @staticmethod
    def _logit(p: np.ndarray) -> np.ndarray:
        p = np.clip(np.asarray(p, dtype=float), EPS, 1 - EPS)
        return np.log(p / (1 - p)).reshape(-1, 1)

    def fit(self, p: np.ndarray, y: np.ndarray) -> PlattScaler:
        self._lr.fit(self._logit(p), np.asarray(y, dtype=int))
        self.fitted = True
        return self

    def transform(self, p: np.ndarray) -> np.ndarray:
        return self._lr.predict_proba(self._logit(p))[:, 1]


def brier(p: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean((np.asarray(p, float) - np.asarray(y, float)) ** 2))


def reliability_table(p: np.ndarray, y: np.ndarray,
                      edges=(0, 0.02, 0.05, 0.1, 0.2, 0.4, 0.7, 1.0001)) -> pd.DataFrame:
    p, y = np.asarray(p, float), np.asarray(y, float)
    b = pd.cut(p, bins=list(edges), right=False)
    df = pd.DataFrame({"bin": b, "p": p, "y": y}).groupby("bin", observed=True).agg(
        n=("y", "size"), mean_pred=("p", "mean"), observed=("y", "mean"))
    return df.reset_index()
