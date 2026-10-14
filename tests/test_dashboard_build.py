import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_dashboard_builds_with_data_inlined_and_valid_json(tmp_path):
    work = tmp_path / "w"
    (work / "dashboard").mkdir(parents=True)
    for name in ("template.html", "data.json", "states-albers-10m.json"):
        shutil.copy(ROOT / "dashboard" / name, work / "dashboard" / name)
    shutil.copy(ROOT / "scripts" / "build_dashboard.py", work / "build.py")
    r = subprocess.run([sys.executable, "build.py"], cwd=work, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    html = (work / "docs" / "index.html").read_text()
    assert html.startswith("<!doctype html>") and "<title>DO Sentinel</title>" in html
    assert "/*__DATA__*/" not in html and "/*__ATLAS__*/" not in html
    payloads = re.findall(r'<script id="(?:data|atlas)" type="application/json">(.*?)</script>', html, flags=re.S)
    assert len(payloads) == 2
    data = json.loads(payloads[0])
    assert len(data["sites"]) == 14 and len(data["asofs"]) == 120
    assert all(len(s["replay"]) == len(data["asofs"]) for s in data["sites"])
    assert (work / "docs" / ".nojekyll").exists()


def test_dashboard_data_is_internally_consistent():
    data = json.loads((ROOT / "dashboard" / "data.json").read_text())
    n_series = {len(s["series"]) for s in data["sites"]}
    assert len(n_series) == 1  # every site shares one hourly grid
    for s in data["sites"]:
        for row in s["replay"]:
            assert row["p"] is None or 0.0 <= row["p"] <= 1.0
            assert len(row["f"]) in (0, len(data["horizons"]))
            for low, med, high in row["f"]:
                assert low <= med + 1e-6 <= high + 1e-6
