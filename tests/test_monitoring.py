import numpy as np
import pandas as pd

from dosentinel.monitoring import RetrainRule, data_age_minutes, input_health, log_forecast, read_log, retrain_decision, score_log


def _report():
    t0 = pd.Timestamp("2026-06-01T00:00:00Z")
    return {"site_id": "S-1", "as_of": t0.isoformat(), "p_low_do_within_24h": 0.2, "action": "none",
            "forecasts": [{"horizon_h": h, "target_time": (t0 + pd.Timedelta(hours=h)).isoformat(),
                           "median_mgL": 7.0, "low_mgL": 6.0, "high_mgL": 8.0} for h in (1, 6, 24)]}


def test_data_age():
    assert data_age_minutes(pd.Timestamp("2026-01-01T00:00Z"), pd.Timestamp("2026-01-01T01:30Z")) == 90.0


def test_input_health_gap_and_completeness():
    idx = pd.date_range("2026-01-01", periods=96 * 8, freq="15min", tz="UTC")
    f = pd.DataFrame({"do": 8.0, "temp": 15.0, "q": np.nan}, index=idx)
    f.iloc[-40:, 0] = np.nan  # 10 h gap at the end
    h = input_health(f)
    assert h["do_current_gap_hours"] == 10.0 and h["do_gap_alert"]
    assert h["parameters"]["q"]["observed_24h"] == 0.0 and h["parameters"]["temp"]["observed_7d"] == 1.0


def test_log_roundtrip_and_scoring(tmp_path):
    p = tmp_path / "logs" / "f.jsonl"
    assert log_forecast(_report(), p, issued_at=pd.Timestamp("2026-06-01T00:05Z")) == 3
    log = read_log(p)
    assert len(log) == 3 and log["horizon_h"].tolist() == [1, 6, 24]
    obs = pd.Series({pd.Timestamp("2026-06-01T01:00Z"): 7.4, pd.Timestamp("2026-06-01T06:00Z"): 9.0})
    s = score_log(log, obs)
    assert s["abs_error"].round(2).tolist()[:2] == [0.4, 2.0]
    assert s["inside"].tolist()[:2] == [1.0, 0.0] and np.isnan(s["abs_error"].iloc[2])  # 24 h not matured


def test_retrain_rule_fires_only_with_enough_evidence():
    n = 300
    sc = pd.DataFrame({"horizon_h": 6, "abs_error": 0.8, "inside": 0.5}, index=range(n))
    r = retrain_decision(sc, {6: 0.5}, model_age_days=30, missing_do_7d=0.05)
    assert r["retrain"] and any("MAE at 6 h" in x for x in r["reasons"]) and any("coverage" in x for x in r["reasons"])
    few = sc.iloc[:50]
    assert not retrain_decision(few, {6: 0.5}, 30, 0.05)["retrain"]  # too few matured forecasts
    ok = pd.DataFrame({"horizon_h": 6, "abs_error": 0.5, "inside": 0.8}, index=range(n))
    assert not retrain_decision(ok, {6: 0.5}, 30, 0.05)["retrain"]
    assert retrain_decision(ok, {6: 0.5}, 400, 0.05, RetrainRule())["reasons"] == ["model is 400 days old"]


def test_reference_maes_only_lists_forecasters_with_numbers():
    from dosentinel.monitoring import reference_maes
    m = {"reference_mae_h": {1: 0.1, 24: 0.5}, "blend_reference_mae_h": {1: 0.08, 24: 0.4}}
    ref = reference_maes(m)
    assert set(ref) == {"gbm", "gbm_weather+chronos2"} and ref["gbm"] == {1: 0.1, 24: 0.5}
    assert reference_maes({}) == {}


def test_retrain_decision_compares_each_forecaster_with_its_own_reference():
    n = 300
    rows = []
    for name, err in (("gbm", 0.12), ("gbm_weather+chronos2", 0.20)):
        for _ in range(n):
            rows.append({"horizon_h": 1, "abs_error": err, "inside": 1.0, "forecast_model": name})
    scored = pd.DataFrame(rows)
    # the blend's own reference is 0.10, so 0.20 is 2x and flags; the GBM is at 0.12 vs 0.10 and does not
    d = retrain_decision(scored, {"gbm": {1: 0.10}, "gbm_weather+chronos2": {1: 0.10}}, 10, 0.0)
    assert d["retrain"] and len(d["reasons"]) == 1 and "gbm_weather+chronos2" in d["reasons"][0]
    # a forecaster without a reference of its own is compared with the base model's
    d2 = retrain_decision(scored, {"gbm": {1: 0.10}}, 10, 0.0)
    assert d2["retrain"] and any("gbm_weather+chronos2" in r for r in d2["reasons"])


def test_site_status_scores_the_log_against_observed_values():
    from types import SimpleNamespace

    from dosentinel.monitoring import site_status
    idx = pd.date_range("2026-06-01", periods=96 * 10, freq="15min", tz="UTC")
    do = pd.Series(7.0, index=idx)
    log = pd.DataFrame({
        "site_id": "S-1", "horizon_h": 1, "issued_at": idx[100], "as_of": idx[100],
        "target_time": idx[104:304], "median_mgL": 7.0, "low_mgL": 6.5, "high_mgL": 7.5,
        "p_low_do_within_24h": 0.0, "action": "none", "forecast_model": "gbm"})
    b = SimpleNamespace(site_id="S-1", calibrated_through=idx[0] - pd.Timedelta(days=30),
                        metrics={"reference_mae_h": {1: 0.1}})
    st = site_status(b, log, do, now=idx[-1])
    assert st["site_id"] == "S-1" and st["logged_forecasts"] == 200 and st["model_age_days"] == 39
    assert st["retrain"] is False and st["scored_forecasts"] == 200
