# Data

Raw, cleaned and weather series are **not stored in git** (about 230 MB, and USGS revises provisional values);
`MANIFEST.json` records the hash, size, row count and time range of every processed file so that any reported
number can be tied to an exact snapshot. Regenerate with `scripts/pull_data.py`, `scripts/pull_weather.py` and
`scripts/validate_sites.py`, then verify with `python scripts/make_manifest.py --check`.

Small files kept in the repository:

| File | What it is |
|---|---|
| `MANIFEST.json` | hashes and shapes of the processed data |
| `site_coords.json` | latitude and longitude of the 14 USGS stations (public) |
| `site_candidates.csv` | Arizona-only discovery output from the first, narrower search |
| `site_candidates_us.csv` | nationwide discovery: every site reporting DO, with companions and record length |
| `site_meta.csv` | names and basin areas for long-record, all-covariate sites |
| `site_profile.csv` | one-year profile of the first three contrast sites |
| `expansion_candidates.csv`, `expansion_profile.csv` | the stratified shortlist and last-year data-quality profile used to add 11 sites |
