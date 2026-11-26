# Monitoring report, 2026-11-26T00:42:56+00:00

* Sites checked: 14 (failed: none)
* Retrain recommended: USGS-02110400, USGS-02344630, USGS-08065350
* Input drift alerts (two or more inputs far outside their normal year-to-year range): none
* Matured forecasts scored so far (forward holdout): 11389

## Forward holdout

| Forecaster | Horizon | n | MAE (mg/L) | 80% coverage |
|---|---|---|---|---|
| gbm | 1 h | 2175 | 0.104 | 0.80 |
| gbm | 3 h | 2178 | 0.181 | 0.80 |
| gbm | 6 h | 2180 | 0.261 | 0.81 |
| gbm | 12 h | 2162 | 0.319 | 0.84 |
| gbm | 24 h | 2134 | 0.363 | 0.86 |
| gbm_weather | 1 h | 112 | 0.104 | 0.80 |
| gbm_weather | 3 h | 112 | 0.196 | 0.84 |
| gbm_weather | 6 h | 112 | 0.315 | 0.82 |
| gbm_weather | 12 h | 112 | 0.386 | 0.77 |
| gbm_weather | 24 h | 112 | 0.508 | 0.70 |

## Sites

| Site | Model | Weather | Action | Input drift | Output drift | Retrain |
|---|---|---|---|---|---|---|
| USGS-01184000 | gbm | disabled | none | major (1 major) | major | no |
| USGS-01654500 | gbm | disabled | none | elevated | major | no |
| USGS-02110400 | gbm | disabled | none | stable | major | yes: MAE at 1 h (gbm) is 1.27x its evaluation level |
| USGS-02110704 | gbm | disabled | none | major (1 major) | major | no |
| USGS-02336313 | gbm | disabled | none | stable | major | no |
| USGS-02344630 | gbm | disabled | none | elevated | major | yes: MAE at 1 h (gbm) is 6.73x its evaluation level; MAE at 3 h (gbm) is 3.17x its evaluation level; MAE at 6 h (gbm) is 1.97x its evaluation level; MAE at 12 h (gbm) is 1.58x its evaluation level; MAE at 24 h (gbm) is 1.31x its evaluation level; interval coverage (gbm) 0.33 below 0.65 |
| USGS-03290500 | gbm | disabled | none | stable | major | no |
| USGS-03293500 | gbm | disabled | none | elevated | major | no |
| USGS-05576195 | gbm | disabled | maintenance | elevated | major | no |
| USGS-07143672 | gbm | disabled | none | elevated | major | no |
| USGS-08065350 | gbm | disabled | none | elevated | major | yes: MAE at 1 h (gbm) is 5.21x its evaluation level; MAE at 3 h (gbm) is 3.63x its evaluation level; MAE at 6 h (gbm) is 2.60x its evaluation level; MAE at 12 h (gbm) is 1.98x its evaluation level; MAE at 24 h (gbm) is 1.80x its evaluation level; interval coverage (gbm) 0.40 below 0.65 |
| USGS-08068000 | gbm | disabled | none | major (1 major) | major | no |
| USGS-08211200 | gbm | disabled | none | major (1 major) | major | no |
| USGS-10133650 | gbm | disabled | none | elevated | major | no |
