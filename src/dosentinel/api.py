"""FastAPI service: latest forecast, calibrated probability, fault flags and action per site.

    DOSENTINEL_MODELS=models USGS_API_KEY=... uvicorn dosentinel.api:app

GET  /forecast/{site_id}   pulls the latest ~40 days from USGS (cached 5 min)
POST /forecast/{site_id}   forecasts from series you supply (no network needed)
"""
from __future__ import annotations

import hmac
import json
import os
import threading
import time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path

import joblib
import pandas as pd
import requests
import yaml
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from .drift import input_drift, log_drift
from .features import PARAM_COLS
from .ingest import UsgsClient
from .monitoring import (
    data_age_minutes,
    input_health,
    log_forecast,
    read_log,
    reference_maes,
    retrain_decision,
    score_log,
)
from .serving import SiteBundle, clean_frame_from_raw, forecast
from .weather_live import fetch_many, from_snapshot

HISTORY_DAYS = 40
CACHE_SECONDS = 600
WEATHER_CACHE_SECONDS = 1800
WEATHER_RETRY_SECONDS = 300  # after a failed fetch, do not try again (and pay its latency) for this long
SNAPSHOT_MAX_AGE_H = 12  # a published weather snapshot older than this is not used
FETCH_DEADLINE_S = 35  # hard limit for the whole USGS pull
CAVEATS = [
    "Alerts built on this probability were competitive with, not better than, a simple 'DO < threshold + 0.5 mg/L' rule in evaluation.",
    "Slow sensor drift is not reliably detectable; flags cover jumps, spikes, flatlines and stuck-low readings.",
    "Models are trained per site on USGS provisional/approved data; provisional values are revised later.",
]

app = FastAPI(title="do-sentinel", version="0.1.0",
              description="Short-horizon dissolved-oxygen forecasting, sensor-fault flags and alert decisions.")
# ---- access control -------------------------------------------------------------------------
# DOSENTINEL_API_KEYS="key1,key2" requires one of them in the X-API-Key header on every route
# except /health. DOSENTINEL_RATE_LIMIT=N caps requests per client per minute (default 60; 0
# disables). Both are off-by-default-safe: with no keys set the API stays public, as before.
RATE_WINDOW_S = 60.0
_hits: dict[str, deque] = defaultdict(deque)
_hits_lock = threading.Lock()
OPEN_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}


def _api_keys() -> list[str]:
    return [k.strip() for k in os.environ.get("DOSENTINEL_API_KEYS", "").split(",") if k.strip()]


def _rate_limit() -> int:
    try:
        return int(os.environ.get("DOSENTINEL_RATE_LIMIT", "60"))
    except ValueError:
        return 60


def _client_id(request: Request, key: str | None) -> str:
    if key:
        return "key:" + key
    # behind one trusted proxy the *last* X-Forwarded-For entry is the one the proxy appended;
    # earlier entries are client-supplied and must not be used to dodge the limit
    fwd = request.headers.get("x-forwarded-for", "")
    return "ip:" + (fwd.split(",")[-1].strip() if fwd else (request.client.host if request.client else "?"))


def rate_check(client: str, limit: int, now: float | None = None) -> float | None:
    """Record a hit; return seconds to wait if `client` is over `limit` in the window, else None."""
    if limit <= 0:
        return None
    now = time.monotonic() if now is None else now
    with _hits_lock:
        q = _hits[client]
        while q and now - q[0] > RATE_WINDOW_S:
            q.popleft()
        if len(q) >= limit:
            return max(1.0, RATE_WINDOW_S - (now - q[0]))
        q.append(now)
        if len(_hits) > 10000:  # bound memory: drop idle clients
            for k in [k for k, d in _hits.items() if not d or now - d[-1] > RATE_WINDOW_S]:
                _hits.pop(k, None)
    return None


