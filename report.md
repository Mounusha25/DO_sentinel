# Monitoring report, 2026-11-14T18:23:34+00:00

* Sites checked: 14 (failed: none)
* Retrain recommended: USGS-02110400, USGS-02344630, USGS-08065350, USGS-08068000
* Input drift alerts (two or more inputs far outside their normal year-to-year range): none
* Matured forecasts scored so far (forward holdout): 8238

## Forward holdout

| Forecaster | Horizon | n | MAE (mg/L) | 80% coverage |
|---|---|---|---|---|
| gbm | 1 h | 1546 | 0.104 | 0.81 |
| gbm | 3 h | 1549 | 0.182 | 0.80 |
| gbm | 6 h | 1549 | 0.263 | 0.82 |
| gbm | 12 h | 1531 | 0.321 | 0.84 |
| gbm | 24 h | 1503 | 0.362 | 0.87 |
| gbm_weather | 1 h | 112 | 0.104 | 0.80 |
| gbm_weather | 3 h | 112 | 0.196 | 0.84 |
| gbm_weather | 6 h | 112 | 0.315 | 0.82 |
| gbm_weather | 12 h | 112 | 0.386 | 0.77 |
| gbm_weather | 24 h | 112 | 0.508 | 0.70 |

## Sites

| Site | Model | Weather | Action | Input drift | Output drift | Retrain |
|---|---|---|---|---|---|---|
| USGS-01184000 | gbm | disabled | none | elevated | major | no |
| USGS-01654500 | gbm | disabled | none | stable | major | no |
| USGS-02110400 | gbm | disabled | none | stable | major | yes: MAE at 1 h (gbm) is 1.41x its evaluation level |
| USGS-02110704 | gbm | disabled | warning | major (1 major) | major | no |
| USGS-02336313 | gbm | disabled | none | stable | major | no |
| USGS-02344630 | gbm | disabled | none | elevated | major | yes: MAE at 1 h (gbm) is 4.53x its evaluation level; MAE at 3 h (gbm) is 2.38x its evaluation level; MAE at 6 h (gbm) is 1.62x its evaluation level; MAE at 12 h (gbm) is 1.34x its evaluation level; interval coverage (gbm) 0.55 below 0.65 |
| USGS-03290500 | gbm | disabled | none | stable | major | no |
| USGS-03293500 | gbm | disabled | none | stable | major | no |
| USGS-05576195 | gbm | disabled | maintenance | elevated | major | no |
| USGS-07143672 | gbm | disabled | none | moderate | major | no |
| USGS-08065350 | gbm | disabled | none | stable | major | yes: MAE at 1 h (gbm) is 1.29x its evaluation level; MAE at 3 h (gbm) is 1.40x its evaluation level; MAE at 6 h (gbm) is 1.53x its evaluation level; MAE at 12 h (gbm) is 1.53x its evaluation level; MAE at 24 h (gbm) is 1.40x its evaluation level |
| USGS-08068000 | gbm | disabled | none | stable | major | yes: MAE at 1 h (gbm) is 1.38x its evaluation level |
| USGS-08211200 | gbm | disabled | none | moderate | major | no |
| USGS-10133650 | gbm | disabled | none | stable | major | no |
