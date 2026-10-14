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
