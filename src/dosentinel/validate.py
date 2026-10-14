"""Quality flags for continuous sensor series.

Each check is a boolean column on a regular 15-minute grid. Flags annotate; they
never drop or modify values, so downstream code decides what to do with them
(train on clean points, feed the fault detector, suppress alerts).

Thresholds are first-pass values informed by these three sites (see RULES). They are a
starting point for the fault detector, not ground truth: USGS-Approved data has already
had most faults removed, so flag rates there understate how often sensors misbehave.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

FREQ = "15min"


@dataclass(frozen=True)
class Rule:
    lo: float  # physically implausible below this
    hi: float  # physically implausible above this
    max_step: float  # largest believable change between consecutive 15-min readings
    flat_steps: int  # run of identical readings (in steps) treated as a stuck sensor


# Calibrated against the three configured sites (3 years, 15-min): flatline limits sit
# above the longest run seen in USGS-Approved data for DO/temp/turbidity, and step limits
# sit above its largest 15-min change. DO and temperature are reported to 0.1, so equal
# readings for many hours are normal in stable water; short flatline limits are wrong.
# Discharge is a covariate, not a fault target: constant dam releases are legitimate and
# storm rises are huge, so only the plausibility range is meaningful for it.
RULES: dict[str, Rule] = {
    "00300": Rule(lo=0.0, hi=25.0, max_step=1.5, flat_steps=288),  # DO mg/L, 72 h
    "00010": Rule(lo=-1.0, hi=40.0, max_step=1.5, flat_steps=144),  # water temp degC, 36 h
    "00095": Rule(lo=0.0, hi=20000.0, max_step=600.0, flat_steps=192),  # spec. cond. uS/cm, 48 h
    "63680": Rule(lo=0.0, hi=4000.0, max_step=1000.0, flat_steps=48),  # turbidity FNU, 12 h
    "00060": Rule(lo=0.0, hi=1e7, max_step=1e9, flat_steps=672),  # discharge cfs, 7 d
}

FLAGS = ["missing", "out_of_range", "flatline", "spike"]


def to_grid(s: pd.Series, freq: str = FREQ) -> pd.Series:
    """Reindex a time-indexed series onto a regular grid; absent steps become NaN."""
    s = s[~s.index.duplicated(keep="last")].sort_index()
    if s.empty:
        return s
    grid = pd.date_range(s.index.min().ceil(freq), s.index.max().floor(freq), freq=freq)
    # Off-grid timestamps are snapped to the nearest step within half a step.
    return s.reindex(grid, method="nearest", tolerance=pd.Timedelta(freq) / 2)


def flag_series(s: pd.Series, rule: Rule, freq: str = FREQ) -> pd.DataFrame:
    """Return value + boolean flag columns for one series (UTC DatetimeIndex)."""
    v = to_grid(s, freq)
    out = pd.DataFrame({"value": v})
    out["missing"] = v.isna()
    out["out_of_range"] = (v < rule.lo) | (v > rule.hi)

    run_id = (v != v.shift()).cumsum()
    run_len = v.groupby(run_id).transform("size")
    out["flatline"] = (run_len >= rule.flat_steps) & v.notna()

    # A step is only judged when both neighbours are observed, so gaps do not
    # masquerade as spikes.
    step = v.diff().abs()
    out["spike"] = (step > rule.max_step).fillna(False)
    return out


def summarize(flags: pd.DataFrame) -> dict[str, float]:
    n = len(flags)
    obs = int((~flags["missing"]).sum())
    row = {"steps": n, "observed_frac": obs / n if n else float("nan")}
    for f in FLAGS[1:]:
        row[f"{f}_frac"] = float(flags[f].sum() / obs) if obs else float("nan")
    return row
