"""Assemble the standalone dashboard from the template, the precomputed data and the US map.

    python scripts/build_dashboard.py

Writes reports/dashboard.html (opens from a file, has a doctype) and
reports/dashboard/artifact.html (the same page without the document wrapper, for publishing).
Run scripts/build_dashboard_data.py first.
"""
from __future__ import annotations

import json
from pathlib import Path

tpl = Path("app/dashboard.template.html").read_text()
data = Path("reports/dashboard/data.json").read_text()
atlas = Path("reports/dashboard/states-albers-10m.json").read_text()
for name, blob in (("DATA", data), ("ATLAS", atlas)):
    json.loads(blob)  # fail early if the payload is not valid JSON
    assert "</script" not in blob.lower()
frag = tpl.replace("/*__DATA__*/", data).replace("/*__ATLAS__*/", atlas)
Path("reports/dashboard/artifact.html").write_text(frag)
page = ('<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
        '<style>:root{color-scheme:light}body{margin:0}</style></head><body>\n' + frag + "\n</body></html>\n")
Path("reports/dashboard.html").write_text(page)
print(f"wrote reports/dashboard.html ({len(page) / 1e3:.0f} KB)")
