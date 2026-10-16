# Model card: do-sentinel

## Model details
* **What it is:** per-site gradient-boosted quantile regression (scikit-learn
  `HistGradientBoostingRegressor`) forecasting dissolved oxygen (DO) at 1, 3, 6, 12 and 24 hours,
  with and without weather inputs; an optional 50/50 blend with the pretrained Chronos-2
  (zero-shot, with covariates); a gradient-boosted classifier for "DO below 5 mg/L within 24
  hours" with Platt scaling; conformal prediction margins for 80% intervals (fitted separately for
  the blend); rule-based and forecast-error fault flags. Where a site has no trained classifier,
  the alert reads the forecast interval instead.
* **Inputs:** the site's own 15-minute DO, water temperature, specific conductance, turbidity
  and discharge; calendar terms; weather (rainfall that has fallen, forecast air temperature,
  sunlight and rainfall for the next 6-24 h) fetched live from Open-Meteo. Without weather the
  service falls back to the base model and says so (`forecast_model`).
* **Trained on:** USGS continuous water-quality data, 14 sites (basins 3 to 16,600 sq mi, 11 US
  states), five years to October 2026. One model per site and horizon.
* **Version/date:** 0.1.0, 2026-10-13. Trained through 2025-10-12, calibrated on the year after.
* **Code:** this repository. Seeds are fixed; dependency lower bounds are in `pyproject.toml`.

## Intended use
Decision support for a field-operations team triaging sensor sites: which sites are likely to see
low oxygen in the next 6-24 hours, and which sensors look suspect. Treat output as a prompt for a
human to check, not as an automatic trigger. See PROBLEM.md for the exact definition and
success criteria.

## Out of scope
* Regulatory compliance or enforcement decisions.
* Detecting slow sensor drift below about 2 mg/L, or over less than two weeks (RESULTS.md section 14).
* Sites that differ from the 14 evaluated ones without re-validation; cross-site transfer kept
  roughly half of the site-specific gain at 1-6 h and almost none at 24 h.
* Real-time operation without monitoring (monitoring is an add-on, not in the evaluation).

## Evaluation (final year, walk-forward, 14 sites; details in README and results/)
| Horizon | MAE skill vs best simple baseline |
|---|---|
| 1 h | +19.8% |
| 3 h | +23.4% |
| 6 h | +25.2% |
| 12 h | +18.4% |
| 24 h | +8.4% (+14.0% with weather) |

* Weather boosting: +13.7% at 24 h and +23.6% at 6 h; the blend with Chronos-2 +16.2% at 24 h and
  +31.3% at 6 h (folds 2-4, 14 of 14 sites better than the baseline at every horizon).
* 80% intervals: coverage 0.78-0.79 after conformal calibration (raw 0.71-0.76); the blend, with
  walk-forward calibration, 0.805-0.812.
* Low-DO probability beats a "current state only" forecaster at about 80% of sites up to 6 h
  (median Brier skill 0.24-0.29); not at 24 h.
* Alerts: at a 10:1 miss:false-alarm cost, forecast-based rules cost 0.9-1.1 per site-week against
  3.6 for a "DO < threshold + 0.5 mg/L" rule (recall 0.89-0.99 against 0.76, at lower precision).
  Between the classifier and the interval rule there is no significant difference. At the 0.5
  alerts/site/week budget only the classifier is clearly better than the margin rule.
* Faults (synthetic): spikes 84%, stuck-low 99%, offsets 65%, flatlines 59%. Slow drift: about 3 mg/L over two
  weeks is caught 70 to 90% of the time at one false alarm per 5 to 20 site-weeks; under 1 mg/L is not detectable.

## Experiments not adopted
A global model across sites and a saturation-fraction target were no better than per-site
models. Slow sensor drift stays undetectable from one sensor, and a neighbouring sensor within
70 km did not change that. The Chronos-2 blend is implemented but opt-in, because it needs the
`foundation` extra (torch, a 119M-parameter model) that the default image does not carry.

## Limitations and caveats
* **Single evaluation period.** The final year was reused across experiments; the only fully
  untouched test is the forward holdout described in PROBLEM.md.
* **Provisional data.** USGS values are revised after review; the model forecasts from what is
  available now, and the fault benchmark is synthetic (no real labelled faults exist here).
* **Selection:** sites were filtered on last-year data quality, which may favor well-instrumented
  ones. Weather results use reanalysis as a stand-in for observed rainfall, and the 24 h weather
  gain is mildly optimistic.
* **Low-DO episodes are rare** at several sites (as few as 7 in two years at one site), so
  per-site alert metrics are noisy.
* **A site with no training events has no usable probability model.** At USGS-01184000 (3% event rate) the low-DO classifier saw no events in training and is a constant, so its probability does not respond to conditions; the alert reads the forecast interval there (`alert_rule: forecast_interval`).
* **Blend margins depend on cached forecasts.** They are fitted on the calibration year's Chronos-2 forecasts (`run_chronos2.py`); after a retrain without those, the bundle serves the weather boosting only.
* **Weather at serving time is the latest forecast,** fresher than the day-old forecast used in training. A bad or missing weather response degrades to the base model.
* **Seasonal.** Event rates differ by season; probability scaling needs a year of calibration data.

## Ethical and operational considerations
Low oxygen harms aquatic life, and a missed event or a false alarm both have costs. Cost
ratios are user-configurable (default 10:1, missed event to false alert). Do not use this to
withhold a response that a human would otherwise make.

## Retraining policy
Retrain at least yearly, or when monitored forecast error rises materially above its
evaluation level, a site's sensor is replaced, or a covariate series changes.
