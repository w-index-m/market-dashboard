# -*- coding: utf-8 -*-
import os
import logging
import warnings
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Tuple

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
#Google解析
import os
import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(
    page_title="My App",
    layout="wide"
)

# ===== Google Analytics 注入 =====
GA_MEASUREMENT_ID = os.getenv("GA_MEASUREMENT_ID", "G-L4LRKQ582C")

def inject_ga():
    components.html(
        f"""
        <!-- Google tag (gtag.js) -->
        <script async src="https://www.googletagmanager.com/gtag/js?id={GA_MEASUREMENT_ID}"></script>
        <script>
          window.dataLayer = window.dataLayer || [];
          function gtag(){{dataLayer.push(arguments);}}
          gtag('js', new Date());
          gtag('config', '{GA_MEASUREMENT_ID}', {{'send_page_view': true}});
        </script>
        """,
        height=0,
        width=0,
    )

inject_ga()
# ================================

# ↓↓↓ ここから通常のStreamlit UI ↓↓↓
#

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
# 日本語フォント（リポジトリ内 fonts/ 優先）
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
LINE_NEUTRAL = "#1f77b4"

# ----------------------------
# 取得対象
#  - US主要3指数は先物(rt_symbol)で「現在値」を取りに行く
#  - 個別株で Tiingo を使うものは provider="tiingo" を付ける
# ----------------------------
MARKETS = {
    "日本": [
        {"name": "日経平均", "symbol": "^N225", "flag": "JP"},
        {"name": "TOPIX（ETF）", "symbol": "1306.T", "flag": "JP"},
        {"name": "グロース250（ETF）", "symbol": "2516.T", "flag": "JP"},
        {"name": "日経225先物", "symbol": "NK=F", "flag": "JP"}
    ],
    "日本（個別株）": [
        {"name": "フジクラ", "symbol": "5803.T", "flag": "JP", "provider": "tiingo"},
        {"name": "三菱重工", "symbol": "7011.T", "flag": "JP", "provider": "tiingo"},
        {"name": "三菱商事", "symbol": "8058.T", "flag": "JP"},
        {"name": "ＩＨＩ", "symbol": "7013.T", "flag": "JP"},
        {"name": "トヨタ自動車", "symbol": "7203.T", "flag": "JP"},
        {"name": "ソニーG", "symbol": "6758.T", "flag": "JP"},
        {"name": "三菱UFJ", "symbol": "8306.T", "flag": "JP"},
        {"name": "任天堂", "symbol": "7974.T", "flag": "JP"},
    ],
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
    "為替": [
        {"name": "ドル円", "symbol": "USDJPY=X", "flag": "FX"},
        {"name": "ユーロ円", "symbol": "EURJPY=X", "flag": "FX"},
        {"name": "ユーロドル", "symbol": "EURUSD=X", "flag": "FX"},
    ],
    "コモディティ": [
        {"name": "ゴールド", "symbol": "GC=F", "flag": "CMD"},
        {"name": "プラチナ（先物）", "symbol": "PL=F", "flag": "CMD"},
        {"name": "原油（WTI）", "symbol": "CL=F", "flag": "CMD"},
    ],
    "暗号資産": [
        {"name": "ビットコイン", "symbol": "BTC-USD", "flag": "CRYPTO"},
    ],
}

# ----------------------------
# キャッシュ設定（Cloud向けに長め）
# ----------------------------
TTL_DAILY = 180
TTL_INTRADAY = 180

# ----------------------------
# Tiingo
# ----------------------------
def get_tiingo_key() -> Optional[str]:
    # Streamlit Secrets → 環境変数
    try:
        k = st.secrets.get("TIINGO_API_KEY", None)
        if k:
            return str(k)
    except Exception:
        pass
    return os.getenv("TIINGO_API_KEY")

