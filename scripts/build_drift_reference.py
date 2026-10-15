"""Build the drift reference: per site, feature and calendar month, a histogram sketch of the
training period (everything up to each bundle's `trained_through`).

    python scripts/build_drift_reference.py [--out models/drift_reference.json]

The output is a few hundred kB and is committed with the bundles, so the scheduled monitoring job
can check input drift without the training data. Re-run when the bundles are retrained.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import joblib
import yaml

sys.path.insert(0, "src")
from dosentinel.drift import build_reference  # noqa: E402
from dosentinel.features import load_site_frame  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/sites.yaml")
    ap.add_argument("--clean", default="data/clean")
    ap.add_argument("--models", default="models")
    ap.add_argument("--out", default="models/drift_reference.json")
    args = ap.parse_args()
    ref = {}
    for s in yaml.safe_load(Path(args.config).read_text())["sites"]:
        b = joblib.load(Path(args.models) / f"{s['id']}.joblib")
        f = load_site_frame(s["id"], args.clean)
        train = f.loc[f.index <= b.trained_through]
        ref[s["id"]] = {"trained_through": b.trained_through.isoformat(), "rows": int(len(train)),
                        "features": build_reference(train)}
        print(f"{s['id']}: {len(train)} training rows", flush=True)
    Path(args.out).write_text(json.dumps(ref, separators=(",", ":")))
    print(f"wrote {args.out} ({Path(args.out).stat().st_size / 1e3:.0f} KB)")


if __name__ == "__main__":
    main()
