# Monitoring report, 2026-11-23T00:46:46+00:00

* Sites checked: 14 (failed: none)
* Retrain recommended: USGS-02110400, USGS-02344630, USGS-08065350
* Input drift alerts (two or more inputs far outside their normal year-to-year range): USGS-08068000
* Matured forecasts scored so far (forward holdout): 10549

## Forward holdout

| Forecaster | Horizon | n | MAE (mg/L) | 80% coverage |
|---|---|---|---|---|
| gbm | 1 h | 2007 | 0.103 | 0.80 |
| gbm | 3 h | 2010 | 0.178 | 0.80 |
| gbm | 6 h | 2012 | 0.252 | 0.82 |
| gbm | 12 h | 1994 | 0.309 | 0.85 |
| gbm | 24 h | 1966 | 0.352 | 0.88 |
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
| USGS-02344630 | gbm | disabled | none | stable | major | yes: MAE at 1 h (gbm) is 3.65x its evaluation level; MAE at 3 h (gbm) is 1.95x its evaluation level; MAE at 6 h (gbm) is 1.39x its evaluation level; interval coverage (gbm) 0.63 below 0.65 |
| USGS-03290500 | gbm | disabled | none | elevated | major | no |
| USGS-03293500 | gbm | disabled | none | elevated | major | no |
| USGS-05576195 | gbm | disabled | warning | elevated | major | no |
| USGS-07143672 | gbm | disabled | none | elevated | major | no |
| USGS-08065350 | gbm | disabled | none | elevated | major | yes: MAE at 1 h (gbm) is 5.59x its evaluation level; MAE at 3 h (gbm) is 3.87x its evaluation level; MAE at 6 h (gbm) is 2.75x its evaluation level; MAE at 12 h (gbm) is 2.11x its evaluation level; MAE at 24 h (gbm) is 1.92x its evaluation level; interval coverage (gbm) 0.37 below 0.65 |
| USGS-08068000 | gbm | disabled | none | major (2 major) | major | no |
| USGS-08211200 | gbm | disabled | none | major (1 major) | major | no |
| USGS-10133650 | gbm | disabled | none | major (1 major) | major | no |
