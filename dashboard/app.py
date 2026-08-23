"""
Cross-Asset Anomaly Monitor — Streamlit Dashboard.
"""

import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st

# ---------------------------------------------------------------------------
# Page configuration — MUST be the first Streamlit command
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Cross-Asset Anomaly Monitor",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ---------------------------------------------------------------------------
# Backend URL resolution
# ---------------------------------------------------------------------------

def _resolve_backend_url() -> Optional[str]:
    try:
        url = st.secrets.get("BACKEND_URL")
        if url:
            return url.rstrip("/")
    except Exception:
        pass
    return os.environ.get("BACKEND_URL", "").rstrip("/") or None


BACKEND_URL = _resolve_backend_url()
USING_MOCK = BACKEND_URL is None

# Anomaly-timeline window options: label -> trailing minutes (None = last N rows)
CHART_WINDOWS = {"1H": 60, "6H": 360, "24H": 1440, "7D": 7 * 1440, "ALL": None}
DEFAULT_WINDOW = "24H"

# ---------------------------------------------------------------------------
# Data fetching with mock fallback
# ---------------------------------------------------------------------------

def _fetch(path: str, params: dict = None) -> Optional[Any]:
    if not BACKEND_URL:
        return None
    try:
        resp = requests.get(f"{BACKEND_URL}{path}", params=params, timeout=8)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


def _dataset_params(dataset: str, params: dict = None) -> dict:
    params = dict(params or {})
    params["dataset"] = dataset
    return params


def fetch_health() -> dict:
    data = _fetch("/health")
    if data:
        return data
    from mock_data import get_health
    return get_health()


def fetch_assets(dataset: str = "live") -> list[dict]:
    data = _fetch("/assets", _dataset_params(dataset))
    if data is not None:
        return data
    from mock_data import get_assets
    return get_assets()


def fetch_anomalies(dataset: str = "live", limit: int = 50, min_score: float = 0.0) -> list[dict]:
    data = _fetch("/anomalies", _dataset_params(dataset, {"limit": limit, "min_score": min_score}))
    if data is not None:
        return data
    from mock_data import get_anomalies
    return get_anomalies(limit=limit, min_score=min_score)


def fetch_correlations(dataset: str = "live") -> dict:
    data = _fetch("/correlations", _dataset_params(dataset))
    if data is not None:
        return data
    from mock_data import get_correlations
    return get_correlations()


def fetch_chart(symbol: str, dataset: str = "live", limit: int = 100, window: Optional[int] = None) -> list[dict]:
    params = {"limit": limit}
    if window is not None:
        params["window_minutes"] = window
    data = _fetch(f"/chart/{symbol}", _dataset_params(dataset, params))
    if data is not None:
        return data
    from mock_data import get_chart
    return get_chart(symbol, limit=limit)


def fetch_meta(dataset: str = "live") -> dict:
    data = _fetch("/meta", _dataset_params(dataset))
    if data:
        return data
    # Mock mode has no seeded data
    return {"server_time": datetime.now(timezone.utc).isoformat(), "demo_data_end": None}


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _fmt_pct(val: Optional[float]) -> str:
    if val is None:
        return "—"
    return f"{val * 100:+.4f}%"


def _fmt_score(val: float) -> str:
    return f"{val:.3f}"


def _risk_badge(level: str) -> str:
    return {"high": "🔴 High", "medium": "🟡 Medium", "low": "🟢 Low"}.get(level, level)


def _signals_str(z: bool, ewma: bool, pca: bool) -> str:
    parts = []
    if z:
        parts.append("Z")
    if ewma:
        parts.append("EWMA")
    if pca:
        parts.append("PCA")
    return " | ".join(parts) if parts else "—"


def _ts_display(iso: Optional[str]) -> str:
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return iso


def _fmt_age(seconds: Optional[float]) -> str:
    """Compact human age: 42s / 18m / 3h / 2d."""
    if seconds is None or seconds < 0:
        return "—"
    s = int(seconds)
    if s < 90:
        return f"{s}s"
    if s < 5400:
        return f"{s // 60}m"
    if s < 172_800:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


