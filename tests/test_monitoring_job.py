import json

import numpy as np
import pandas as pd
import pytest

from dosentinel import drift
from dosentinel.monitoring_job import holdout_summary, merge_scored, render_markdown, run_cycle
from dosentinel.serving import train_bundle

FAST = {"max_iter": 20}


def _frame(n=96 * 140, seed=0, shift=0.0):
    idx = pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC")
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    do = 8.0 + 1.5 * np.sin(2 * np.pi * t / 96) + rng.normal(0, 0.1, n) - 3.5 * ((t % 960) > 880) + shift
    return pd.DataFrame({"do": np.round(do, 1), "temp": 15 + rng.normal(0, 0.2, n),
                         "spcond": 300 + rng.normal(0, 5, n), "turb": np.abs(rng.normal(8, 2, n)),
                         "q": np.abs(rng.normal(40, 5, n))}, index=idx)


@pytest.fixture(scope="module")
def setup():
    full = _frame()
    cut = 96 * 100
    b = train_bundle(full.iloc[:cut], "S-1", role="test", cal_days=40, stride=4, params=FAST)
    return full, cut, b


def test_issue_then_score_then_never_rescore(setup, tmp_path):
    full, cut, b = setup
    t0 = full.index[cut - 1]
    rep = run_cycle({"S-1": b}, lambda s: full.iloc[:cut], tmp_path, now=t0)
    assert rep["sites"]["S-1"]["issued"] == 5 and rep["forward_holdout_rows"] == 0
    assert (tmp_path / "forecasts.jsonl").exists() and (tmp_path / "report.md").exists()

    t1 = t0 + pd.Timedelta(days=2)  # all five horizons (<= 24 h) have matured
    rep = run_cycle({"S-1": b}, lambda s: full.iloc[:cut + 96 * 2], tmp_path, now=t1, issue=False)
    assert rep["forward_holdout_rows"] == 5
    again = run_cycle({"S-1": b}, lambda s: full.iloc[:cut + 96 * 2], tmp_path, now=t1, issue=False)
    assert again["forward_holdout_rows"] == 5  # a forecast is scored exactly once
    scored = pd.read_csv(tmp_path / "scored.csv")
    assert set(scored["horizon_h"]) == {1, 3, 6, 12, 24} and scored["abs_error"].notna().all()
    assert {r["horizon_h"] for r in again["forward_holdout"]} == {1, 3, 6, 12, 24}


def test_a_failing_site_is_recorded_and_the_others_still_run(setup, tmp_path):
    full, cut, b = setup

    def fetch(sid):
        if sid == "S-bad":
            raise TimeoutError("USGS timed out")
        return full.iloc[:cut]

    rep = run_cycle({"S-1": b, "S-bad": b}, fetch, tmp_path, now=full.index[cut - 1])
    assert rep["sites_failed"] == ["S-bad"] and rep["sites"]["S-bad"]["error"].startswith("TimeoutError")
    assert rep["sites"]["S-1"]["issued"] == 5
    assert "error" in render_markdown(rep)


def test_weather_status_and_model_are_logged(setup, tmp_path):
    full, cut, b = setup
    rep = run_cycle({"S-1": b}, lambda s: full.iloc[:cut], tmp_path, now=full.index[cut - 1],
                    weather=lambda bundle: (None, "fetch_failed: HTTPError (HTTP 429)"))
    s = rep["sites"]["S-1"]
    assert s["forecast_model"] == "gbm" and s["weather_status"].startswith("fetch_failed")
    log = pd.read_json(tmp_path / "forecasts.jsonl", lines=True)
    assert set(log["forecast_model"]) == {"gbm"}


def test_retrain_is_flagged_when_live_error_is_far_above_reference(setup, tmp_path):
    full, cut, b = setup
    shifted = _frame(shift=-4.0)  # the world changes after the model was calibrated
    t0 = full.index[cut - 1]
    for k in range(45):  # 45 issues x 5 horizons = 225 forecasts, above the rule's 200 minimum
        run_cycle({"S-1": b}, lambda s, k=k: shifted.iloc[:cut + 4 * k], tmp_path, now=t0 + pd.Timedelta(minutes=15 * 4 * k))
    rep = run_cycle({"S-1": b}, lambda s: shifted.iloc[:cut + 96 * 3], tmp_path, now=t0 + pd.Timedelta(days=3), issue=False)
    rt = rep["sites"]["S-1"]["retrain"]
    assert rt["retrain"] and rep["retrain_recommended"] == ["S-1"] and rt["scored_forecasts"] >= 200


def test_input_drift_is_reported_against_the_reference(setup, tmp_path):
    full, cut, b = setup
    years = []
    for k, y in enumerate((2023, 2024, 2025)):  # a reference needs several years to know normal variability
        f = _frame(seed=10 + k).iloc[:cut]
        f.index = f.index - pd.DateOffset(years=2026 - y)
        years.append(f)
    ref = {"S-1": {"features": drift.build_reference(pd.concat(years))}}
    healthy = run_cycle({"S-1": b}, lambda s: full.iloc[:cut], tmp_path / "a", reference=ref, now=full.index[cut - 1])
    assert healthy["sites"]["S-1"]["input_drift"]["worst"] in ("stable", "moderate", "elevated", "unknown")
    bad = full.iloc[:cut].copy()
    bad.iloc[-96 * 30:, bad.columns.get_loc("temp")] += 20.0
    bad.iloc[-96 * 30:, bad.columns.get_loc("spcond")] *= 4.0
    rep = run_cycle({"S-1": b}, lambda s: bad, tmp_path / "b", reference=ref, now=full.index[cut - 1])
    d = rep["sites"]["S-1"]["input_drift"]
    assert d["features"]["temp"] == "major" and d["alert"] is True and rep["input_drift_alerts"] == ["S-1"]


def test_merge_scored_keeps_first_and_ignores_unmatured():
    a = pd.DataFrame({"site_id": "S", "issued_at": pd.to_datetime(["2026-01-01"], utc=True), "horizon_h": 1,
                      "abs_error": 0.2, "inside": 1.0, "forecast_model": "gbm"})
    b = pd.DataFrame({"site_id": ["S", "S"], "issued_at": pd.to_datetime(["2026-01-01", "2026-01-02"], utc=True),
                      "horizon_h": [1, 1], "abs_error": [9.9, np.nan], "inside": [0.0, np.nan], "forecast_model": "gbm"})
    m = merge_scored(a, b)
    assert len(m) == 1 and m["abs_error"].iloc[0] == 0.2
    assert holdout_summary(m)[0]["n"] == 1 and holdout_summary(pd.DataFrame()) == []
    json.dumps(holdout_summary(m))
