import numpy as np
import pandas as pd

from dosentinel.monitoring import (RetrainRule, data_age_minutes, input_health, log_forecast, read_log,
                                   retrain_decision, score_log)


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
