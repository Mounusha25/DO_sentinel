"""Population stability (PSI) drift checks on model inputs and on the forecast log.

Inputs are compared with the *same calendar month* of the training period, because most of what
moves in a stream (temperature, dissolved oxygen, discharge) is seasonal and a naive "recent vs
everything" comparison would flag every winter. A reference is a small histogram sketch per site,
feature and month, so a monitoring job needs no training data.

PSI = sum((cur - ref) * ln(cur / ref)) over bins. The conventional thresholds (0.1 and 0.25)
do not transfer here: one month of one stream, compared with the same month of other years, has a
PSI of 0.5 to 2.4 on these sites, because year-to-year hydrology varies more than any
within-year window. So a value is judged against the site's own variability: each training year's
month is scored against the other years, and the current window is "stable" if it is within the
90th percentile of those, "moderate" if within their maximum, "elevated" if within twice it, and
"major" beyond that. A site raises `alert` when two or more inputs are "major". On the held-out year
(12 windows of 30 days at 14 sites) that rule fired in 10% of site-windows; the thresholds were
chosen after seeing that year, so the rate is optimistic. A single input beyond its maximum is
common (a fifth year exceeds the max of four with probability 1/5 by chance), hence the two-input
rule. With 30 days of strongly autocorrelated data, "moderate" means "look".
"""
from __future__ import annotations

import numpy as np
import pandas as pd

EPS = 1e-4
MODERATE, MAJOR = 0.1, 0.25
FEATURES = ("do", "temp", "spcond", "turb", "q")  # turbidity and discharge are compared on log1p
MIN_REFERENCE = 500
MIN_CURRENT = 200


def status(value: float | None) -> str:
    """Conventional fixed thresholds; only for comparing two windows with no history of their own."""
    if value is None or not np.isfinite(value):
        return "unknown"
    return "stable" if value < MODERATE else ("moderate" if value < MAJOR else "major")


def status_vs_null(value: float | None, null: list[float] | None) -> str:
    """Judge a PSI against the same site's leave-one-year-out PSIs (see the module docstring)."""
    if value is None or not np.isfinite(value) or not null or len(null) < 2:
        return "unknown"
    top = max(max(null), 0.05)  # a floor, so a site with almost no year-to-year spread is not hypersensitive
    if value <= float(np.quantile(null, 0.9)):
        return "stable"
    if value <= top:
        return "moderate"
    return "elevated" if value <= 2 * top else "major"


def sketch(values: np.ndarray | pd.Series, bins: int = 10, min_n: int = MIN_REFERENCE) -> dict | None:
    """Quantile-bin edges and bin frequencies of a reference sample (None if too small)."""
    v = np.asarray(values, float)
    v = v[np.isfinite(v)]
    if len(v) < min_n:
        return None
    edges = np.unique(np.quantile(v, np.linspace(0, 1, bins + 1)))
    if len(edges) < 3:  # a (near-)constant feature has no distribution to drift from
        return None
    inner = edges[1:-1]
    freq = np.bincount(np.searchsorted(inner, v, side="right"), minlength=len(edges) - 1) / len(v)
    return {"edges": [float(e) for e in edges], "freq": [float(f) for f in freq], "n": int(len(v))}


def psi_from_sketch(ref: dict, current: np.ndarray | pd.Series, min_n: int = MIN_CURRENT) -> float | None:
    """PSI of `current` against a reference sketch; None if there is too little current data."""
    v = np.asarray(current, float)
    v = v[np.isfinite(v)]
    if len(v) < min_n or ref is None:
        return None
    inner = np.asarray(ref["edges"])[1:-1]
    cur = np.bincount(np.searchsorted(inner, v, side="right"), minlength=len(ref["freq"])) / len(v)
    r = np.clip(np.asarray(ref["freq"], float), EPS, None)
    c = np.clip(cur, EPS, None)
    return float(np.sum((c - r) * np.log(c / r)))


def psi(reference, current, bins: int = 10, min_reference: int = MIN_REFERENCE,
        min_current: int = MIN_CURRENT) -> float | None:
    """PSI between two samples (quantile bins from the reference)."""
    return psi_from_sketch(sketch(reference, bins, min_reference), current, min_current)


