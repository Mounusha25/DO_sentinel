"""Quantile gradient boosting for the change in DO from the origin value."""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor

QUANTILES = (0.1, 0.5, 0.9)

# Fixed, untuned. Early stopping is off: sklearn's default validation split is random,
# which would leak across time. Capacity is modest because the series are highly
# autocorrelated and the effective sample size is far below the row count.
PARAMS = dict(max_iter=150, learning_rate=0.06, max_depth=5, min_samples_leaf=80,
              l2_regularization=1.0, early_stopping=False, random_state=0)


def fit_predict_quantiles(Xtr: pd.DataFrame, ytr: np.ndarray, Xte: pd.DataFrame,
                          quantiles=QUANTILES, params: dict | None = None) -> pd.DataFrame:
    """Fit one model per quantile and return predictions (columns q10, q50, ...), sorted
    row-wise so quantiles never cross."""
    p = {**PARAMS, **(params or {})}
    preds = []
    for q in quantiles:
        m = HistGradientBoostingRegressor(loss="quantile", quantile=q, **p)
        m.fit(Xtr, ytr)
        preds.append(m.predict(Xte))
    arr = np.sort(np.column_stack(preds), axis=1)
    return pd.DataFrame(arr, index=Xte.index, columns=[f"q{int(q * 100)}" for q in quantiles])


def fit_predict_proba(Xtr: pd.DataFrame, ytr: np.ndarray, Xte: pd.DataFrame,
                      params: dict | None = None) -> np.ndarray:
    """P(y = 1) from a gradient-boosted classifier; constant base rate if one class only."""
    y = np.asarray(ytr, dtype=int)
    if y.min() == y.max():
        return np.full(len(Xte), float(y.mean()))
    m = HistGradientBoostingClassifier(**{**PARAMS, **(params or {})})
    m.fit(Xtr, y)
    return m.predict_proba(Xte)[:, 1]
