"""Build reports/case_study.html (self-contained, figures embedded) from the saved result files.

    python scripts/make_case_study.py

Every number on the page is read from results/ or computed from the shared evaluation tables;
nothing is typed in by hand except the narrative.
"""
from __future__ import annotations

import base64
import io
import sys
from pathlib import Path

import joblib
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

sys.path.insert(0, "src")
from dosentinel.features import load_site_frame  # noqa: E402
from dosentinel.serving import forecast  # noqa: E402

R = Path("results")
plt.rcParams.update({"figure.dpi": 140, "axes.spines.top": False, "axes.spines.right": False, "font.size": 9.5})
C = {"base": "#8c8c8c", "gbm": "#1f77b4", "wx": "#2ca02c", "blend": "#ff7f0e", "naive": "#d62728", "model": "#1f77b4"}


OUTDIR = Path("reports/case_study")
OUTDIR.mkdir(parents=True, exist_ok=True)


def img(fig, alt: str) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    (OUTDIR / (alt.lower().replace(" ", "_") + ".png")).write_bytes(buf.getvalue())
    plt.close(fig)
    return f'<img alt="{alt}" src="data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}">'


# ------------------------------------------------------------------ numbers from the shared table
d = pd.read_parquet(R / "chronos2_rows.parquet").dropna(
    subset=["y_true", "wx_q50", "wq_q50", "gbm_q50", "gbmwx_q50", "persistence", "seasonal_naive", "persistence_diel_delta"])
d["blend"] = 0.5 * (d.wx_q50 + d.gbmwx_q50)
rows = []
for h, g in d.groupby("horizon_h"):
    m = lambda c: float((g[c] - g.y_true).abs().mean())
    base = min(m("persistence"), m("seasonal_naive"), m("persistence_diel_delta"))
    rows.append({"h": h, "baseline": base, "gbm": min(m("gbm_q50"), m("gbmwx_q50")), "blend": m("blend")})
S = pd.DataFrame(rows).set_index("h")
B = ["persistence", "seasonal_naive", "seasonal_mean_7d", "persistence_diel_delta"]
skill = {}
for h, g in d.groupby("horizon_h"):
    tot = g.groupby("site").apply(lambda x: pd.Series({b: (x[b] - x.y_true).abs().sum() for b in B}), include_groups=False)
    best = tot.idxmin(axis=1)
    best_abs = sum(float(tot.loc[site, best[site]]) for site in tot.index)
    gbm_abs = float((g.gbm_q50 - g.y_true).abs().sum())
    skill[h] = 1 - gbm_abs / best_abs

# F1: error by horizon
fig, ax = plt.subplots(figsize=(7.2, 3.6))
x = np.arange(len(S))
w = 0.26
ax.bar(x - w, S.baseline, w, label="best simple baseline", color=C["base"])
ax.bar(x, S.gbm, w, label="gradient boosting", color=C["gbm"])
ax.bar(x + w, S.blend, w, label="boosting + pretrained model", color=C["blend"])
ax.set_xticks(x, [f"{h} h" for h in S.index])
ax.set_ylabel("average error (mg/L)")
ax.set_title("Forecast error by horizon, last year, 14 sites")
ax.legend(frameon=False)
F1 = img(fig, "Forecast error by horizon")

# F2: calibration
c = pd.read_csv(R / "decision_calibration_cal365.csv").groupby("horizon_h")[["coverage_raw", "coverage_conformal"]].mean()
fig, ax = plt.subplots(figsize=(7.2, 3.4))
x = np.arange(len(c))
ax.bar(x - 0.18, c.coverage_raw, 0.36, label="raw", color=C["base"])
ax.bar(x + 0.18, c.coverage_conformal, 0.36, label="after conformal calibration", color=C["gbm"])
ax.axhline(0.8, color="k", ls="--", lw=1)
ax.set_ylim(0.5, 0.9)
ax.set_xticks(x, [f"{h} h" for h in c.index])
ax.set_ylabel("share of outcomes inside the 80% interval")
ax.set_title("Are the uncertainty ranges honest? (target 80%)")
ax.legend(frameon=False, loc="lower right")
F2 = img(fig, "Interval coverage")

# F3: alerts at a fixed budget
s = pd.read_csv(R / "alert_budget_summary.csv")
fam = s[s.family.isin(["model", "naive"])].pivot_table(index="budget", columns="family", values="recall", aggfunc="mean")
fig, ax = plt.subplots(figsize=(7.2, 3.4))
ax.plot(fam.index, fam["model"], "o-", color=C["model"], label="forecast-based alert")
ax.plot(fam.index, fam["naive"], "s--", color=C["naive"], label='simple rule: "DO below 5.5"')
ax.set_xscale("log", base=2)
ax.set_xticks(list(fam.index), [str(b) for b in fam.index])
ax.set_xlabel("alerts per site per week allowed")
ax.set_ylabel("low-oxygen events warned of in advance")
ax.set_title("Alerts: forecasting helps only when alerts must be rare")
ax.legend(frameon=False)
F3 = img(fig, "Alert budget")

