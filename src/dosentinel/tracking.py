"""Optional MLflow experiment tracking.

    from dosentinel.tracking import track
    track("blend", params={...}, metrics={...}, artifacts=["results/blend_skill.csv"])

Does nothing, and returns None, when MLflow is not installed or DOSENTINEL_TRACKING=off, so a
script never depends on it. Runs go to a local SQLite store, ./mlruns/mlflow.db (override with
MLFLOW_TRACKING_URI; MLflow 3 has put its plain file store in maintenance mode), and every run is tagged with the git commit, whether the working tree was
dirty, and a hash of data/MANIFEST.json, so a number can be traced to code and data.

Install with the `tracking` extra and browse with `mlflow ui`.
"""
from __future__ import annotations

import hashlib
import math
import os
import subprocess
from pathlib import Path


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True, timeout=10, check=True).stdout.strip()
    except Exception:
        return ""


def provenance(manifest: str = "data/MANIFEST.json") -> dict[str, str]:
    """Code and data identity of the current working tree."""
    p = Path(manifest)
    return {
        "git_commit": _git("rev-parse", "HEAD") or "unknown",
        "git_dirty": str(bool(_git("status", "--porcelain", "--", "src", "scripts"))),
        "data_manifest_sha256": hashlib.sha256(p.read_bytes()).hexdigest()[:16] if p.exists() else "absent",
    }


def _clean_metrics(metrics: dict) -> dict[str, float]:
    out = {}
    for k, v in metrics.items():
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(f):
            out[str(k).replace(" ", "_").replace("%", "pct")] = f
    return out


def track(experiment: str, params: dict | None = None, metrics: dict | None = None,
          artifacts: list[str | Path] | None = None, run_name: str | None = None,
          tags: dict | None = None) -> str | None:
    """Log one run; return its id, or None if tracking is unavailable or switched off."""
    if os.environ.get("DOSENTINEL_TRACKING", "on").lower() == "off":
        return None
    try:
        import mlflow
    except ImportError:
        return None
    try:
        if not os.environ.get("MLFLOW_TRACKING_URI"):
            Path("mlruns").mkdir(exist_ok=True)
            mlflow.set_tracking_uri("sqlite:///mlruns/mlflow.db")
        mlflow.set_experiment(experiment)
        with mlflow.start_run(run_name=run_name) as run:
            mlflow.set_tags({**provenance(), **(tags or {})})
            mlflow.log_params({str(k): str(v)[:250] for k, v in (params or {}).items()})
            mlflow.log_metrics(_clean_metrics(metrics or {}))
            for a in artifacts or []:
                if Path(a).exists():
                    mlflow.log_artifact(str(a))
            return run.info.run_id
    except Exception as e:  # tracking must never break the experiment it observes
        print(f"[tracking] skipped: {type(e).__name__}: {e}")
        return None
