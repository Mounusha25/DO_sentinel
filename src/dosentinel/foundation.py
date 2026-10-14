"""Helpers for evaluating pretrained time-series models on the DO series.

Foundation models receive a context window ending exactly at the forecast origin and return a
path for the next `horizon` steps. Everything here is about building those windows without
leakage and aligning outputs with the targets used by the other models.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

HORIZONS_H = (1, 3, 6, 12, 24)


def hourly_series(v: pd.Series) -> pd.Series:
    """Values at whole hours from a 15-minute series (instantaneous, not hourly means)."""
    return v[v.index.minute == 0]


def build_contexts(hv: pd.Series, origins: pd.DatetimeIndex, ctx_len: int) -> np.ndarray:
    """(n_origins, ctx_len) array; row i holds hv at [origin_i - ctx_len + 1 .. origin_i] hours.

    The last column is the value at the origin itself; nothing later is ever included.
    Missing hours are NaN (the model masks them).
    """
    full = hv.reindex(pd.date_range(hv.index.min(), hv.index.max(), freq="1h"))
    pos = full.index.get_indexer(origins)
    if (pos < 0).any():
        raise ValueError("some origins are not hourly timestamps within the series")
    vals = full.to_numpy(dtype=np.float32)
    padded = np.concatenate([np.full(ctx_len - 1, np.nan, np.float32), vals])
    return np.stack([padded[p: p + ctx_len] for p in pos])


def targets_at(hv: pd.Series, origins: pd.DatetimeIndex, horizons=HORIZONS_H) -> pd.DataFrame:
    """Observed values at origin + h hours for each horizon (NaN if unobserved)."""
    return pd.DataFrame({h: hv.reindex(origins + pd.Timedelta(hours=h)).to_numpy() for h in horizons},
                        index=origins)
