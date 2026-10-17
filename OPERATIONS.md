# Operating notes

Moved out of the README. See also [MODEL_CARD.md](MODEL_CARD.md) and [PROBLEM.md](PROBLEM.md).

## Deployment

| Part | Where |
|---|---|
| Dashboard and case study (static) | GitHub Pages, from `docs/` |
| API (live forecasts) | any container host; `render.yaml` is a Render blueprint |

Operating notes:

- **Live weather is currently off** (`DOSENTINEL_WEATHER=off` in `render.yaml` and the monitor workflow, from 2026-10-17): the free Open-Meteo endpoint timed out from shared addresses, so served and monitored forecasts are the weather-free `gbm` model and `weather_status` is `disabled`. The first five monitoring cycles (2026-10-16) used weather and are tagged `gbm_weather` in `scored.csv`; the report separates the two forecasters, so they are never pooled. The weather-aware model gains about 8.6% at 24 h and nothing for alerts. To turn weather back on, remove the setting and set `OPEN_METEO_API_KEY`.
- **Forecast model (when weather is on).** A live request fetches weather from Open-Meteo (no key) and answers from the weather-aware boosting. Every response has `forecast_model`: `gbm_weather+chronos2` (when `DOSENTINEL_FOUNDATION=chronos2` and the `foundation` extra is installed), `gbm_weather`, or `gbm` when weather or the pretrained model is unavailable. Failures degrade; they never fail the request, and `weather_status` says why a response did not use weather (`ok`, `disabled`, `bundle_has_no_weather_models`, `bundle_has_no_coordinates`, `stale_cache`, `fetch_failed: <error>`). `DOSENTINEL_WEATHER=off` disables the weather fetch. A cloud host shares its IP address, and Open-Meteo's free tier throttles shared addresses; if `weather_status` shows `HTTP 429`, set `OPEN_METEO_API_KEY` (a paid key uses a separate endpoint without that limit). After a failed fetch the provider is not called again for 5 minutes, so an outage costs no latency.
  Weather for all sites is fetched in **one** request (`models/site_coordinates.json` lists the stations), and the scheduled job publishes the weather it fetched as `weather_snapshot.json` on the `monitoring` branch. When the host's own fetch is throttled the API uses that snapshot if it is under 12 hours old (`weather_status: snapshot (N h old)`); set `DOSENTINEL_WEATHER_SNAPSHOT_URL` (default in `render.yaml`) or `DOSENTINEL_WEATHER_SNAPSHOT_FILE`. `POST /forecast/{site}` forecasts only from the series you send, with the base model.
- **Access control (optional).** `DOSENTINEL_API_KEYS="k1,k2"` requires `X-API-Key` on everything except `/health` and the docs. `DOSENTINEL_RATE_LIMIT` caps requests per client per minute (default 60, 0 disables); behind a proxy the client is the last `X-Forwarded-For` entry.
- **A USGS API key is required on a shared host.** Anonymous requests from a cloud address share a quota with other users of that address; the deployed service received HTTP 429 with `Retry-After` of 2 to 11 minutes until a key was set. Set `USGS_API_KEY` in the host's dashboard, never in the repository. With a key a live forecast costs five requests per site and is cached for ten minutes.
- **The endpoint fails fast.** Series are fetched concurrently under a 35 s deadline; on failure it returns 503 with `Retry-After`, or the last good data marked `stale`. `POST /forecast/{site}` with your own series needs no USGS access.
- **Public and unauthenticated.** Payloads are capped at 20,000 observations and CORS is limited to `DOSENTINEL_CORS`. Add authentication before using it for more than a demonstration.
- **Free tiers sleep**, so the first request after a pause can take about a minute.
- **Models do not update themselves.** They were trained through 2025-10 and calibrated on the following year; the retraining policy is in the model card. `python scripts/retrain.py --log <forecast log>` applies the retrain rule to every site and, with `--apply`, retrains the flagged ones into `models_new/`. A scheduled job still has to pull fresh data first.


## Monitoring

Each live response reports data age and input completeness. With `DOSENTINEL_LOG` set, every served forecast is logged and later scored against observed values (this is the forward holdout described in PROBLEM.md). `GET /monitor/{site}` scores matured forecasts and applies the retrain rule: live error more than 1.25 times its evaluation level (with at least 200 matured forecasts), interval coverage below 0.65, more than 30% of DO missing over a week, or a model older than a year.

## Scheduled monitoring and the forward holdout

`.github/workflows/monitor.yml` runs `scripts/run_monitoring.py` every six hours (and on demand). Each
cycle, for every site: fetches live USGS data, issues a forecast (logged with the model that answered
and why weather was or was not used), scores every logged forecast whose target time has passed, applies
the retrain rule, and checks input drift. State is kept on a `monitoring` branch, never on `main`:

| File on the `monitoring` branch | Contents |
|---|---|
| `forecasts.jsonl` | every issued forecast, one line per horizon |
| `scored.csv` | cumulative matured forecasts with observed value, error and interval hit (each scored once) |
| `report.json`, `report.md` | latest cycle: failed sites, retrain recommendations, drift alerts, forward-holdout MAE and coverage per forecaster and horizon |

Secrets: `USGS_API_KEY` (runner addresses are shared, so the anonymous quota is exhausted quickly) and
`OPEN_METEO_API_KEY` only if live weather is turned back on. With weather off the Weather column shows
`disabled`. Run it locally with
`python scripts/run_monitoring.py --state monitoring_state`.

Scoring is what makes the forward holdout in PROBLEM.md real: nothing in `scored.csv` was seen by any
modelling decision. Early rows are few (14 sites, four issues a day), so read it after a few weeks.

## Drift

`GET /drift/{site}` and the monitoring report compare the last 30 days of each input (DO, temperature,
conductance, log turbidity, log discharge) with the same calendar month of the training years, using PSI
judged against the site's own year-to-year variability (`models/drift_reference.json`, built by
`scripts/build_drift_reference.py`; rebuild it when bundles are retrained). A site alerts when two or more
inputs are far outside anything seen in training. Fixed PSI thresholds (0.1 / 0.25) were measured and
rejected: the same month of different years scores 0.5 to 2.4 on these streams. Output drift (the last
two weeks of the forecast log against the two before) is informational.

## Experiment tracking

The experiment scripts, `train_models.py` and the monitoring job log to a local MLflow store
(`pip install -e ".[tracking]"`; `mlflow ui --backend-store-uri sqlite:///mlruns/mlflow.db`). Each run
carries the git commit, whether the tree was dirty, and a hash of `data/MANIFEST.json`, the parameters,
headline metrics and the result tables as artifacts. `DOSENTINEL_TRACKING=off` disables it; a missing
MLflow never breaks a script.
