# Problem definition

*Fixed on 2026-10-13, before any deep-learning experiments. Targets below were set without
looking at the new models' results.*

## User and decision
**User:** a field-operations team maintaining a network of continuous dissolved-oxygen (DO)
sensors on streams and rivers (for example a state water-quality program or a watershed
group), with limited staff and a few dozen sites.

**Decision, made each morning:** which sites need attention in the next 24 hours.

* **Low-oxygen risk:** a real low-DO event is likely, so send a crew or issue an advisory.
* **Sensor suspect:** the sensor itself looks faulty, so schedule maintenance and do not act on
  its readings.

## Prediction task
* DO at 6, 12 and 24 hours ahead (1 and 3 hours reported for completeness), 15-minute data,
  with calibrated 80% intervals.
* Probability that DO falls below 5 mg/L within the next 24 hours.
* A fault flag on the latest readings.
* Minimum useful lead time: **6 hours** (time to mobilize a crew). One-hour forecasts are
  scored but are not the target.

## Alert budget
At most **0.5 alerts per site per week**. Alert fatigue, not accuracy alone, is the constraint.

## Out of scope
Slow sensor drift (shown not to be detectable from one sensor), regulatory compliance
decisions, causal attribution of why oxygen dropped, and real-time operation (no live
monitoring is claimed).

## Success criteria
| # | Criterion | Target |
|---|---|---|
| 1 | Forecast skill | at least 10% lower MAE than the best simple baseline at 6 h and at 12 h, bootstrap CI excluding zero |
| 2 | Calibration | 80% intervals cover between 75% and 85% |
| 3 | Alerts | anticipated recall of at least 0.7 at no more than 0.5 alerts per site per week, and not worse than the naive "DO below 5.5 mg/L" rule |
| 4 | Fault triage | at least 80% of injected jumps and flatlines flagged, at no more than 1 false flag per site per week |
| 5 | Operational | service answers in under 10 s and reports data freshness and missing inputs |

Failing a criterion is a reportable result, not a reason to change the criterion.

## Evaluation policy
* Chronological splits only; preprocessing, thresholds and calibration fit on earlier data.
* The final year of data was already used to evaluate the gradient-boosting experiments, so
  it is not untouched. New deep-learning models are tuned on the validation year
  (2024-10 to 2025-10) only and scored on the final year once.
* Forward holdout: models are frozen on 2026-10-13. Forecasts made from that date are logged and
  scored after new observations arrive; this is the only truly untouched test.

## Scorecard (2026-10-13)

Evaluated against the criteria above on the final year (14 sites, walk-forward). The model
is the per-site gradient boosting; Chronos-2 is a documented experiment and is not part of
the product (see MODEL_CARD.md).

| # | Criterion | Result | Verdict |
|---|---|---|---|
| 1 | At least 10% lower MAE than the best baseline at 6 h and 12 h, CI excluding zero | 6 h: +25.2% [+23.6, +26.7]; 12 h: +18.4% [+16.5, +20.2] (with weather the 12-24 h skill is higher) | **Met** |
| 2 | 80% intervals cover 75-85% | After conformal calibration: mean 0.78-0.79 per horizon; 93% of site-horizon cells inside 0.75-0.85; worst sites 0.67-0.68. Raw intervals: 0.71-0.76 | **Met on average, not at every site** |
| 3 | Anticipated recall >= 0.7 at <= 0.5 alerts/site/week, not worse than the naive rule | Mean recall 0.795 vs 0.418 for the naive rule; 10 of 14 sites >= 0.7; paired difference +0.38 [+0.03, +0.69], sign test p = 0.23; no advantage at >= 1 alert/week | **Met on average; evidence weak** |
| 4 | >= 80% of injected jumps and flatlines flagged at <= 1 false flag/site/week | Spikes 84% and stuck-low 99% at about 1.0 false flag/week; flatlines 59%, offsets 65%; drift 30% (chance level) | **Partly met**: spikes and stuck readings yes; flatlines and offsets no |
| 5 | Answers in < 10 s, reports freshness and missing inputs | Live call about 5 s including the USGS pull; reports fraction of the last 24 h observed; freshness field added in the monitoring step | **Met after monitoring step** |

Criteria failed or only partly met are reported as such; none were changed after seeing results.
