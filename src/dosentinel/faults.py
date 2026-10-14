"""Synthetic sensor-fault injection for benchmarking detectors.

Real USGS approved data has had its faults removed, so there are few real labels. We inject
known faults into clean stretches of a DO series and score detectors against the known
labels. The fault models are simple and deliberately stated; results show how detectors
behave on *these* faults, not on every failure real probes exhibit.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

KINDS = ("spike", "offset", "drift", "flatline", "stuck_low")
SEVERITIES = ("small", "medium", "large")
# magnitudes in mg/L for spike / offset / drift (by severity index)
MAGNITUDE = {"spike": (1.0, 2.0, 4.0), "offset": (0.5, 1.0, 2.0), "drift": (0.5, 1.0, 2.0)}
# durations in 15-min steps: (low, high) inclusive, by severity index where it varies
DURATION = {
    "spike": [(1, 3)] * 3,
    "offset": [(24, 288)] * 3,
    "drift": [(288, 1344)] * 3,
    "flatline": [(24, 48), (48, 96), (96, 192)],
    "stuck_low": [(8, 24), (24, 48), (48, 96)],
}
DEFAULT_COUNTS = {"spike": 60, "offset": 30, "drift": 15, "flatline": 30, "stuck_low": 20}


@dataclass(frozen=True)
class FaultEvent:
    id: int
    kind: str
    severity: str
    start: int  # position of first faulty step
    end: int  # position one past the last faulty step
    magnitude: float


def inject_faults(v: pd.Series, rng: np.random.Generator, counts: dict | None = None,
                  start_pos: int = 0, buffer: int = 96, max_tries: int = 100):
    """Return (corrupted series, list of FaultEvent, per-step event id or -1).

    Faults are placed at random, non-overlapping positions (with `buffer` clean steps
    between events) inside windows that are at least 95% observed, at or after `start_pos`.
    Existing NaNs are preserved. Values are clipped at 0 mg/L.
    """
    counts = counts or DEFAULT_COUNTS
    vals = v.to_numpy(dtype=float).copy()
    orig = vals.copy()
    n = len(vals)
    obs = ~np.isnan(vals)
    occupied = np.zeros(n, bool)
    occupied[:start_pos] = True
    label = np.full(n, -1, dtype=int)
    events: list[FaultEvent] = []

    plan = [(k, si) for k in KINDS for si in range(3) for _ in range(counts.get(k, 0) // 3)]
    order = rng.permutation(len(plan))
    for j in order:
        kind, si = plan[j]
        lo, hi = DURATION[kind][si]
        dur = int(rng.integers(lo, hi + 1))
        for _ in range(max_tries):
            s = int(rng.integers(start_pos, max(start_pos + 1, n - dur - 1)))
            e = s + dur
            if e >= n or occupied[max(0, s - buffer): e + buffer].any():
                continue
            if obs[s:e].mean() < 0.95 or np.isnan(orig[s]):
                continue
            sign = 1.0 if rng.random() < 0.5 else -1.0
            seg = slice(s, e)
            mag = 0.0
            if kind in ("spike", "offset"):
                mag = sign * MAGNITUDE[kind][si]
                vals[seg] = np.where(obs[seg], orig[seg] + mag, np.nan)
            elif kind == "drift":
                mag = sign * MAGNITUDE[kind][si]
                ramp = mag * np.arange(1, dur + 1) / dur
                vals[seg] = np.where(obs[seg], orig[seg] + ramp, np.nan)
            elif kind == "flatline":
                vals[seg] = np.where(obs[seg], orig[s], np.nan)
            elif kind == "stuck_low":
                mag = float(rng.uniform(0.0, 0.3))
                vals[seg] = np.where(obs[seg], mag, np.nan)
            vals[seg] = np.where(np.isnan(vals[seg]), np.nan, np.clip(vals[seg], 0.0, None))
            occupied[s:e] = True
            label[s:e] = len(events)
            events.append(FaultEvent(len(events), kind, SEVERITIES[si], s, e, float(mag)))
            break
    return pd.Series(vals, index=v.index, name=v.name), events, label


def inject_drifts(v: pd.Series, rng: np.random.Generator,
                  magnitudes=(0.5, 1.0, 2.0), durations_days=(3, 7, 14), per_cell: int = 1,
                  start_pos: int = 0, buffer: int = 22 * 96, max_tries: int = 300):
    """Drift-only injection on a magnitude x duration grid.

    Each event ramps linearly from 0 to +/- magnitude mg/L over its duration and then snaps
    back (a cleaned sensor). Events are separated by `buffer` steps (default 22 days) so one
    event's tail cannot contaminate a long-lag reference used to detect the next.
    Returns (series, events, per-step event id or -1); `severity` is e.g. "1mg_7d".
    """
    vals = v.to_numpy(dtype=float).copy()
    orig = vals.copy()
    n = len(vals)
    obs = ~np.isnan(vals)
    occupied = np.zeros(n, bool)
    occupied[:start_pos] = True
    label = np.full(n, -1, dtype=int)
    events: list[FaultEvent] = []
    cells = [(m, d) for m in magnitudes for d in durations_days for _ in range(per_cell)]
    for j in rng.permutation(len(cells)):
        mag, days = cells[j]
        dur = int(days * 96)
        for _ in range(max_tries):
            s0 = int(rng.integers(start_pos, max(start_pos + 1, n - dur - 1)))
            e0 = s0 + dur
            if e0 >= n or occupied[max(0, s0 - buffer): e0 + buffer].any():
                continue
            if obs[s0:e0].mean() < 0.9 or np.isnan(orig[s0]):
                continue
            sign = 1.0 if rng.random() < 0.5 else -1.0
            ramp = sign * mag * np.arange(1, dur + 1) / dur
            seg = slice(s0, e0)
            vals[seg] = np.where(obs[seg], np.clip(orig[seg] + ramp, 0.0, None), np.nan)
            occupied[s0:e0] = True
            label[s0:e0] = len(events)
            events.append(FaultEvent(len(events), "drift", f"{mag:g}mg_{days}d", s0, e0, float(sign * mag)))
            break
    return pd.Series(vals, index=v.index, name=v.name), events, label
