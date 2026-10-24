# Monitoring report, 2026-10-24T12:37:29+00:00

* Sites checked: 14 (failed: none)
* Retrain recommended: none
* Input drift alerts (two or more inputs far outside their normal year-to-year range): none
* Matured forecasts scored so far (forward holdout): 2388

## Forward holdout

| Forecaster | Horizon | n | MAE (mg/L) | 80% coverage |
|---|---|---|---|---|
| gbm | 1 h | 378 | 0.088 | 0.81 |
| gbm | 3 h | 377 | 0.161 | 0.84 |
| gbm | 6 h | 378 | 0.228 | 0.86 |
| gbm | 12 h | 359 | 0.306 | 0.85 |
| gbm | 24 h | 336 | 0.307 | 0.89 |
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
| USGS-02336313 | gbm | disabled | warning | stable | unknown | no |
| USGS-02344630 | gbm | disabled | none | stable | unknown | no |
| USGS-03290500 | gbm | disabled | none | elevated | unknown | no |
| USGS-03293500 | gbm | disabled | none | stable | unknown | no |
| USGS-05576195 | gbm | disabled | warning | elevated | unknown | no |
| USGS-07143672 | gbm | disabled | none | moderate | unknown | no |
| USGS-08065350 | gbm | disabled | none | stable | unknown | no |
| USGS-08068000 | gbm | disabled | none | stable | unknown | no |
| USGS-08211200 | gbm | disabled | none | major (1 major) | unknown | no |
| USGS-10133650 | gbm | disabled | none | stable | unknown | no |
