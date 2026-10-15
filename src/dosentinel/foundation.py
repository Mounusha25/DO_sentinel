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


# ---- Chronos-2 with covariates, used at serving time ------------------------------------------
# The construction below mirrors scripts/run_chronos2.py (variant "wx") exactly, so the margins
# calibrated on that script's cached forecasts apply to what is served.
CTX, PRED = 512, 24
QL = [0.1, 0.5, 0.9]
MAX_FUTURE_GAP = 0.25  # skip an item when more than this fraction of a future covariate is missing


def hourly_covariate_frame(frame: pd.DataFrame, W: pd.DataFrame) -> pd.DataFrame:
    """Water-quality values at :00 and weather on one complete hourly UTC grid."""
    h = frame[frame.index.minute == 0]
    full = pd.date_range(min(h.index.min(), W.index.min()), max(h.index.max(), W.index.max()), freq="1h")
    out = pd.DataFrame(index=full)
    for c in ("do", "temp", "spcond", "turb", "q"):
        out[c] = h[c].reindex(full)
    out["q"] = np.log1p(out["q"].clip(lower=0))
    out["turb"] = np.log1p(out["turb"].clip(lower=0))
    for name in ("obs", "fc1"):
        out[f"{name}_air"] = W[f"{name}_temperature_2m"].reindex(full)
        out[f"{name}_sun"] = W[f"{name}_shortwave_radiation"].reindex(full)
        out[f"{name}_rain"] = W[f"{name}_precipitation"].reindex(full)
    return out


def chronos2_item(H: pd.DataFrame, origin: pd.Timestamp) -> dict | None:
    """One Chronos-2 input at hourly `origin`, or None when the weather forecast has too many gaps.

    A context that starts before the first row is left-padded with NaN (the model masks it)."""
    p = H.index.get_loc(origin)
    if p + PRED >= len(H):
        return None
    arr = {c: H[c].to_numpy(np.float32) for c in H.columns}

    def ctx(c: str) -> np.ndarray:
        lo = p - CTX + 1
        a = arr[c][max(lo, 0): p + 1]
        return np.concatenate([np.full(max(0, -lo), np.nan, np.float32), a])

    fut = {k: arr[f"fc1_{k}"][p + 1: p + 1 + PRED] for k in ("air", "sun", "rain")}
    if any(np.isnan(v).mean() > MAX_FUTURE_GAP for v in fut.values()):
        return None
    fut = {k: np.nan_to_num(v, nan=float(np.nanmean(v)) if np.isfinite(v).any() else 0.0) for k, v in fut.items()}
    return {"target": ctx("do"),
            "past_covariates": {"water_temp": ctx("temp"), "spcond": ctx("spcond"), "turb": ctx("turb"),
                                "disch": ctx("q"), "air": ctx("obs_air"), "sun": ctx("obs_sun"),
                                "rain": ctx("obs_rain")},
            "future_covariates": fut}


class Chronos2Blender:
    """Lazy wrapper around the pretrained Chronos-2 pipeline (needs the `foundation` extra)."""

    def __init__(self, model_id: str = "amazon/chronos-2", device: str | None = None):
        self.model_id, self.device, self._pipe = model_id, device, None

    def _load(self):
        if self._pipe is None:
            import torch
            from chronos import Chronos2Pipeline
            dev = self.device or ("mps" if torch.backends.mps.is_available() else "cpu")
            self._pipe = Chronos2Pipeline.from_pretrained(self.model_id, device_map=dev)
        return self._pipe

    def predict(self, frame: pd.DataFrame, W: pd.DataFrame) -> dict[int, tuple[float, float, float]] | None:
        """{horizon_h: (q10, q50, q90)} from the last whole hour of `frame`, or None if unusable."""
        origin = frame.index[-1].floor("h")
        H = hourly_covariate_frame(frame.loc[:origin], W)
        item = chronos2_item(H, origin)
        if item is None or np.isnan(item["target"][-1]):
            return None
        q, _ = self._load().predict_quantiles([item], prediction_length=PRED, quantile_levels=QL)
        arr = q[0][0].numpy()  # (PRED, 3)
        return {h: tuple(float(x) for x in np.sort(arr[h - 1])) for h in HORIZONS_H}