@app.middleware("http")
async def access_control(request: Request, call_next):
    if request.method == "OPTIONS" or request.url.path in OPEN_PATHS:
        return await call_next(request)
    keys = _api_keys()
    given = request.headers.get("x-api-key")
    if keys and not (given and any(hmac.compare_digest(given, k) for k in keys)):
        return JSONResponse({"detail": "missing or invalid API key (X-API-Key header)"}, status_code=401)
    wait_s = rate_check(_client_id(request, given if keys else None), _rate_limit())
    if wait_s is not None:
        return JSONResponse({"detail": "rate limit exceeded"}, status_code=429,
                            headers={"Retry-After": str(int(wait_s) + 1)})
    return await call_next(request)


# Added last so it is outermost: 401/429 responses from the access check still carry CORS headers.
# Read-only public API: allow browsers on the listed origins (comma-separated) to call it.
_origins = [o.strip() for o in os.environ.get("DOSENTINEL_CORS", "https://mounusha25.github.io").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=_origins, allow_methods=["GET", "POST", "OPTIONS"],
                   allow_headers=["*"], max_age=3600)
MAX_OBSERVATIONS = 20000  # total points per request (a 40-day history is about 3,840 per series)
_bundles: dict[str, SiteBundle] = {}
_cache: dict[str, tuple[float, dict]] = {}
_wx_cache: dict[str, tuple[float, pd.DataFrame]] = {}
_foundation_state: dict = {}


def models_dir() -> Path:
    return Path(os.environ.get("DOSENTINEL_MODELS", "models"))


def get_bundle(site_id: str) -> SiteBundle:
    if site_id not in _bundles:
        path = models_dir() / f"{site_id}.joblib"
        if not path.exists():
            raise HTTPException(404, f"no model bundle for site {site_id}")
        _bundles[site_id] = joblib.load(path)
    return _bundles[site_id]


_wx_batch_fail: list = []  # [time, status] of the last failed batch refresh
_wx_status: dict[str, str] = {}  # site -> how its cached weather was obtained ("ok" or "snapshot (N h old)")


def site_coordinates() -> dict[str, tuple[float, float]]:
    """Station coordinates for every served site (models/site_coordinates.json), so one weather request
    can cover them all."""
    path = Path(os.environ.get("DOSENTINEL_COORDS", str(models_dir() / "site_coordinates.json")))
    if not path.exists():
        return {}
    return {k: (v["lat"], v["lon"]) for k, v in json.loads(path.read_text()).items()}


def _snapshot_frames() -> tuple[dict[str, pd.DataFrame], str] | None:
    """Weather published by the scheduled job (DOSENTINEL_WEATHER_SNAPSHOT_URL or _FILE), if recent."""
    src = os.environ.get("DOSENTINEL_WEATHER_SNAPSHOT_FILE") or os.environ.get("DOSENTINEL_WEATHER_SNAPSHOT_URL")
    if not src:
        return None
    try:
        if src.startswith(("http://", "https://")):
            r = requests.get(src, timeout=4.0)
            r.raise_for_status()
            snap = r.json()
        else:
            snap = json.loads(Path(src).read_text())
        frames, generated = from_snapshot(snap)
    except Exception:
        return None
    age_h = (pd.Timestamp.now(tz="UTC") - generated).total_seconds() / 3600
    if age_h > SNAPSHOT_MAX_AGE_H:
        return None
    return frames, f"snapshot ({age_h:.1f} h old)"


def refresh_weather(bundle: SiteBundle) -> str:
    """Refresh the weather cache for *all* sites with one request; fall back to the published snapshot.

    Returns a status for `bundle`'s site. Cloud hosts share an address and the free provider limits
    each address per day, so one batched call per half hour is what keeps this under the limit."""
    now = time.time()
    coords = site_coordinates() or {bundle.site_id: (bundle.lat, bundle.lon)}
    coords.setdefault(bundle.site_id, (bundle.lat, bundle.lon))
    if _wx_batch_fail and now - _wx_batch_fail[0] < WEATHER_RETRY_SECONDS:
        status = _wx_batch_fail[1]
    else:
        try:
            frames = fetch_many(coords, api_key=os.environ.get("OPEN_METEO_API_KEY", "").strip() or None)
            for sid, W in frames.items():
                _wx_cache[sid] = (now, W)
                _wx_status[sid] = "ok"
            _wx_batch_fail.clear()
            return "ok"
        except Exception as e:  # network, HTTP or schema problems must not fail a forecast
            detail = str(e)[:120]
            status = f"fetch_failed: {type(e).__name__}" + (f" ({detail})" if detail else "")
            _wx_batch_fail[:] = [now, status]
    snap = _snapshot_frames()
    if snap is not None and bundle.site_id in snap[0]:
        for sid, W in snap[0].items():
            _wx_cache[sid] = (now - WEATHER_CACHE_SECONDS + 600, W)  # re-check the live source in ten minutes
            _wx_status[sid] = snap[1]
        return snap[1]
    return status


