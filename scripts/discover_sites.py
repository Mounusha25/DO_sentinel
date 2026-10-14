"""Find USGS sites with continuous dissolved oxygen plus companion parameters.

Run this on a machine with normal internet access:

    python scripts/discover_sites.py --state-name Arizona --out data/site_candidates.csv

It lists sites that report DO (00300) and counts how many of the companion
parameters (temperature 00010, specific conductance 00095, turbidity 63680,
discharge 00060) each also has, along with the record start/end. Use the output
to choose three contrasting sites for config/sites.yaml.

Verified against the live service: `time-series-metadata` filters on `state_name`
(full name, e.g. "Arizona"), not a two-letter code; `begin`/`end` are present.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

import pandas as pd

from dosentinel.ingest import UsgsClient

TARGET = "00300"
COMPANIONS = {"00010": "temp", "00095": "spcond", "63680": "turb", "00060": "discharge"}


def _site_of(props: dict) -> str | None:
    return props.get("monitoring_location_id")


def _dates_of(props: dict) -> tuple[str | None, str | None]:
    return props.get("begin_utc") or props.get("begin"), props.get("end_utc") or props.get("end")


def collect(client: UsgsClient, parameter_code: str, extra: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    params = {"parameter_code": parameter_code, "limit": 1000, **extra}
    for feat in client.iter_features("time-series-metadata", params):
        props = feat.get("properties", {})
        site = _site_of(props)
        if not site:
            continue
        begin, end = _dates_of(props)
        rec = out.setdefault(site, {"begin": begin, "end": end})
        if begin and (not rec["begin"] or begin < rec["begin"]):
            rec["begin"] = begin
        if end and (not rec["end"] or end > rec["end"]):
            rec["end"] = end
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state-name", help='full state name filter, e.g. "Arizona"')
    ap.add_argument("--out", default="data/site_candidates.csv")
    ap.add_argument(
        "--active-days",
        type=int,
        default=30,
        help="a site counts as active if DO was reported within this many days",
    )
    args = ap.parse_args()

    extra = {"state_name": args.state_name} if args.state_name else {}
    client = UsgsClient()

    do_sites = collect(client, TARGET, extra)
    have: dict[str, set[str]] = defaultdict(set)
    for code, name in COMPANIONS.items():
        for site in collect(client, code, extra):
            have[site].add(name)

    rows = []
    for site, rec in do_sites.items():
        comp = have.get(site, set())
        rows.append(
            {
                "site": site,
                "do_begin": rec["begin"],
                "do_end": rec["end"],
                "n_companions": len(comp),
                "companions": ",".join(sorted(comp)),
                "has_discharge": "discharge" in comp,
            }
        )
    df = pd.DataFrame(rows)
    begin = pd.to_datetime(df["do_begin"], utc=True, errors="coerce")
    end = pd.to_datetime(df["do_end"], utc=True, errors="coerce")
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=args.active_days)
    df["record_days"] = (end - begin).dt.days
    df["active"] = end >= cutoff
    # Live sites first, then richest covariates, then longest record.
    df = df.sort_values(
        ["active", "has_discharge", "n_companions", "record_days"],
        ascending=[False, False, False, False],
    )
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.out, index=False)
    print(df.head(40).to_string(index=False))
    print(f"\n{len(df)} sites with DO ({int(df['active'].sum())} active) written to {args.out}")


if __name__ == "__main__":
    main()
