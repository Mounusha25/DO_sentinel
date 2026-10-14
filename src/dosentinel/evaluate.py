"""Walk-forward evaluation of forecasts, sliced by horizon, fold, season, and storm state."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .baselines import BASELINES, HORIZONS_H, STEPS_PER_DAY, forecast_frame

LOW_DO = 5.0  # mg/L, aquatic-life criterion commonly used; configurable by caller
SEASONS = {12: "DJF", 1: "DJF", 2: "DJF", 3: "MAM", 4: "MAM", 5: "MAM",
           6: "JJA", 7: "JJA", 8: "JJA", 9: "SON", 10: "SON", 11: "SON"}


def clean_values(flags: pd.DataFrame) -> pd.Series:
    """DO values with every flagged point set to NaN."""
    bad = flags[["out_of_range", "flatline", "spike"]].any(axis=1)
    return flags["value"].where(~bad)


def storm_state(discharge: pd.Series, index: pd.DatetimeIndex, window_days: int = 30,
                factor: float = 2.0) -> pd.Series:
    """True where discharge exceeds `factor` x its trailing median. Uses only past data."""
    q = discharge.reindex(index)
    med = q.rolling(window_days * STEPS_PER_DAY, min_periods=STEPS_PER_DAY).median()
    return (q > factor * med).fillna(False)


def walk_forward_folds(index: pd.DatetimeIndex, n_folds: int = 4, fold_days: int = 91):
    """Consecutive test windows ending at the last timestamp.

    Returns (fold, train_end, test_start, test_end). Training data for a fitted model is
    everything before test_start; the baselines ignore it but share the same windows so
    their numbers are comparable with fitted models later.
    """
    end = index.max()
    folds = []
    for i in range(n_folds):
        test_end = end - pd.Timedelta(days=fold_days * (n_folds - 1 - i))
        test_start = test_end - pd.Timedelta(days=fold_days)
        folds.append((i + 1, test_start, test_start, test_end))
    return folds


def evaluation_table(v: pd.Series, storm: pd.Series | None = None, methods: dict | None = None,
                     horizons_h: list[int] | None = None, folds=None) -> pd.DataFrame:
    """Long table of (horizon_h, fold, origin, y_true, method, y_pred, season, storm).

    Only origins where the target and every method's forecast exist are kept, so all
    methods are scored on identical points.
    """
    methods = methods or BASELINES
    folds = folds or walk_forward_folds(v.index)
    names = list(methods)
    parts = []
    for hh in horizons_h or HORIZONS_H:
        f = forecast_frame(v, hh * 4, methods).dropna(subset=["y_true", *names])
        for fold, _, t0, t1 in folds:
            sub = f[(f.index > t0) & (f.index <= t1)].copy()
            sub["horizon_h"], sub["fold"] = hh, fold
            parts.append(sub)
    df = pd.concat(parts)
    df["season"] = df.index.month.map(SEASONS)
    df["storm"] = storm.reindex(df.index).fillna(False).values if storm is not None else False
    return df


def _metrics(g: pd.DataFrame, names: list[str]) -> pd.DataFrame:
    rows = []
    for m in names:
        e = g[m] - g["y_true"]
        rows.append({"method": m, "n": len(g), "mae": e.abs().mean(), "rmse": np.sqrt((e**2).mean()),
                     "bias": e.mean()})
    return pd.DataFrame(rows)


def skill_table(df: pd.DataFrame, names: list[str], by: list[str], ref: str = "persistence") -> pd.DataFrame:
    """MAE/RMSE per group and method, plus MAE skill vs `ref` (1 = perfect, 0 = same, <0 = worse)."""
    res = df.groupby(by, observed=True).apply(lambda g: _metrics(g, names), include_groups=False)
    res = res.reset_index(level=-1, drop=True).reset_index()
    base = res[res["method"] == ref].set_index(by)["mae"]
    res["mae_skill_vs_" + ref] = 1 - res["mae"].values / base.reindex(pd.MultiIndex.from_frame(res[by])
                                                                    if len(by) > 1 else res[by[0]]).values
    return res


def event_table(df: pd.DataFrame, names: list[str], threshold: float = LOW_DO) -> pd.DataFrame:
    """Low-DO detection at the target time, overall and for *onsets*.

    An onset is a target below `threshold` while the value at the origin was at or above
    it, i.e. a new excursion that persistence cannot anticipate. Overall precision and
    recall are dominated by events that are simply continuing, so onset metrics are the
    ones that separate forecasters from persistence.
    """
    rows = []
    for hh, g in df.groupby("horizon_h"):
        truth = g["y_true"] < threshold
        started_ok = g["y_origin"] >= threshold
        for m in names:
            pred = g[m] < threshold
            tp = int((truth & pred).sum()); fp = int((~truth & pred).sum()); fn = int((truth & ~pred).sum())
            on_truth = truth & started_ok
            on_tp = int((on_truth & pred).sum())
            on_fp = int((~truth & started_ok & pred).sum())
            rows.append({
                "horizon_h": hh, "method": m, "events": int(truth.sum()), "tp": tp, "fp": fp, "fn": fn,
                "precision": tp / (tp + fp) if tp + fp else np.nan,
                "recall": tp / (tp + fn) if tp + fn else np.nan,
                "onsets": int(on_truth.sum()), "onset_tp": on_tp, "onset_fp": on_fp,
                "onset_recall": on_tp / int(on_truth.sum()) if on_truth.sum() else np.nan,
                "onset_precision": on_tp / (on_tp + on_fp) if on_tp + on_fp else np.nan,
            })
    return pd.DataFrame(rows)
