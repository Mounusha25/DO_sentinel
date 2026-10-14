"""Pull raw continuous data for the sites in config/sites.yaml into data/raw/.

    USGS_API_KEY=... python scripts/pull_data.py --years 3

Writes one parquet per (site, parameter). The window is fetched in half-year
chunks so a failed request only repeats a small piece. Re-running overwrites;
provisional values get revised upstream, so a fresh pull is the revision-aware path.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
import yaml

from dosentinel.ingest import UsgsClient
from dosentinel.ingest.usgs import COLUMNS

FMT = "%Y-%m-%dT%H:%M:%SZ"


def chunks(start: pd.Timestamp, end: pd.Timestamp, days: int = 183):
    cur = start
    while cur < end:
        nxt = min(cur + pd.Timedelta(days=days), end)
        yield cur, nxt
        cur = nxt


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/sites.yaml")
    ap.add_argument("--out", default="data/raw")
    ap.add_argument("--years", type=float, default=3.0)
    ap.add_argument("--force", action="store_true", help="re-pull series that already cover the window")
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text())
    codes = [cfg["parameters"]["target"], *cfg["parameters"]["covariates"]]
    end = pd.Timestamp.now(tz="UTC").floor("h")
    start = end - pd.Timedelta(days=round(365 * args.years))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    client = UsgsClient()

    failures = []
    for site in cfg["sites"]:
        for code in codes:
            path = out / f"{site['id']}_{code}.parquet"
            if path.exists() and not args.force:
                have = pd.read_parquet(path)
                if have.empty or have["time"].min() <= start + pd.Timedelta(days=14):
                    print(f"{site['id']} {code}: already covers window, skipped", flush=True)
                    continue
            try:
                parts = [
                    client.fetch_continuous(site["id"], code, a.strftime(FMT), b.strftime(FMT))
                    for a, b in chunks(start, end)
                ]
            except Exception as e:  # keep going; report at the end and re-run to resume
                failures.append((site["id"], code, str(e)[:120]))
                print(f"{site['id']} {code}: FAILED {e}", flush=True)
                continue
            parts = [p for p in parts if not p.empty]
            df = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=COLUMNS)
            if not df.empty:
                # Chunk edges are inclusive on both sides; drop the repeated boundary rows.
                df = df.drop_duplicates(["time_series_id", "time"], keep="last")
            df.to_parquet(path, index=False)
            print(f"{site['id']} {code}: {len(df):>7} rows -> {path}", flush=True)
    if failures:
        print(f"\n{len(failures)} series failed; re-run to resume:")
        for f in failures:
            print("  ", *f)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
