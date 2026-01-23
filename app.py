# -*- coding: utf-8 -*-

import os
import time
import logging
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

import pytz
import pandas as pd
import yfinance as yf

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm

import streamlit as st

# =========================================================
# ログ・警告抑止
# =========================================================
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
logging.getLogger("urllib3").setLevel(logging.CRITICAL)
warnings.filterwarnings("ignore", message="Glyph .* missing from font")
warnings.filterwarnings("ignore", category=UserWarning)

JST = pytz.timezone("Asia/Tokyo")

# =========================================================
# 日本語フォント（repo内 fonts を優先）
#   fonts/NotoSansCJKjp-Regular.otf
#   fonts/IPAexGothic.ttf
#   fonts/ipaexg.ttf
# =========================================================
def setup_japanese_font() -> str:
    candidates = [
        os.path.join("fonts", "NotoSansCJKjp-Regular.otf"),
        os.path.join("fonts", "IPAexGothic.ttf"),
        os.path.join("fonts", "ipaexg.ttf"),
    ]
    for fp in candidates:
        if os.path.exists(fp):
            fm.fontManager.addfont(fp)
            prop = fm.FontProperties(fname=fp)
            name = prop.get_name()
            matplotlib.rcParams["font.family"] = name
            return name
    matplotlib.rcParams["font.family"] = "DejaVu Sans"
    return "DejaVu Sans"

FONT_NAME = setup_japanese_font()

# =========================================================
# 表示（世界の株価風 タイル）
# =========================================================
st.set_page_config(page_title="Market Dashboard", layout="wide")