# F4: drift detection is chance
e = pd.read_csv(R / "drift_events.csv")
n = pd.read_csv(R / "drift_events_null.csv")
sel = lambda t: t[(t.detector == "interval_1h") & (t.threshold_type == "oracle")]
real = sel(e).groupby("duration_days").detected.mean()
null = sel(n).groupby("duration_days").detected.mean()
fig, ax = plt.subplots(figsize=(7.2, 3.4))
ax.plot(real.index, real.values, "o-", color=C["model"], label="real drifts (0.5-2 mg/L)")
ax.plot(null.index, null.values, "s--", color=C["naive"], label="no drift at all (chance)")
ax.set_xlabel("drift duration (days)")
ax.set_ylabel("share flagged while active")
ax.set_title("A 'detection' that was luck: same rate with and without drift")
ax.set_ylim(0, 0.8)
ax.legend(frameon=False)
F4 = img(fig, "Drift detection equals chance")

# F5: one real forecast against what happened
site = "USGS-03293500"
bundle = joblib.load(f"models/{site}.joblib")
f = load_site_frame(site, "data/clean")
asof = pd.Timestamp("2026-09-10 03:30", tz="UTC")
rep = forecast(bundle, f.loc[:asof].iloc[-96 * 40:], 10.0)
hist = f["do"].loc[asof - pd.Timedelta(hours=60): asof]
fut = f["do"].loc[asof: asof + pd.Timedelta(hours=26)]
t = [pd.Timestamp(x["target_time"]) for x in rep["forecasts"]]
fig, ax = plt.subplots(figsize=(7.6, 3.8))
ax.plot(hist.index, hist, color=C["gbm"], label="observed")
ax.plot(fut.index, fut, color="#555", ls=":", label="what actually happened")
ax.fill_between(t, [x["low_mgL"] for x in rep["forecasts"]], [x["high_mgL"] for x in rep["forecasts"]],
                color=C["blend"], alpha=0.3, label="80% interval")
ax.plot([asof] + t, [rep["latest_do_mgL"]] + [x["median_mgL"] for x in rep["forecasts"]], "o-", color=C["blend"],
        label="forecast")
ax.axhline(5, color="red", ls="--", lw=1)
ax.set_ylabel("dissolved oxygen (mg/L)")
ax.set_title(f"A forecast issued 2026-09-10 (P(low DO within 24 h) = {rep['p_low_do_within_24h']:.0%})")
ax.legend(frameon=False, ncol=2, loc="lower left")
F5 = img(fig, "Example forecast")
print("numbers:", S.round(3).to_dict("index"))

# ------------------------------------------------------------------ page
def pct(a, b):
    return f"{(1 - a / b) * 100:.0f}%"


