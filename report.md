# Monitoring report, 2026-10-17T01:00:11+00:00

* Sites checked: 14 (failed: none)
* Retrain recommended: none
* Input drift alerts (two or more inputs far outside their normal year-to-year range): none
* Matured forecasts scored so far (forward holdout): 296

## Forward holdout

| Forecaster | Horizon | n | MAE (mg/L) | 80% coverage |
|---|---|---|---|---|
| gbm_weather | 1 h | 70 | 0.124 | 0.76 |
| gbm_weather | 3 h | 70 | 0.217 | 0.80 |
| gbm_weather | 6 h | 70 | 0.317 | 0.79 |
| gbm_weather | 12 h | 56 | 0.361 | 0.77 |
| gbm_weather | 24 h | 30 | 0.524 | 0.67 |

## Sites

| Site | Model | Weather | Action | Input drift | Output drift | Retrain |
|---|---|---|---|---|---|---|
| USGS-01184000 | gbm | fetch_failed: ReadTimeout (HTTPSConnectionPool(host='api.open-meteo.com', port=443): Read timed out. (read timeout=8.0)) | none | stable | unknown | no |
| USGS-01654500 | gbm | fetch_failed: ReadTimeout (HTTPSConnectionPool(host='api.open-meteo.com', port=443): Read timed out. (read timeout=8.0)) | none | elevated | unknown | no |
| USGS-02110400 | gbm | fetch_failed: ReadTimeout (HTTPSConnectionPool(host='api.open-meteo.com', port=443): Read timed out. (read timeout=8.0)) | warning | elevated | unknown | no |
| USGS-02110704 | gbm | fetch_failed: ReadTimeout (HTTPSConnectionPool(host='api.open-meteo.com', port=443): Read timed out. (read timeout=8.0)) | warning | major (1 major) | unknown | no |
| USGS-02336313 | gbm | fetch_failed: ReadTimeout (HTTPSConnectionPool(host='api.open-meteo.com', port=443): Read timed out. (read timeout=8.0)) | warning | moderate | unknown | no |
| USGS-02344630 | gbm | fetch_failed: ReadTimeout (HTTPSConnectionPool(host='api.open-meteo.com', port=443): Read timed out. (read timeout=8.0)) | none | elevated | unknown | no |
| USGS-03290500 | gbm | fetch_failed: ReadTimeout (HTTPSConnectionPool(host='api.open-meteo.com', port=443): Read timed out. (read timeout=8.0)) | none | elevated | unknown | no |
| USGS-03293500 | gbm | fetch_failed: ReadTimeout (HTTPSConnectionPool(host='api.open-meteo.com', port=443): Read timed out. (read timeout=8.0)) | warning | moderate | unknown | no |
| USGS-05576195 | gbm | fetch_failed: ReadTimeout (HTTPSConnectionPool(host='api.open-meteo.com', port=443): Read timed out. (read timeout=8.0)) | warning | elevated | unknown | no |
| USGS-07143672 | gbm | fetch_failed: ReadTimeout (HTTPSConnectionPool(host='api.open-meteo.com', port=443): Read timed out. (read timeout=8.0)) | none | moderate | unknown | no |
| USGS-08065350 | gbm | fetch_failed: ReadTimeout (HTTPSConnectionPool(host='api.open-meteo.com', port=443): Read timed out. (read timeout=8.0)) | none | stable | unknown | no |
| USGS-08068000 | gbm | fetch_failed: ReadTimeout (HTTPSConnectionPool(host='api.open-meteo.com', port=443): Read timed out. (read timeout=8.0)) | none | elevated | unknown | no |
| USGS-08211200 | gbm | fetch_failed: ReadTimeout (HTTPSConnectionPool(host='api.open-meteo.com', port=443): Read timed out. (read timeout=8.0)) | none | major (1 major) | unknown | no |
| USGS-10133650 | gbm | fetch_failed: ReadTimeout (HTTPSConnectionPool(host='api.open-meteo.com', port=443): Read timed out. (read timeout=8.0)) | none | elevated | unknown | no |
