# -*- coding: utf-8 -*-
"""
Market Dashboard (Streamlit)
"""

import os
import logging
import warnings
from datetime import datetime, timezone
from typing import Optional, Dict, Any

import pytz
import pandas as pd
import yfinance as yf
import requests

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.font_manager as fm

import streamlit as st
import streamlit.components.v1 as components
import urllib.parse

# =====================
# Page config
# =====================
st.set_page_config(page_title="Market Dashboard", layout="wide")

# =====================
# Google Analytics
# =====================
GA_MEASUREMENT_ID = os.getenv("GA_MEASUREMENT_ID", "G-XXXXXXXXXX")

def inject_ga():
    components.html(
        f"""
        <script async src="https://www.googletagmanager.com/gtag/js?id={GA_MEASUREMENT_ID}"></script>
        <script>
          window.dataLayer = window.dataLayer || [];
          function gtag(){{dataLayer.push(arguments);}}
          gtag('js', new Date());
          gtag('config', '{GA_MEASUREMENT_ID}');
        </script>
        """,
        height=0,
        width=0,
    )

inject_ga()

# =====================
# Yahoo Finance URL
# =====================
def yahoo_chart_url(symbol: str, market: str = "US") -> str:
    if market == "US":
        return "https://finance.yahoo.com/chart/" + urllib.parse.quote(symbol, safe="-=^.")
    else:
        return "https://finance.yahoo.co.jp/quote/" + urllib.parse.quote(symbol, safe="-=^.")

# =====================
# Basic settings
# =====================
JST = pytz.timezone("Asia/Tokyo")

logging.getLogger("yfinance").setLevel(logging.CRITICAL)
warnings.filterwarnings("ignore")

# =====================
# Colors
# =====================
GREEN = "#1a7f37"
RED = "#d1242f"
BG_UP = "rgba(26,127,55,0.08)"
BG_DN = "rgba(209,36,47,0.08)"
LINE_NEUTRAL = "#1f77b4"

# =====================
# Markets
# =====================
MARKETS = {
    "Magnificent 7": [
        {"name": "Apple", "symbol": "AAPL", "flag": "US"},
        {"name": "Microsoft", "symbol": "MSFT", "flag": "US"},
        {"name": "Alphabet", "symbol": "GOOGL", "flag": "US"},
        {"name": "Amazon", "symbol": "AMZN", "flag": "US"},
        {"name": "NVIDIA", "symbol": "NVDA", "flag": "US"},
        {"name": "Meta", "symbol": "META", "flag": "US"},
        {"name": "Tesla", "symbol": "TSLA", "flag": "US"},
    ],
}

# =====================
# Data fetch
# =====================
@st.cache_data(ttl=180)
def fetch_intraday(symbol: str) -> pd.DataFrame:
    for interval in ("1m", "2m", "5m", "15m"):
        try:
            df = yf.Ticker(symbol).history(period="1d", interval=interval)
            if not df.empty:
                df.index = df.index.tz_localize("UTC").tz_convert(JST)
                df.attrs["interval"] = interval
                return df
        except Exception:
            pass
    return pd.DataFrame()

@st.cache_data(ttl=180)
def fetch_daily(symbol: str) -> pd.DataFrame:
    df = yf.Ticker(symbol).history(period="30d", interval="1d")
    if not df.empty:
        df.index = df.index.tz_localize("UTC").tz_convert(JST)
    return df

# =====================
# Card calculation
# =====================
def compute_card(symbol: str) -> Dict[str, Any]:
    intra = fetch_intraday(symbol)
    if not intra.empty:
        now = intra["Close"].iloc[-1]
        base = intra["Open"].iloc[0]
        pct = (now / base - 1) * 100
        return {
            "ok": True,
            "mode": "INTRADAY",
            "now": now,
            "base": base,
            "pct": pct,
            "chg": now - base,
            "series": intra["Close"].tail(60),
        }

    daily = fetch_daily(symbol)
    if len(daily) >= 2:
        now = daily["Close"].iloc[-1]
        prev = daily["Close"].iloc[-2]
        pct = (now / prev - 1) * 100
        return {
            "ok": True,
            "mode": "CLOSE",
            "now": now,
            "base": prev,
            "pct": pct,
            "chg": now - prev,
            "series": daily["Close"].tail(30),
        }

    return {"ok": False}

# =====================
# Sparkline
# =====================
def make_sparkline(series, base, up):
    fig, ax = plt.subplots(figsize=(5.6, 1.6))
    ax.plot(series.index, series.values, color=LINE_NEUTRAL)
    ax.axhline(base, color="black", alpha=0.4)
    ax.fill_between(series.index, series.values, base,
                    where=(series.values >= base), color=GREEN, alpha=0.15)
    ax.fill_between(series.index, series.values, base,
                    where=(series.values < base), color=RED, alpha=0.15)
    ax.axis("off")
    return fig

# =====================
# Render row
# =====================
def render_market_row(items, cols=4):
    columns = st.columns(cols)

    for i, it in enumerate(items):
        with columns[i % cols]:
            data = compute_card(it["symbol"])
            if not data["ok"]:
                st.warning("データ取得不可")
                continue

            up = data["pct"] >= 0
            bg = BG_UP if up else BG_DN
            color = GREEN if up else RED

            st.markdown(
                f"""
                <div style="background:{bg}; padding:10px; border-radius:10px;">
                  <b>{it["name"]}</b> ({it["symbol"]})<br>
                  <span style="font-size:24px; color:{color};">
                    {data["pct"]:+.2f}%
                  </span><br>
                  Now: {data["now"]:.2f}
                </div>
                """,
                unsafe_allow_html=True,
            )

            # Yahoo Finance link
            url = yahoo_chart_url(it["symbol"], "US")
            st.markdown(f"[📈 Yahoo Financeで開く]({url})")

            fig = make_sparkline(data["series"], data["base"], up)
            st.pyplot(fig, clear_figure=True)

# =====================
# Main
# =====================
def main():
    st.title("Market Dashboard")
    st.caption(f"JST: {datetime.now(JST):%Y-%m-%d %H:%M:%S}")

    for title, items in MARKETS.items():
        st.subheader(title)
        render_market_row(items, cols=4)
        st.divider()

main()