def _transform(frame: pd.DataFrame, feature: str) -> pd.Series:
    s = frame[feature]
    return np.log1p(s.clip(lower=0)) if feature in ("turb", "q") else s


def build_reference(frame: pd.DataFrame, bins: int = 10) -> dict:
    """{feature: {month: {"sketch", "null"}}} from a training-period frame (do,temp,spcond,turb,q).

    `null` holds, for each training year with enough data in that month, the PSI of that year
    against a sketch of the other years: how far a normal year sits from the rest."""
    out: dict = {}
    for f in FEATURES:
        x = _transform(frame, f)
        out[f] = {}
        for month, g in x.groupby(x.index.month):
            sk = sketch(g.to_numpy(), bins)
            if sk is None:
                continue
            null = []
            years = g.index.year
            for y in np.unique(years):
                cur, rest = g[years == y], g[years != y]
                v = psi(rest.to_numpy(), cur.to_numpy(), bins)
                if v is not None:
                    null.append(round(v, 4))
            out[f][int(month)] = {"sketch": sk, "null": null}
    return out


def input_drift(frame: pd.DataFrame, reference: dict, recent_days: int = 30) -> dict:
    """PSI of the last `recent_days` of each input against the same calendar month of training,
    judged against that site's own year-to-year variability.

    The month is the one holding most of the window. Returns {feature: {psi, status, n, null_p90,
    null_max}} and a top-level `worst` status."""
    recent = frame.loc[frame.index > frame.index.max() - pd.Timedelta(days=recent_days)]
    month = int(recent.index.month.value_counts().idxmax()) if len(recent) else None
    res: dict = {}
    for f in FEATURES:
        entry = (reference.get(f) or {}).get(str(month)) or (reference.get(f) or {}).get(month)
        x = _transform(recent, f)
        v = psi_from_sketch(entry["sketch"], x) if entry and month is not None else None
        null = entry["null"] if entry else None
        res[f] = {"psi": None if v is None else round(v, 4), "status": status_vs_null(v, null),
                  "null_p90": None if not null else round(float(np.quantile(null, 0.9)), 3),
                  "null_max": None if not null else round(float(max(null)), 3),
                  "n": int(x.notna().sum()) if len(recent) else 0}
    order = ["unknown", "stable", "moderate", "elevated", "major"]
    feats = [r for r in res.values() if isinstance(r, dict)]
    res["worst"] = max((r["status"] for r in feats), key=order.index)
    res["n_major"] = sum(r["status"] == "major" for r in feats)
    res["alert"] = res["n_major"] >= 2
    res["window_days"], res["reference_month"] = recent_days, month
    return res


def log_drift(log: pd.DataFrame, site_id: str, window_days: int = 14, horizon_h: int = 24) -> dict:
    """PSI of what the service has been saying: the latest window of the forecast log against the
    window before it, for the 24 h median and the low-DO probability.

    This detects a change in outputs (a model or data regime change), not whether the outputs are
    right; accuracy is the retrain rule's job. Informational only: two adjacent windows have no
    history of their own to judge against, so the conventional thresholds are used and should be
    read loosely (they are too tight for hydrology, see the module docstring)."""
    d = log[(log["site_id"] == site_id)] if len(log) else log
    if d.empty:
        return {"status": "unknown", "reason": "no logged forecasts"}
    issued = pd.to_datetime(d["issued_at"], utc=True)
    end = issued.max()
    cur = d[issued > end - pd.Timedelta(days=window_days)]
    ref = d[(issued <= end - pd.Timedelta(days=window_days)) & (issued > end - pd.Timedelta(days=2 * window_days))]
    out: dict = {"window_days": window_days, "n_current": int(len(cur)), "n_reference": int(len(ref))}
    vals = []
    for name, col, sub in (("median_24h", "median_mgL", lambda x: x[x["horizon_h"] == horizon_h]),
                           ("p_low_do", "p_low_do_within_24h", lambda x: x.drop_duplicates(["issued_at"]))):
        a, b = sub(ref), sub(cur)
        v = psi(a[col].to_numpy(), b[col].to_numpy(), min_reference=50, min_current=50)
        out[name] = {"psi": None if v is None else round(v, 4), "status": status(v)}
        vals.append(v)
    known = [v for v in vals if v is not None]
    out["status"] = status(max(known)) if known else "unknown"
    return out
