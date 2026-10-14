import numpy as np
import pandas as pd
import pytest

from dosentinel.features import base_features, horizon_features, train_origin_mask
from dosentinel.models import fit_predict_quantiles

N = 96 * 45


def _frame(seed=0):
    idx = pd.date_range("2026-01-01", periods=N, freq="15min", tz="UTC")
    rng = np.random.default_rng(seed)
    t = np.arange(N)
    return pd.DataFrame({
        "do": 8 + 2 * np.sin(2 * np.pi * t / 96) + rng.normal(0, 0.1, N),
        "temp": 15 + rng.normal(0, 0.2, N),
        "spcond": 300 + rng.normal(0, 5, N),
        "turb": np.abs(rng.normal(10, 3, N)),
        "q": np.abs(rng.normal(50, 10, N)),
    }, index=idx)


@pytest.mark.parametrize("h", [1, 24, 96])
def test_features_at_origin_do_not_depend_on_later_data(h):
    f = _frame()
    cut = f.index[96 * 30]
    g = f.copy()
    g.loc[g.index > cut] = 1e6
    a = horizon_features(base_features(f), f["do"], h)
    b = horizon_features(base_features(g), g["do"], h)
    pd.testing.assert_frame_equal(a[a.index <= cut], b[b.index <= cut])


def test_horizon_features_reject_out_of_range():
    f = _frame()
    with pytest.raises(ValueError):
        horizon_features(base_features(f), f["do"], 97)


def test_train_mask_excludes_origins_whose_target_reaches_test_window():
    idx = pd.date_range("2026-01-01", periods=10, freq="15min", tz="UTC")
    test_start = idx[6]
    m = train_origin_mask(idx, 2, test_start)  # target = origin + 30 min
    assert m.tolist() == [True] * 5 + [False] * 5  # origin idx[4] targets idx[6]: allowed; idx[5] targets idx[7]: not


def test_quantiles_are_ordered_and_roughly_calibrated():
    rng = np.random.default_rng(1)
    X = pd.DataFrame({"a": rng.normal(size=4000)})
    y = X["a"].to_numpy() + rng.normal(0, 1, 4000)
    Xtr, Xte, ytr, yte = X[:3000], X[3000:], y[:3000], y[3000:]
    p = fit_predict_quantiles(Xtr, ytr, Xte, params={"max_iter": 60})
    assert (p["q10"] <= p["q50"]).all() and (p["q50"] <= p["q90"]).all()
    cover = ((yte >= p["q10"].to_numpy()) & (yte <= p["q90"].to_numpy())).mean()
    assert 0.7 < cover < 0.9


def test_load_site_frame_treats_absent_covariate_as_missing(tmp_path):
    f = _frame()
    for code in ("00300", "00010", "00095"):  # no turbidity or discharge files
        col = {"00300": "do", "00010": "temp", "00095": "spcond"}[code]
        pd.DataFrame({"value": f[col], "out_of_range": False, "flatline": False, "spike": False}
                     ).to_parquet(tmp_path / f"S_{code}.parquet")
    from dosentinel.features import load_site_frame
    g = load_site_frame("S", str(tmp_path))
    assert list(g.columns) == ["do", "temp", "spcond", "turb", "q"]
    assert g["turb"].isna().all() and g["q"].isna().all() and g["do"].notna().all()
    X = base_features(g)  # must not raise with all-NaN covariates
    assert len(X) == len(g)