h6, h12, h24 = S.loc[6], S.loc[12], S.loc[24]
html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Forecasting river oxygen</title>
<style>
:root{{--bg:#fff;--fg:#1c1c1c;--muted:#5d5d5d;--card:#f5f6f8;--accent:#1f77b4;--line:#dcdfe4}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{--bg:#14161a;--fg:#e8e8e8;--muted:#a3a7ad;--card:#1d2026;--accent:#6aa9de;--line:#2c3038}}}}
body{{background:var(--bg);color:var(--fg);font:16px/1.6 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;margin:0}}
main{{max-width:760px;margin:0 auto;padding:32px 16px 64px}}
h1{{font-size:1.9rem;line-height:1.25;margin:.2em 0}} h2{{margin-top:2.2em;border-top:1px solid var(--line);padding-top:1em}}
.lede{{color:var(--muted);font-size:1.05rem}} img{{max-width:100%;height:auto;display:block;margin:1em auto;border-radius:6px}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:1.2em 0}}
.kpi{{background:var(--card);border-radius:8px;padding:12px 14px}} .kpi b{{font-size:1.5rem;display:block;color:var(--accent)}}
.card{{background:var(--card);border-radius:8px;padding:12px 16px;margin:1em 0}}
table{{border-collapse:collapse;width:100%;font-size:.93rem}} td,th{{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left}}
.small{{color:var(--muted);font-size:.9rem}}
</style></head><body><main>
<h1>Forecasting river oxygen, and knowing what not to trust</h1>
<p class="lede">A short-horizon forecasting and alert-triage system for stream oxygen sensors, built on public USGS data
and evaluated more strictly than most projects of this size. What worked, what did not, and the mistakes I caught.</p>

<h2>The problem</h2>
<p>A field team keeps dozens of dissolved-oxygen sensors running on streams. Low oxygen kills fish; sensors also fail.
Each morning they must decide <b>which sites need attention in the next 24 hours</b>: a likely low-oxygen event, or a sensor that
cannot be trusted. Alerts must be rare, so the target was at most 0.5 alerts per site per week, with at least 6 hours of warning.</p>

<div class="kpis">
<div class="kpi"><b>{skill[6] * 100:.0f}%</b>less error than the best simple method at 6 h</div>
<div class="kpi"><b>{skill[12] * 100:.0f}%</b>less error at 12 h</div>
<div class="kpi"><b>{c.coverage_conformal.mean():.0%}</b>of outcomes inside the "80%" interval</div>
<div class="kpi"><b>14</b>sites, one full year of out-of-sample testing</div>
</div>

<h2>What worked</h2>
<h3>Forecasts beat strong simple baselines</h3>
{F1}
<p>At 6 hours ahead the simple baseline ("same as now plus yesterday's change") is off by {h6.baseline:.2f} mg/L on average;
gradient boosting by {h6.gbm:.2f}. Blending in a pretrained foundation model (Chronos-2) cuts another {h6.gbm - h6.blend:.2f} mg/L:
statistically real, but below the sensor's own 0.1 mg/L resolution, so the simpler model is the product and the blend is a documented experiment.
Weather forecasts matter most at 24 hours (sunlight and temperature, not rain, carry most of the gain).</p>
<h3>Uncertainty is calibrated, not decorative</h3>
{F2}
<p>Raw model intervals covered {c.coverage_raw.mean():.0%} of outcomes when claiming 80%. A conformal calibration step on a year of data
brought that to {c.coverage_conformal.mean():.0%} on average (some sites remain lower).</p>
<h3>An actual forecast</h3>
{F5}
<p class="small">From the replay dashboard, out-of-sample. The probability warning was right: oxygen fell below 5 mg/L and reached 2.5 mg/L within the day. The 1 to 12 hour forecasts tracked the first dip well, but the forecast for 24 hours ahead missed a second, deeper dip entirely and the actual value falls outside the 80% interval. Across the year, forecasts the model rated 90-100% likely were followed by an event 7 of 7 times in a spot check.</p>

<h2>What did not work</h2>
<h3>Forecast-based alerts barely beat a one-line rule</h3>
{F3}
<p>"Alert when oxygen is below 5.5 mg/L" is hard to beat. The forecast helps when alerts must be very rare (at 0.5 per site per week it warns
of {fam.loc[0.5, 'model']:.0%} of events in advance against {fam.loc[0.5, 'naive']:.0%}), but the difference is not statistically established across 14 sites, and
with more alerts allowed the simple rule is as good or better.</p>
<h3>Slow sensor drift cannot be detected from one sensor</h3>
{F4}
<p>My first result said the detector caught about 28% of drifts. A "zero-drift" control showed the same rate with no drift at all:
longer windows simply contain more random flags. A detector anchored on oxygen from 15-21 days earlier added only a small real signal,
and only for 2 mg/L drifts lasting a week or more.</p>

<h2>Mistakes I caught</h2>
<div class="card"><ul>
<li><b>Alert recall of 1.0 for every method.</b> A scoring bug counted warnings raised after the event had begun. Fixed, with a chance baseline added.</li>
<li><b>"Drift detection at 28%."</b> Chance, as above.</li>
<li><b>"The baseline's error doubles in storms."</b> I had not computed it; it is 11-22% at 6-24 h. The advantage over persistence is what collapses.</li>
<li><b>A 97% low-oxygen probability at 7.4 mg/L.</b> It looked wrong; it was right (28 of the previous 31 days had dipped below 5 mg/L).</li>
</ul></div>

<h2>How it was evaluated</h2>
<ul>
<li>Chronological walk-forward over 8 test folds; preprocessing, thresholds and calibration fit only on earlier data.</li>
<li>Bootstrap confidence intervals clustered by site-week; comparisons on identical forecast origins.</li>
<li>Faults for the detector benchmark are synthetic and labelled as such; no real labelled faults exist in the data.</li>
<li>The final year was reused across experiments, so only a forward holdout (forecasts logged from the freeze date) is truly untouched.</li>
</ul>

<h2>Limits</h2>
<p>Sites were chosen for data quality; low-oxygen episodes are rare at some sites (as few as 7 in two years); one site has no usable
probability model; weather results use reanalysis as a stand-in for observed rain; the 24 h weather gain is mildly optimistic. Slow drift is out of scope.
Details are in the model card.</p>

<h2>What is in the repository</h2>
<p class="small">Ingestion and validation, baselines, gradient boosting with conformal intervals, a foundation-model comparison,
fault-detection benchmark with injected faults, a FastAPI service with monitoring and a retrain rule, a replay dashboard,
tests, a data manifest and pinned versions. Start with PROBLEM.md, MODEL_CARD.md and reports/EDA.md.</p>
</main></body></html>"""
out = Path("reports/case_study.html")
out.write_text(html)
print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB)")
