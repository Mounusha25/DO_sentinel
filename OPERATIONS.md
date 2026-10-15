# Operating notes

Moved out of the README. See also [MODEL_CARD.md](MODEL_CARD.md) and [PROBLEM.md](PROBLEM.md).

## Deployment

| Part | Where |
|---|---|
| Dashboard and case study (static) | GitHub Pages, from `docs/` |
| API (live forecasts) | any container host; `render.yaml` is a Render blueprint |

Operating notes:

- **Forecast model.** A live request fetches weather from Open-Meteo (no key) and answers from the weather-aware boosting. Every response has `forecast_model`: `gbm_weather+chronos2` (when `DOSENTINEL_FOUNDATION=chronos2` and the `foundation` extra is installed), `gbm_weather`, or `gbm` when weather or the pretrained model is unavailable. Failures degrade; they never fail the request. `DOSENTINEL_WEATHER=off` disables the weather fetch. `POST /forecast/{site}` forecasts only from the series you send, with the base model.
- **Access control (optional).** `DOSENTINEL_API_KEYS="k1,k2"` requires `X-API-Key` on everything except `/health` and the docs. `DOSENTINEL_RATE_LIMIT` caps requests per client per minute (default 60, 0 disables); behind a proxy the client is the last `X-Forwarded-For` entry.
- **A USGS API key is required on a shared host.** Anonymous requests from a cloud address share a quota with other users of that address; the deployed service received HTTP 429 with `Retry-After` of 2 to 11 minutes until a key was set. Set `USGS_API_KEY` in the host's dashboard, never in the repository. With a key a live forecast costs five requests per site and is cached for ten minutes.
- **The endpoint fails fast.** Series are fetched concurrently under a 35 s deadline; on failure it returns 503 with `Retry-After`, or the last good data marked `stale`. `POST /forecast/{site}` with your own series needs no USGS access.
- **Public and unauthenticated.** Payloads are capped at 20,000 observations and CORS is limited to `DOSENTINEL_CORS`. Add authentication before using it for more than a demonstration.
- **Free tiers sleep**, so the first request after a pause can take about a minute.
- **Models do not update themselves.** They were trained through 2025-10 and calibrated on the following year; the retraining policy is in the model card. `python scripts/retrain.py --log <forecast log>` applies the retrain rule to every site and, with `--apply`, retrains the flagged ones into `models_new/`. A scheduled job still has to pull fresh data first.


## Monitoring

Each live response reports data age and input completeness. With `DOSENTINEL_LOG` set, every served forecast is logged and later scored against observed values (this is the forward holdout described in PROBLEM.md). `GET /monitor/{site}` scores matured forecasts and applies the retrain rule: live error more than 1.25 times its evaluation level (with at least 200 matured forecasts), interval coverage below 0.65, more than 30% of DO missing over a week, or a model older than a year.
