# Monitoring report, 2026-10-17T06:42:27+00:00

* Sites checked: 14 (failed: none)
* Retrain recommended: none
* Input drift alerts (two or more inputs far outside their normal year-to-year range): none
* Matured forecasts scored so far (forward holdout): 353

## Forward holdout

| Forecaster | Horizon | n | MAE (mg/L) | 80% coverage |
|---|---|---|---|---|
| gbm | 1 h | 14 | 0.087 | 0.93 |
| gbm | 3 h | 14 | 0.222 | 0.64 |
| gbm | 6 h | 7 | 0.340 | 0.71 |
| gbm_weather | 1 h | 70 | 0.124 | 0.76 |
| gbm_weather | 3 h | 70 | 0.217 | 0.80 |
| gbm_weather | 6 h | 70 | 0.317 | 0.79 |
| gbm_weather | 12 h | 68 | 0.359 | 0.76 |
| gbm_weather | 24 h | 40 | 0.558 | 0.65 |

## Sites

| Site | Model | Weather | Action | Input drift | Output drift | Retrain |
|---|---|---|---|---|---|---|
| USGS-01184000 | gbm_weather | ok | none | stable | unknown | no |
| USGS-01654500 | gbm_weather | ok | none | elevated | unknown | no |
| USGS-02110400 | gbm_weather | ok | warning | elevated | unknown | no |
| USGS-02110704 | gbm_weather | ok | warning | major (1 major) | unknown | no |
| USGS-02336313 | gbm_weather | ok | warning | moderate | unknown | no |
| USGS-02344630 | gbm_weather | ok | none | moderate | unknown | no |
| USGS-03290500 | gbm_weather | ok | none | elevated | unknown | no |
| USGS-03293500 | gbm_weather | ok | warning | moderate | unknown | no |
| USGS-05576195 | gbm_weather | ok | warning | elevated | unknown | no |
| USGS-07143672 | gbm_weather | ok | none | moderate | unknown | no |
| USGS-08065350 | gbm_weather | ok | none | stable | unknown | no |
| USGS-08068000 | gbm_weather | ok | none | elevated | unknown | no |
| USGS-08211200 | gbm_weather | ok | none | major (1 major) | unknown | no |
| USGS-10133650 | gbm_weather | ok | none | elevated | unknown | no |
