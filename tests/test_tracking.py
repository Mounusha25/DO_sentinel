import builtins
import math

import pytest

from dosentinel import tracking


def test_clean_metrics_drops_non_finite_and_non_numeric():
    out = tracking._clean_metrics({"a": 1, "b": math.nan, "c": math.inf, "d": "x", "e": "2.5", "f g": 3, "h%": 4})
    assert out == {"a": 1.0, "e": 2.5, "f_g": 3.0, "hpct": 4.0}


def test_track_is_a_noop_when_switched_off_or_unavailable(monkeypatch):
    monkeypatch.setenv("DOSENTINEL_TRACKING", "off")
    assert tracking.track("x", metrics={"m": 1}) is None
    monkeypatch.delenv("DOSENTINEL_TRACKING")
    real = builtins.__import__

    def no_mlflow(name, *a, **k):
        if name == "mlflow":
            raise ImportError("no mlflow")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_mlflow)
    assert tracking.track("x", metrics={"m": 1}) is None  # a script must run without the extra


def test_track_logs_params_metrics_artifacts_and_provenance(tmp_path, monkeypatch):
    mlflow = pytest.importorskip("mlflow")
    monkeypatch.setenv("MLFLOW_TRACKING_URI", f"sqlite:///{tmp_path / 'm.db'}")
    monkeypatch.chdir(tmp_path)
    art = tmp_path / "result.csv"
    art.write_text("a,b\n1,2\n")
    rid = tracking.track("unit", params={"alpha": 0.2}, metrics={"skill": 0.31, "bad": math.nan},
                         artifacts=[art, tmp_path / "missing.csv"], run_name="t", tags={"kind": "test"})
    assert rid
    run = mlflow.get_run(rid)
    assert run.data.params["alpha"] == "0.2" and run.data.metrics == {"skill": 0.31}
    assert run.data.tags["kind"] == "test" and "git_commit" in run.data.tags and "data_manifest_sha256" in run.data.tags
    assert any(a.path == "result.csv" for a in mlflow.artifacts.list_artifacts(run_id=rid)) if hasattr(mlflow, "artifacts") else True


def test_track_swallows_backend_errors(tmp_path, monkeypatch):
    mlflow = pytest.importorskip("mlflow")
    monkeypatch.setenv("MLFLOW_TRACKING_URI", f"sqlite:///{tmp_path / 'm.db'}")
    monkeypatch.chdir(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("tracking server unreachable")

    monkeypatch.setattr(mlflow, "start_run", boom)
    assert tracking.track("unit", metrics={"m": 1}) is None  # tracking must never break the experiment
