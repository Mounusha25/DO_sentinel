# Monitoring report, 2026-10-17T18:31:32+00:00

* Sites checked: 14 (failed: none)
* Retrain recommended: none
* Input drift alerts (two or more inputs far outside their normal year-to-year range): none
* Matured forecasts scored so far (forward holdout): 497

## Forward holdout

| Forecaster | Horizon | n | MAE (mg/L) | 80% coverage |
|---|---|---|---|---|
| gbm | 1 h | 14 | 0.087 | 0.93 |
| gbm | 3 h | 14 | 0.222 | 0.64 |
| gbm | 6 h | 14 | 0.278 | 0.79 |
| gbm | 12 h | 14 | 0.423 | 0.64 |
| gbm_weather | 1 h | 98 | 0.105 | 0.80 |
| gbm_weather | 3 h | 98 | 0.196 | 0.83 |
| gbm_weather | 6 h | 95 | 0.290 | 0.81 |
| gbm_weather | 12 h | 83 | 0.348 | 0.75 |
| gbm_weather | 24 h | 67 | 0.496 | 0.67 |

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
| USGS-03293500 | gbm_weather | ok | none | moderate | unknown | no |
| USGS-05576195 | gbm_weather | ok | warning | elevated | unknown | no |
| USGS-07143672 | gbm_weather | ok | none | moderate | unknown | no |
| USGS-08065350 | gbm_weather | ok | none | stable | unknown | no |
| USGS-08068000 | gbm_weather | ok | none | moderate | unknown | no |
| USGS-08211200 | gbm_weather | ok | none | major (1 major) | unknown | no |
| USGS-10133650 | gbm_weather | ok | none | moderate | unknown | no |