@st.cache_data(ttl=TTL_DAILY, show_spinner=False)
def fetch_daily_tiingo(symbol: str, days: int = 20) -> pd.DataFrame:
    key = get_tiingo_key()
    if not key:
        return pd.DataFrame()

    try:
        end_utc = datetime.now(timezone.utc)
        start_utc = end_utc - pd.Timedelta(days=days)

        candidates = [symbol]
        if symbol.endswith(".T"):
            code = symbol.replace(".T", "")
            candidates += [code, f"tse:{code}"]

        for tk in candidates:
            url = f"https://api.tiingo.com/tiingo/daily/{tk}/prices"
            params = {
                "startDate": start_utc.date().isoformat(),
                "endDate": end_utc.date().isoformat(),
                "token": key,
            }
            r = requests.get(url, params=params, timeout=10)
            if r.status_code != 200:
                continue

            js = r.json()
            if not js:
                continue

            df = pd.DataFrame(js)
            if "date" not in df.columns or "close" not in df.columns:
                continue

            df["date"] = pd.to_datetime(df["date"], utc=True)
            df = df.set_index("date").sort_index()

            out = pd.DataFrame(index=df.index)
            out["Open"] = df.get("open")
            out["High"] = df.get("high")
            out["Low"] = df.get("low")
            out["Close"] = df.get("close")
            out["Volume"] = df.get("volume", 0)

            out = out.tz_convert(JST).dropna(subset=["Close"])
            return out

    except Exception:
        return pd.DataFrame()

    return pd.DataFrame()

# ----------------------------
# Yahoo(yfinance) 日足
# ----------------------------
@st.cache_data(ttl=TTL_DAILY, show_spinner=False)
def fetch_daily_yahoo(symbol: str, days: int = 20) -> pd.DataFrame:
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

def fetch_daily(symbol: str, days: int = 20, provider: str = "yahoo") -> pd.DataFrame:
    # provider が tiingo のときだけ Tiingo を先に試す（失敗したら Yahoo）
    if provider == "tiingo":
        df_t = fetch_daily_tiingo(symbol, days=days)
        if not df_t.empty and df_t["Close"].dropna().shape[0] >= 2:
            return df_t
    return fetch_daily_yahoo(symbol, days=days)

# ----------------------------
# Yahoo(yfinance) イントラ（1m→2m→5m→15m）
# ----------------------------
@st.cache_data(ttl=TTL_INTRADAY, show_spinner=False)
def fetch_intraday(symbol: str) -> pd.DataFrame:
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

# ----------------------------
# 安全な値取り
# ----------------------------
def safe_last_price(df: pd.DataFrame) -> Optional[float]:
    try:
        s = df["Close"].dropna()
        return float(s.iloc[-1]) if not s.empty else None
    except Exception:
        return None

def safe_first_open(df: pd.DataFrame) -> Optional[float]:
    try:
        s = df["Open"].dropna()
        return float(s.iloc[0]) if not s.empty else None
    except Exception:
        return None

# ----------------------------
# カード用計算
# ----------------------------
def compute_card(symbol: str, rt_symbol: Optional[str] = None, provider: str = "yahoo") -> Dict[str, Any]:
    """
    - intradayが取れれば「当日開始比」
    - 取れない/市場休みなら dailyで「前日比」
    - rt_symbol があれば intraday はそちら（先物）で取得
    - daily は provider に従う（フジクラだけTiingo等）
    """
    intraday_sym = rt_symbol or symbol
    intra = fetch_intraday(intraday_sym)
    daily = fetch_daily(...)
#    if not intra.empty:
#        now = safe_last_price(intra)
#        base = safe_first_open(intra)
#        last_ts = intra.index[-1]
#        interval = intra.attrs.get("interval", "1m")
#
#        if now is not None and base not in (None, 0):
#            chg = now - base
#            pct = (now / base - 1.0) * 100.0
#            return {
#                "ok": True,
#                "mode": "INTRADAY",
#                "interval": interval,
#                "now": now,
#                "base": base,
#                "chg": chg,
#                "pct": pct,
#                "last_ts": last_ts,
#                "date_label": last_ts.strftime("%Y-%m-%d"),
#                "rt_used": bool(rt_symbol),
#             }


    # intraday無い → daily（短期だけでOKなら days=15 くらいで十分）
    daily = fetch_daily(symbol, days=15, provider=provider)

    if (daily.empty or daily["Close"].dropna().shape[0] < 2) and rt_symbol:
        daily = fetch_daily(rt_symbol, days=15, provider=provider)

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
# 短期チャート（当日: 時間 / CLOSE: 日付）
# ----------------------------
def make_sparkline(series: pd.Series, base: float, mode: str, up: bool):
    fig, ax = plt.subplots(figsize=(5.6, 1.75))

    if series is None or series.empty:
        ax.text(0.5, 0.5, "N/A", ha="center", va="center")
        ax.axis("off")
        return fig

    x = series.index
    y = series.values

    ax.axhline(base, linewidth=1, alpha=0.6, color="black")
    ax.plot(x, y, linewidth=1.8, color=LINE_NEUTRAL, alpha=0.95)

    fill_color = GREEN if up else RED
    ax.fill_between(x, y, base, where=(y >= base), alpha=0.12, color=GREEN)
    ax.fill_between(x, y, base, where=(y < base), alpha=0.12, color=RED)

    if mode == "INTRADAY":
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=JST))
        ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=3, maxticks=6))
    else:
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d", tz=JST))
        ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=3, maxticks=6))

    ax.tick_params(axis="x", labelsize=10, rotation=0)
    ax.tick_params(axis="y", labelsize=10)
    ax.margins(x=0.01)

    for spine in ax.spines.values():
        spine.set_alpha(0.2)
    ax.grid(True, axis="y", alpha=0.15)

    plt.tight_layout()
    return fig

