"""Apply quality flags to data/raw/*.parquet and write data/clean/*.parquet.

    python scripts/validate_sites.py

Prints per-series flag rates so thresholds can be sanity-checked before use.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from dosentinel.validate import RULES, flag_series, summarize


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="data/raw")
    ap.add_argument("--out", default="data/clean")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    rows = []
    for path in sorted(Path(args.raw).glob("*.parquet")):
        site, code = path.stem.rsplit("_", 1)
        df = pd.read_parquet(path)
        if df.empty:
            rows.append({"site": site, "param": code, "steps": 0})
            continue
        s = df.drop_duplicates("time", keep="last").set_index("time")["value"]
        flags = flag_series(s, RULES[code])
        status = df.drop_duplicates("time", keep="last").set_index("time")["approval_status"]
        flags["provisional"] = status.reindex(flags.index, method="nearest", tolerance=pd.Timedelta("7min")).eq("Provisional")
        flags.to_parquet(out / path.name)
        rows.append({"site": site, "param": code, **summarize(flags),
                     "begin": flags.index.min().date(), "end": flags.index.max().date()})
    res = pd.DataFrame(rows)
    pd.set_option("display.width", 220)
    print(res.round(4).to_string(index=False))


if __name__ == "__main__":
    main()
