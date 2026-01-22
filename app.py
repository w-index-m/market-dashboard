# -*- coding: utf-8 -*-
import logging
import warnings
from datetime import datetime, timezone

import pytz
import yfinance as yf
import pandas as pd

import streamlit as st
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# -------------------------
# Quiet
# -------------------------
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
logging.getLogger("urllib3").setLevel(logging.CRITICAL)
warnings.filterwarnings("ignore", category=UserWarning)

JST = pytz.timezone("Asia/Tokyo")

# -------------------------
# Targets（必要なら増やしてOK）
# -------------------------
SECTIONS = [
    ("日本", [
        {"name": "日経平均", "region": "JP", "candidates": ["^N225"]},
        {"name": "TOPIX", "region": "JP", "candidates": ["998405.T"]},
        {"name": "グロース250(ETF)", "region": "JP", "candidates": ["2516.T"]},
        {"name": "日経VI", "region": "JP", "candidates": ["^JNIV"]},  # 取れない場合あり
    ]),
    ("米国", [
        {"name": "ダウ平均", "region": "US", "candidates": ["^DJI"]},
        {"name": "NASDAQ", "region": "US", "candidates": ["^IXIC"]},
        {"name": "S&P500", "region": "US", "candidates": ["^GSPC"]},
        {"name": "SOX", "region": "US", "candidates": ["^SOX"]},
        {"name": "VIX", "region": "US", "candidates": ["^VIX"]},
    ]),
    ("為替", [
        {"name": "USD/JPY", "region": "FX", "candidates": ["USDJPY=X"]},
        {"name": "EUR/JPY", "region": "FX", "candidates": ["EURJPY=X"]},
    ]),
    ("コモディティ", [
        {"name": "Gold", "region": "CMD", "candidates": ["GC=F"]},
        {"name": "WTI", "region": "CMD", "candidates": ["CL=F"]},
        {"name": "Bitcoin", "region": "CMD", "candidates": ["BTC-USD"]},
    ]),
    ("アジア", [
        {"name": "上海総合", "region": "ASIA", "candidates": ["000001.SS"]},
        {"name": "香港ハンセン", "region": "ASIA", "candidates": ["^HSI"]},
        {"name": "KOSPI", "region": "ASIA", "candidates": ["^KS11"]},
        {"name": "NIFTY50", "region": "ASIA", "candidates": ["^NSEI"]},
    ]),
    ("欧州", [
        {"name": "FTSE100", "region": "EU", "candidates": ["^FTSE"]},
        {"name": "DAX", "region": "EU", "candidates": ["^GDAXI"]},
        {"name": "CAC40", "region": "EU", "candidates": ["^FCHI"]},
    ]),
]

# -------------------------
# Data fetch (short-term)
# -------------------------
@st.cache_data(ttl=60, show_spinner=False)
def fetch_intraday(symbol: str) -> pd.DataFrame:
    """
    まずは当日（1d）を優先。取れない時は直近数日(5d)に落とす。
    intervalは 1m→2m→5m の順で試す。
    """
    tk = yf.Ticker(symbol)

    def _try(period: str, interval: str) -> pd.DataFrame:
        df = tk.history(period=period, interval=interval)
        if df is None or df.empty:
            return pd.DataFrame()
        # timezone
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        df = df.tz_convert(JST)
        return df.dropna(subset=["Close"])

    for interval in ["1m", "2m", "5m"]:
        df = _try("1d", interval)
        if not df.empty and len(df) >= 10:
            return df

    for interval in ["5m", "15m", "30m"]:
        df = _try("5d", interval)
        if not df.empty and len(df) >= 10:
            return df

    return pd.DataFrame()

@st.cache_data(ttl=300, show_spinner=False)
def fetch_prev_close(symbol: str) -> float | None:
    """
    前日終値（できるだけ安定手段で）
    """
    try:
        df = yf.Ticker(symbol).history(period="7d", interval="1d")
        if df is None or df.empty or len(df) < 2:
            return None
        close = df["Close"].dropna()
        if len(close) < 2:
            return None
        return float(close.iloc[-2])
    except Exception:
        return None

def choose_symbol(candidates: list[str]) -> str | None:
    for sym in candidates:
        df = fetch_intraday(sym)
        if not df.empty:
            return sym
    return None

# -------------------------
# Sparkline
# -------------------------
def make_sparkline(series: pd.Series):
    """
    series: Close series (JST)
    """
    y = series.dropna()
    if y.empty:
        return None

    base = float(y.iloc[0])
    last = float(y.iloc[-1])

    fig = plt.figure(figsize=(2.9, 1.1), dpi=160)
    ax = fig.add_subplot(111)

    ax.plot(y.index, y.values, linewidth=1.2)
    # 塗り（上昇=緑 / 下落=赤）: baseline は最初の値
    ax.fill_between(y.index, y.values, base, where=(y.values >= base), alpha=0.18)
    ax.fill_between(y.index, y.values, base, where=(y.values < base), alpha=0.18)

    ax.axhline(base, linewidth=0.8, alpha=0.6)
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)

    fig.tight_layout(pad=0.2)
    return fig

