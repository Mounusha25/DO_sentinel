import joblib
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from dosentinel import api
from dosentinel.serving import clean_frame_from_raw, forecast, train_bundle

FAST = {"max_iter": 20}
N = 96 * 140


def _frame(seed=0, n=N, dip=True):
    idx = pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC")
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    do = 8.0 + 1.5 * np.sin(2 * np.pi * t / 96) + rng.normal(0, 0.1, n)
    if dip:  # occasional low-DO episodes, so both classes exist in every window
        do -= 3.5 * ((t % 960) > 880)
    return pd.DataFrame({"do": np.round(do, 1), "temp": 15 + rng.normal(0, 0.2, n),
                         "spcond": 300 + rng.normal(0, 5, n), "turb": np.abs(rng.normal(8, 2, n)),
                         "q": np.abs(rng.normal(40, 5, n))}, index=idx)


@pytest.fixture(scope="module")
def bundle():
    return train_bundle(_frame(), "S-1", role="test", cal_days=40, stride=4, params=FAST)


def test_bundle_has_all_horizons_and_margins(bundle):
    assert set(bundle.quantile_models) == {4, 12, 24, 48, 96}
    assert all(np.isfinite(m) for m in bundle.cqr_margin.values())
    assert all(np.isfinite(v) for v in bundle.detector_thresholds.values())
    assert bundle.cal_events > 0 and bundle.platt is not None  # synthetic dips give calibration events
    assert set(bundle.detector_thresholds) == {"run_length", "persistence", "interval_1h"}


def test_forecast_shape_and_ordering(bundle):
    f = _frame()
    out = forecast(bundle, f.iloc[-96 * 30:])
    assert [x["horizon_h"] for x in out["forecasts"]] == [1, 3, 6, 12, 24]
    for x in out["forecasts"]:
        assert x["low_mgL"] <= x["median_mgL"] <= x["high_mgL"] + 1e-9
    assert 0.0 <= out["p_low_do_within_24h"] <= 1.0
    assert out["action"] in ("none", "warning", "maintenance")
    assert set(out["fault_flags"]) >= {"rules", "flatline", "jump_persistence", "jump_interval_1h", "any"}


def test_forecast_uses_only_data_up_to_as_of(bundle):
    f = _frame()
    cut = 96 * 100
    a = forecast(bundle, f.iloc[:cut])
    g = f.copy()
    g.iloc[cut:] = 1e4  # garbage after the as-of time
    b = forecast(bundle, g.iloc[:cut])
    assert a["forecasts"] == b["forecasts"] and a["p_low_do_within_24h"] == b["p_low_do_within_24h"]


def test_missing_latest_value_means_maintenance(bundle):
    f = _frame().iloc[-96 * 30:].copy()
    f.iloc[-1, f.columns.get_loc("do")] = np.nan
    out = forecast(bundle, f)
    assert out["action"] == "maintenance" and out["forecasts"] == []


def test_stuck_sensor_is_flagged_and_suppresses_warning(bundle):
    f = _frame().iloc[-96 * 30:].copy()
    f.iloc[-100:, f.columns.get_loc("do")] = 3.0  # >24 h identical readings
    out = forecast(bundle, f)
    assert out["fault_flags"]["flatline"] and out["action"] == "maintenance"


def test_short_history_is_rejected(bundle):
    with pytest.raises(ValueError):
        forecast(bundle, _frame().iloc[:96 * 5])


def test_clean_frame_masks_out_of_range_and_fills_missing_covariates():
    f = _frame().iloc[:96 * 3]
    do = f["do"].copy()
    do.iloc[10] = 99.0
    g = clean_frame_from_raw({"00300": do})
    assert np.isnan(g["do"].iloc[10]) and g["q"].isna().all() and g["temp"].isna().all()