CSS = """
<style>
.tile {
  border: 1px solid rgba(49,51,63,0.2);
  border-radius: 10px;
  padding: 10px 10px 6px 10px;
  background: white;
  box-shadow: 0 1px 2px rgba(0,0,0,0.04);
  height: 230px;
}
.tile-header {
  display:flex; align-items:center; justify-content:space-between;
  font-weight: 600; font-size: 14px;
  margin-bottom: 4px;
}
.tile-sub {
  color: rgba(49,51,63,0.7);
  font-size: 11px;
  margin-bottom: 6px;
}
.big {
  font-size: 28px;
  font-weight: 800;
  line-height: 1.0;
  margin: 2px 0 4px 0;
}
.smallrow {
  font-size: 12px;
  color: rgba(49,51,63,0.75);
  margin-bottom: 6px;
}
.section-title {
  font-size: 16px;
  font-weight: 800;
  margin: 12px 0 6px 0;
}
.badge {
  display:inline-block;
  font-size: 11px;
  padding: 2px 6px;
  border-radius: 999px;
  border: 1px solid rgba(49,51,63,0.2);
  color: rgba(49,51,63,0.8);
}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)

# =========================================================
# 取得設定（短期だけ）
# =========================================================
DEFAULT_INTRADAY_PERIOD = "1d"     # 当日（または直近取れる範囲）
DEFAULT_INTRADAY_INTERVAL = "5m"  # 1mはレート制限に当たりやすいので5m推奨
CACHE_TTL_SEC = 180               # Cloud向け

# =========================================================
# yfinance レート制限対策：落ちない & 軽いリトライ
# =========================================================
def _safe_history(ticker: str, period: str, interval: str, tries: int = 2, sleep_sec: float = 0.8) -> pd.DataFrame:
    tk = yf.Ticker(ticker)
    last_err = None
    for i in range(tries):
        try:
            df = tk.history(period=period, interval=interval)
            if df is None:
                return pd.DataFrame()
            return df
        except Exception as e:
            last_err = e
            time.sleep(sleep_sec * (i + 1))
    return pd.DataFrame()

@st.cache_data(ttl=CACHE_TTL_SEC, show_spinner=False)
def fetch_intraday(ticker: str, period: str, interval: str) -> pd.DataFrame:
    df = _safe_history(ticker, period=period, interval=interval, tries=2, sleep_sec=0.8)
    if df is None or df.empty:
        return pd.DataFrame()
    # timezone
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df = df.tz_convert(JST)
    # Closeが無い行を落とす
    if "Close" in df.columns:
        df = df.dropna(subset=["Close"])
    return df

@st.cache_data(ttl=CACHE_TTL_SEC, show_spinner=False)
def fetch_daily_5d(ticker: str) -> pd.DataFrame:
    df = _safe_history(ticker, period="5d", interval="1d", tries=2, sleep_sec=0.8)
    if df is None or df.empty:
        return pd.DataFrame()
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df = df.tz_convert(JST)
    if "Close" in df.columns:
        df = df.dropna(subset=["Close"])
    return df

def get_quote_fallback(ticker: str):
    try:
        tk = yf.Ticker(ticker)
        fi = tk.fast_info
        for k in ("last_price", "regular_market_price"):
            v = fi.get(k)
            if v is not None:
                return float(v)
    except Exception:
        pass
    try:
        tk = yf.Ticker(ticker)
        info = tk.info
        for k in ("regularMarketPrice", "currentPrice"):
            v = info.get(k)
            if v is not None:
                return float(v)
    except Exception:
        pass
    return None

# =========================================================
# タイル定義
# =========================================================
@dataclass
class Tile:
    group: str
    title: str
    ticker: str
    badge: str = ""

TILES: list[Tile] = [
    # 日本
    Tile("日本", "日経平均", "^N225", "🇯🇵"),
    Tile("日本", "TOPIX", "998405.T", "🇯🇵"),
    Tile("日本", "グロース250(ETF)", "2516.T", "🇯🇵"),
    Tile("日本", "日経VI", "^JNIV", "🇯🇵"),  # 取れない場合はN/A

    # 米国
    Tile("米国", "ダウ平均", "^DJI", "🇺🇸"),
    Tile("米国", "NASDAQ", "^IXIC", "🇺🇸"),
    Tile("米国", "S&P500", "^GSPC", "🇺🇸"),
    Tile("米国", "半導体(SOX)", "^SOX", "🇺🇸"),
    Tile("米国", "恐怖指数(VIX)", "^VIX", "🇺🇸"),
    Tile("米国", "サンデーダウ", "^DJI", "🇺🇸"),  # 代替（本家が取れないこと多いので）

    # 国債
    Tile("国債", "日本国債10年", "^TNX", "🇯🇵/※代替"),
    Tile("国債", "米国債10年", "^TNX", "🇺🇸"),

    # 為替
    Tile("為替", "ドル円", "USDJPY=X", "💱"),
    Tile("為替", "ユーロ円", "EURJPY=X", "💱"),

    # コモディティ
    Tile("コモディティ", "ゴールド", "GC=F", "🟡"),
    Tile("コモディティ", "原油(WTI)", "CL=F", "🛢️"),

    # 暗号資産
    Tile("暗号資産", "ビットコイン", "BTC-USD", "₿"),

    # 北東アジア
    Tile("北東アジア", "中国 上海総合", "000001.SS", "🇨🇳"),
    Tile("北東アジア", "香港 ハンセン", "^HSI", "🇭🇰"),
    Tile("北東アジア", "韓国 KOSPI", "^KS11", "🇰🇷"),
    Tile("北東アジア", "台湾 加権", "^TWII", "🇹🇼"),

    # 欧州
    Tile("欧州", "英 FTSE100", "^FTSE", "🇬🇧"),
    Tile("欧州", "独 DAX", "^GDAXI", "🇩🇪"),
    Tile("欧州", "仏 CAC40", "^FCHI", "🇫🇷"),

    # インド
    Tile("インド", "NIFTY50", "^NSEI", "🇮🇳"),
]

GROUP_ORDER = ["日本", "米国", "国債", "為替", "コモディティ", "暗号資産", "北東アジア", "欧州", "インド"]

# =========================================================
# 計算（当日 or 直近1日）
#   - intraday が取れれば：始値基準（最初の値）→現在（最後の値）
#   - intraday が無理なら：直近2本の daily で前日比
#   - どちらも無理なら：N/A
# =========================================================
def compute_tile(t: Tile, period: str, interval: str):
    intra = fetch_intraday(t.ticker, period=period, interval=interval)

    if not intra.empty and "Close" in intra.columns:
        s = intra["Close"].dropna()
        if len(s) >= 2:
            base = float(s.iloc[0])
            now = float(s.iloc[-1])
            chg = now - base
            pct = (now / base - 1.0) * 100.0 if base != 0 else None
            last_ts = intra.index[-1].to_pydatetime()
            return {
                "mode": "INTRADAY",
                "series": s,
                "base": base,
                "now": now,
                "chg": chg,
                "pct": pct,
                "last_ts": last_ts,
            }

    # daily fallback
    d5 = fetch_daily_5d(t.ticker)
    if not d5.empty and "Close" in d5.columns:
        s = d5["Close"].dropna()
        if len(s) >= 2:
            prev = float(s.iloc[-2])
            now = float(s.iloc[-1])
            chg = now - prev
            pct = (now / prev - 1.0) * 100.0 if prev != 0 else None
            last_ts = d5.index[-1].to_pydatetime()
            # “短期チャート”っぽく見せるため、5日線をそのままスパークに使う
            return {
                "mode": "DAILY",
                "series": s.tail(5),
                "base": prev,
                "now": now,
                "chg": chg,
                "pct": pct,
                "last_ts": last_ts,
            }

    # quote fallback（数値だけでも）
    q = get_quote_fallback(t.ticker)
    if q is not None:
        return {
            "mode": "QUOTE",
            "series": pd.Series([q]),
            "base": None,
            "now": float(q),
            "chg": None,
            "pct": None,
            "last_ts": None,
        }

    return None

# =========================================================
# スパークライン描画（上昇=薄緑、下落=薄赤）
# =========================================================
def make_spark(series: pd.Series, base: float | None, width=4.0, height=1.4):
    fig, ax = plt.subplots(figsize=(width, height))
    ax.plot(series.index, series.values, linewidth=1.6)

    if base is not None and len(series) >= 2:
        y = series.values
        x = range(len(y))
        ax.axhline(base, linewidth=1.0, alpha=0.5)
        # baseより上/下で塗り分け（色指定は最低限）
        ax.fill_between(x, y, base, where=(y >= base), alpha=0.18)
        ax.fill_between(x, y, base, where=(y < base), alpha=0.18)

    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)
    fig.tight_layout(pad=0.2)
    return fig

def fmt_num(x, digits=2):
    if x is None:
        return "N/A"
    return f"{x:,.{digits}f}"

def fmt_pct(x):
    if x is None:
        return "N/A"
    sign = "+" if x >= 0 else ""
    return f"{sign}{x:.2f}%"

def pct_color(pct):
    if pct is None:
        return "#333"
    return "#0a7f2e" if pct >= 0 else "#b00020"

# =========================================================
# UI
# =========================================================
st.title("Market Dashboard（短期）")
now = datetime.now(JST)
st.caption(f"Run at (JST): {now:%Y-%m-%d %H:%M:%S} / Font: {FONT_NAME}")

with st.sidebar:
    st.subheader("設定")
    period = st.selectbox("表示期間", ["1d", "2d", "5d"], index=0)
    interval = st.selectbox("足", ["5m", "15m", "30m", "60m"], index=0)
    if st.button("キャッシュクリアして更新"):
        st.cache_data.clear()
        st.rerun()
    st.caption("※ yfinance制限中は一部が N/A になります（落ちないようにしています）。")

# グルーピング
tiles_by_group = {g: [] for g in GROUP_ORDER}
for t in TILES:
    if t.group in tiles_by_group:
        tiles_by_group[t.group].append(t)
    else:
        tiles_by_group[t.group] = [t]

# 表示（1行4枚）
for g in GROUP_ORDER:
    group_tiles = tiles_by_group.get(g, [])
    if not group_tiles:
        continue

    st.markdown(f"<div class='section-title'>{g}</div>", unsafe_allow_html=True)

    cols = st.columns(4)
    col_i = 0

    for t in group_tiles:
        data = compute_tile(t, period=period, interval=interval)

        with cols[col_i]:
            st.markdown("<div class='tile'>", unsafe_allow_html=True)

            # header
            badge = f"<span class='badge'>{t.badge}</span>" if t.badge else ""
            st.markdown(
                f"<div class='tile-header'><div>{t.title}</div><div>{badge}</div></div>",
                unsafe_allow_html=True
            )
            st.markdown(
                f"<div class='tile-sub'>{t.ticker}</div>",
                unsafe_allow_html=True
            )

            if data is None:
                st.markdown("<div class='big' style='color:#333'>N/A</div>", unsafe_allow_html=True)
                st.write("取得できませんでした")
            else:
                pct = data["pct"]
                color = pct_color(pct)

                st.markdown(
                    f"<div class='big' style='color:{color}'>{fmt_pct(pct)}</div>",
                    unsafe_allow_html=True
                )

                st.markdown(
                    f"<div class='smallrow'>Now: {fmt_num(data['now'])} &nbsp; Chg: {fmt_num(data['chg'])}</div>",
                    unsafe_allow_html=True
                )

                # spark
                s = data["series"]
                base = data["base"]
                if len(s) >= 2:
                    fig = make_spark(s, base)
                    st.pyplot(fig, clear_figure=True, use_container_width=True)
                else:
                    st.write("（チャートなし）")

                if data["last_ts"] is not None:
                    st.caption(f"Last tick: {data['last_ts']:%m/%d %H:%M} JST / {data['mode']}")
                else:
                    st.caption(f"{data['mode']}")

            st.markdown("</div>", unsafe_allow_html=True)

        col_i = (col_i + 1) % 4