def live_weather(bundle: SiteBundle) -> tuple[pd.DataFrame | None, str]:
    """Recent and forecast weather for the site, cached, with a status that says why it is absent.

    The status is returned in every live response (`weather_status`), so a silent fallback to the
    base model can be diagnosed from outside: "ok", "snapshot (N h old)", "disabled",
    "bundle_has_no_weather_models", "bundle_has_no_coordinates", "stale_cache" or
    "fetch_failed: <error type> (<reason>)"."""
    if os.environ.get("DOSENTINEL_WEATHER", "on") == "off":
        return None, "disabled"
    if not bundle.wx_quantile_models:
        return None, "bundle_has_no_weather_models"
    if bundle.lat is None or bundle.lon is None:
        return None, "bundle_has_no_coordinates"
    now = time.time()
    hit = _wx_cache.get(bundle.site_id)
    if hit is not None and now - hit[0] < WEATHER_CACHE_SECONDS:
        return hit[1], _wx_status.get(bundle.site_id, "ok")
    status = refresh_weather(bundle)
    hit = _wx_cache.get(bundle.site_id)
    if hit is not None and (status == "ok" or status.startswith("snapshot")):
        return hit[1], status
    if hit is not None and now - hit[0] < 6 * 3600:
        return hit[1], "stale_cache"
    return None, status


def foundation_model():
    """The pretrained Chronos-2 wrapper when DOSENTINEL_FOUNDATION=chronos2 and its extra is installed."""
    if "model" not in _foundation_state:
        _foundation_state["model"] = None
        if os.environ.get("DOSENTINEL_FOUNDATION", "") == "chronos2":
            try:
                import chronos  # noqa: F401

                from .foundation import Chronos2Blender
                _foundation_state["model"] = Chronos2Blender()
            except ImportError:
                pass
    return _foundation_state["model"]


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
        raise HTTPException(422, str(e)) from e


def log_path() -> Path | None:
    p = os.environ.get("DOSENTINEL_LOG")
    return Path(p) if p else None


def _respond(bundle: SiteBundle, frame: pd.DataFrame, cost_ratio: float, live: bool = False) -> dict:
    # weather is fetched only for live requests; a POST forecasts from exactly the series supplied
    weather, weather_status = live_weather(bundle) if live else (None, "not_requested")
    try:
        res = forecast(bundle, frame, cost_ratio, weather=weather,
                       foundation=foundation_model() if weather is not None else None)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    res["weather_status"] = weather_status
    res["input_health"] = input_health(frame)
    hv = frame["do"][frame.index.minute == 0].iloc[-72:]  # last three days, hourly, for charts
    res["history"] = {"start": hv.index[0].isoformat() if len(hv) else None, "step_hours": 1,
                      "do": [None if pd.isna(v) else round(float(v), 2) for v in hv.to_numpy()]}
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


class UpstreamError(Exception):
    """USGS could not provide the DO series in time (rate limit, outage or timeout)."""