def test_api_roundtrip(bundle, tmp_path, monkeypatch):
    joblib.dump(bundle, tmp_path / "S-1.joblib")
    monkeypatch.setenv("DOSENTINEL_MODELS", str(tmp_path))
    monkeypatch.setenv("DOSENTINEL_CONFIG", str(tmp_path / "none.yaml"))
    api._bundles.clear()
    c = TestClient(api.app)
    assert c.get("/health").json()["bundles_available"] == ["S-1"]
    assert c.get("/sites").json()[0]["site_id"] == "S-1"
    f = _frame().iloc[-96 * 30:]
    series = {"00300": [{"time": t.isoformat(), "value": None if np.isnan(x) else float(x)} for t, x in f["do"].items()],
              "00010": [{"time": t.isoformat(), "value": float(x)} for t, x in f["temp"].items()]}
    r = c.post("/forecast/S-1", json={"series": series, "cost_ratio": 5})
    assert r.status_code == 200
    body = r.json()
    assert body["alert_cutoff"] == pytest.approx(1 / 6) and len(body["forecasts"]) == 5
    assert body["history"]["step_hours"] == 1 and 1 <= len(body["history"]["do"]) <= 72
    assert body["caveats"] and body["model"]["role"] == "test"
    assert c.post("/forecast/NOPE", json={"series": series}).status_code == 404
    assert c.post("/forecast/S-1", json={"series": {"99999": []}}).status_code == 422


def test_bundle_stores_reference_mae(bundle):
    ref = bundle.metrics["reference_mae_h"]
    assert set(ref) == {1, 3, 6, 12, 24} and all(np.isfinite(v) and v >= 0 for v in ref.values())


def test_api_logs_and_monitors(bundle, tmp_path, monkeypatch):
    joblib.dump(bundle, tmp_path / "S-1.joblib")
    monkeypatch.setenv("DOSENTINEL_MODELS", str(tmp_path))
    monkeypatch.setenv("DOSENTINEL_LOG", str(tmp_path / "log.jsonl"))
    api._bundles.clear()
    api._cache.clear()
    f = _frame().iloc[-96 * 30:]
    frame = f  # cleaned frame stands in for a live pull
    api._cache["S-1"] = (__import__("time").time(), frame)
    c = TestClient(api.app)
    r = c.get("/forecast/S-1")
    assert r.status_code == 200
    body = r.json()
    assert body["data_age_minutes"] is not None and "input_health" in body
    assert (tmp_path / "log.jsonl").exists()
    m = c.get("/monitor/S-1").json()
    assert m["logged_forecasts"] >= 5 and "retrain" in m and m["model_age_days"] >= 0


