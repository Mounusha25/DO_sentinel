# Monitoring report, 2026-10-29T18:29:12+00:00

* Sites checked: 14 (failed: none)
* Retrain recommended: USGS-02344630, USGS-08065350
* Input drift alerts (two or more inputs far outside their normal year-to-year range): none
* Matured forecasts scored so far (forward holdout): 3834

## Forward holdout

| Forecaster | Horizon | n | MAE (mg/L) | 80% coverage |
|---|---|---|---|---|
| gbm | 1 h | 667 | 0.095 | 0.82 |
| gbm | 3 h | 666 | 0.170 | 0.82 |
| gbm | 6 h | 665 | 0.233 | 0.85 |
| gbm | 12 h | 650 | 0.298 | 0.86 |
| gbm | 24 h | 626 | 0.316 | 0.89 |
| gbm_weather | 1 h | 112 | 0.104 | 0.80 |
| gbm_weather | 3 h | 112 | 0.196 | 0.84 |
| gbm_weather | 6 h | 112 | 0.315 | 0.82 |
| gbm_weather | 12 h | 112 | 0.386 | 0.77 |
| gbm_weather | 24 h | 112 | 0.508 | 0.70 |

## Sites

| Site | Model | Weather | Action | Input drift | Output drift | Retrain |
|---|---|---|---|---|---|---|
| USGS-01184000 | gbm | disabled | none | stable | unknown | no |
| USGS-01654500 | gbm | disabled | none | moderate | unknown | no |
| USGS-02110400 | gbm | disabled | none | stable | unknown | no |
| USGS-02110704 | gbm | disabled | warning | major (1 major) | unknown | no |
| USGS-02336313 | gbm | disabled | none | stable | unknown | no |
| USGS-02344630 | gbm | disabled | none | stable | unknown | yes: MAE at 1 h (gbm) is 9.82x its evaluation level; MAE at 3 h (gbm) is 4.63x its evaluation level; MAE at 6 h (gbm) is 2.87x its evaluation level; MAE at 12 h (gbm) is 2.26x its evaluation level; MAE at 24 h (gbm) is 1.84x its evaluation level; interval coverage (gbm) 0.13 below 0.65 |
| USGS-03290500 | gbm | disabled | none | stable | unknown | no |
| USGS-03293500 | gbm | disabled | warning | stable | unknown | no |
| USGS-05576195 | gbm | disabled | warning | elevated | unknown | no |
| USGS-07143672 | gbm | disabled | none | moderate | unknown | no |
| USGS-08065350 | gbm | disabled | none | stable | unknown | yes: MAE at 3 h (gbm) is 1.36x its evaluation level; MAE at 6 h (gbm) is 1.37x its evaluation level; MAE at 12 h (gbm) is 1.49x its evaluation level; MAE at 24 h (gbm) is 1.50x its evaluation level |
| USGS-08068000 | gbm | disabled | none | stable | unknown | no |
| USGS-08211200 | gbm | disabled | none | elevated | unknown | no |
| USGS-10133650 | gbm | disabled | none | stable | unknown | no |
