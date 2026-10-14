"""Write or verify data/MANIFEST.json: SHA-256, size and time range of every processed data file.

    python scripts/make_manifest.py            # write
    python scripts/make_manifest.py --check    # verify the files still match

Raw USGS pulls are re-pullable but revised upstream (provisional data change), so the hashes
identify exactly which snapshot every reported number was computed from.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

ROOTS = ["data/clean", "data/weather"]
EXTRA = ["config/sites.yaml", "data/site_coords.json"]
MANIFEST = Path("data/MANIFEST.json")


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def describe(p: Path) -> dict:
    d = {"sha256": sha256(p), "bytes": p.stat().st_size}
    if p.suffix == ".parquet":
        df = pd.read_parquet(p)
        d["rows"] = int(len(df))
        if isinstance(df.index, pd.DatetimeIndex) and len(df):
            d["start"], d["end"] = df.index.min().isoformat(), df.index.max().isoformat()
    return d


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    files = sorted(p for r in ROOTS for p in Path(r).glob("*") if p.is_file()) + [Path(e) for e in EXTRA if Path(e).exists()]
    if a.check:
        old = json.loads(MANIFEST.read_text())["files"]
        bad = [str(p) for p in files if str(p) in old and sha256(p) != old[str(p)]["sha256"]]
        missing = [k for k in old if not Path(k).exists()]
        new = [str(p) for p in files if str(p) not in old]
        print(f"checked {len(old)} files: {len(bad)} changed, {len(missing)} missing, {len(new)} not in manifest")
        for x in bad[:10]:
            print("  changed:", x)
        sys.exit(1 if bad or missing else 0)
    out = {"created": datetime.now(UTC).isoformat(timespec="seconds"),
           "source": "USGS OGC API (api.waterdata.usgs.gov) continuous collection; Open-Meteo archive and previous-runs APIs",
           "files": {str(p): describe(p) for p in files}}
    MANIFEST.write_text(json.dumps(out, indent=1))
    print(f"wrote {MANIFEST}: {len(files)} files, {sum(v['bytes'] for v in out['files'].values()) / 1e6:.0f} MB")


if __name__ == "__main__":
    main()
