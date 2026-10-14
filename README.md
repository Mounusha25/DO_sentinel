# do-sentinel

Short-horizon **dissolved-oxygen forecasting**, **sensor-fault detection** and **alert triage** for stream sensors, built on public USGS data and evaluated against strong baselines with confidence intervals.

**Live:** [Dashboard](https://mounusha25.github.io/DO_sentinel/) · [Case study](https://mounusha25.github.io/DO_sentinel/case-study.html) · [API docs](https://do-sentinel-api.onrender.com/docs) · [Model card](MODEL_CARD.md) · [Problem definition](PROBLEM.md) · [All results](reports/RESULTS.md)

[![DO Sentinel dashboard: map, triage queue and a forecast with its 80% interval](docs/assets/dashboard.png)](https://mounusha25.github.io/DO_sentinel/)

## What it does

A field team maintains dozens of oxygen sensors on streams. Low oxygen kills fish, and sensors also fail. Each morning the team has to decide which sites need attention in the next 24 hours: a likely low-oxygen event, or a sensor that cannot be trusted. This project forecasts oxygen 1 to 24 hours ahead with calibrated uncertainty, estimates the chance of falling below 5 mg/L, flags suspect readings, and turns that into one of three actions: **warning**, **check sensor**, or **no action**. The user, decision and success criteria are fixed in [PROBLEM.md](PROBLEM.md), with a scorecard against them.

## Results at a glance

Fourteen sites in 11 states, five years of 15-minute data, one full year of out-of-sample testing. Skill is the reduction in average error against the best simple baseline.

| Question | Answer |
|---|---|
| Do forecasts beat simple baselines? | Yes: **25% less error at 6 h, 18% at 12 h, 8% at 24 h** (14% with weather). Better at 13 to 14 of 14 sites. |
| Are the uncertainty ranges honest? | Raw 80% intervals covered 71 to 76%. After conformal calibration, **78 to 79%**. |
| Is the low-oxygen probability informative? | Yes up to 6 h: it beats a "current state only" forecaster at about 80% of sites. |
| Do forecast-based alerts beat a one-line rule? | **Only when alerts must be rare**: at 0.5 alerts per site per week they warn of 80% of events in advance against 42%. Not statistically established across 14 sites. |
| Can sensor faults be detected? | Injected spikes 84%, stuck near zero 99%, offsets 65%, flatlines 59%. |
| Does weather help? | +8.6% at 24 h, mostly through sunlight and temperature, not rain. It does not improve alerts. |
| Does a pretrained foundation model help? | Blended with the boosting it cuts 6 h error by 0.03 mg/L, below the sensor's 0.1 mg/L resolution. Not part of the product. |

## What did not work

These are reported, not hidden:

- **Slow sensor drift cannot be detected** from one sensor. An early result of 28% detection was chance; a zero-drift control gave the same rate.
- **Forecast-based alerts barely beat a simple rule** ("alert when DO is below 5.5 mg/L"), apart from the very-low-volume case above.
- **A global model across sites, and predicting percent saturation, were worse** than per-site models.
- **One site has no usable probability model**: no low-oxygen events in its training data.

Several early results looked better than they were and were corrected; see the "mistakes I caught" section of the case study.

## How it works

| Stage | What | Code |
|---|---|---|
| Ingest | USGS OGC API; keeps approval status; honours `Retry-After` | `src/dosentinel/ingest/` |
| Validate | range, flatline, spike and gap flags; flags annotate, never alter | `validate.py` |
| Features | leakage-safe lags, rolling and calendar terms, saturation, optional weather | `features.py`, `weather.py` |
| Models | per-site quantile gradient boosting; direct "low DO within 24 h" classifier | `models.py`, `direct.py` |
| Calibrate | conformal intervals, Platt probability scaling | `calibration.py` |
| Decide | cost-derived cutoff, episode-level scoring against chance, sensor-suspect suppression | `decision.py` |
| Detect faults | causal detectors, synthetic-fault benchmark with a null experiment | `detect.py`, `faults.py` |
| Serve and monitor | FastAPI in Docker, forecast log, scoring of matured forecasts, retrain rule | `serving.py`, `api.py`, `monitoring.py` |

Evaluation is chronological and walk-forward; preprocessing, thresholds and calibration are fit only on earlier data; comparisons use identical origins; intervals are cluster bootstraps. Details and every number are in [reports/RESULTS.md](reports/RESULTS.md).

## Repository layout

```
src/dosentinel/    library: ingest, validate, features, models, calibration, decision, faults, serving, api
scripts/           runnable pipelines and experiments (see scripts/README.md)
tests/             105 tests, including leakage tests for every forecaster and feature set
dashboard/         template, precomputed replay data and US map for the dashboard
docs/              built dashboard and case study (served by GitHub Pages)
config/            sites.yaml: the 14 sites, their selection rationale and exclusions
data/              site-selection tables, coordinates and the data manifest (see data/README.md)
results/           result tables written by the scripts (see results/README.md)
reports/           RESULTS.md (all experiments) and EDA.md with figures
models/            trained per-site bundles used by the API
PROBLEM.md  MODEL_CARD.md  render.yaml  Dockerfile  requirements-lock.txt
```

## Run it

```bash
pip install -e ".[dev,serve]"              # Python 3.11 or newer
pytest                                     # 105 tests
ruff check src scripts tests               # lint

python scripts/build_dashboard.py          # rebuild docs/index.html from dashboard/
docker build -t do-sentinel .
docker run -p 8000:8000 do-sentinel        # http://localhost:8000/docs
```

To regenerate the data and models from scratch (hours, mostly waiting on USGS rate limits):

```bash
export USGS_API_KEY=...                    # free key: https://api.waterdata.usgs.gov/signup
python scripts/pull_data.py --years 5      # raw USGS data for the sites in config/sites.yaml
python scripts/validate_sites.py           # quality flags -> data/clean
python scripts/train_models.py             # per-site bundles -> models/
python scripts/pull_weather.py             # optional: weather for the weather experiments
python scripts/make_manifest.py --check    # verify processed data against data/MANIFEST.json
```

Experiment scripts and what each produces are indexed in [scripts/README.md](scripts/README.md). The pretrained-model experiments need the `foundation` extra (`pip install -e ".[foundation]"`) and were run in a separate virtual environment.

## Deployment

| Part | Where |
|---|---|
| Dashboard and case study (static) | GitHub Pages, from `docs/` |
| API (live forecasts) | any container host; `render.yaml` is a Render blueprint |

Operating notes:

- **A USGS API key is required on a shared host.** Anonymous requests from a cloud address share a quota with other users of that address; the deployed service received HTTP 429 with `Retry-After` of 2 to 11 minutes until a key was set. Set `USGS_API_KEY` in the host's dashboard, never in the repository. With a key a live forecast costs five requests per site and is cached for ten minutes.
- **The endpoint fails fast.** Series are fetched concurrently under a 35 s deadline; on failure it returns 503 with `Retry-After`, or the last good data marked `stale`. `POST /forecast/{site}` with your own series needs no USGS access.
- **Public and unauthenticated.** Payloads are capped at 20,000 observations and CORS is limited to `DOSENTINEL_CORS`. Add authentication before using it for more than a demonstration.
- **Free tiers sleep**, so the first request after a pause can take about a minute.
- **Models do not update themselves.** They were trained through 2025-10 and calibrated on the following year; the retraining policy is in the model card.

## Monitoring

Each live response reports data age and input completeness. With `DOSENTINEL_LOG` set, every served forecast is logged and later scored against observed values (this is the forward holdout described in PROBLEM.md). `GET /monitor/{site}` scores matured forecasts and applies the retrain rule: live error more than 1.25 times its evaluation level (with at least 200 matured forecasts), interval coverage below 0.65, more than 30% of DO missing over a week, or a model older than a year.

## Limitations

- **One evaluation period.** The final year was reused across experiments; only the forward holdout is untouched, and it has not accumulated yet.
- **Faults are synthetic.** No real labelled sensor faults exist in the data, and the injected shapes are simple.
- **Selection.** Sites were filtered on last-year data quality, which may favour well-instrumented ones; low-oxygen episodes are rare at some sites (as few as 7 in two years).
- **Provisional data is revised** by USGS after the fact, and the model forecasts from what is available now.
- **Weather results use reanalysis** as a stand-in for observed rainfall, and the 24 h weather gain is mildly optimistic.
- **Not a compliance tool.** It supports a human decision; see [MODEL_CARD.md](MODEL_CARD.md).

## Reproducibility

Exact package versions are in [requirements-lock.txt](requirements-lock.txt); `data/MANIFEST.json` holds SHA-256 hashes, sizes, row counts and time ranges of every processed data file; seeds are fixed in the models and set in the scripts; CI runs the tests and lint on every push.

## License

MIT, see [LICENSE](LICENSE). The data are public USGS and Open-Meteo data; check their terms before redistributing derived data at scale.
