import numpy as np
import pandas as pd

from dosentinel import drift


def _years(seed=0, shift=None, years=(2021, 2022, 2023, 2024), month=6, days=30):
    """Frames of one calendar month per year with modest year-to-year wobble."""
    rng = np.random.default_rng(seed)
    parts = []
    for k, y in enumerate(years):
        idx = pd.date_range(f"{y}-{month:02d}-01", periods=days * 96, freq="15min", tz="UTC")
        n = len(idx)
        wobble = 0.3 * (k - 1.5)
        do = 8 + wobble + 1.2 * np.sin(2 * np.pi * np.arange(n) / 96) + rng.normal(0, 0.3, n)
        parts.append(pd.DataFrame({"do": do, "temp": 18 + wobble + rng.normal(0, 0.5, n),
                                   "spcond": 300 + 10 * wobble + rng.normal(0, 8, n),
                                   "turb": np.abs(rng.normal(6, 2, n)), "q": np.abs(rng.normal(40, 6, n))}, index=idx))
    return pd.concat(parts)


def test_psi_zero_for_the_same_sample_and_large_for_a_shift():
    x = np.random.default_rng(1).normal(0, 1, 5000)
    assert drift.psi(x, x) < 1e-3
    assert drift.psi(x, x + 2.0) > 1.0
    assert drift.psi(x, np.random.default_rng(2).normal(0, 1, 5000)) < 0.05


def test_psi_is_none_when_data_is_too_thin_or_constant():
    assert drift.psi(np.ones(1000), np.ones(1000)) is None  # constant reference: nothing to drift from
    assert drift.psi(np.arange(1000.0), np.arange(50.0)) is None  # too few current points
    assert drift.psi(np.arange(100.0), np.arange(1000.0)) is None  # too few reference points
    assert drift.psi(np.r_[np.arange(1000.0), np.full(10, np.nan)], np.arange(1000.0)) is not None  # NaNs ignored


def test_status_vs_null_tiers():
    null = [0.1, 0.2, 0.3, 0.4]
    assert drift.status_vs_null(0.2, null) == "stable"
    assert drift.status_vs_null(0.4, null) == "moderate"
    assert drift.status_vs_null(0.7, null) == "elevated"
    assert drift.status_vs_null(0.9, null) == "major"
    assert drift.status_vs_null(None, null) == "unknown" and drift.status_vs_null(0.5, []) == "unknown"


def test_reference_has_leave_one_year_out_nulls_per_month():
    ref = drift.build_reference(_years())
    entry = ref["do"][6]
    assert len(entry["null"]) == 4 and entry["sketch"]["n"] > 5000
    assert set(ref) == set(drift.FEATURES)


def test_a_normal_year_is_stable_and_an_injected_shift_alerts():
    ref = drift.build_reference(_years(seed=0))
    ok = _years(seed=5, years=(2025,))
    d = drift.input_drift(ok, ref)
    assert d["reference_month"] == 6 and d["alert"] is False and d["worst"] in ("stable", "moderate", "elevated")
    bad = ok.copy()
    bad["do"] += 3.0
    bad["temp"] += 8.0
    bad["spcond"] *= 3.0
    dd = drift.input_drift(bad, ref)
    assert dd["do"]["status"] == "major" and dd["temp"]["status"] == "major" and dd["alert"] is True and dd["n_major"] >= 3


def test_seasonal_change_is_not_drift():
    # winter windows are compared with winter, so a cold month alone must not be flagged
    ref_frames = []
    for m, offset in ((6, 0.0), (12, -9.0)):
        f = _years(month=m)
        f["temp"] = f["temp"] + offset
        ref_frames.append(f)
    ref = drift.build_reference(pd.concat(ref_frames))
    winter = _years(seed=9, years=(2025,), month=12)
    winter["temp"] = winter["temp"] - 9.0
    d = drift.input_drift(winter, ref)
    assert d["reference_month"] == 12 and d["temp"]["status"] in ("stable", "moderate", "elevated")


def test_input_drift_handles_a_missing_reference_month():
    d = drift.input_drift(_years(years=(2025,), month=3), drift.build_reference(_years()))
    assert d["worst"] == "unknown" and d["alert"] is False


def test_log_drift_needs_two_windows_and_detects_a_regime_change():
    n = 400
    t = pd.date_range("2026-09-01", periods=n, freq="1h", tz="UTC")
    rng = np.random.default_rng(0)

    def rows(vals, p):
        return pd.DataFrame({"site_id": "S-1", "issued_at": t, "horizon_h": 24, "median_mgL": vals,
                             "p_low_do_within_24h": p})

    stable = rows(rng.normal(7, 0.5, n), rng.uniform(0, 0.1, n))
    assert drift.log_drift(stable, "S-1", window_days=7)["status"] in ("stable", "moderate")
    shifted = pd.concat([stable.iloc[:200], rows(rng.normal(4, 0.5, n), rng.uniform(0.4, 0.9, n)).iloc[200:]])
    assert drift.log_drift(shifted, "S-1", window_days=7)["status"] == "major"
    assert drift.log_drift(stable.iloc[:10], "S-1")["status"] == "unknown"
    assert drift.log_drift(stable.iloc[:0], "S-1")["status"] == "unknown"