# ----------------------------
# カードCSS（フォント大きめ & 1行ヘッダ）
# ----------------------------
def card_css(bg: str) -> str:
    return f"""
    <style>
    .wk-card {{
      border: 1px solid rgba(0,0,0,0.08);
      border-radius: 10px;
      padding: 10px 12px;
      background: {bg};
      box-shadow: 0 1px 2px rgba(0,0,0,0.04);
    }}
    .wk-head {{
      display:flex;
      align-items:baseline;
      justify-content:space-between;
      gap: 10px;
      margin-bottom: 6px;
    }}
    .wk-name {{
      font-weight: 900;
      font-size: 18px;
      line-height: 1.1;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .wk-sym {{
      font-weight: 700;
      font-size: 12px;
      color: rgba(0,0,0,0.55);
      margin-left: 8px;
    }}
    .wk-pct {{
      font-weight: 900;
      font-size: 26px;
      line-height: 1;
      white-space: nowrap;
    }}
    .wk-now {{
      font-size: 14px;
      color: rgba(0,0,0,0.75);
      margin-bottom: 4px;
    }}
    .wk-foot {{
      font-size: 12px;
      color: rgba(0,0,0,0.55);
    }}
    </style>
    """

def render_market_row(items, cols=4):
    columns = st.columns(cols)
    for i, it in enumerate(items):
        col = columns[i % cols]
        with col:
            data = compute_card(
                it["symbol"],
                it.get("rt_symbol"),
                it.get("provider", "yahoo"),
            )

            if not data.get("ok"):
                st.markdown(card_css(BG_NEUTRAL), unsafe_allow_html=True)
                st.markdown(
                    f"""
                    <div class="wk-card">
                      <div class="wk-head">
                        <div class="wk-name">{it["name"]}<span class="wk-sym">{it["symbol"]}</span></div>
                        <div class="wk-pct" style="color:#666;">N/A</div>
                      </div>
                      <div class="wk-now">取得できませんでした</div>
                      <div class="wk-foot">Provider: {it.get("provider","yahoo")} / RT: {it.get("rt_symbol","-")}</div>
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
            last_ts = (
                data["last_ts"].strftime("%m/%d %H:%M JST")
                if mode == "INTRADAY"
                else data["last_ts"].strftime("%Y-%m-%d")
            )

            up = pct >= 0
            color = GREEN if up else RED
            bg = BG_UP if up else BG_DN

            st.markdown(card_css(bg), unsafe_allow_html=True)
            st.markdown(
                f"""
                <div class="wk-card">
                  <div class="wk-head">
                    <div class="wk-name">{it["name"]}<span class="wk-sym">{it["symbol"]}</span></div>
                    <div class="wk-pct" style="color:{color};">{pct:+.2f}%</div>
                  </div>
                  <div class="wk-now">Now: {now:,.2f} &nbsp;&nbsp; Chg: {chg:+,.2f}</div>
                  <div class="wk-foot">Date: {date_label} / Last: {last_ts} / {mode}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

            fig = make_sparkline(data["series"], data["base"], data["mode"], up=up)
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

        st.subheader("Tiingo")
        key_exists = bool(get_tiingo_key())
        st.write(f"TIINGO_API_KEY: {'設定あり' if key_exists else '未設定'}")
        st.caption("Streamlit Cloud の Settings → Secrets に TIINGO_API_KEY を入れる想定です。")

    for title, items in MARKETS.items():
        st.subheader(title)
        render_market_row(items, cols=4)
        st.divider()

main()






