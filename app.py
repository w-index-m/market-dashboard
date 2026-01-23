# -*- coding: utf-8 -*-
import os
import time
import logging
import warnings
from datetime import datetime, timezone

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

# Streamlit Cloud向け：キャッシュ長め（レート制限回避）
TTL_INTRADAY = 180
TTL_DAILY = 600

logging.getLogger("yfinance").setLevel(logging.CRITICAL)
logging.getLogger("urllib3").setLevel(logging.CRITICAL)
warnings.filterwarnings("ignore", message="Glyph .* missing from font")
warnings.filterwarnings("ignore", category=UserWarning)

# ----------------------------
# 日本語フォント（fonts/ に同梱したものを優先）
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
# 世界株価っぽい色（上げ=緑 / 下げ=赤）
# ----------------------------
GREEN = "#008000"
RED = "#cc0000"
BG_UP = "#e9f7ea"
BG_DN = "#fdeaea"
BG_NEUTRAL = "#f6f6f6"
LINE = "#1f77b4"
FILL_UP = "#cfe8d2"
FILL_DN = "#f6d2cc"

# ----------------------------
# 表示したい市場（必要ならここをあなたの希望に合わせて揃える）
# ※ ここは「ティッカー固定」＝choose_symbol廃止
# ----------------------------
MARKETS = {
    "日本": [
        {"name": "日経平均", "symbol": "^N225", "flag": "JP"},
        {"name": "TOPIX", "symbol": "998405.T", "flag": "JP"},
        {"name": "グロース250（ETF）", "symbol": "2516.T", "flag": "JP"},
        {"name": "日経VI", "symbol": "^JNIV", "flag": "JP"},
    ],
    "米国": [
        {"name": "ダウ平均", "symbol": "^DJI", "flag": "US"},
        {"name": "NASDAQ", "symbol": "^IXIC", "flag": "US"},
        {"name": "S&P500", "symbol": "^GSPC", "flag": "US"},
        {"name": "半導体（SOX）", "symbol": "^SOX", "flag": "US"},
        {"name": "恐怖指数（VIX）", "symbol": "^VIX", "flag": "US"},
    ],
    "欧州": [
        {"name": "英FTSE100", "symbol": "^FTSE", "flag": "UK"},
        {"name": "独DAX", "symbol": "^GDAXI", "flag": "DE"},
        {"name": "仏CAC40", "symbol": "^FCHI", "flag": "FR"},
    ],
    "アジア": [
        {"name": "香港ハンセン", "symbol": "^HSI", "flag": "HK"},
        {"name": "中国 上海総合", "symbol": "000001.SS", "flag": "CN"},
        {"name": "インド NIFTY50", "symbol": "^NSEI", "flag": "IN"},
        {"name": "韓国 KOSPI", "symbol": "^KS11", "flag": "KR"},
        {"name": "台湾 加権", "symbol": "^TWII", "flag": "TW"},
    ],
    "為替": [
        {"name": "ドル円", "symbol": "USDJPY=X", "flag": "FX"},
        {"name": "ユーロ円", "symbol": "EURJPY=X", "flag": "FX"},
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
# yfinance 取得（例外キャッチで落とさない）
# ----------------------------
def _to_jst_index(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    idx = df.index
    if getattr(idx, "tz", None) is None:
        df.index = df.index.tz_localize("UTC")
    df.index = df.index.tz_convert(JST)
    return df

@st.cache_data(ttl=180, show_spinner=False)
def fetch_intraday(symbol: str) -> pd.DataFrame:
    """当日足を最優先。取れない時は5d/5mにフォールバック。落ちない。"""
    tk = yf.Ticker(symbol)

    def _get(period: str, interval: str) -> pd.DataFrame:
        df = tk.history(period=period, interval=interval)
        if df is None or df.empty:
            return pd.DataFrame()
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        return df.tz_convert(JST).dropna(subset=["Close"])

    # まず当日1分足（これが理想）
    df = _get("1d", "1m")
    if not df.empty:
        return df

    # 取れない銘柄はここで救う（指数系に多い）
    df = _get("5d", "5m")
    return df

@st.cache_data(ttl=TTL_DAILY, show_spinner=False)
def fetch_daily(symbol: str, days: int = 10) -> pd.DataFrame:
    tk = yf.Ticker(symbol)
    try:
        end_utc = datetime.now(timezone.utc)
        start_utc = end_utc - pd.Timedelta(days=days)
        df = tk.history(start=start_utc, end=end_utc, interval="1d")
        df = _to_jst_index(df)
        if not df.empty and "Close" in df:
            return df.dropna(subset=["Close"])
    except Exception:
        pass
    return pd.DataFrame()

def safe_last_price(df: pd.DataFrame) -> float | None:
    try:
        return float(df["Close"].dropna().iloc[-1])
    except Exception:
        return None

def safe_first_open(df: pd.DataFrame) -> float | None:
    # 当日開始比（= 寄り付き基準）
    try:
        if "Open" in df and df["Open"].dropna().shape[0] > 0:
            return float(df["Open"].dropna().iloc[0])
        # Openが無い/欠損ならClose先頭で代用
        return float(df["Close"].dropna().iloc[0])
    except Exception:
        return None

def compute_card(symbol: str) -> dict:
    """
    当日動いているなら intraday を使い「当日開始比」
    intraday が取れない/市場休みなら daily の最新日を使う
    """
    intra = fetch_intraday(symbol)

    if not intra.empty:
        now = safe_last_price(intra)
        base = safe_first_open(intra)
        last_ts = intra.index[-1]
        mode = "INTRADAY"
        interval = intra.attrs.get("interval", "1m")

        if now is None or base in (None, 0):
            return {"ok": False, "reason": "intraday値が不完全です"}

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
        }

    # intradayが無いときは daily（最後の日だけ）
    daily = fetch_daily(symbol, days=15)
    if daily.empty or daily["Close"].dropna().shape[0] < 2:
        return {"ok": False, "reason": "取得できませんでした"}

    closes = daily["Close"].dropna()
    now = float(closes.iloc[-1])
    prev = float(closes.iloc[-2])
    chg = now - prev
    pct = (now / prev - 1.0) * 100.0
    last_ts = closes.index[-1]

    # “短期だけ”なので dailyは2点でもOK（線として最低限）
    return {
        "ok": True,
        "mode": "CLOSE",
        "interval": "1d",
        "now": now,
        "base": prev,
        "chg": chg,
        "pct": pct,
        "series": closes.tail(30),  # CLOSE時も少しだけ見せたいならここ。完全に不要なら tail(2) に。
        "last_ts": last_ts,
        "date_label": last_ts.strftime("%Y-%m-%d"),
    }

# ----------------------------
# ここが欲しいと言ってた make_sparkline
# 横軸：時間（intraday） / 日付（close）
# かつ、各チャートに日付を出す
# ----------------------------
def make_sparkline(series: pd.Series, base: float, mode: str):
    """
    series: Closeの系列（indexはdatetime）
    base: 基準値（当日開始 or 前日終値）
    mode: INTRADAY / CLOSE
    """
    fig, ax = plt.subplots(figsize=(4.6, 1.35))

    if series is None or series.empty:
        ax.text(0.5, 0.5, "N/A", ha="center", va="center")
        ax.axis("off")
        return fig

    x = series.index
    y = series.values

    # 基準線
    ax.axhline(base, linewidth=1, alpha=0.6)

    # 塗り（上=緑系 / 下=赤系）
    ax.plot(x, y, linewidth=1.6, color=LINE)
    ax.fill_between(x, y, base, where=(y >= base), alpha=0.55)
    ax.fill_between(x, y, base, where=(y < base), alpha=0.55)

    # X軸フォーマット
    if mode == "INTRADAY":
        # 時間を表示（JST）
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=JST))
        ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=3, maxticks=6))
    else:
        # 日付を表示
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d", tz=JST))
        ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=3, maxticks=6))

    ax.tick_params(axis="x", labelsize=8, rotation=0)
    ax.tick_params(axis="y", labelsize=8)
    ax.margins(x=0.01)

    # 枠・余白をスッキリ
    for spine in ax.spines.values():
        spine.set_alpha(0.2)
    ax.grid(True, axis="y", alpha=0.15)

    plt.tight_layout()
    return fig

