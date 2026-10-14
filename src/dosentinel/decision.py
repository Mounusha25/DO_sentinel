"""Decision layer: turn forecast probabilities and fault state into alert actions.

Actions per origin: ``warning`` (low-DO risk), ``maintenance`` (data suspect, ecological
alerts suppressed), or ``none``. Alerts are scored per *episode* with lead time and
alerts per week; per-timestep metrics would overstate quality because low-DO states
persist for hours.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

STEP_MIN = 15
STEPS_PER_WEEK = 7 * 24 * 60 // STEP_MIN


@dataclass(frozen=True)
class Costs:
    """Relative cost of a missed low-DO episode and of a false alert (cluster)."""
    miss: float = 10.0
    false_alarm: float = 1.0

    @property
    def cutoff(self) -> float:
        """Bayes-optimal probability cutoff when probabilities are calibrated."""
        return self.false_alarm / (self.false_alarm + self.miss)


def decide(p: np.ndarray, suspect: np.ndarray, cutoff: float) -> np.ndarray:
    """Maintenance wins over warning: no ecological alert on suspect data."""
    out = np.where(np.asarray(p) > cutoff, "warning", "none").astype(object)
    out[np.asarray(suspect, bool)] = "maintenance"
    return out


def episodes_from_series(v: pd.Series, thr: float, merge_gap: int = 8, min_len: int = 4):
    """Low-DO episodes as (start, end) integer positions (inclusive).

    Runs below `thr` separated by at most `merge_gap` steps are merged (sensor noise around
    the threshold is one event), and runs shorter than `min_len` steps are dropped.
    Missing values are treated as not low.
    """
    low = (v.to_numpy() < thr)
    idx = np.flatnonzero(low)
    if idx.size == 0:
        return []
    breaks = np.flatnonzero(np.diff(idx) > 1)
    starts = np.r_[idx[0], idx[breaks + 1]]
    ends = np.r_[idx[breaks], idx[-1]]
    merged: list[list[int]] = []
    for s, e in zip(starts, ends):
        if merged and s - merged[-1][1] - 1 <= merge_gap:
            merged[-1][1] = int(e)
        else:
            merged.append([int(s), int(e)])
    return [(s, e) for s, e in merged if e - s + 1 >= min_len]


def alert_clusters(alert: np.ndarray, cooldown: int = 24):
    """Group alerting steps into clusters; a quiet spell longer than `cooldown` ends one."""
    idx = np.flatnonzero(np.asarray(alert, bool))
    if idx.size == 0:
        return []
    breaks = np.flatnonzero(np.diff(idx) > cooldown)
    starts = np.r_[idx[0], idx[breaks + 1]]
    ends = np.r_[idx[breaks], idx[-1]]
    return list(zip(starts.astype(int), ends.astype(int)))


def evaluate_alerts(episodes, alert: np.ndarray, horizon_steps: int = 96, cooldown: int = 24,
                    costs: Costs = Costs(), not_low: np.ndarray | None = None) -> dict:
    """Episode-level scoring of an alert stream.

    * *detected*: some alert fired between `horizon_steps` before an episode's start and
      its end (includes alerts raised once the episode is already under way).
    * *anticipated*: an alert fired strictly before the start at an origin where DO was
      not already below the threshold (`not_low`). Only anticipation is useful warning,
      so cost counts un-anticipated episodes as misses.
    * An alert cluster is a *false alarm* if no episode overlaps
      [cluster start, cluster end + horizon_steps].
    """
    alert = np.asarray(alert, bool)
    n_steps = len(alert)
    can_anticipate = np.ones(n_steps, bool) if not_low is None else np.asarray(not_low, bool)
    detected = anticipated = 0
    leads = []
    for s, e in episodes:
        lo = max(0, s - horizon_steps)
        if alert[lo:e + 1].any():
            detected += 1
        early = np.flatnonzero(alert[lo:s] & can_anticipate[lo:s])
        if early.size:
            anticipated += 1
            leads.append((s - (lo + int(early[0]))) * STEP_MIN / 60)
    clusters = alert_clusters(alert, cooldown)
    false_alarms = sum(
        1 for a0, a1 in clusters
        if not any(s <= a1 + horizon_steps and e >= a0 for s, e in episodes)
    )
    weeks = n_steps / STEPS_PER_WEEK
    events = len(episodes)
    return {
        "events": events, "detected": detected, "anticipated": anticipated,
        "recall": detected / events if events else np.nan,
        "recall_anticipated": anticipated / events if events else np.nan,
        "median_lead_h": float(np.median(leads)) if leads else np.nan,
        "clusters": len(clusters), "false_alarms": false_alarms,
        "cluster_precision": (len(clusters) - false_alarms) / len(clusters) if clusters else np.nan,
        "alerts_per_week": len(clusters) / weeks if weeks else np.nan,
        "false_alarms_per_week": false_alarms / weeks if weeks else np.nan,
        "cost_per_week": (costs.miss * (events - anticipated) + costs.false_alarm * false_alarms) / weeks
        if weeks else np.nan,
    }


def chance_baseline(episodes, alert: np.ndarray, horizon_steps: int = 96, cooldown: int = 24,
                    costs: Costs = Costs(), not_low: np.ndarray | None = None,
                    trials: int = 200, seed: int = 0) -> dict:
    """Score randomly placed alerts with the same number and durations of clusters.

    Answers "how much of this precision/recall would any alerting at this rate get by
    luck?". Episodes that occur every few days make chance performance far from zero.
    """
    rng = np.random.default_rng(seed)
    n = len(alert)
    spans = [a1 - a0 + 1 for a0, a1 in alert_clusters(alert, cooldown)]
    if not spans:
        return {}
    keys = ("cluster_precision", "recall_anticipated", "cost_per_week")
    acc = {k: [] for k in keys}
    for _ in range(trials):
        fake = np.zeros(n, bool)
        for L in spans:
            a0 = int(rng.integers(0, max(1, n - L)))
            fake[a0:a0 + L] = True
        r = evaluate_alerts(episodes, fake, horizon_steps, cooldown, costs, not_low)
        for k in keys:
            acc[k].append(r[k])
    return {f"chance_{k}": float(np.nanmean(v)) for k, v in acc.items()}
