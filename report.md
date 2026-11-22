# Monitoring report, 2026-11-22T18:24:38+00:00

* Sites checked: 14 (failed: none)
* Retrain recommended: USGS-02110400, USGS-02344630, USGS-08065350
* Input drift alerts (two or more inputs far outside their normal year-to-year range): USGS-08068000
* Matured forecasts scored so far (forward holdout): 10479

## Forward holdout

| Forecaster | Horizon | n | MAE (mg/L) | 80% coverage |
|---|---|---|---|---|
| gbm | 1 h | 1993 | 0.102 | 0.80 |
| gbm | 3 h | 1996 | 0.177 | 0.80 |
| gbm | 6 h | 1998 | 0.251 | 0.82 |
| gbm | 12 h | 1980 | 0.308 | 0.85 |
| gbm | 24 h | 1952 | 0.350 | 0.88 |
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
| USGS-02110400 | gbm | disabled | warning | stable | major | yes: MAE at 1 h (gbm) is 1.33x its evaluation level |
| USGS-02110704 | gbm | disabled | none | major (1 major) | major | no |
| USGS-02336313 | gbm | disabled | none | stable | major | no |
| USGS-02344630 | gbm | disabled | none | stable | major | yes: MAE at 1 h (gbm) is 3.67x its evaluation level; MAE at 3 h (gbm) is 1.97x its evaluation level; MAE at 6 h (gbm) is 1.39x its evaluation level; interval coverage (gbm) 0.63 below 0.65 |
| USGS-03290500 | gbm | disabled | none | elevated | major | no |
| USGS-03293500 | gbm | disabled | none | elevated | major | no |
| USGS-05576195 | gbm | disabled | warning | elevated | major | no |
| USGS-07143672 | gbm | disabled | none | elevated | major | no |
| USGS-08065350 | gbm | disabled | none | elevated | major | yes: MAE at 1 h (gbm) is 5.62x its evaluation level; MAE at 3 h (gbm) is 3.89x its evaluation level; MAE at 6 h (gbm) is 2.76x its evaluation level; MAE at 12 h (gbm) is 2.11x its evaluation level; MAE at 24 h (gbm) is 1.93x its evaluation level; interval coverage (gbm) 0.36 below 0.65 |
| USGS-08068000 | gbm | disabled | none | major (2 major) | major | no |
| USGS-08211200 | gbm | disabled | none | major (1 major) | major | no |
| USGS-10133650 | gbm | disabled | none | major (1 major) | major | no |