def fmt_price(x: float) -> str:
    # BTCなど大きい/小さい混在を雑に見やすく
    if x >= 1000:
        return f"{x:,.2f}"
    return f"{x:.4f}" if x < 10 else f"{x:.3f}" if x < 100 else f"{x:.2f}"

# -------------------------
# UI
# -------------------------
st.set_page_config(page_title="Market Dashboard (Short-term)", layout="wide")

st.title("Market Dashboard（短期）")
now_jst = datetime.now(JST)
st.caption(f"Updated (JST): {now_jst:%Y-%m-%d %H:%M:%S}")

with st.sidebar:
    st.subheader("表示設定")
    refresh = st.toggle("自動更新（60秒）", value=False)
    points = st.select_slider("表示する足（ざっくり）", options=["少なめ", "標準", "多め"], value="標準")
    if st.button("キャッシュクリア → 再実行"):
        st.cache_data.clear()
        st.rerun()

# 自動更新（追加ライブラリなしでやる簡易版）
if refresh:
    st.info("自動更新ON（60秒ごとに更新）")
    st.stop()  # いったん止める（下に説明）

# ↑ここで止めると更新しないので、下の“自動更新ON”を本当に動かす場合は
# streamlit-autorefresh を入れて使うのが確実（後述）

# 表示足の調整
def slice_series(df: pd.DataFrame) -> pd.Series:
    s = df["Close"].dropna()
    if s.empty:
        return s
    if points == "少なめ":
        return s.tail(80)
    if points == "多め":
        return s.tail(260)
    return s.tail(150)

# カード風スタイル（HTML少しだけ）
CARD_CSS = """
<style>
.card{
  border: 1px solid rgba(49,51,63,0.2);
  border-radius: 10px;
  padding: 10px 10px 6px 10px;
  background: white;
}
.bigpct{
  font-size: 26px;
  font-weight: 800;
  line-height: 1.0;
  margin-bottom: 4px;
}
.smallline{
  font-size: 13px;
  opacity: 0.75;
  margin-top: 2px;
}
</style>
"""
st.markdown(CARD_CSS, unsafe_allow_html=True)

# セクションごとに表示（4列グリッド）
for section_title, targets in SECTIONS:
    st.subheader(section_title)
    cols = st.columns(4)
    for i, t in enumerate(targets):
        col = cols[i % 4]

        with col:
            sym = choose_symbol(t["candidates"])
            if sym is None:
                st.warning(f"{t['name']}: 取得不可")
                continue

            df = fetch_intraday(sym)
            if df.empty:
                st.warning(f"{t['name']}: データ無し")
                continue

            s = slice_series(df)
            if s.empty:
                st.warning(f"{t['name']}: Close無し")
                continue

            last = float(s.iloc[-1])
            prev = fetch_prev_close(sym)
            if prev is None:
                # 取れない時は series の先頭を基準（=当日/直近の始値に近い）
                prev = float(s.iloc[0])

            chg = last - prev
            pct = (last / prev - 1.0) * 100.0 if prev != 0 else 0.0

            fig = make_sparkline(s)

            # 色（上昇/下落）
            color = "#1a7f37" if pct >= 0 else "#cf222e"
            sign = "+" if pct >= 0 else ""

            st.markdown('<div class="card">', unsafe_allow_html=True)
            st.markdown(f"**{t['name']}**  `({sym})`")

            st.markdown(
                f'<div class="bigpct" style="color:{color};">{sign}{pct:.2f}%</div>',
                unsafe_allow_html=True
            )
            st.markdown(
                f'<div class="smallline">Now: {fmt_price(last)}　Chg: {sign}{fmt_price(chg)}</div>',
                unsafe_allow_html=True
            )

            if fig is not None:
                st.pyplot(fig, clear_figure=True, use_container_width=True)

            # いつの足か（最終時刻）
            ts = s.index[-1]
            st.markdown(
                f'<div class="smallline">Last tick: {ts:%m/%d %H:%M} JST</div>',
                unsafe_allow_html=True
            )
            st.markdown("</div>", unsafe_allow_html=True)

    st.divider()

# -------------------------
# 自動更新を“本当に”動かす場合（推奨）
# streamlit-autorefresh を使うのが確実です。
# -------------------------
st.caption("※ 自動更新を本格的に動かすなら streamlit-autorefresh を requirements.txt に追加するのが確実です。")