def _freshness_status(age_seconds: Optional[float]) -> str:
    """Classify data freshness.

    Thresholds reflect the data sources: crypto/FX ticks arrive within
    seconds; equities, futures, and yields are delayed ~10-20 minutes at
    the source, so anything under 45 minutes is normal for them.
    """
    if age_seconds is None:
        return "—", "unknown"
    if age_seconds < 150:
        return "live", "live"
    if age_seconds < 45 * 60:
        return "delayed", "delayed"
    return "stale", "stale"


# ---------------------------------------------------------------------------
# Custom CSS
# ---------------------------------------------------------------------------

st.markdown("""
<style>
    .app-header {
        padding: 1.2rem 0 0.6rem 0;
        border-bottom: 1px solid rgba(74, 222, 128, 0.2);
        margin-bottom: 1.2rem;
    }
    .app-header h1 {
        margin: 0 0 0.15rem 0;
        font-size: 1.75rem;
        font-weight: 700;
        letter-spacing: -0.02em;
    }
    .app-header p {
        margin: 0;
        color: rgba(255,255,255,0.5);
        font-size: 0.85rem;
    }
    .data-source-badge {
        display: inline-block;
        padding: 0.15rem 0.55rem;
        border-radius: 4px;
        font-size: 0.7rem;
        font-weight: 600;
        letter-spacing: 0.03em;
        text-transform: uppercase;
        vertical-align: middle;
        margin-left: 0.5rem;
    }
    .badge-live {
        background: rgba(74, 222, 128, 0.15);
        color: #4ade80;
        border: 1px solid rgba(74, 222, 128, 0.3);
    }
    .badge-mock {
        background: rgba(251, 191, 36, 0.15);
        color: #fbbf24;
        border: 1px solid rgba(251, 191, 36, 0.3);
    }
    .demo-banner {
        background: rgba(251, 191, 36, 0.12);
        border: 1px solid rgba(251, 191, 36, 0.45);
        color: #fbbf24;
        border-radius: 6px;
        padding: 0.55rem 1rem;
        font-size: 0.9rem;
        font-weight: 600;
        text-align: center;
        margin-bottom: 1rem;
    }
    [data-testid="stMetricValue"] {
        font-size: 1.6rem !important;
    }
    [data-testid="stMetricLabel"] {
        font-size: 0.78rem !important;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        color: rgba(255,255,255,0.55) !important;
    }
    .section-header {
        font-size: 0.95rem;
        font-weight: 600;
        color: rgba(255,255,255,0.75);
        margin: 1.2rem 0 0.5rem 0;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }
    .stDataFrame {
        border-radius: 8px;
    }
    .block-container {
        padding-top: 2rem;
        padding-bottom: 2rem;
    }
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

@st.cache_data(ttl=30)
def _load_assets(dataset: str):
    return fetch_assets(dataset)


@st.cache_data(ttl=30)
def _load_anomalies(dataset: str):
    return fetch_anomalies(dataset, limit=50, min_score=0.0)


@st.cache_data(ttl=60)
def _load_correlations(dataset: str):
    return fetch_correlations(dataset)


@st.cache_data(ttl=300)
def _load_meta(dataset: str):
    return fetch_meta(dataset)


@st.cache_data(ttl=30)
def _load_chart(symbol: str, window_label: str, dataset: str):
    window = CHART_WINDOWS.get(window_label)
    if window is not None:
        return fetch_chart(symbol, dataset=dataset, limit=2000, window=window)
    return fetch_chart(symbol, dataset=dataset, limit=2000)


# ---------------------------------------------------------------------------
# 1. Header + dataset mode
# ---------------------------------------------------------------------------

refresh_time = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
badge_class = "badge-live" if not USING_MOCK else "badge-mock"
badge_text = "LIVE" if not USING_MOCK else "DEMO"

header_col, mode_col = st.columns([5, 1])
with header_col:
    st.markdown(
        f"""
        <div class="app-header">
            <h1>
                📊 Cross-Asset Anomaly Monitor
                <span class="data-source-badge {badge_class}">{badge_text}</span>
            </h1>
            <p>Real-time anomaly detection across equities, crypto, commodities, FX & rates &nbsp;·&nbsp; Last refresh: {refresh_time}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
with mode_col:
    data_mode = st.radio(
        "Dataset",
        options=["LIVE", "DEMO"],
        horizontal=True,
        label_visibility="collapsed",
    )

DATASET = data_mode.lower()
DEMO_MODE = DATASET == "demo"

if DEMO_MODE:
    st.markdown(
        '<div class="demo-banner">⚠️ DEMO MODE — SYNTHETIC DATA · '
        'Prices and anomalies are seeded demo values, NOT real market data</div>',
        unsafe_allow_html=True,
    )

# ---------------------------------------------------------------------------
# 2. Top metrics row
# ---------------------------------------------------------------------------

assets = _load_assets(DATASET)
anomalies = _load_anomalies(DATASET)

total_assets = len(assets)
active_anomalies = sum(1 for a in anomalies if a["anomaly_score"] > 0.5)
highest_risk_asset = "—"
max_risk_score = 0.0
for a in assets:
    if a.get("composite_score", 0) > max_risk_score:
        max_risk_score = a["composite_score"]
        highest_risk_asset = a["symbol"]
max_anomaly_score = max((a["anomaly_score"] for a in anomalies), default=0.0)

col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("Assets Monitored", total_assets)
with col2:
    st.metric("Active Anomalies", active_anomalies)
with col3:
    st.metric("Highest Risk Asset", highest_risk_asset)
with col4:
    st.metric("Max Anomaly Score", f"{max_anomaly_score:.3f}")

# ---------------------------------------------------------------------------
# 3. Asset status table
# ---------------------------------------------------------------------------

st.markdown('<div class="section-header">Asset Status</div>', unsafe_allow_html=True)

if assets:
    meta = _load_meta(DATASET)
    try:
        now_ref = datetime.fromisoformat(meta["server_time"])
    except Exception:
        now_ref = datetime.now(timezone.utc)

    table_data = []
    for a in assets:
        if DEMO_MODE:
            # Static seeded dataset — age/status are meaningless
            freshness = "static"
        else:
            age_s = None
            if a.get("timestamp"):
                try:
                    ts = a["timestamp"]
                    if isinstance(ts, str):
                        ts = datetime.fromisoformat(ts)
                    age_s = (now_ref - ts).total_seconds()
                except Exception:
                    age_s = None
            age_str, status = _freshness_status(age_s)
            freshness = f"{_fmt_age(age_s)} · {status}"
        table_data.append({
            "Symbol": a["symbol"],
            "Price": a.get("price"),
            "1m Return": _fmt_pct(a.get("return_1m")),
            "Z-Score": a.get("z_score"),
            "EWMA Vol": a.get("ewma_vol"),
            "Freshness": freshness,
            "Risk Level": _risk_badge(a.get("risk_level", "low")),
        })
    df = pd.DataFrame(table_data)

    def _color_risk(val: str):
        if "High" in val:
            return "color: #f87171; font-weight: 600"
        if "Medium" in val:
            return "color: #fbbf24; font-weight: 600"
        if "Low" in val:
            return "color: #4ade80"
        return ""

    def _color_zscore(val):
        if val is None or pd.isna(val):
            return ""
        v = float(val)
        if abs(v) > 2.5:
            return "color: #f87171; font-weight: 600"
        if abs(v) > 1.5:
            return "color: #fbbf24"
        return ""

    def _color_freshness(val: str):
        if val.endswith("live"):
            return "color: #4ade80"
        if val.endswith("delayed"):
            return "color: #fbbf24"
        if val.endswith("stale"):
            return "color: #f87171"
        return ""

    styled = df.style.map(_color_risk, subset=["Risk Level"])
    styled = styled.map(_color_zscore, subset=["Z-Score"])
    styled = styled.map(_color_freshness, subset=["Freshness"])
    styled = styled.format({
        "Price": "{:,.4f}",
        "EWMA Vol": "{:.8f}",
        "Z-Score": "{:.4f}",
    })
    styled = styled.hide(axis="index")
    st.dataframe(styled, use_container_width=True, height=340)
else:
    st.info("No asset data available.")

# ---------------------------------------------------------------------------
# 4. Correlation heatmap
# ---------------------------------------------------------------------------

st.markdown('<div class="section-header">Cross-Asset Correlations</div>', unsafe_allow_html=True)

corr_data = _load_correlations(DATASET)

if corr_data and corr_data.get("labels"):
    labels = corr_data["labels"]
    matrix = np.array(corr_data["matrix"])

    short_labels = {
        "^GSPC": "S&P 500",
        "^IXIC": "Nasdaq",
        "BTC-USD": "BTC",
        "GC=F": "Gold",
        "CL=F": "Oil",
        "EURUSD=X": "EUR/USD",
        "^TNX": "10Y Yield",
    }
    display_labels = [short_labels.get(l, l) for l in labels]

    fig = px.imshow(
        matrix,
        x=display_labels,
        y=display_labels,
        color_continuous_scale="RdBu_r",
        zmin=-1,
        zmax=1,
        aspect="auto",
        text_auto=".2f",
    )
    fig.update_traces(
        textfont=dict(size=11, color="white"),
        hovertemplate="%{x} ↔ %{y}<br>Correlation: %{z:.4f}<extra></extra>",
    )
    fig.update_layout(
        title=dict(
            text="Latest Correlation Snapshot",
            font=dict(size=14, color="rgba(255,255,255,0.8)"),
            x=0.0,
            xanchor="left",
            pad=dict(b=10),
        ),
        margin=dict(l=80, r=30, t=40, b=10),
        height=420,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(tickfont=dict(size=11)),
        yaxis=dict(tickfont=dict(size=11)),
        coloraxis_colorbar=dict(
            title=dict(text="Correlation", font=dict(size=11)),
            tickfont=dict(size=10),
            thickness=15,
            len=0.85,
        ),
    )
    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("No correlation data available.")

# ---------------------------------------------------------------------------
# 5. Anomaly timeline
# ---------------------------------------------------------------------------

st.markdown('<div class="section-header">Anomaly Timeline</div>', unsafe_allow_html=True)

chart_col1, chart_col2, chart_col3 = st.columns([1, 1, 3])
with chart_col1:
    chart_symbol = st.selectbox(
        "Symbol",
        options=[a["symbol"] for a in assets] if assets else ["^GSPC"],
        label_visibility="collapsed",
    )
with chart_col2:
    default_window = "ALL" if DEMO_MODE else DEFAULT_WINDOW
    chart_window = st.selectbox(
        "Window",
        options=list(CHART_WINDOWS.keys()),
        index=list(CHART_WINDOWS.keys()).index(default_window),
        label_visibility="collapsed",
    )
with chart_col3:
    st.write("")

chart_data = _load_chart(chart_symbol, chart_window, DATASET)

if chart_data:
    df_chart = pd.DataFrame(chart_data)
    # Mixed ISO variants (with/without fractional seconds, Z/+00:00) —
    # pandas 2.x strict parsing needs the explicit ISO8601 format
    df_chart["timestamp"] = pd.to_datetime(df_chart["timestamp"], format="ISO8601", utc=True)

    fig_chart = go.Figure()

    fig_chart.add_trace(go.Scatter(
        x=df_chart["timestamp"],
        y=df_chart["price"],
        name="Price",
        line=dict(color="rgba(148, 163, 184, 0.6)", width=1.5),
        yaxis="y2",
        hovertemplate="%{y:,.4f}<extra></extra>",
    ))

    fig_chart.add_trace(go.Scatter(
        x=df_chart["timestamp"],
        y=df_chart["composite_score"],
        name="Anomaly Score",
        line=dict(color="#4ade80", width=2),
        fill="tozeroy",
        fillcolor="rgba(74, 222, 128, 0.08)",
        hovertemplate="Score: %{y:.3f}<extra></extra>",
    ))

    fig_chart.add_hline(
        y=0.5,
        line_dash="dash",
        line_color="#ef4444",
        line_width=1.5,
        annotation_text="Alert Threshold (0.5)",
        annotation_position="top left",
        annotation_font=dict(size=10, color="#ef4444"),
        annotation_bgcolor="rgba(0,0,0,0.5)",
        annotation_bordercolor="#ef4444",
    )

    above = df_chart[df_chart["composite_score"] >= 0.5]
    if not above.empty:
        fig_chart.add_trace(go.Scatter(
            x=above["timestamp"],
            y=above["composite_score"],
            name="Above Threshold",
            mode="markers",
            marker=dict(color="#ef4444", size=6, symbol="circle"),
            hovertemplate="⚠️ Score: %{y:.3f}<extra></extra>",
            showlegend=False,
        ))

    # Shade the seeded-demo portion when the visible window spans the
    # demo/live boundary reported by the API
    try:
        meta = _load_meta(DATASET)
        demo_end = meta.get("demo_data_end")
        if isinstance(demo_end, str):
            demo_end = datetime.fromisoformat(demo_end)
        visible_start = df_chart["timestamp"].min()
        if demo_end is not None and pd.notna(visible_start) and visible_start < demo_end:
            fig_chart.add_vrect(
                x0=visible_start,
                x1=demo_end,
                fillcolor="rgba(148, 163, 184, 0.10)",
                line_width=0,
                annotation_text="seeded demo data",
                annotation_position="top left",
                annotation_font=dict(size=10, color="rgba(255,255,255,0.45)"),
            )
    except Exception:
        pass  # shading is cosmetic — never break the chart over it

    fig_chart.update_layout(
        title=dict(
            text=f"{chart_symbol} — Anomaly Score & Price ({chart_window})",
            font=dict(size=14, color="rgba(255,255,255,0.8)"),
            x=0.0,
            xanchor="left",
        ),
        margin=dict(l=60, r=60, t=40, b=30),
        height=380,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
            font=dict(size=11),
        ),
        xaxis=dict(
            gridcolor="rgba(255,255,255,0.06)",
            tickfont=dict(size=10),
        ),
        yaxis=dict(
            title=dict(text="Anomaly Score", font=dict(size=11)),
            range=[0, 1.05],
            gridcolor="rgba(255,255,255,0.06)",
            tickfont=dict(size=10),
        ),
        yaxis2=dict(
            title=dict(text="Price", font=dict(size=11)),
            overlaying="y",
            side="right",
            gridcolor="rgba(0,0,0,0)",
            tickfont=dict(size=10),
            showgrid=False,
        ),
        hovermode="x unified",
    )
    st.plotly_chart(fig_chart, use_container_width=True)
