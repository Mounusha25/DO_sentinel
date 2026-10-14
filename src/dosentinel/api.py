"""FastAPI service: latest forecast, calibrated probability, fault flags and action per site.

    DOSENTINEL_MODELS=models USGS_API_KEY=... uvicorn dosentinel.api:app

GET  /forecast/{site_id}   pulls the latest ~40 days from USGS (cached 5 min)
POST /forecast/{site_id}   forecasts from series you supply (no network needed)
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import joblib
import pandas as pd
import yaml
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

from .features import PARAM_COLS
from .ingest import UsgsClient
from .monitoring import (data_age_minutes, input_health, log_forecast, read_log, retrain_decision,
                         score_log)
from .serving import SiteBundle, clean_frame_from_raw, forecast

HISTORY_DAYS = 40
CACHE_SECONDS = 300
CAVEATS = [
    "Alerts built on this probability were competitive with, not better than, a simple 'DO < threshold + 0.5 mg/L' rule in evaluation.",
    "Slow sensor drift is not reliably detectable; flags cover jumps, spikes, flatlines and stuck-low readings.",
    "Models are trained per site on USGS provisional/approved data; provisional values are revised later.",
]

app = FastAPI(title="do-sentinel", version="0.1.0",
              description="Short-horizon dissolved-oxygen forecasting, sensor-fault flags and alert decisions.")
# Read-only public API: allow browsers on the listed origins (comma-separated) to call it.
_origins = [o.strip() for o in os.environ.get("DOSENTINEL_CORS", "https://mounusha25.github.io").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=_origins, allow_methods=["GET", "POST", "OPTIONS"],
                   allow_headers=["*"], max_age=3600)
MAX_OBSERVATIONS = 20000  # total points per request (a 40-day history is about 3,840 per series)
_bundles: dict[str, SiteBundle] = {}
_cache: dict[str, tuple[float, dict]] = {}


def models_dir() -> Path:
    return Path(os.environ.get("DOSENTINEL_MODELS", "models"))


def get_bundle(site_id: str) -> SiteBundle:
    if site_id not in _bundles:
        path = models_dir() / f"{site_id}.joblib"
        if not path.exists():
            raise HTTPException(404, f"no model bundle for site {site_id}")
        _bundles[site_id] = joblib.load(path)
    return _bundles[site_id]


class Observation(BaseModel):
    time: str = Field(..., description="RFC 3339 timestamp, UTC")
    value: float | None


class SeriesPayload(BaseModel):
    series: dict[str, list[Observation]] = Field(
        ..., description="parameter code -> observations; 00300 (DO) required, 00010/00095/63680/00060 optional")
    cost_ratio: float = Field(10.0, gt=0, le=1000, description="cost of a missed episode relative to a false alert")

    @field_validator("series")
    @classmethod
    def _bounded(cls, v):
        if sum(len(x) for x in v.values()) > MAX_OBSERVATIONS:
            raise ValueError(f"too many observations (limit {MAX_OBSERVATIONS})")
        return v


def _frame_from_payload(series: dict[str, list[Observation]]) -> pd.DataFrame:
    raw = {}
    for code, obs in series.items():
        if code not in PARAM_COLS:
            raise HTTPException(422, f"unknown parameter code {code}")
        s = pd.Series([o.value for o in obs], index=pd.to_datetime([o.time for o in obs], utc=True), dtype=float)
        raw[code] = s[~s.index.duplicated(keep="last")].sort_index()
    try:
        return clean_frame_from_raw(raw)
    except ValueError as e:
        raise HTTPException(422, str(e))


def log_path() -> Path | None:
    p = os.environ.get("DOSENTINEL_LOG")
    return Path(p) if p else None


def _respond(bundle: SiteBundle, frame: pd.DataFrame, cost_ratio: float, live: bool = False) -> dict:
    try:
        res = forecast(bundle, frame, cost_ratio)
    except ValueError as e:
        raise HTTPException(422, str(e))
    res["input_health"] = input_health(frame)
    res["data_age_minutes"] = data_age_minutes(frame.index[-1]) if live else None
    if live and log_path() is not None:
        log_forecast(res, log_path())
    res["model"] = {"trained_through": bundle.trained_through.isoformat(),
                    "calibrated_through": bundle.calibrated_through.isoformat(),
                    "role": bundle.role}
    res["caveats"] = CAVEATS
    return res


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "models_dir": str(models_dir()), "bundles_available": sorted(
        p.stem for p in models_dir().glob("*.joblib")) if models_dir().exists() else []}


@app.get("/sites")
def sites() -> list[dict]:
    out = []
    cfg = Path(os.environ.get("DOSENTINEL_CONFIG", "config/sites.yaml"))
    meta = {s["id"]: s for s in yaml.safe_load(cfg.read_text())["sites"]} if cfg.exists() else {}
    for p in sorted(models_dir().glob("*.joblib")) if models_dir().exists() else []:
        sid = p.stem
        m = meta.get(sid, {})
        out.append({"site_id": sid, "name": m.get("name"), "state": m.get("state"), "role": m.get("role"),
                    "drainage_sqmi": m.get("drainage_sqmi")})
    return out


@app.get("/forecast/{site_id}")
def forecast_live(site_id: str, cost_ratio: float = Query(10.0, gt=0)) -> dict:
    bundle = get_bundle(site_id)
    now = time.time()
    hit = _cache.get(site_id)
    if hit is None or now - hit[0] > CACHE_SECONDS:
        end = pd.Timestamp.now(tz="UTC").floor("15min")
        start = end - pd.Timedelta(days=HISTORY_DAYS)
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        client = UsgsClient()
        raw = {}
        for code in PARAM_COLS:
            try:
                df = client.fetch_continuous(site_id, code, start.strftime(fmt), end.strftime(fmt))
            except Exception as e:  # network / upstream failure
                if code == "00300":
                    raise HTTPException(502, f"could not fetch DO data from USGS: {e}")
                continue
            if not df.empty:
                raw[code] = df.drop_duplicates("time").set_index("time")["value"].sort_index()
        try:
            frame = clean_frame_from_raw(raw)
        except ValueError as e:
            raise HTTPException(502, str(e))
        _cache[site_id] = (now, frame)
        hit = _cache[site_id]
    return _respond(bundle, hit[1], cost_ratio, live=True)


@app.post("/forecast/{site_id}")
def forecast_from_data(site_id: str, payload: SeriesPayload) -> dict:
    bundle = get_bundle(site_id)
    return _respond(bundle, _frame_from_payload(payload.series), payload.cost_ratio)


@app.get("/monitor/{site_id}")
def monitor(site_id: str) -> dict:
    """Score logged forecasts for this site against the latest observations and apply the retrain rule."""
    bundle = get_bundle(site_id)
    path = log_path()
    if path is None:
        raise HTTPException(409, "forecast logging is off; set DOSENTINEL_LOG to a file path")
    hit = _cache.get(site_id)
    if hit is None:
        forecast_live(site_id)  # populates the cache (and logs one forecast)
        hit = _cache[site_id]
    frame = hit[1]
    log = read_log(path)
    log = log[log["site_id"] == site_id]
    scored = score_log(log, frame["do"].dropna())
    matured = scored.dropna(subset=["abs_error"])
    ref = {int(h): float(v) for h, v in bundle.metrics.get("reference_mae_h", {}).items()}
    age = (pd.Timestamp.now(tz="UTC") - bundle.calibrated_through).days
    decision = retrain_decision(scored, ref, age, 1.0 - input_health(frame)["parameters"]["do"]["observed_7d"])
    by_h = {int(h): {"n": int(len(g)), "mae": float(g["abs_error"].mean()), "coverage": float(g["inside"].mean()),
                     "reference_mae": ref.get(int(h))} for h, g in matured.groupby("horizon_h")}
    return {"site_id": site_id, "logged_forecasts": int(len(log)), "matured_forecasts": int(len(matured)),
            "by_horizon": by_h, "model_age_days": int(age), "retrain": decision}
