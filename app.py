# -*- coding: utf-8 -*-
import os
import logging
import warnings
from datetime import datetime, timezone
from typing import Optional

import pytz
import pandas as pd
import yfinance as yf

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.font_manager as fm

import streamlit as st

# ----------------------------
# 基本設定
# ----------------------------
JST = pytz.timezone("Asia/Tokyo")

# ログ抑止
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
logging.getLogger("urllib3").setLevel(logging.CRITICAL)
warnings.filterwarnings("ignore", message="Glyph .* missing from font")
warnings.filterwarnings("ignore", category=UserWarning)

# ----------------------------
# 日本語フォント（リポジトリ内の fonts/ を優先）
# ----------------------------
def setup_japanese_font() -> str:
    candidates = [
        os.path.join("fonts", "NotoSansCJKjp-Regular.otf"),
        os.path.join("fonts", "NotoSansJP-Regular.otf"),
        os.path.join("fonts", "IPAexGothic.ttf"),
        os.path.join("fonts", "ipaexg.ttf"),
    ]
    for fp in candidates:
        if os.path.exists(fp):
            fm.fontManager.addfont(fp)
            prop = fm.FontProperties(fname=fp)
            matplotlib.rcParams["font.family"] = prop.get_name()
            return prop.get_name()
    matplotlib.rcParams["font.family"] = "DejaVu Sans"
    return "DejaVu Sans"

FONT_NAME = setup_japanese_font()

# ----------------------------
# 世界株価風カラー
# ----------------------------
GREEN = "#1a7f37"
RED = "#d1242f"
BG_UP = "rgba(26,127,55,0.08)"
BG_DN = "rgba(209,36,47,0.08)"
BG_NEUTRAL = "rgba(0,0,0,0.03)"
LINE = "rgba(0,0,0,0.75)"

# ----------------------------
# 取得対象（★US主要3指数は rt_symbol を先物に）
#   symbol: 表示ラベルの“指数”
#   rt_symbol: 現在値/当日足をより新しくする“代替”（先物）
# ----------------------------
MARKETS = {
    "日本": [
        {"name": "日経平均", "symbol": "^N225", "flag": "JP"},
        {"name": "TOPIX", "symbol": "998405.T", "flag": "JP"},
        {"name": "グロース250（ETF）", "symbol": "2516.T", "flag": "JP"},
        {"name": "日経VI", "symbol": "^JNIV", "flag": "JP"},
    ],
 #     "日本（個別株）": [
 #       {"name": "トヨタ自動車", "symbol": "7203.T", "flag": "JP"},
 #       {"name": "ソニーG", "symbol": "6758.T", "flag": "JP"},
 #       {"name": "三菱UFJ", "symbol": "8306.T", "flag": "JP"},
 #       {"name": "任天堂", "symbol": "7974.T", "flag": "JP"},
 #   ],
    "アジア": [
        {"name": "香港ハンセン", "symbol": "^HSI", "flag": "HK"},
        {"name": "中国 上海総合", "symbol": "000001.SS", "flag": "CN"},
        {"name": "インド NIFTY50", "symbol": "^NSEI", "flag": "IN"},
        {"name": "韓国 KOSPI", "symbol": "^KS11", "flag": "KR"},
        {"name": "台湾 加権", "symbol": "^TWII", "flag": "TW"},
      ],
    "米国": [
        {"name": "ダウ平均", "symbol": "^DJI", "flag": "US", "rt_symbol": "YM=F"},
        {"name": "NASDAQ", "symbol": "^IXIC", "flag": "US", "rt_symbol": "NQ=F"},
        {"name": "S&P500", "symbol": "^GSPC", "flag": "US", "rt_symbol": "ES=F"},
        {"name": "半導体（SOX）", "symbol": "^SOX", "flag": "US"},
        {"name": "恐怖指数（VIX）", "symbol": "^VIX", "flag": "US"},
    ],
    "欧州": [
        {"name": "英FTSE100", "symbol": "^FTSE", "flag": "UK"},
        {"name": "独DAX", "symbol": "^GDAXI", "flag": "DE"},
        {"name": "仏CAC40", "symbol": "^FCHI", "flag": "FR"},
    ],

    ],
    "為替": [
        {"name": "ドル円", "symbol": "USDJPY=X", "flag": "FX"},
        {"name": "ユーロ円", "symbol": "EURJPY=X", "flag": "FX"},
        {"name": "ユーロドル", "symbol": "EURUSD=X", "flag": "FX"},
    ],
    "コモディティ": [
        {"name": "ゴールド", "symbol": "GC=F", "flag": "CMD"},
        {"name": "原油（WTI）", "symbol": "CL=F", "flag": "CMD"},
    ],
    "暗号資産": [
        {"name": "ビットコイン", "symbol": "BTC-USD", "flag": "CRYPTO"},
    ],

}