else:
    st.info(f"No chart data available for {chart_symbol}.")

# ---------------------------------------------------------------------------
# 6. Recent alerts table
# ---------------------------------------------------------------------------

st.markdown('<div class="section-header">Recent Alerts</div>', unsafe_allow_html=True)

if anomalies:
    alert_rows = []
    for a in anomalies[:20]:
        alert_rows.append({
            "Time": _ts_display(a.get("timestamp")),
            "Symbol": a["symbol"],
            "Score": a["anomaly_score"],
            "Signals": _signals_str(
                a.get("z_flag", False),
                a.get("ewma_flag", False),
                a.get("pca_flag", False),
            ),
            "Description": (a.get("description") or "")[:120],
        })
    df_alerts = pd.DataFrame(alert_rows)

    def _color_score_cell(val):
        if pd.isna(val):
            return ""
        v = float(val)
        if v >= 0.7:
            return "color: #f87171; font-weight: 700"
        if v >= 0.5:
            return "color: #fb923c; font-weight: 600"
        if v >= 0.3:
            return "color: #fbbf24; font-weight: 500"
        return "color: #4ade80"

    styled_alerts = df_alerts.style.map(_color_score_cell, subset=["Score"])
    styled_alerts = styled_alerts.format({"Score": "{:.3f}"})
    styled_alerts = styled_alerts.hide(axis="index")

    st.dataframe(styled_alerts, use_container_width=True, height=440)
else:
    st.info("No anomaly alerts in the selected window.")

# ---------------------------------------------------------------------------
# Auto-refresh countdown
# ---------------------------------------------------------------------------

REFRESH_SECONDS = 60
refresh_placeholder = st.empty()

for remaining in range(REFRESH_SECONDS, 0, -1):
    refresh_placeholder.caption(f"⏱️  Next refresh in {remaining}s")
    time.sleep(1)

st.rerun()