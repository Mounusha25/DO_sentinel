"""Operational monitoring: input health, forecast logging, later scoring, and a retrain rule.

Every served forecast is appended to a JSON-lines log. Once the target times have passed, the log
is scored against observed values; that gives (a) live quality tracking and (b) the forward
holdout described in PROBLEM.md, the only evaluation that no modelling decision has seen.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

LOG_FIELDS = ("site_id", "issued_at", "as_of", "horizon_h", "target_time", "median_mgL", "low_mgL",
              "high_mgL", "p_low_do_within_24h", "action", "forecast_model")


def data_age_minutes(as_of: pd.Timestamp, now: pd.Timestamp | None = None) -> float:
    """Minutes between the latest observation used and the time of the request."""
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    return float((now - pd.Timestamp(as_of)).total_seconds() / 60.0)


def input_health(frame: pd.DataFrame, max_gap_hours: float = 6.0) -> dict:
    """Per-parameter completeness over the last 24 h and 7 d, and the longest current gap in DO."""
    out = {"parameters": {}}
    last24, last7d = frame.iloc[-96:], frame.iloc[-96 * 7:]
    for c in frame.columns:
        out["parameters"][c] = {"observed_24h": float(last24[c].notna().mean()),
                                "observed_7d": float(last7d[c].notna().mean())}
    do = frame["do"]
    ok = do.notna().to_numpy()
    gap = 0
    for v in ok[::-1]:
        if v:
            break
        gap += 1
    out["do_current_gap_hours"] = gap * 0.25
    out["do_gap_alert"] = bool(gap * 0.25 > max_gap_hours)
    return out


def log_forecast(report: dict, path: str | Path, issued_at: pd.Timestamp | None = None) -> int:
    """Append one line per horizon to the log; returns the number of lines written."""
    issued = (pd.Timestamp.now(tz="UTC") if issued_at is None else pd.Timestamp(issued_at)).isoformat()
    lines = []
    for f in report.get("forecasts", []):
        lines.append(json.dumps({
            "site_id": report["site_id"], "issued_at": issued, "as_of": report["as_of"],
            "horizon_h": f["horizon_h"], "target_time": f["target_time"], "median_mgL": f["median_mgL"],
            "low_mgL": f["low_mgL"], "high_mgL": f["high_mgL"],
            "p_low_do_within_24h": report.get("p_low_do_within_24h"), "action": report.get("action"),
            "forecast_model": report.get("forecast_model", "gbm")}))
    if lines:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a") as fh:
            fh.write("\n".join(lines) + "\n")
    return len(lines)


def read_log(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        return pd.DataFrame(columns=LOG_FIELDS)
    df = pd.read_json(p, lines=True)
    if "forecast_model" not in df.columns:  # logs written before weather models existed
        df["forecast_model"] = "gbm"
    df["forecast_model"] = df["forecast_model"].fillna("gbm")
    for c in ("issued_at", "as_of", "target_time"):
        df[c] = pd.to_datetime(df[c], utc=True)
    return df


def score_log(log: pd.DataFrame, observed: pd.Series) -> pd.DataFrame:
    """Attach observed values (UTC-indexed series) to logged forecasts and score what has matured."""
    if log.empty:
        return log.assign(observed=np.nan, abs_error=np.nan, inside=np.nan)
    d = log.copy()
    obs = observed[~observed.index.duplicated(keep="last")]
    d["observed"] = obs.reindex(pd.DatetimeIndex(d["target_time"])).to_numpy()
    d["abs_error"] = (d["median_mgL"] - d["observed"]).abs()
    d["inside"] = ((d["observed"] >= d["low_mgL"]) & (d["observed"] <= d["high_mgL"])).astype(float)
    d.loc[d["observed"].isna(), ["abs_error", "inside"]] = np.nan
    return d


@dataclass(frozen=True)
class RetrainRule:
    error_ratio: float = 1.25  # live MAE / reference MAE above this ...
    min_scored: int = 200      # ... with at least this many matured forecasts
    coverage_floor: float = 0.65
    max_missing_do: float = 0.30  # fraction of the last 7 days
    max_age_days: float = 365.0


def retrain_decision(scored: pd.DataFrame, reference_mae: dict, model_age_days: float,
                     missing_do_7d: float, rule: RetrainRule = RetrainRule()) -> dict:
    """Which, if any, retrain triggers fire.

    `reference_mae` maps horizon (h) to its evaluation MAE, or, to compare each forecaster with
    its own level, maps a `forecast_model` name to such a dict (names missing from it are
    compared with the "gbm" entry)."""
    reasons = []
    m = scored.dropna(subset=["abs_error"])
    per_model = bool(reference_mae) and isinstance(next(iter(reference_mae.values())), dict)
    groups = ([(name, g) for name, g in m.groupby("forecast_model")] if per_model and "forecast_model" in m
              else [(None, m)])
    for name, sub in groups:
        if len(sub) < rule.min_scored:
            continue
        refs = (reference_mae.get(name) or reference_mae.get("gbm", {})) if per_model else reference_mae
        tag = f" ({name})" if per_model and len(groups) > 1 else ""
        for h, g in sub.groupby("horizon_h"):
            ref = refs.get(int(h))
            if ref and g["abs_error"].mean() > rule.error_ratio * ref:
                reasons.append(f"MAE at {int(h)} h{tag} is {g['abs_error'].mean() / ref:.2f}x its evaluation level")
        if sub["inside"].mean() < rule.coverage_floor:
            reasons.append(f"interval coverage{tag} {sub['inside'].mean():.2f} below {rule.coverage_floor:.2f}")
    if missing_do_7d > rule.max_missing_do:
        reasons.append(f"{missing_do_7d:.0%} of DO missing over the last 7 days")
    if model_age_days > rule.max_age_days:
        reasons.append(f"model is {model_age_days:.0f} days old")
    return {"retrain": bool(reasons), "reasons": reasons, "scored_forecasts": int(len(m))}


# bundle.metrics key holding the calibration-window MAE of each forecaster the API can answer with
REFERENCE_KEYS = {"gbm": "reference_mae_h", "gbm_weather": "wx_reference_mae_h",
                  "gbm_weather+chronos2": "blend_reference_mae_h"}


def reference_maes(metrics: dict) -> dict[str, dict[int, float]]:
    """{forecast_model: {horizon_h: MAE}} for the forecasters a bundle has reference numbers for."""
    return {name: {int(h): float(v) for h, v in metrics[key].items()}
            for name, key in REFERENCE_KEYS.items() if metrics.get(key)}


def site_status(bundle, log: pd.DataFrame, observed_do: pd.Series, now: pd.Timestamp | None = None,
                rule: RetrainRule = RetrainRule()) -> dict:
    """One site's retrain verdict from its logged forecasts and the DO series observed since.

    `bundle` needs `.site_id`, `.calibrated_through` and `.metrics`; `observed_do` is the cleaned,
    UTC-indexed DO series (any frequency the log's target times fall on)."""
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    age = float((now - bundle.calibrated_through).days)
    recent = observed_do.iloc[-96 * 7:]
    missing = float(1.0 - recent.notna().mean()) if len(recent) else 1.0
    mine = log[log["site_id"] == bundle.site_id] if len(log) else log
    scored = score_log(mine, observed_do.dropna())
    d = retrain_decision(scored, reference_maes(bundle.metrics), age, missing, rule)
    return {"site_id": bundle.site_id, "model_age_days": age, "missing_do_7d": missing,
            "logged_forecasts": int(len(mine)), **d}
