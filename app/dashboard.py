"""Streamlit dashboard: replay a forecast at any past time and see what actually happened.

    streamlit run app/dashboard.py

Uses the trained bundles in models/ and the cleaned data in data/clean/, so it works offline.
Forecasts at times after the model's training cutoff are genuinely out-of-sample; earlier times
are marked as in-training. The Results tab shows the project's evaluation, including what did
not work.
"""
from __future__ import annotations

import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dosentinel.features import load_site_frame  # noqa: E402
from dosentinel.monitoring import input_health  # noqa: E402
from dosentinel.serving import forecast  # noqa: E402

st.set_page_config(page_title="do-sentinel", layout="wide")


@st.cache_data
def sites() -> dict:
    cfg = yaml.safe_load((ROOT / "config" / "sites.yaml").read_text())
    return {s["id"]: s for s in cfg["sites"]}


@st.cache_resource
def bundle(site_id: str):
    return joblib.load(ROOT / "models" / f"{site_id}.joblib")


@st.cache_data
def frame(site_id: str) -> pd.DataFrame:
    return load_site_frame(site_id, str(ROOT / "data" / "clean"))


def forecast_figure(f: pd.DataFrame, asof: pd.Timestamp, rep: dict, hours_back: int = 72, hours_fwd: int = 26):
    hist = f["do"].loc[asof - pd.Timedelta(hours=hours_back): asof]
    fut = f["do"].loc[asof: asof + pd.Timedelta(hours=hours_fwd)]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=hist.index, y=hist, name="observed (history)", line=dict(color="#1f77b4")))
    fig.add_trace(go.Scatter(x=fut.index, y=fut, name="what actually happened", line=dict(color="#555", dash="dot")))
    if rep.get("forecasts"):
        t = [pd.Timestamp(x["target_time"]) for x in rep["forecasts"]]
        lo = [x["low_mgL"] for x in rep["forecasts"]]
        hi = [x["high_mgL"] for x in rep["forecasts"]]
        med = [x["median_mgL"] for x in rep["forecasts"]]
        fig.add_trace(go.Scatter(x=t + t[::-1], y=hi + lo[::-1], fill="toself", name="80% interval",
                                 line=dict(color="rgba(255,127,14,0)"), fillcolor="rgba(255,127,14,0.25)"))
        fig.add_trace(go.Scatter(x=[asof] + t, y=[rep["latest_do_mgL"]] + med, name="forecast (median)",
                                 mode="lines+markers", line=dict(color="#ff7f0e")))
    fig.add_hline(y=rep.get("threshold_mgL", 5.0), line_dash="dash", line_color="red",
                  annotation_text="low-DO threshold")
    fig.add_vline(x=asof, line_color="#999")
    fig.update_layout(height=420, margin=dict(l=10, r=10, t=30, b=10), yaxis_title="dissolved oxygen (mg/L)",
                      legend=dict(orientation="h", y=-0.2))
    return fig


st.title("do-sentinel: dissolved-oxygen forecast and alert triage")
tab_replay, tab_results, tab_about = st.tabs(["Forecast replay", "Evaluation results", "About and limits"])

