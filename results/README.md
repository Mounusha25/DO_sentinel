# Results

Tables written by the scripts in `scripts/` (see `scripts/README.md` for which script writes what).
The narrative, with confidence intervals and caveats, is in [`reports/RESULTS.md`](../reports/RESULTS.md).

| Prefix | From | Contents |
|---|---|---|
| `baselines_*` | `run_baselines.py` | Error and skill of the simple baselines by site, horizon and slice; low-DO onset metrics |
| `gbm_*` | `run_gbm.py` | The same for quantile gradient boosting, plus calibration and events |
| `improve_*` | `run_improve.py` | Weekly error totals per site for each model variant (input to the bootstrap) |
| `decision_*` | `run_decision.py`, `run_decision_direct.py` | Interval coverage, probability scores, alert episodes and costs. A `_cal365` suffix means a 365-day calibration window. |
| `alert_budget_*` | `run_alert_budget.py` | Recall and precision at fixed alerts per week, per rule |
| `loso_*` | `run_loso.py` | Leave-one-site-out transfer |
| `weather_*` | `run_weather.py`, `run_weather_alerts.py` | Weather vs no weather, upper bound, ablation, alert-level test |
| `faults_*`, `drift_*` | `run_faults.py`, `run_drift.py` | Injected-fault detection, drift detection, no-fault and zero-drift controls |
| `blend_*` | `run_blend.py` | Skill and walk-forward conformal coverage of the Chronos-2 + boosting blend |
| `alert_bound_*` | `run_alert_bound.py` | Interval-based alert rules against the classifier and the margin rule, at fixed parameters and at fixed budgets |
| `linear_*` | `run_linear_baseline.py` | Ridge baseline against the boosting, per site and horizon |
| `drift_peer_*` | `run_drift_peer.py` | Drift detection with a neighbouring sensor (`_null` is the chance control) |
| `drift_cusum_*` | `run_drift_cusum.py` | Event-level detection, detection-versus-magnitude curve with chance, and minimum detectable drift |
| `budget_fit_*` | `run_budget_fit.py` | Cutoffs fitted on year 1, frozen, and out-of-sample results and paired differences |
| `holdout_*` | `run_holdout_eval.py` | Frozen pipeline on the pre-registered held-out sites: skill, interval coverage, budget-fit alerts, and sites excluded for too little history |
| `forward_holdout_time` | `estimate_holdout_time.py` | Low-DO episodes per site-year and months of holdout needed |

Three large tables are **not in git** because of their size; regenerate them with the scripts named in
`scripts/README.md`: `budget_fit_scores.parquet`, `chronos_rows.parquet` and `chronos2_rows.parquet` (`run_chronos*.py`, need the
`foundation` extra and a GPU or patience) and `stats_baseline_rows.parquet` (`run_stats_baseline.py`).
`build_dashboard_data.py` and `make_case_study.py` read them.