# ----------------------------
# yfinance取得（例外は握りつぶして空を返す）
# ----------------------------
TTL_DAILY = 180
TTL_INTRADAY = 180  # Cloud向けに長め

@st.cache_data(ttl=TTL_DAILY, show_spinner=False)
def fetch_daily(symbol: str, days: int = 20) -> pd.DataFrame:
    try:
        end_utc = datetime.now(timezone.utc)
        start_utc = end_utc - pd.Timedelta(days=days)
        df = yf.Ticker(symbol).history(start=start_utc, end=end_utc, interval="1d", auto_adjust=False)
        if df is None or df.empty:
            return pd.DataFrame()
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        return df.tz_convert(JST).dropna(subset=["Close"])
    except Exception:
        return pd.DataFrame()

@st.cache_data(ttl=TTL_INTRADAY, show_spinner=False)
def fetch_intraday(symbol: str) -> pd.DataFrame:
    # 1mが取れないケースがあるので 1m→2m→5m→15m の順で試す
    for interval in ("1m", "2m", "5m", "15m"):
        try:
            df = yf.Ticker(symbol).history(period="1d", interval=interval, auto_adjust=False)
            if df is None or df.empty:
                continue
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC")
            df = df.tz_convert(JST).dropna(subset=["Close"])
            if df.empty:
                continue
            df.attrs["interval"] = interval
            return df
        except Exception:
            continue
    return pd.DataFrame()

def safe_last_price(df: pd.DataFrame) -> Optional[float]:
    try:
        s = df["Close"].dropna()
        if s.empty:
            return None
        return float(s.iloc[-1])
    except Exception:
        return None

def safe_first_open(df: pd.DataFrame) -> Optional[float]:
    try:
        s = df["Open"].dropna()
        if s.empty:
            return None
        return float(s.iloc[0])
    except Exception:
        return None

def compute_card(symbol: str, rt_symbol: Optional[str] = None) -> dict:
    """
    - 当日動いているなら intraday で「当日開始比」
    - intradayが取れない/市場休みなら dailyで「前日比」
    - 指数が遅延しやすいので、rt_symbol があれば intradayはそちらで取得
    """
    intraday_sym = rt_symbol or symbol
    intra = fetch_intraday(intraday_sym)

    if not intra.empty:
        now = safe_last_price(intra)
        base = safe_first_open(intra)
        last_ts = intra.index[-1]
        mode = "INTRADAY"
        interval = intra.attrs.get("interval", "1m")

        if now is not None and base not in (None, 0):
            chg = now - base
            pct = (now / base - 1.0) * 100.0
            return {
                "ok": True,
                "mode": mode,
                "interval": interval,
                "now": now,
                "base": base,
                "chg": chg,
                "pct": pct,
                "series": intra["Close"].dropna(),
                "last_ts": last_ts,
                "date_label": last_ts.strftime("%Y-%m-%d"),
                "rt_used": bool(rt_symbol),
            }

    # intradayが無い/不完全 → daily
    daily = fetch_daily(symbol, days=15)
    if (daily.empty or daily["Close"].dropna().shape[0] < 2) and rt_symbol:
        daily = fetch_daily(rt_symbol, days=15)

    if daily.empty or daily["Close"].dropna().shape[0] < 2:
        return {"ok": False, "reason": "取得できませんでした"}

    closes = daily["Close"].dropna()
    now = float(closes.iloc[-1])
    prev = float(closes.iloc[-2])
    chg = now - prev
    pct = (now / prev - 1.0) * 100.0
    last_ts = closes.index[-1]

    return {
        "ok": True,
        "mode": "CLOSE",
        "interval": "1d",
        "now": now,
        "base": prev,
        "chg": chg,
        "pct": pct,
        "series": closes.tail(30),
        "last_ts": last_ts,
        "date_label": last_ts.strftime("%Y-%m-%d"),
        "rt_used": bool(rt_symbol),
    }