def test_api_rejects_oversized_payload_and_sets_cors(bundle, tmp_path, monkeypatch):
    joblib.dump(bundle, tmp_path / "S-1.joblib")
    monkeypatch.setenv("DOSENTINEL_MODELS", str(tmp_path))
    api._bundles.clear()
    c = TestClient(api.app)
    big = {"00300": [{"time": f"2026-01-01T00:{i % 60:02d}:00Z", "value": 7.0} for i in range(api.MAX_OBSERVATIONS + 5)]}
    assert c.post("/forecast/S-1", json={"series": big}).status_code == 422
    r = c.options("/forecast/S-1", headers={"Origin": "https://mounusha25.github.io",
                                              "Access-Control-Request-Method": "GET"})
    assert r.headers.get("access-control-allow-origin") == "https://mounusha25.github.io"
    r2 = c.options("/forecast/S-1", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in r2.headers


def test_window_start_pads_a_recently_resumed_sensor_instead_of_rejecting_it(bundle):
    f = _frame().iloc[-96 * 30:]
    recent = {"00300": f["do"].iloc[-96 * 5:], "00010": f["temp"].iloc[-96 * 5:]}  # only 5 days reported
    narrow = clean_frame_from_raw(recent)
    with pytest.raises(ValueError):
        forecast(bundle, narrow)  # 5 days of history is too short
    padded = clean_frame_from_raw(recent, window_start=f.index[0])
    assert padded.index[0] <= f.index[0] + pd.Timedelta("15min") and padded["do"].iloc[: 96 * 20].isna().all()
    out = forecast(bundle, padded)
    assert out["observed_fraction_last_24h"] > 0.9 and len(out["forecasts"]) == 5


def test_window_start_does_not_pad_the_end():
    idx = pd.date_range("2026-01-01", periods=96 * 3, freq="15min", tz="UTC")
    s = pd.Series(7.0 + 0.1 * (np.arange(len(idx)) % 7), index=idx)  # varies, so no flatline mask
    g = clean_frame_from_raw({"00300": s}, window_start=idx[0] - pd.Timedelta(days=10))
    assert g.index[-1] == idx[-1] and g["do"].iloc[-1] == pytest.approx(s.iloc[-1])
    assert g.index[0] == idx[0] - pd.Timedelta(days=10)


def test_live_endpoint_fails_fast_then_serves_stale(bundle, tmp_path, monkeypatch):
    joblib.dump(bundle, tmp_path / "S-1.joblib")
    monkeypatch.setenv("DOSENTINEL_MODELS", str(tmp_path))
    api._bundles.clear()
    api._cache.clear()

    def down(site_id):
        raise api.UpstreamError("HTTP 429")

    monkeypatch.setattr(api, "_fetch_live_frame", down)
    c = TestClient(api.app)
    r = c.get("/forecast/S-1")
    assert r.status_code == 503 and "Retry-After" in r.headers and "unavailable" in r.json()["detail"]
    # a previously fetched frame is served when USGS is down, and marked stale
    import time as _t
    api._cache["S-1"] = (_t.time() - api.CACHE_SECONDS - 5, _frame().iloc[-96 * 30:])
    r2 = c.get("/forecast/S-1")
    assert r2.status_code == 200 and r2.json()["stale"] is True and r2.json()["stale_reason"] == "HTTP 429"


def test_fetch_deadline_does_not_block_on_a_hanging_series(monkeypatch):
    import threading
    gate = threading.Event()

    class Hang:
        def __init__(self, *a, **k): pass
        def fetch_continuous(self, *a, **k):
            gate.wait(5)  # simulates an upstream that never answers in time
            return pd.DataFrame()

    monkeypatch.setattr(api, "UsgsClient", Hang)
    monkeypatch.setattr(api, "FETCH_DEADLINE_S", 0.3)
    t0 = __import__("time").time()
    with pytest.raises(api.UpstreamError):
        api._fetch_live_frame("S-1")
    assert __import__("time").time() - t0 < 2.0
    gate.set()


# ---- weather-aware models and the conformalized blend ----------------------------------------
def _weather(frame, seed=1):
    idx = pd.date_range(frame.index[0].floor("h") - pd.Timedelta(days=9), frame.index[-1].ceil("h") + pd.Timedelta(days=3),
                        freq="1h", tz="UTC")
    rng = np.random.default_rng(seed)
    t = np.arange(len(idx))
    air = 12 + 6 * np.sin(2 * np.pi * t / 24) + rng.normal(0, 0.3, len(idx))
    sun = np.clip(600 * np.sin(2 * np.pi * (t - 6) / 24), 0, None)
    rain = np.abs(rng.normal(0, 0.2, len(idx)))
    cols = {}
    for p in ("obs", "fc1"):
        cols.update({f"{p}_temperature_2m": air, f"{p}_shortwave_radiation": sun, f"{p}_precipitation": rain})
    return pd.DataFrame(cols, index=idx)


def _chronos_cal(frame, cal_days=40, noise=0.4, seed=2):
    """Stand-in for cached Chronos-2 quantiles: the truth at each horizon plus noise."""
    rng = np.random.default_rng(seed)
    v = frame["do"]
    origins = v.index[(v.index.minute == 0) & (v.index.hour % 4 == 0) & (v.index > v.index[-1] - pd.Timedelta(days=cal_days))]
    cols = {}
    for hh in (1, 3, 6, 12, 24):
        y = v.shift(-hh * 4).reindex(origins).to_numpy() + rng.normal(0, noise, len(origins))
        for k, off in (("q10", -0.5), ("q50", 0.0), ("q90", 0.5)):
            cols[f"wx_{k}_{hh}"] = y + off
    return pd.DataFrame(cols, index=origins)


class _StubFoundation:
    def __init__(self, bias=0.0):
        self.bias, self.calls = bias, 0

    def predict(self, frame, W):
        self.calls += 1
        base = float(frame["do"].iloc[-1])
        return {h: (base - 0.5 + self.bias, base + self.bias, base + 0.5 + self.bias) for h in (1, 3, 6, 12, 24)}


@pytest.fixture(scope="module")
def wx_bundle():
    f = _frame()
    return train_bundle(f, "S-1", role="test", cal_days=40, stride=4, params=FAST,
                        weather=_weather(f), chronos_cal=_chronos_cal(f))


def test_weather_bundle_has_models_margins_and_blend_margins(wx_bundle):
    assert set(wx_bundle.wx_quantile_models) == {4, 12, 24, 48, 96}
    assert set(wx_bundle.blend_margin) == {4, 12, 24, 48, 96}
    assert all(np.isfinite(m) for m in wx_bundle.wx_cqr_margin.values())
    assert "wx_reference_mae_h" in wx_bundle.metrics and "blend_reference_mae_h" in wx_bundle.metrics


def test_bundle_without_weather_is_unchanged(bundle):
    assert bundle.wx_quantile_models == {} and bundle.blend_margin == {}


def test_forecast_uses_weather_model_then_blend(wx_bundle):
    f = _frame().iloc[-96 * 30:]
    W = _weather(_frame())
    base = forecast(wx_bundle, f)
    assert base["forecast_model"] == "gbm"
    wx = forecast(wx_bundle, f, weather=W)
    assert wx["forecast_model"] == "gbm_weather" and len(wx["forecasts"]) == 5
    stub = _StubFoundation()
    bl = forecast(wx_bundle, f, weather=W, foundation=stub)
    assert bl["forecast_model"] == "gbm_weather+chronos2" and stub.calls == 1
    for out in (wx, bl):
        assert out["forecast_issued_from"] == f.index[-1].floor("h").isoformat()
        for x in out["forecasts"]:
            assert 0 <= x["low_mgL"] <= x["median_mgL"] <= x["high_mgL"]
    # the probability, fault flags and action do not depend on which forecaster answered
    assert bl["p_low_do_within_24h"] == base["p_low_do_within_24h"] and bl["action"] == base["action"]


def test_foundation_or_weather_failure_falls_back_instead_of_failing(wx_bundle):
    f = _frame().iloc[-96 * 30:]
    W = _weather(_frame())

    class Boom:
        def predict(self, frame, W):
            raise RuntimeError("out of memory")

    out = forecast(wx_bundle, f, weather=W, foundation=Boom())
    assert out["forecast_model"] == "gbm" and "RuntimeError" in out["forecast_model_note"] and len(out["forecasts"]) == 5
    # weather with no usable columns for the origin hour: also a fallback
    broken = W.drop(columns=["fc1_precipitation"])
    assert forecast(wx_bundle, f, weather=broken)["forecast_model"] == "gbm"
    # no foundation output (e.g. too many gaps in the covariates) keeps the weather model
    class Nothing:
        def predict(self, frame, W):
            return None
    assert forecast(wx_bundle, f, weather=W, foundation=Nothing())["forecast_model"] == "gbm_weather"


def test_blend_margin_is_not_the_members_margin(wx_bundle):
    # the stub foundation is nearly exact, so the blend is tighter than the weather GBM alone
    assert wx_bundle.blend_margin[24] != wx_bundle.wx_cqr_margin[24]


def test_weather_forecast_ignores_values_after_as_of(wx_bundle):
    f = _frame()
    W = _weather(f)
    cut = 96 * 100
    a = forecast(wx_bundle, f.iloc[:cut], weather=W)
    g = f.copy()
    g.iloc[cut:] = 1e4
    b = forecast(wx_bundle, g.iloc[:cut], weather=W)
    assert a["forecasts"] == b["forecasts"]


def test_alert_rule_reads_the_forecast_interval_even_with_a_constant_classifier():
    # no low-DO events before the calibration window: the classifier is a constant and its
    # probability can never alert; the interval-based rule must still work
    n = 96 * 140
    f = _frame(dip=False)
    t = np.arange(n)
    f["do"] = np.round(8.0 + 1.0 * np.sin(2 * np.pi * t / 96), 1)
    f.iloc[-96 * 10:, f.columns.get_loc("do")] -= 3.2  # low DO only inside the final (calibration) window
    b = train_bundle(f.iloc[:-96 * 8], "S-2", cal_days=40, stride=4, params=FAST)
    assert isinstance(b.clf, float)
    low = f.iloc[-96 * 30:].copy()  # ends inside the low-DO stretch
    ok = f.iloc[-96 * 60:-96 * 30].copy()  # a normal stretch (about 8 mg/L)
    a, c = forecast(b, low), forecast(b, ok)
    assert a["alert_rule"] == "forecast_interval" and a["probability_calibrated"] is False
    assert not a["fault_flags"]["any"] and not c["fault_flags"]["any"]
    assert a["action"] == "warning" and "forecast interval" in a["reason"]
    assert a["p_low_do_forecast_interval"] > a["alert_cutoff"]
    assert c["action"] == "none" and c["p_low_do_forecast_interval"] < c["alert_cutoff"]


def test_trained_classifier_keeps_the_probability_rule(bundle):
    out = forecast(bundle, _frame().iloc[-96 * 30:])
    assert out["alert_rule"] == "probability" and out["probability_calibrated"] is True
    assert 0.0 <= out["p_low_do_forecast_interval"] <= 1.0  # reported for every site, used only as the fallback


def test_cost_ratio_moves_the_cutoff_not_the_forecast(bundle):
    f = _frame().iloc[-96 * 30:]
    lo = forecast(bundle, f, cost_ratio=1.0)
    hi = forecast(bundle, f, cost_ratio=1000.0)
    assert hi["alert_cutoff"] < lo["alert_cutoff"]
    assert lo["p_low_do_forecast_interval"] == hi["p_low_do_forecast_interval"]
    assert lo["p_low_do_within_24h"] == hi["p_low_do_within_24h"]


# ---- access control -------------------------------------------------------------------------
def _client(bundle, tmp_path, monkeypatch, **env):
    joblib.dump(bundle, tmp_path / "S-1.joblib")
    monkeypatch.setenv("DOSENTINEL_MODELS", str(tmp_path))
    for k in ("DOSENTINEL_API_KEYS", "DOSENTINEL_RATE_LIMIT"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    api._bundles.clear()
    api._hits.clear()
    return TestClient(api.app)


def test_api_is_open_without_keys(bundle, tmp_path, monkeypatch):
    c = _client(bundle, tmp_path, monkeypatch)
    assert c.get("/sites").status_code == 200


def test_api_key_required_when_configured_but_health_stays_open(bundle, tmp_path, monkeypatch):
    c = _client(bundle, tmp_path, monkeypatch, DOSENTINEL_API_KEYS="alpha, beta")
    assert c.get("/health").status_code == 200
    assert c.get("/sites").status_code == 401
    assert c.get("/sites", headers={"X-API-Key": "nope"}).status_code == 401
    assert c.get("/sites", headers={"X-API-Key": "beta"}).status_code == 200
    # a rejected request still carries CORS headers, so a browser can show the real error
    r = c.get("/sites", headers={"Origin": "https://mounusha25.github.io"})
    assert r.status_code == 401 and r.headers.get("access-control-allow-origin") == "https://mounusha25.github.io"


def test_rate_limit_returns_429_with_retry_after(bundle, tmp_path, monkeypatch):
    c = _client(bundle, tmp_path, monkeypatch, DOSENTINEL_RATE_LIMIT="3")
    codes = [c.get("/sites").status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]
    r = c.get("/sites")
    assert int(r.headers["Retry-After"]) >= 1
    assert c.get("/health").status_code == 200  # health checks are never limited


def test_rate_limit_is_per_client_and_window_expires():
    api._hits.clear()
    assert api.rate_check("a", 2, now=0.0) is None and api.rate_check("a", 2, now=1.0) is None
    assert api.rate_check("a", 2, now=2.0) is not None
    assert api.rate_check("b", 2, now=2.0) is None  # another client is unaffected
    assert api.rate_check("a", 2, now=61.5) is None  # the window has passed
    assert api.rate_check("a", 0, now=0.0) is None  # 0 disables


# ---- live weather status is never silent -------------------------------------------------------
def _live_client(b, tmp_path, monkeypatch):
    joblib.dump(b, tmp_path / "S-1.joblib")
    monkeypatch.setenv("DOSENTINEL_MODELS", str(tmp_path))
    monkeypatch.delenv("DOSENTINEL_API_KEYS", raising=False)
    monkeypatch.delenv("DOSENTINEL_WEATHER", raising=False)
    monkeypatch.setenv("DOSENTINEL_RATE_LIMIT", "0")
    api._bundles.clear()
    api._cache.clear()
    api._wx_cache.clear()
    api._wx_fail.clear()
    api._cache["S-1"] = (__import__("time").time(), _frame().iloc[-96 * 30:])
    return TestClient(api.app)


def test_live_response_says_why_weather_was_not_used(bundle, tmp_path, monkeypatch):
    c = _live_client(bundle, tmp_path, monkeypatch)  # a bundle without weather models
    body = c.get("/forecast/S-1").json()
    assert body["forecast_model"] == "gbm" and body["weather_status"] == "bundle_has_no_weather_models"
    monkeypatch.setenv("DOSENTINEL_WEATHER", "off")
    api._bundles.clear()
    assert c.get("/forecast/S-1").json()["weather_status"] == "disabled"


def test_live_weather_failure_and_success_are_reported(wx_bundle, tmp_path, monkeypatch):
    wx_bundle.lat, wx_bundle.lon = 38.8, -77.2
    c = _live_client(wx_bundle, tmp_path, monkeypatch)

    def boom(lat, lon, **k):
        raise TimeoutError("open-meteo")

    monkeypatch.setattr(api, "fetch_live_weather", boom)
    body = c.get("/forecast/S-1").json()
    assert body["forecast_model"] == "gbm" and body["weather_status"].startswith("fetch_failed: TimeoutError")
    assert len(body["forecasts"]) == 5  # a weather outage never fails the request
    # after a failure the provider is not called again for a while, so a bad day costs no latency
    calls = []

    def count(lat, lon, **k):
        calls.append(1)
        raise TimeoutError("again")

    monkeypatch.setattr(api, "fetch_live_weather", count)
    api._cache["S-1"] = (__import__("time").time(), _frame().iloc[-96 * 30:])
    assert c.get("/forecast/S-1").json()["weather_status"].startswith("fetch_failed")
    assert calls == []

    W = _weather(_frame())
    monkeypatch.setattr(api, "fetch_live_weather", lambda lat, lon, **k: W)
    api._wx_cache.clear()
    api._wx_fail.clear()
    api._cache["S-1"] = (__import__("time").time(), _frame().iloc[-96 * 30:])
    body = c.get("/forecast/S-1").json()
    assert body["weather_status"] == "ok" and body["forecast_model"] == "gbm_weather"
    assert body["forecast_issued_from"]


def test_forecast_notes_when_weather_models_are_absent(bundle):
    out = forecast(bundle, _frame().iloc[-96 * 30:], weather=_weather(_frame()))
    assert out["forecast_model"] == "gbm" and "weather model not used" in out["forecast_model_note"]


def test_drift_endpoint_reports_input_and_output_drift(bundle, tmp_path, monkeypatch):
    from dosentinel import drift as drift_mod
    years = []
    for k, y in enumerate((2023, 2024, 2025)):
        f = _frame(seed=20 + k)
        f.index = f.index - pd.DateOffset(years=2026 - y)
        years.append(f)
    ref = {"S-1": {"trained_through": "2025-12-31", "rows": 0, "features": drift_mod.build_reference(pd.concat(years))}}
    (tmp_path / "ref.json").write_text(__import__("json").dumps(ref))
    monkeypatch.setenv("DOSENTINEL_DRIFT_REFERENCE", str(tmp_path / "ref.json"))
    api._drift_reference.clear()
    c = _live_client(bundle, tmp_path, monkeypatch)
    r = c.get("/drift/S-1")
    assert r.status_code == 200
    body = r.json()
    assert set(body["input"]) >= {"do", "temp", "worst", "alert", "n_major"} and body["output"]["status"] == "unknown"
    assert c.get("/drift/NOPE").status_code == 404
    api._drift_reference.clear()
    api._drift_reference["other"] = {}
    assert c.get("/drift/S-1").status_code == 409  # no reference for this site
    api._drift_reference.clear()
