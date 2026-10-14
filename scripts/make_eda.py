"""Exploratory figures and summary numbers, each tied to a modeling decision.

    python scripts/make_eda.py

Writes reports/eda/*.png and reports/eda/eda_numbers.json. Uses the cleaned data in data/clean
(quality-flagged values already masked) and the site metadata in config/sites.yaml.
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import yaml  # noqa: E402

from dosentinel.features import do_saturation, load_site_frame  # noqa: E402

OUT = Path("reports/eda")
OUT.mkdir(parents=True, exist_ok=True)
CFG = yaml.safe_load(Path("config/sites.yaml").read_text())["sites"]
COORDS = json.loads(Path("data/site_coords.json").read_text())
SHORT = {s["id"]: s["id"].replace("USGS-", "")[-8:] for s in CFG}
SEASON = {12: "DJF", 1: "DJF", 2: "DJF", 3: "MAM", 4: "MAM", 5: "MAM", 6: "JJA", 7: "JJA", 8: "JJA",
          9: "SON", 10: "SON", 11: "SON"}
plt.rcParams.update({"figure.dpi": 130, "axes.spines.top": False, "axes.spines.right": False, "font.size": 9})
nums: dict = {}
frames = {s["id"]: load_site_frame(s["id"], "data/clean") for s in CFG}
ids = [s["id"] for s in CFG]


def label(sid: str) -> str:
    s = next(x for x in CFG if x["id"] == sid)
    return f"{SHORT[sid]}  {s['role']}"


# ---- 1. coverage over time and by parameter
months = pd.period_range("2021-10", "2026-10", freq="M")
cov = pd.DataFrame({sid: frames[sid]["do"].notna().groupby(frames[sid].index.tz_localize(None).to_period("M")).mean()
                    for sid in ids}).reindex(months).T
par = pd.DataFrame({sid: frames[sid][["do", "temp", "spcond", "turb", "q"]].notna().mean() for sid in ids}).T
fig, ax = plt.subplots(1, 2, figsize=(13, 4.8), gridspec_kw={"width_ratios": [3, 1]})
im = ax[0].imshow(cov.to_numpy(float), aspect="auto", cmap="viridis", vmin=0, vmax=1)
ax[0].set_yticks(range(len(ids)), [label(i) for i in ids])
ax[0].set_xticks(range(0, len(months), 6), [str(m) for m in months[::6]], rotation=45)
ax[0].set_title("Share of 15-minute DO readings present, by month")
im2 = ax[1].imshow(par.to_numpy(float), aspect="auto", cmap="viridis", vmin=0, vmax=1)
ax[1].set_xticks(range(5), ["DO", "water T", "cond.", "turb.", "disch."])
ax[1].set_yticks([])
ax[1].set_title("Overall completeness")
fig.colorbar(im2, ax=ax, shrink=0.8, label="fraction observed")
fig.savefig(OUT / "01_coverage.png", bbox_inches="tight")
plt.close(fig)
nums["coverage"] = {"median_do_observed": float(par["do"].median()), "min_do_observed": float(par["do"].min()),
                    "sites_missing_discharge": [s for s in ids if par.loc[s, "q"] < 0.05],
                    "sites_turbidity_below_50pct": [s for s in ids if par.loc[s, "turb"] < 0.5]}

# ---- 2. diel cycle by season, in local solar time
pick = [i for i in ("USGS-03293500", "USGS-02344630", "USGS-08065350", "USGS-01184000") if i in frames]
fig, axes = plt.subplots(1, len(pick), figsize=(14, 3.4), sharey=False)
amps = {}
for ax, sid in zip(np.atleast_1d(axes), pick):
    f = frames[sid]
    lon = COORDS[sid]["lon"]
    solar = (f.index.hour + f.index.minute / 60 + lon / 15) % 24
    d = pd.DataFrame({"do": f["do"].to_numpy(), "h": np.floor(solar).astype(int), "season": f.index.month.map(SEASON)})
    for season, col in zip(("DJF", "MAM", "JJA", "SON"), ("#1f77b4", "#2ca02c", "#d62728", "#ff7f0e")):
        g = d[d.season == season].groupby("h")["do"].median()
        ax.plot(g.index, g.values, label=season, color=col)
    ax.set_title(label(sid))
    ax.set_xlabel("local solar hour")
    amps[sid] = {sea: float(d[d.season == sea].groupby("h")["do"].median().agg(lambda s: s.max() - s.min()))
                 for sea in ("DJF", "MAM", "JJA", "SON")}
np.atleast_1d(axes)[0].set_ylabel("median DO (mg/L)")
hd, lb = np.atleast_1d(axes)[0].get_legend_handles_labels()
fig.legend(hd, lb, loc="lower center", ncol=4, frameon=False, bbox_to_anchor=(0.5, -0.08))
fig.suptitle("Diel cycle by season (median DO by local solar hour)", y=1.02)
fig.savefig(OUT / "02_diel_cycle.png", bbox_inches="tight")
plt.close(fig)
nums["diel_amplitude_mgL_by_season"] = amps

# ---- 3. when low-DO happens: share of days with a minimum below 5 mg/L, by calendar month
ev = {}
for sid in ids:
    dm = frames[sid]["do"].resample("1D").min().dropna()
    ev[sid] = (dm < 5).groupby(dm.index.month).mean().reindex(range(1, 13))
ev = pd.DataFrame(ev).T
fig, ax = plt.subplots(figsize=(9, 4.6))
im = ax.imshow(ev.to_numpy(float), aspect="auto", cmap="magma_r", vmin=0, vmax=max(0.5, float(np.nanmax(ev.to_numpy()))))
ax.set_yticks(range(len(ids)), [label(i) for i in ids])
ax.set_xticks(range(12), ["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"])
ax.set_title("Share of days whose minimum DO fell below 5 mg/L, by calendar month")
fig.colorbar(im, label="share of days")
fig.savefig(OUT / "03_low_do_by_month.png", bbox_inches="tight")
plt.close(fig)
ann = {sid: float((frames[sid]["do"].resample("1D").min().dropna() < 5).mean()) for sid in ids}
nums["low_do_day_share"] = ann
summer = ev[[6, 7, 8, 9]].mean(axis=1)
winter = ev[[12, 1, 2]].mean(axis=1)
nums["low_do_summer_vs_winter"] = {"summer_mean": float(summer.mean()), "winter_mean": float(winter.mean()),
                                   "sites_with_summer_gt_2x_winter": int(((summer > 2 * winter) & (summer > 0.02)).sum())}

# ---- 4. site contrast: basin size, flashiness, event rate
fig, ax = plt.subplots(figsize=(7.4, 5))
colors = {"flashy": "#d62728", "flashy_urban": "#d62728", "regulated": "#9467bd", "moderate": "#ff7f0e",
          "steady": "#2ca02c", "slow_river": "#2ca02c", "no_discharge": "#7f7f7f"}
for s in CFG:
    rb = s["history"].get("richards_baker_2y")
    x, y = np.log10(s["drainage_sqmi"]), (rb if rb is not None else -0.1)
    ax.scatter(x, y, s=60 + 900 * ann[s["id"]], color=colors.get(s["role"], "k"), alpha=0.75, edgecolor="k")
    k = ids.index(s["id"])
    ax.annotate(SHORT[s["id"]], (x, y), textcoords="offset points", xytext=(6, 5 if k % 2 == 0 else -11), fontsize=7)
ax.axhline(0, color="#ccc", lw=0.8)
ax.set_xlabel("log10 drainage area (sq mi)")
ax.set_ylabel("flow flashiness (Richards-Baker, 2 y); -0.1 = no discharge series")
ax.set_title("The 14 sites: basin size, flashiness, and low-DO frequency (bubble size)")
fig.savefig(OUT / "04_site_contrast.png", bbox_inches="tight")
plt.close(fig)

# ---- 5. a storm at a flashy site: DO against discharge
sid = "USGS-03293500"
f = frames[sid].iloc[-96 * 365:]
q = f["q"]
peak = q.rolling(96).max().idxmax() if q.notna().any() else f.index[len(f) // 2]
w = frames[sid].loc[peak - pd.Timedelta(days=4): peak + pd.Timedelta(days=5)]
fig, ax = plt.subplots(2, 1, figsize=(10, 5), sharex=True)
ax[0].plot(w.index, w["do"], color="#1f77b4")
ax[0].axhline(5, color="red", ls="--", lw=1)
ax[0].set_ylabel("DO (mg/L)")
ax[1].plot(w.index, w["q"], color="#555")
ax[1].set_yscale("log")
ax[1].set_ylabel("discharge (cfs)")
fig.suptitle(f"Storm response at {label(sid)}: DO and discharge around the year's largest rise")
fig.savefig(OUT / "05_storm_example.png", bbox_inches="tight")
plt.close(fig)
d1 = frames[sid]["do"].diff(4)
ql = np.log1p(frames[sid]["q"]).diff(4)
nums["storm_example"] = {"site": sid, "corr_do_change_vs_log_discharge_change_1h": float(d1.corr(ql))}

# ---- 6. memory and resolution
fig, ax = plt.subplots(1, 2, figsize=(12, 3.8))
lags = np.arange(1, 4 * 24 + 1)
acf24 = {}
for sid in pick:
    h = frames[sid]["do"][frames[sid].index.minute == 0].iloc[-24 * 730:]
    a = [h.autocorr(int(l)) for l in lags]
    ax[0].plot(lags, a, label=label(sid))
    acf24[sid] = float(a[23])
ax[0].set_xlabel("lag (hours)")
ax[0].set_ylabel("autocorrelation of DO")
ax[0].axvline(24, color="#999", ls=":")
ax[0].legend(frameon=False, fontsize=7)
ax[0].set_title("DO remembers the same time of day")
steps = pd.concat([frames[s]["do"].diff().abs().dropna() for s in ids])
share = {"0.0": float((steps == 0).mean()), "0.1": float(((steps > 0) & (steps <= 0.1)).mean()),
         "0.2-0.5": float(((steps > 0.1) & (steps <= 0.5)).mean()), ">0.5": float((steps > 0.5).mean())}
ax[1].bar(list(share), list(share.values()), color="#1f77b4")
ax[1].set_title("Size of 15-minute changes in DO (mg/L): readings are quantized")
ax[1].set_ylabel("share of steps")
fig.savefig(OUT / "06_memory_and_resolution.png", bbox_inches="tight")
plt.close(fig)
nums["autocorr_at_24h"] = acf24
nums["step_size_share"] = share

# ---- 7. percent saturation
sat = {}
for sid in ids:
    f = frames[sid]
    p = (f["do"] / do_saturation(f["temp"])).dropna()
    sat[sid] = p.to_numpy()[::8]
fig, ax = plt.subplots(figsize=(9, 4))
ax.boxplot([sat[s] * 100 for s in ids], tick_labels=[SHORT[s] for s in ids], showfliers=False)
ax.axhline(100, color="#999", ls=":")
ax.set_ylabel("DO as % of saturation (at sea level)")
ax.set_title("How far each site sits from saturation (high-altitude sites read lower by construction)")
plt.setp(ax.get_xticklabels(), rotation=45, ha="right")
fig.savefig(OUT / "07_saturation.png", bbox_inches="tight")
plt.close(fig)
nums["pct_saturation_median"] = {s: float(np.median(sat[s]) * 100) for s in ids}

(OUT / "eda_numbers.json").write_text(json.dumps(nums, indent=1))
print(json.dumps(nums, indent=1))
