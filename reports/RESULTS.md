# Results in detail

Every experiment run for this project, with the numbers that support the summary in the
[README](../README.md). Conventions throughout:

* **Skill** is the percentage reduction in mean absolute error against the best simple baseline
  (persistence, same time yesterday, seven-day mean, or "now plus yesterday's change"), chosen per site.
* **Intervals** in brackets are 95% cluster-bootstrap confidence intervals that resample whole site-weeks;
  "site-level" intervals resample whole sites and are the more conservative.
* Comparisons use identical forecast origins and training windows. The final year was reused across
  experiments, so the only fully untouched test is the forward holdout described in [PROBLEM.md](../PROBLEM.md).
* Numbers in this file were produced by the scripts named in each section; the tables they read are in
  [`results/`](../results/).

Sections: [1 Forecasting and alerts](#1-forecasting-calibration-alerts-and-transfer-14-sites-5-years-of-history-2-year-walk-forward-test) ·
[2 Improvements](#2-improvement-experiments-with-confidence-intervals) · [3 Weather](#3-weather-experiment) ·
[4 Chronos-Bolt](#4-pretrained-foundation-model-chronos-bolt-zero-shot) · [5 Chronos-2](#5-chronos-2-with-covariates-zero-shot-and-the-blend) ·
[6 Classical baseline](#6-classical-statistical-baseline) · [7 Fault detection](#7-sensor-fault-detection-synthetic-benchmark) ·
[8 Conformalized blend](#8-the-chronos-2--gradient-boosting-blend-with-walk-forward-conformal-intervals) ·
[9 Alerting on the forecast interval](#9-alerting-on-the-forecast-interval) ·
[10 Linear baseline](#10-a-well-specified-linear-model) · [11 Peer sensor and drift](#11-does-a-neighbouring-sensor-help-detect-drift) ·
[12 Input drift monitoring](#12-input-drift-monitoring)

## 1. Forecasting, calibration, alerts and transfer (14 sites, 5 years of history, 2-year walk-forward test)

Sites span basins from 3 to 16,600 sq mi across 11 states; 976 low-DO episodes (< 5 mg/L) in the test window. Models are trained only on data before each test fold, with a 90-day calibration window in between.

- **Point forecasts:** quantile gradient boosting beats the best baseline at 13-14 of 14 sites at every horizon. Mean MAE improvement: 17% (1 h), 22% (3 h), 24% (6 h), 18% (12 h), 7.5% (24 h). Gains are small at 24 h.
- **Intervals:** raw 80% intervals under-cover (0.72-0.78); conformal calibration brings the cross-site mean to 0.80 at every horizon.
- **Probabilities:** the classifier beats a "current state only" forecaster at about 80% of sites for horizons up to 6 h (median Brier skill 0.24-0.29) but not at 24 h. With a 90-day calibration window Platt scaling could be fit in only about half of the folds (events are seasonal); a 365-day window raises that to 88%.
- **Alerts:** scored per episode against chance and against the naive rule "alert when DO < threshold + 0.5 mg/L". Every model-based rule is far better than chance. Alerting on the maximum of per-horizon probabilities beat the naive rule on cost at only 2 of 14 sites. Training and calibrating a classifier directly on "any DO below 5 mg/L in the next 24 h" (`scripts/run_decision_direct.py`) improved that to 5 of 14 (median cost 1.2x naive) while sending about half as many alerts, but it misses more episodes at low-event sites (anticipated recall 0.67-0.85 at three sites) and still has lower precision than the naive rule at 13 of 14. Its probability is genuinely informative (beats a state-only forecaster at 13 of 14 sites, median Brier skill 0.26) but that does not translate into clearly cheaper alerting than a simple margin rule. At one site (USGS-01184000, 3% event rate, no events in any calibration window) it never alerts; such sites should fall back to the margin rule. Longer calibration windows (365 days) fixed probability scaling (applied in 88% of folds instead of 51%) but did not change the alert comparison.
- **Cross-site transfer (leave-one-site-out):** a model trained only on the other 13 sites beats the best baseline at 9/12/9 of 14 sites (1/6/24 h) and keeps roughly half of the site-specific gain at 1-6 h and almost none at 24 h. Pooling does help the rare-event probability model.

Caveats: lead time is a weak metric where episodes recur daily; the alert comparison depends on probability calibration that is incomplete (see above); site selection was filtered on last-year data quality, which may favour well-instrumented sites. See `results/` and `config/sites.yaml`.

## 2. Improvement experiments (with confidence intervals)

Point-forecast variants scored on identical origins, 14 sites, 2-year walk-forward test. Intervals are cluster bootstrap (`scripts/analyze_improve.py`); the site-level interval is the conservative one.

| Horizon | Current local model vs best baseline | + saturation features | Saturation-fraction target | Global model (all sites) | Local + global blend |
|---|---|---|---|---|---|
| 1 h | +18.3% [site CI +12, +24] | 0.0% | -3.2% | -4.2% | 0.0% |
| 6 h | +24.7% [+21.5, +27.8] | **+1.1%** [+0.3, +1.8] | -6.3% | -4.4% | +0.9% [+0.2, +1.6] |
| 24 h | +7.7% [+5.2, +10.2] | +0.5% [-0.0, +1.0] | -5.0% | -0.8% | **+1.1%** [+0.6, +1.5] |

(Columns 3-6 are changes relative to the current local model.)

- The headline forecast skill is robust to resampling. The refinements are small: saturation features and a local+global blend add about 1% MAE at 6-24 h and nothing at 1 h. Predicting percent saturation as the target and a pure global model are worse than the local model.
- Conclusion: these inputs are close to what the site's own history can support. Larger gains need new information; weather was tested next (see Weather experiment): it helps the median forecast at 12-24 h but not the low-DO alerts.
- **Alerts at a matched alert budget** (best anticipated recall each rule can reach without exceeding N alerts per week, parameters chosen per site on the test window): at 0.5 alerts/week the calibrated-probability rule reaches recall 0.80 against 0.42 for the naive margin rule (paired difference +0.38, 95% CI +0.03 to +0.69, but model better at 8 sites, worse at 3, sign test p = 0.23); at 1 per week they tie; at 2 per week the naive rule is better (0.99 vs 0.91). A hybrid "naive AND probability above a cutoff" is worse than the naive rule. Model-based alerting therefore helps mainly when alert volume must be very low. See `results/alert_budget_*.csv`.

## 3. Weather experiment

Does weather information improve DO forecasts? Weather from Open-Meteo: rainfall that has already fallen (ERA5, a stand-in for radar/gauge rainfall) and, for the target window, the GFS forecast issued about a day earlier, so it existed at the forecast origin. Archived precipitation/radiation forecasts only start on 2024-01-19, so the realistic test uses that shorter history (last year as test); an "upper bound" run replaces forecasts with the weather that actually happened, on the full 5 years. Each comparison uses identical rows, folds and training window (`scripts/run_weather.py`, `run_weather_alerts.py`; leakage tests in `tests/test_weather.py`).

| Horizon | Weather vs no weather (realistic forecasts) | Perfect-forecast upper bound | Skill vs best baseline, without -> with weather (realistic) |
|---|---|---|---|
| 6 h | +2.2% [+1.7, +2.6] | +2.8% | 22.8% -> 24.5% |
| 12 h | +4.7% [+4.1, +5.4] | +5.4% | 13.8% -> 17.9% |
| 24 h | **+8.6%** [+7.7, +9.6] | +10.0% | 5.8% -> **14.0%** |

- Better at all 14 sites at every horizon (site-level CIs exclude zero); realistic forecasts retain about 80-87% of the upper bound. At 24 h the weather model also beats the long-history model without weather by 6.1% [+5.0, +7.1].
- **Sunlight and air temperature carry most of the gain, not rain.** At 24 h: air temperature + sunlight only +6.4%, rain only +3.7%, all +8.6%. The gain is as large in dry target windows (8.6%) as in wet ones (8.6%); at 6-12 h wet windows gain more (5.1% vs 1.5% at 6 h).
- **It does not carry through to low-DO alerts.** The probability of DO falling below 5 mg/L within 24 h improves by +2.4% [-2.4, +6.7] (realistic; better at 8 of 14 sites) and by +4.0% [+1.0, +6.9] even with perfect weather (also 8 of 14); anticipated recall at a fixed alerts-per-week budget is unchanged or slightly lower. So the alerting result does not depend on weather information.
- Caveats: the realistic test is one year with 1.5-2.5 years of training; at 24 h the forecast used is about 5 h fresher than a model run could have been at the origin, so the 24 h gain is mildly optimistic; ERA5 stands in for observed rainfall; the Open-Meteo model grid differs from the gauge location. The API does not use weather.

## 4. Pretrained foundation model (Chronos-Bolt, zero-shot)

Amazon's Chronos-Bolt (48M parameters), used with no training on this project's data, against the tuned local gradient boosting (history + saturation features, no weather), on identical hourly origins over the last year (14 sites, about 4,100 origins per site and horizon; `scripts/run_chronos.py`). Chronos sees only the previous 512 hourly DO values; gaps are passed as missing.

| Horizon | Chronos vs best baseline | Gradient boosting vs best baseline | Chronos vs gradient boosting |
|---|---|---|---|
| 1 h | +4.3% [+2.6, +5.9] | +20.1% | **-19.7%** [-21.9, -17.8] |
| 6 h | +16.7% [+15.2, +18.0] | +25.3% | -11.6% [-13.6, -9.7] |
| 12 h | +7.9% [+6.1, +9.6] | +18.4% | -12.9% [-15.0, -10.8] |
| 24 h | -2.0% [-3.4, -0.8] | +8.4% | -11.4% [-13.2, -9.6] |

- Zero-shot, a pretrained model beats the simple baselines at 1-12 h but loses to the tuned gradient boosting at every horizon, and at 12-14 of 14 sites. At 24 h it is no better than the best simple baseline.
- Its raw 80% intervals are closer to nominal (coverage 0.77-0.85 vs 0.72-0.77) but wider; pinball loss is worse at every horizon.
- Averaging Chronos and the gradient boosting helps slightly at 3-6 h (+1.4-1.5%, CI excludes zero) and not at other horizons.
- Caveat: the comparison favors the gradient boosting by construction, since Chronos-Bolt here is univariate and sees no temperature, conductance, discharge or weather. It answers "does a pretrained model beat a tuned, covariate-aware model out of the box?", not "could a fine-tuned or covariate-aware foundation model?". The test year post-dates the model's pretraining.

## 5. Chronos-2 with covariates (zero-shot) and the blend

Chronos-2 (119M parameters, pretrained, **no training on this project's data**) given the same covariates the gradient boosting uses: past water temperature, conductance, turbidity and discharge (`wq`), plus weather (`wx`: past observed, future as the forecast issued a day earlier). Same hourly origins (every 4 h, last 364 days, 14 sites, about 10,300 per horizon), same targets, bootstrap CIs (`scripts/run_chronos2.py`).

| Horizon | Chronos-2 DO only | Chronos-2 + water covariates | Chronos-2 + weather | GBM (no weather) | GBM + weather | **Blend of Chronos-2 + GBM (both with weather)** |
|---|---|---|---|---|---|---|
| 1 h | +14.2% | +14.9% | +12.4% | +19.8% | +19.3% | **+21.4%** |
| 3 h | +22.3% | +26.1% | +24.9% | +23.4% | +21.9% | **+28.3%** |
| 6 h | +22.1% | +28.4% | +27.7% | +25.2% | +24.6% | **+31.3%** |
| 12 h | +13.0% | +20.4% | +21.1% | +18.4% | +17.9% | **+25.5%** |
| 24 h | +0.6% | +4.9% | +9.4% | +8.4% | +14.0% | **+17.4%** |

(MAE skill versus the best simple baseline; all intervals in `results/`.)

- Covariates are what make the pretrained model competitive: Chronos-2 on DO alone loses to the tuned gradient boosting at 3-24 h; with water covariates it beats it at 3-12 h (+3.6%, +4.2%, +2.5%; better at 10, 12 and 10 of 14 sites) and loses at 1 h (-6.2%) and 24 h (-3.9%).
- At 24 h the gradient boosting with weather is the best single model (+14.0%).
- An unweighted 50/50 average of the two families beats the better single model at every horizon: +3.5% (1 h), +5.6% (3 h), +6.0% (6 h), +6.4% (12 h), +4.0% (24 h) with weather, all with CIs excluding zero. The weights were not tuned.
- Interval quality: Chronos-2's raw 80% intervals cover 0.72-0.81 (gradient boosting 0.67-0.77) and have lower pinball loss at 1-12 h; the gradient boosting is better at 24 h.
- Caveats: the 24 h weather result is mildly optimistic (forecast about 5 h fresher than available); the gradient boosting with weather had a shorter training history (from 2024-01-20); the test year was used for earlier experiments (Chronos-2 itself was not tuned on it); the pretraining data cutoff of Chronos-2 could not be verified; ERA5 stands in for observed rainfall; no conformal calibration has been applied to the blend yet.

## 6. Classical statistical baseline

Holt-Winters exponential smoothing with an additive 24-hour season, refit at every origin on the previous 14 days of hourly DO, no covariates (`scripts/run_stats_baseline.py`; 46,000 forecasts at 00:00 and 12:00 UTC, 14 sites, last year, identical rows to the other models).

| Horizon | ETS vs best simple baseline | GBM vs best simple baseline | Chronos-Bolt (zero-shot) vs best baseline | GBM vs ETS |
|---|---|---|---|---|
| 1 h | -5.1% [-8.3, -2.0] | +18.2% | +5.0% | +22.2% |
| 3 h | -16.5% [-20.9, -12.0] | +24.1% | +17.1% | +34.8% |
| 6 h | -36.6% [-44.6, -29.1] | +23.1% | +14.1% | +43.7% |
| 12 h | -55.2% [-65.5, -45.7] | +17.4% | +7.4% | +46.8% |
| 24 h | +0.0% [-0.2, +0.2] | +8.4% | -1.9% | +8.4% |

- The classical method is worse than "same as an hour ago plus yesterday's change" at 1-12 h, and at 24 h it effectively reproduces the last value. It is a weak baseline here and establishes that the gradient boosting's gain is not just "any model with a daily season".
- Caveats: one classical specification (no trend, no covariates); a seasonal ARIMA with exogenous inputs was not tried.

## 7. Sensor-fault detection (synthetic benchmark)

USGS-approved data has had its faults removed, so detectors are scored on faults injected into the final year of each site's clean data (spike, stuck sensor, offset, slow drift, stuck near zero; 3 severities; about 1,900 events over 14 sites). Detectors are causal. Thresholds come from a clean calibration year; "equal footing" thresholds set every detector to 0.67 false flags per week on the test year's own normal steps. A fault counts as detected only if flagged while it is active. See `scripts/run_faults.py` and `results/faults_*.csv`.

| Fault | Best detector (equal footing) | Detected | Notes |
|---|---|---|---|
| Spike | 1-hour forecast interval | 85% (67% small, 98% large) | caught at onset |
| Stuck near zero | any jump-based detector | 95-100% | caught at the jump |
| Offset | 1-hour forecast interval | 67% (38% for 0.5 mg/L, 86% for 2 mg/L) | caught at onset |
| Flatline | run-length | 73% | forecast-based detectors catch about 1-7%; the existing rules (72 h limit) catch 0% of these faults (median 16 h) |
| Slow drift | none reliable | at chance for 0.5-1 mg/L; about +9-14 points over chance for 2 mg/L over 7+ days (long-lag detector) | see the drift note below; the earlier 28% figure was chance |

- **Drift note (`scripts/run_drift.py`):** a "zero-magnitude drift" null experiment shows the 1-hour forecast-interval detector's drift "detection" is chance: it depends on window length (21% / 32% / 52% for 3 / 7 / 14 days even with no drift) and not on magnitude. Detectors anchored on DO from 15-21 days ago (so a drift under 15 days has not contaminated the reference) add a real but small signal, only for 2 mg/L drifts lasting 7+ days (detection 17-31% against 8-12% chance, with equal-footing thresholds). The 72-hour-mean residual noise is 0.3-1.4 mg/L (median 0.55), the same size as the drifts, so small drift is fundamentally hard to see from one DO sensor plus covariates. Thresholds set on the calibration year do not transfer for these detectors (15-27 false flags/week against a target of 0.67). Do not rely on this system to catch slow drift; scheduled field checks are the safer control.
- A covariate-only model (DO predicted from temperature, conductance, turbidity, discharge) was meant to see drift that persistence-based residuals absorb. It did not: 6-25% detection and unstable thresholds. It is not useful in this form.
- Combining all detectors with a shared false-alarm budget is worse than the best single detector per fault type (e.g. spikes 66% combined vs 85%); a per-fault-type union is the better design.
- Persistence-based detectors double-flag: a fault contaminates the "same time yesterday" features, so flags recur a day later (12.7 false flags/week vs 0.94 once echoes are excluded). Needs echo suppression before use.
- Lowering the rules' flatline limit to 6 h catches every injected flatline but produces about 16 false flags/week on real data.
- Caveats: the fault shapes are simple and mine; one random draw per site; real probe failures are messier.

## 8. The Chronos-2 + gradient-boosting blend, with walk-forward conformal intervals

Section 5 left two things open: no conformal calibration had been applied to the blend, and the blend was not served.
`scripts/run_blend.py` reads the cached per-origin quantiles of section 5 and, for each of folds 2-4 (91 days each),
widens the 80% interval by a CQR margin fitted per site and horizon **only on origins whose targets precede the
fold**. Fold 1 has nothing to calibrate on and is excluded from every number below, so raw and calibrated rows are
identical (about 21,700 origins per horizon). The blend is the unweighted mean of each quantile of the weather GBM
and Chronos-2 with weather covariates; its margin is fitted on the blend's own errors.

MAE skill against the best simple baseline per site (95% cluster bootstrap over site-weeks):

| Horizon | GBM + weather | Chronos-2 + weather | **Blend** | Blend better than baseline |
|---|---|---|---|---|
| 1 h | +19.1% [+17.2, +20.9] | +12.2% [+10.5, +13.9] | **+22.1% [+20.5, +23.7]** | 14/14 sites |
| 3 h | +21.4% [+19.7, +23.2] | +24.3% [+22.5, +26.0] | **+28.7% [+27.2, +30.2]** | 14/14 |
| 6 h | +23.6% [+21.4, +25.8] | +26.6% [+24.8, +28.4] | **+31.3% [+29.6, +32.9]** | 14/14 |
| 12 h | +16.0% [+13.2, +18.6] | +18.6% [+16.4, +20.7] | **+24.1% [+22.1, +26.0]** | 14/14 |
| 24 h | +13.7% [+12.2, +15.1] | +7.8% [+5.8, +9.8] | **+16.2% [+14.6, +17.8]** | 14/14 |

Coverage of the nominal 80% interval, raw and after walk-forward conformal calibration (expanding window):

| Horizon | GBM raw | GBM conformal | Chronos-2 raw | Chronos-2 conformal | Blend raw | **Blend conformal** | Interval score, blend vs GBM (lower is better) |
|---|---|---|---|---|---|---|---|
| 1 h | 0.757 | 0.801 | 0.798 | 0.804 | 0.819 | **0.812** | 0.60 vs 0.64 |
| 3 h | 0.736 | 0.799 | 0.755 | 0.798 | 0.790 | **0.805** | 1.09 vs 1.25 |
| 6 h | 0.727 | 0.803 | 0.748 | 0.800 | 0.784 | **0.808** | 1.55 vs 1.80 |
| 12 h | 0.701 | 0.796 | 0.760 | 0.805 | 0.780 | **0.809** | 1.95 vs 2.25 |
| 24 h | 0.680 | 0.805 | 0.811 | 0.812 | 0.796 | **0.811** | 2.22 vs 2.34 |

- The blend's calibrated coverage is 0.805-0.812 at every horizon, per-site coverage 0.76-0.87, and it has the best
  interval score of the three at every horizon; it is also narrower than the calibrated GBM at 3-24 h (24 h: 1.47 vs
  1.51 mg/L) while covering as much. Calibrating on only the previous fold ("last_fold" in `blend_intervals.csv`)
  gives the same coverage to within 0.003.
- Skill here is lower than the section-5 table (24 h +16.2% against +17.4%) because fold 1, the easiest, is excluded.
- Served as of this section: `GET /forecast/{site}` answers from the weather GBM, or from this blend when the
  pretrained model is installed and enabled (`DOSENTINEL_FOUNDATION=chronos2`), and reports which one in
  `forecast_model`. The blend margins in the saved bundles are fitted on the cached forecasts of the calibration year.
- Caveats: the blend weight (0.5) was not tuned, which favours rather than flatters the result; the test year was reused
  by earlier experiments; Chronos-2 is zero-shot and its pretraining cutoff is unverified; the served weather is the
  latest forecast, fresher than the day-old forecast used here (never staler); the blend is issued from the last whole
  hour, because Chronos-2 was evaluated on hourly origins.

## 9. Alerting on the forecast interval

The probability classifier was only competitive with "DO < threshold + 0.5 mg/L" (section 1). A different use of the
same forecasts: alert when the probability that DO is below the threshold at the worst horizon exceeds the cost-derived
cutoff, reading that probability from the conformalized 80% interval (`blend.prob_below`: a normal around the median
with spread from the interval). `scripts/run_alert_bound.py` scores this on the 4-hourly origins of section 8 (folds
2-4, 14 sites, about 1,550 origins per site) with the project's episode scoring, against the naive rule, and against the
classifier trained and calibrated as in `run_decision_direct.py` (365-day window), all on the same origins.

**At parameters fixed by construction, with no sweep on the test window** (naive margin 0.5 mg/L; probability rules at
the cutoff 1/11 that a 10:1 miss:false-alarm ratio implies; the bound rule at "q10 below threshold"):

| Rule | Anticipated recall | Cluster precision | Alerts / site / week | Cost / week (10:1) | Cheaper than naive |
|---|---|---|---|---|---|
| naive: DO < threshold + 0.5 | 0.756 | 0.729 | 1.30 | 3.57 | n/a |
| classifier probability | 0.889 | 0.325 | 0.68 | 1.13 | 10/14 sites |
| interval rule, GBM (no weather) | 0.990 | 0.388 | 1.32 | 0.94 | 11/14 |
| interval rule, GBM + weather | 0.986 | 0.413 | 1.26 | 0.90 | 12/14 |
| interval rule, blend | 0.986 | 0.402 | 1.13 | 0.77 | 13/14 |
| q10 of the blend below threshold | 0.988 | 0.438 | 1.10 | 0.71 | 13/14 |

Mean cost saving against the naive rule, bootstrap over sites: 2.4 [0.2, 5.1] for the classifier and 2.7 [0.7, 5.2]
for the interval rule with weather (`results/alert_bound_fixed.csv`).

- **Both forecast-based rules beat the naive margin rule on cost at the 10:1 ratio**, mostly through recall (0.89-0.99
  against 0.76): the naive rule cannot see an evening dip while DO is still high in the afternoon.
- **The interval rule is not significantly better than the classifier.** Paired by site, the interval rule with weather
  is cheaper at 5 sites and costlier at 9 (sign test p = 0.42; mean saving 0.23 [-0.34, 1.09]). Its mean is helped by
  one site, USGS-01184000, where the classifier is a constant (cost 5.4 per week against 0.4-0.6).
- **Alert volume is above the PROBLEM.md budget.** At the 10:1 cutoff the interval rule sends about 1.3 alerts per site
  per week, against a budget of 0.5. At an exact budget the classifier is better: best anticipated recall at 0.5 alerts
  per week is 0.75 for the classifier and 0.30-0.34 for the interval rule (0.58 for the unrounded q10 bound), with
  parameters chosen per site on the test window for every rule, which is optimistic for all (`alert_bound_paired.csv`).
- Decision: the API uses the classifier probability where a trained classifier exists and the interval rule only where
  it does not (a constant classifier), since that is the one situation where the evidence is clear.
- Caveats: one year, 4-hourly origins (alerts sampled every 4 h for every rule), the test year reused; the rule was
  designed after seeing sweep curves, though its parameter was not tuned on them; precision is lower than the naive rule.

## 10. A well-specified linear model

Is the boosting's gain more than a linear model with the same inputs would give? `scripts/run_linear_baseline.py`
fits a ridge regression per site, horizon and fold on the same features, with the same folds, training windows and
scored origins as section 1 (8 folds, 14 sites; infinities mapped to missing, median imputation with missing
indicators, robust scaling clipped at +-10, penalty by cross-validation).

| Horizon | Ridge: skill vs best baseline | Boosting: skill | Boosting MAE below ridge | Sites where boosting is better |
|---|---|---|---|---|
| 1 h | +12.0% | +17.5% | 6.2% | 13/14 |
| 3 h | +12.9% | +22.3% | 10.7% | 13/14 |
| 6 h | +16.4% | +24.2% | 9.1% | 13/14 |
| 12 h | +14.1% | +18.4% | 5.0% | 12/14 |
| 24 h | +4.0% | +7.5% | 3.6% | 11/14 |

The linear model carries roughly 70-75% of the boosting's gain at 6-12 h and about half at 24 h, so most of the
skill comes from the lags, the diel cycle and the covariates, and the boosting adds a consistent 4-11% MAE on top. Not
tuned beyond the penalty; an ARIMA with exogenous inputs refit at every origin was not tried.

## 11. Does a neighbouring sensor help detect drift?

A nearby station sees the same weather, so its concurrent DO should explain part of the forecast-residual noise that
hides slow drift (section 7). Six sites form three pairs within 70 km (33, 53 and 69 km). `scripts/run_drift_peer.py`
injects the same drift-only faults as `run_drift.py` (0.5, 1 and 2 mg/L over 3, 7 and 14 days, 5 repetitions) into one
member of each pair and predicts its DO from its own covariates plus the other member's uncorrupted DO, with and
without 15-day-lag anchors. Windowed residuals are thresholded at 0.67 false flags per week as before.

| Detector (equal-footing thresholds) | 0.5 mg/L | 1 mg/L | 2 mg/L | Chance (zero-magnitude) |
|---|---|---|---|---|
| single sensor, long-lag anchors, 72 h | 13% | 11% | 19% | 7% |
| peer, 24 h window | 13% | 10% | **28%** | 7% |
| peer, 72 h window | 15% | 7% | 19% | 4% |
| peer, 168 h window | 6% | 7% | 14% | 6% |
| peer + long-lag anchors, 72 h | 13% | 11% | 18% | 4% |

- **No reliable improvement.** Only a 2 mg/L drift with the 24 h peer window rises clearly above chance (28-30% against
  7-12%), and that is the same size as the single-sensor detector's best case. Thresholds set on the calibration year
  produce 1-16 false flags per site-week for the peer detectors (target 0.67), so the deployable version is worse.
- With 6 target sites and about 90 injected events per magnitude, small differences are noise. Do not rely on a
  neighbouring sensor for drift; scheduled field checks remain the control (see section 7).

## 12. Input drift monitoring

Does the standard population-stability check work on these streams? `scripts/build_drift_reference.py` builds, per
site, feature and calendar month, a ten-bin quantile sketch of the training years (everything up to the bundle's
`trained_through`, about 140,000 rows per site). The current 30 days are compared with the same calendar month
(`src/dosentinel/drift.py`).

- **The conventional PSI thresholds do not transfer.** Against the pooled training month, the held-out year scored
  a median PSI of 0.9 for DO and 1.1 to 3.0 for temperature, conductance, turbidity and discharge, and 98% of
  site-months exceeded 0.25 for conductance. Scoring a *training* year against the other training years of the same
  month gives 0.5 to 2.4 at a single site (USGS-02344630, September), so the cause is interannual hydrology, not
  drift.
- **The baseline has to be the site's own year-to-year spread.** Each training year's month is scored against the
  other years (leave-one-year-out), and a window is "stable" within the 90th percentile of those, "moderate" within
  their maximum, "elevated" within twice it and "major" beyond. On the held-out year (12 windows of 30 days at 14
  sites) a single input was "major" in 10% of site-windows per feature and in 33% of site-windows for any of the five;
  requiring two or more inputs to be "major" gave an alert rate of 10% of site-windows (2% for three or more).
  Because four or five training years give an exceedance probability of 1/5 to 1/6 by chance, a single-input rule
  would be mostly noise.
- **Caveats.** The two-input rule and the factor of two were chosen after seeing the held-out year, so the quoted
  10% is optimistic; there is no labelled drift here, so this measures false-alert rate, not detection power;
  injected shifts of 3 mg/L DO, 8 C and a threefold conductance change are caught in the unit tests (and a normal
  year is not); 30 days of 15-minute data are strongly autocorrelated, so a "moderate" reading means "look".
- Output drift (the latest two weeks of the forecast log against the previous two, on the 24 h median and the
  low-DO probability) uses the conventional thresholds because there is no history to judge it against, and is
  informational only.