# ----------------------------
# Streamlit UI（世界株価.com風）
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
      height: 148px;
    }}
    .wk-title {{
      font-weight: 700;
      font-size: 14px;
      margin-bottom: 2px;
    }}
    .wk-sub {{
      color: rgba(0,0,0,0.55);
      font-size: 11px;
      margin-bottom: 8px;
    }}
    .wk-pct {{
      font-weight: 800;
      font-size: 22px;
      line-height: 1.1;
      margin: 2px 0 2px 0;
    }}
    .wk-now {{
      font-size: 12px;
      color: rgba(0,0,0,0.75);
    }}
    .wk-foot {{
      font-size: 11px;
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
            data = compute_card(it["symbol"])

            if not data.get("ok"):
                st.markdown(card_css(BG_NEUTRAL), unsafe_allow_html=True)
                st.markdown(
                    f"""
                    <div class="wk-card">
                      <div class="wk-title">{it["name"]}</div>
                      <div class="wk-sub">{it["symbol"]}</div>
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
            st.markdown(
                f"""
                <div class="wk-card">
                  <div class="wk-title">{it["name"]}</div>
                  <div class="wk-sub">{it["symbol"]}</div>
                  <div class="wk-pct" style="color:{color};">{pct:+.2f}%</div>
                  <div class="wk-now">Now: {now:,.2f} &nbsp;&nbsp; Chg: {chg:+,.2f}</div>
                  <div class="wk-foot">Date: {date_label} / Last tick: {last_ts} / {mode}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

            # チャート（当日 or 最終日）
            fig = make_sparkline(data["series"], data["base"], data["mode"])

            # fill_between の色（上/下）を “世界株価風” に寄せるため、最後に面塗り色を調整
            # ※ matplotlib の fill_between はここで色差し替えが面倒なので、簡易に背景色で雰囲気を作る
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

    for section, items in MARKETS.items():
        st.subheader(section)
        render_market_row(items, cols=4)
        st.divider()

main()


