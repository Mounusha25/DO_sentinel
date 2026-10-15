# Scripts

Run from the repository root. Most scripts read `config/sites.yaml` and write to `data/` or `results/`.
Install the package first (`pip install -e ".[dev,serve]"`); scripts marked *experiments* also need
`pip install -e ".[experiments]"`, and the pretrained-model ones need the `foundation` extra.

## Data

| Script | What it does |
|---|---|
| `discover_sites.py` | Lists USGS sites that report DO plus companion parameters (used to choose sites) |
| `pull_data.py` | Pulls raw 15-minute data for the configured sites into `data/raw/` (resumable) |
| `pull_weather.py` | Pulls hourly weather (ERA5 and archived day-ahead forecasts) into `data/weather/` |
| `validate_sites.py` | Applies quality flags and writes `data/clean/` |
| `make_manifest.py` | Writes or verifies `data/MANIFEST.json` (hashes of processed data) |

## Evaluation experiments (all walk-forward, identical origins)

| Script | Question it answers | Writes |
|---|---|---|
| `run_baselines.py` | How good are the simple baselines? | `results/baselines_*.csv` |
| `run_gbm.py` | Does quantile gradient boosting beat them? | `results/gbm_*.csv` |
| `run_improve.py`, `analyze_improve.py` | Do saturation features, a global model or a blend help, with bootstrap CIs? | `results/improve_*.csv` |
| `run_decision.py` | Are intervals and probabilities calibrated; how do alerts score? | `results/decision_*.csv` |
| `run_decision_direct.py` | Does a classifier trained on the alert event itself do better? | `results/decision_alerts_direct.csv` |
| `run_alert_budget.py` | Which alert rule wins at a fixed alerts-per-week budget? | `results/alert_budget_*.csv` |
| `run_loso.py` | Does a model trained on other sites transfer? | `results/loso_*.csv` |
| `run_weather.py`, `run_weather_alerts.py` | Does weather help forecasts and alerts? | `results/weather_*.csv` |
| `run_faults.py`, `run_drift.py` | How well are injected faults and drift detected, against chance? | `results/faults_*.csv`, `results/drift_*.csv` |
| `run_stats_baseline.py` *(experiments)* | How does Holt-Winters compare? | `results/stats_baseline_rows.parquet` |
| `run_chronos.py`, `run_chronos2.py` *(foundation)* | Does a pretrained model help, zero-shot? | `results/chronos*_rows.parquet` |
| `run_blend.py` | Is the Chronos-2 + boosting blend better and calibrated, walk-forward? | `results/blend_*.csv` |
| `run_alert_bound.py` | Does alerting on the forecast interval beat the classifier or the margin rule? | `results/alert_bound_*.csv` |
| `run_linear_baseline.py` | Does a ridge model with the same inputs match the boosting? | `results/linear_*.csv` |
| `run_drift_peer.py` | Does a neighbouring sensor help detect drift? | `results/drift_peer_*.csv` |

## Product and reports

| Script | What it does |
|---|---|
| `train_models.py` | Trains and saves one serving bundle per site into `models/` (weather models and blend margins when the weather tables and Chronos-2 cache exist) |
| `run_monitoring.py` | One monitoring cycle: issue, score, retrain rule, drift; used by the scheduled workflow | `report.json` / `report.md` in `--state` |
| `build_drift_reference.py` | Per-site, per-month histogram sketches and year-to-year PSI baselines of the training period | `models/drift_reference.json` |
| `retrain.py` | Applies the retrain rule to each site from the forecast log; `--apply` retrains flagged sites into `models_new/` |
| `build_dashboard_data.py` | Precomputes replay forecasts and evidence tables into `dashboard/data.json` (needs `models/` and the result parquet files) |
| `build_dashboard.py` | Template + data + map into `docs/index.html` |
| `make_case_study.py` *(experiments)* | Builds `docs/case-study.html` from the saved results |
| `make_eda.py` *(experiments)* | Builds the exploratory figures in `reports/eda/` |