with tab_replay:
    S = sites()
    c1, c2 = st.columns([1, 2])
    site = c1.selectbox("Site", list(S), format_func=lambda k: f"{k}: {S[k]['name'][:40]} ({S[k]['role']})")
    f = frame(site)
    b = bundle(site)
    lo_t, hi_t = f.index[96 * 20], f.index[-96 * 2]
    asof = c2.slider("Forecast issued at (UTC)", min_value=lo_t.to_pydatetime(), max_value=hi_t.to_pydatetime(),
                     value=(hi_t - pd.Timedelta(days=30)).to_pydatetime(), step=pd.Timedelta(hours=1).to_pytimedelta())
    asof = pd.Timestamp(asof).tz_convert("UTC") if pd.Timestamp(asof).tzinfo else pd.Timestamp(asof, tz="UTC")
    asof = f.index[f.index.get_indexer([asof], method="nearest")[0]]
    ratio = st.sidebar.slider("Cost of a missed event relative to a false alert", 1, 50, 10)
    rep = forecast(b, f.loc[:asof].iloc[-96 * 40:], cost_ratio=float(ratio))
    in_train = asof <= b.trained_through
    c1.metric("DO at issue time", f"{rep['latest_do_mgL']:.1f} mg/L" if rep["latest_do_mgL"] is not None else "missing")
    c1.metric("P(DO < 5 mg/L within 24 h)", f"{rep['p_low_do_within_24h']:.0%}" if rep["p_low_do_within_24h"] is not None else "n/a",
              help=f"alert cutoff {rep['alert_cutoff']:.0%} at cost ratio {ratio}:1")
    color = {"warning": "red", "maintenance": "orange", "none": "green"}[rep["action"]]
    c1.markdown(f"**Action:** :{color}[{rep['action'].upper()}]  \n{rep['reason']}")
    c1.caption("Sensor flags: " + ", ".join(k for k, v in rep["fault_flags"].items() if v and k != "any") or "none")
    if in_train:
        c1.warning("This time lies inside the model's training period, so it is not an honest test.")
    else:
        c1.success("Out-of-sample: after the model's training cutoff.")
    st.plotly_chart(forecast_figure(f, asof, rep), width="stretch")
    h = input_health(f.loc[:asof])
    st.caption("Input completeness, last 24 h: " + ", ".join(f"{k} {v['observed_24h']:.0%}" for k, v in h["parameters"].items()))

with tab_results:
    R = ROOT / "results"
    st.subheader("Forecast error vs simple baselines (14 sites, last year)")
    p = R / "improve_weekly.csv"
    if p.exists():
        w = pd.read_csv(p)
        base = ["persistence", "seasonal_naive", "seasonal_mean_7d", "persistence_diel_delta"]
        rows = []
        for hh, g in w.groupby("horizon_h"):
            n = g["n"].sum()
            best = min(base, key=lambda m: g[m].sum())
            rows.append({"horizon (h)": hh, "best simple baseline MAE": g[best].sum() / n,
                         "our model MAE": g["v1a_local_sat"].sum() / n,
                         "improvement": 1 - g["v1a_local_sat"].sum() / g[best].sum()})
        st.dataframe(pd.DataFrame(rows).style.format({"best simple baseline MAE": "{:.3f}", "our model MAE": "{:.3f}",
                                                       "improvement": "{:.1%}"}), hide_index=True)
    st.subheader("Interval calibration (target 80%)")
    p = R / "decision_calibration_cal365.csv"
    if p.exists():
        c = pd.read_csv(p).groupby("horizon_h")[["coverage_raw", "coverage_conformal"]].mean().reset_index()
        fig = go.Figure()
        fig.add_bar(x=c["horizon_h"], y=c["coverage_raw"], name="raw")
        fig.add_bar(x=c["horizon_h"], y=c["coverage_conformal"], name="after conformal calibration")
        fig.add_hline(y=0.8, line_dash="dash")
        fig.update_layout(height=320, yaxis_range=[0.5, 0.9], xaxis_title="horizon (h)", yaxis_title="coverage")
        st.plotly_chart(fig, width="stretch")
    st.subheader("Weather and a pretrained model")
    st.markdown("""
* **Weather** lowers 24 h error by about 8.6%, mostly through sunlight and temperature, not rain.
* **A pretrained foundation model (Chronos-2)** blended with the gradient boosting improves on it by
  only 0.02-0.04 mg/L, below the sensor's 0.1 mg/L resolution, so it is not part of the product.
""")
    st.subheader("What did not work")
    st.markdown("""
* **Alerts from the forecast** are competitive with, not better than, a simple 'DO below 5.5' rule,
  except at very low alert volumes.
* **Slow sensor drift** cannot be detected from one sensor: the apparent detection was chance.
""")

with tab_about:
    st.markdown((ROOT / "MODEL_CARD.md").read_text() if (ROOT / "MODEL_CARD.md").exists() else "See README.")
