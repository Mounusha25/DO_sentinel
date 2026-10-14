"""Assemble the standalone dashboard from the template, the precomputed data and the US map.

    python scripts/build_dashboard.py

Writes docs/index.html, which GitHub Pages serves as the site root (it opens fine from a file
too). Also writes dashboard/_artifact.html (git-ignored): the same page without the document
wrapper, for publishing as a hosted page. Run scripts/build_dashboard_data.py first.
"""
from __future__ import annotations

import json
from pathlib import Path

tpl = Path("dashboard/template.html").read_text()
data = Path("dashboard/data.json").read_text()
atlas = Path("dashboard/states-albers-10m.json").read_text()
for blob in (data, atlas):
    json.loads(blob)  # fail early if a payload is not valid JSON
    assert "</script" not in blob.lower()
frag = tpl.replace("/*__DATA__*/", data).replace("/*__ATLAS__*/", atlas)
assert "/*__DATA__*/" not in frag and "/*__ATLAS__*/" not in frag

Path("dashboard/_artifact.html").write_text(frag)
page = ('<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
        '<style>:root{color-scheme:light}body{margin:0}</style></head><body>\n' + frag + "\n</body></html>\n")
docs = Path("docs")
docs.mkdir(exist_ok=True)
(docs / "index.html").write_text(page)
(docs / ".nojekyll").write_text("")
print(f"wrote docs/index.html ({len(page) / 1e3:.0f} KB)")
