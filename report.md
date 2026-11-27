# Monitoring report, 2026-11-27T06:35:19+00:00

* Sites checked: 14 (failed: none)
* Retrain recommended: USGS-02344630, USGS-08065350
* Input drift alerts (two or more inputs far outside their normal year-to-year range): none
* Matured forecasts scored so far (forward holdout): 11738

## Forward holdout

| Forecaster | Horizon | n | MAE (mg/L) | 80% coverage |
|---|---|---|---|---|
| gbm | 1 h | 2245 | 0.103 | 0.80 |
| gbm | 3 h | 2248 | 0.180 | 0.80 |
| gbm | 6 h | 2249 | 0.260 | 0.81 |
| gbm | 12 h | 2232 | 0.320 | 0.84 |
| gbm | 24 h | 2204 | 0.370 | 0.86 |
| gbm_weather | 1 h | 112 | 0.104 | 0.80 |
| gbm_weather | 3 h | 112 | 0.196 | 0.84 |
| gbm_weather | 6 h | 112 | 0.315 | 0.82 |
| gbm_weather | 12 h | 112 | 0.386 | 0.77 |
| gbm_weather | 24 h | 112 | 0.508 | 0.70 |

## Sites

| Site | Model | Weather | Action | Input drift | Output drift | Retrain |
|---|---|---|---|---|---|---|
| USGS-01184000 | gbm | disabled | none | elevated | major | no |
| USGS-01654500 | gbm | disabled | none | elevated | major | no |
| USGS-02110400 | gbm | disabled | none | stable | major | no |
| USGS-02110704 | gbm | disabled | none | major (1 major) | major | no |
| USGS-02336313 | gbm | disabled | none | stable | major | no |
| USGS-02344630 | gbm | disabled | none | elevated | major | yes: MAE at 1 h (gbm) is 6.49x its evaluation level; MAE at 3 h (gbm) is 3.06x its evaluation level; MAE at 6 h (gbm) is 1.92x its evaluation level; MAE at 12 h (gbm) is 1.54x its evaluation level; MAE at 24 h (gbm) is 1.29x its evaluation level; interval coverage (gbm) 0.35 below 0.65 |
| USGS-03290500 | gbm | disabled | none | stable | major | no |
| USGS-03293500 | gbm | disabled | none | stable | major | no |
| USGS-05576195 | gbm | disabled | maintenance | elevated | major | no |
| USGS-07143672 | gbm | disabled | none | elevated | major | no |
| USGS-08065350 | gbm | disabled | none | elevated | major | yes: MAE at 1 h (gbm) is 5.09x its evaluation level; MAE at 3 h (gbm) is 3.55x its evaluation level; MAE at 6 h (gbm) is 2.53x its evaluation level; MAE at 12 h (gbm) is 1.92x its evaluation level; MAE at 24 h (gbm) is 1.76x its evaluation level; interval coverage (gbm) 0.42 below 0.65 |
| USGS-08068000 | gbm | disabled | none | major (1 major) | major | no |
| USGS-08211200 | gbm | disabled | none | major (1 major) | major | no |
| USGS-10133650 | gbm | disabled | none | elevated | major | no |
