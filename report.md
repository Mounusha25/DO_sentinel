# Monitoring report, 2026-11-27T18:25:33+00:00

* Sites checked: 14 (failed: none)
* Retrain recommended: USGS-02344630, USGS-08065350
* Input drift alerts (two or more inputs far outside their normal year-to-year range): none
* Matured forecasts scored so far (forward holdout): 11876

## Forward holdout

| Forecaster | Horizon | n | MAE (mg/L) | 80% coverage |
|---|---|---|---|---|
| gbm | 1 h | 2273 | 0.102 | 0.80 |
| gbm | 3 h | 2276 | 0.179 | 0.80 |
| gbm | 6 h | 2278 | 0.259 | 0.81 |
| gbm | 12 h | 2257 | 0.319 | 0.84 |
| gbm | 24 h | 2232 | 0.369 | 0.86 |
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
| USGS-02344630 | gbm | disabled | none | elevated | major | yes: MAE at 1 h is 6.36x its evaluation level; MAE at 3 h is 3.00x its evaluation level; MAE at 6 h is 1.89x its evaluation level; MAE at 12 h is 1.51x its evaluation level; MAE at 24 h is 1.28x its evaluation level; interval coverage 0.36 below 0.65 |
| USGS-03290500 | gbm | disabled | none | stable | major | no |
| USGS-03293500 | gbm | disabled | none | stable | major | no |
| USGS-05576195 | gbm | disabled | maintenance | elevated | major | no |
| USGS-07143672 | gbm | disabled | none | elevated | major | no |
| USGS-08065350 | gbm | disabled | none | elevated | major | yes: MAE at 1 h is 5.04x its evaluation level; MAE at 3 h is 3.52x its evaluation level; MAE at 6 h is 2.52x its evaluation level; MAE at 12 h is 1.90x its evaluation level; MAE at 24 h is 1.75x its evaluation level; interval coverage 0.43 below 0.65 |
| USGS-08068000 | gbm | disabled | none | major (1 major) | major | no |
| USGS-08211200 | gbm | disabled | none | major (1 major) | major | no |
| USGS-10133650 | gbm | disabled | none | elevated | major | no |