# ----------------------------
# 短期チャート（当日：時間軸 / CLOSE：日付軸）
# ----------------------------
def make_sparkline(series: pd.Series, base: float, mode: str):
    fig, ax = plt.subplots(figsize=(5.2, 1.55))

    if series is None or series.empty:
        ax.text(0.5, 0.5, "N/A", ha="center", va="center")
        ax.axis("off")
        return fig

    x = series.index
    y = series.values

    ax.axhline(base, linewidth=1, alpha=0.6)

    ax.plot(x, y, linewidth=1.6, color=LINE)
    ax.fill_between(x, y, base, where=(y >= base), alpha=0.55)
    ax.fill_between(x, y, base, where=(y < base), alpha=0.55)

    if mode == "INTRADAY":
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=JST))
        ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=3, maxticks=6))
    else:
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d", tz=JST))
        ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=3, maxticks=6))

    # ここも少し大きめに
    ax.tick_params(axis="x", labelsize=10, rotation=0)
    ax.tick_params(axis="y", labelsize=10)
    ax.margins(x=0.01)

    for spine in ax.spines.values():
        spine.set_alpha(0.2)
    ax.grid(True, axis="y", alpha=0.15)

    plt.tight_layout()
    return fig

# ----------------------------
# カードCSS（★フォントを大きく）
# ----------------------------
def card_css(bg: str) -> str:
    return f"""
    <style>
    .wk-card {{
      border: 1px solid rgba(0,0,0,0.08);
      border-radius: 10px;
      padding: 12px 14px;
      background: {bg};
      box-shadow: 0 1px 2px rgba(0,0,0,0.04);
      height: 170px;
    }}
    .wk-title {{
      font-weight: 800;
      font-size: 18px;
      margin-bottom: 2px;
    }}
    .wk-sub {{
      color: rgba(0,0,0,0.55);
      font-size: 13px;
      margin-bottom: 8px;
    }}
    .wk-pct {{
      font-weight: 900;
      font-size: 30px;
      line-height: 1.1;
      margin: 2px 0 2px 0;
    }}
    .wk-now {{
      font-size: 14px;
      color: rgba(0,0,0,0.75);
    }}
    .wk-foot {{
      font-size: 13px;
      color: rgba(0,0,0,0.55);
      margin-top: 6px;
    }}
    </style>
    """

def render_market_row(items, cols=4):
    columns = st.columns(cols)
    for i, it in enumerate(items):
        col = columns[i % cols]
        with col:
            data = compute_card(it["symbol"], it.get("rt_symbol"))

            if not data.get("ok"):
                st.markdown(card_css(BG_NEUTRAL), unsafe_allow_html=True)
                sub = it["symbol"] + (f" / RT:{it.get('rt_symbol')}" if it.get("rt_symbol") else "")
                st.markdown(
                    f"""
                    <div class="wk-card">
                      <div class="wk-title">{it["name"]}</div>
                      <div class="wk-sub">{sub}</div>
                      <div class="wk-pct" style="color:#666;">N/A</div>
                      <div class="wk-now">取得できませんでした</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                continue

            pct = data["pct"]
            chg = data["chg"]
            now = data["now"]
            mode = data["mode"]
            date_label = data["date_label"]
            last_ts = data["last_ts"].strftime("%m/%d %H:%M JST") if mode == "INTRADAY" else data["last_ts"].strftime("%Y-%m-%d")

            up = pct >= 0
            color = GREEN if up else RED
            bg = BG_UP if up else BG_DN

            st.markdown(card_css(bg), unsafe_allow_html=True)
            sub = it["symbol"] + (f" / RT:{it.get('rt_symbol')}" if it.get("rt_symbol") else "")
            st.markdown(
                f"""
                <div class="wk-card">
                  <div class="wk-title">{it["name"]}</div>
                  <div class="wk-sub">{sub}</div>
                  <div class="wk-pct" style="color:{color};">{pct:+.2f}%</div>
                  <div class="wk-now">Now: {now:,.2f} &nbsp;&nbsp; Chg: {chg:+,.2f}</div>
                  <div class="wk-foot">Date: {date_label} / Last tick: {last_ts} / {mode}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

            fig = make_sparkline(data["series"], data["base"], data["mode"])
            st.pyplot(fig, clear_figure=True)

def main():
    st.set_page_config(page_title="Market Dashboard", layout="wide")
    now_jst = datetime.now(JST)

    st.title("Market Dashboard")
    st.caption(f"Run at (JST): {now_jst:%Y-%m-%d %H:%M:%S} / Font: {FONT_NAME}")

    with st.sidebar:
        st.subheader("操作")
        st.write("レート制限回避のためキャッシュ長めです。")
        if st.button("キャッシュ削除して更新"):
            st.cache_data.clear()
            st.rerun()

    for title, items in MARKETS.items():
        st.subheader(title)
        render_market_row(items, cols=4)
        st.divider()

main()