def _fetch_live_frame(site_id: str) -> pd.DataFrame:
    """Pull the five series concurrently under a hard deadline; never block a request for minutes."""
    end = pd.Timestamp.now(tz="UTC").floor("15min")
    start = end - pd.Timedelta(days=HISTORY_DAYS)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    # short retries and a capped Retry-After: a web request must fail fast, not sleep for minutes
    client = UsgsClient(max_retries=2, backoff=1.0, timeout=15.0, max_retry_after=2.0)

    def one(code: str):
        return client.fetch_continuous(site_id, code, start.strftime(fmt), end.strftime(fmt))

    ex = ThreadPoolExecutor(max_workers=len(PARAM_COLS))
    futs = {ex.submit(one, c): c for c in PARAM_COLS}
    done, pending = wait(futs, timeout=FETCH_DEADLINE_S)
    ex.shutdown(wait=False, cancel_futures=True)
    raw, errors = {}, {}
    for f in done:
        code = futs[f]
        try:
            df = f.result()
            if not df.empty:
                raw[code] = df.drop_duplicates("time").set_index("time")["value"].sort_index()
        except Exception as e:  # upstream failure for this series
            errors[code] = str(e)
    if "00300" not in raw:
        timed_out = any(futs[p] == "00300" for p in pending)
        raise UpstreamError(errors.get("00300") or ("timed out" if timed_out else "no dissolved-oxygen readings returned"))
    try:
        return clean_frame_from_raw(raw, window_start=start)
    except ValueError as e:
        raise UpstreamError(str(e)) from e


@app.get("/forecast/{site_id}")
def forecast_live(site_id: str, cost_ratio: float = Query(10.0, gt=0, le=1000)) -> dict:
    bundle = get_bundle(site_id)
    now = time.time()
    hit = _cache.get(site_id)
    stale, reason = False, None
    if hit is None or now - hit[0] > CACHE_SECONDS:
        try:
            _cache[site_id] = (now, _fetch_live_frame(site_id))
            hit = _cache[site_id]
        except UpstreamError as e:
            if hit is None:
                raise HTTPException(503, f"USGS data is unavailable right now ({e}). Try again in a few minutes.",
                                    headers={"Retry-After": "120"}) from e
            stale, reason = True, str(e)  # serve the last good data, clearly marked
    res = _respond(bundle, hit[1], cost_ratio, live=True)
    res["stale"] = stale
    if stale:
        res["stale_reason"] = reason
    return res


@app.post("/forecast/{site_id}")
def forecast_from_data(site_id: str, payload: SeriesPayload) -> dict:
    bundle = get_bundle(site_id)
    return _respond(bundle, _frame_from_payload(payload.series), payload.cost_ratio)


_drift_reference: dict = {}


def drift_reference() -> dict:
    """models/drift_reference.json (built by scripts/build_drift_reference.py), loaded once."""
    if not _drift_reference:
        path = Path(os.environ.get("DOSENTINEL_DRIFT_REFERENCE", str(models_dir() / "drift_reference.json")))
        if path.exists():
            _drift_reference.update(json.loads(path.read_text()))
    return _drift_reference


@app.get("/drift/{site_id}")
def drift(site_id: str, window_days: int = Query(30, ge=7, le=35)) -> dict:
    """Input drift of the latest data against the training period (same calendar month, judged
    against the site's own year-to-year variability) and drift in the logged outputs."""
    get_bundle(site_id)
    ref = drift_reference().get(site_id)
    if ref is None:
        raise HTTPException(409, "no drift reference for this site; run scripts/build_drift_reference.py")
    hit = _cache.get(site_id)
    if hit is None:
        forecast_live(site_id)
        hit = _cache[site_id]
    out = {"site_id": site_id, "as_of": hit[1].index[-1].isoformat(),
           "input": input_drift(hit[1], ref["features"], window_days)}
    path = log_path()
    out["output"] = log_drift(read_log(path), site_id) if path is not None else {"status": "unknown",
                                                                                 "reason": "forecast logging is off"}
    return out


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
    ref = reference_maes(bundle.metrics)
    age = (pd.Timestamp.now(tz="UTC") - bundle.calibrated_through).days
    decision = retrain_decision(scored, ref, age, 1.0 - input_health(frame)["parameters"]["do"]["observed_7d"])
    by_h = {int(h): {"n": int(len(g)), "mae": float(g["abs_error"].mean()), "coverage": float(g["inside"].mean()),
                     "reference_mae": ref["gbm"].get(int(h))} for h, g in matured.groupby("horizon_h")}
    return {"site_id": site_id, "logged_forecasts": int(len(log)), "matured_forecasts": int(len(matured)),
            "by_horizon": by_h, "model_age_days": int(age), "retrain": decision}
