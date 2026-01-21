# -*- coding: utf-8 -*-

import logging
import warnings
from datetime import datetime, timedelta, timezone

import pytz
import yfinance as yf
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import streamlit as st

# =========================
# うるさい表示を抑止
# =========================
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
logging.getLogger("urllib3").setLevel(logging.CRITICAL)
warnings.filterwarnings("ignore", message="Glyph .* missing from font")
warnings.filterwarnings("ignore", category=UserWarning)

# =========================
# フォント（可能なら日本語）
# =========================
try:
    import matplotlib.font_manager as fm
    jp_fonts = [
        f for f in fm.findSystemFonts()
        if ("NotoSansCJK" in f or "Noto Sans CJK" in f or "NotoSansJP" in f)
    ]
    matplotlib.rcParams["font.family"] = "Noto Sans CJK JP" if jp_fonts else "DejaVu Sans"
except Exception:
    matplotlib.rcParams["font.family"] = "DejaVu Sans"

JST = pytz.timezone("Asia/Tokyo")

# =========================
# 設定（sidebarで変更できるように）
# =========================
DEFAULT_LOOKBACK_DAYS = 220
DEFAULT_PLOT_LAST_N = 60
DASH_FIGSIZE_W = 12
ROW_HEIGHT = 2.9
X_LABELSIZE = 7
AUTO_ADJUST = False
JAPAN_OPEN_BASIS_ONLY = True

# =========================
# JPX取引時間（簡易）
# =========================
def is_jpx_session_open(now_jst: datetime) -> bool:
    if now_jst.weekday() >= 5:
        return False
    t = now_jst.time()
    morning = (t >= datetime.strptime("09:00", "%H:%M").time()) and (t <= datetime.strptime("11:30", "%H:%M").time())
    afternoon = (t >= datetime.strptime("12:30", "%H:%M").time()) and (t <= datetime.strptime("15:30", "%H:%M").time())
    return morning or afternoon

REGION_STYLE = {
    "JP":   {"edge": "#1f77b4", "title_bg": "#dbe9ff", "label": "日本"},
    "US":   {"edge": "#ff7f0e", "title_bg": "#ffe7cc", "label": "米国"},
    "EU":   {"edge": "#2ca02c", "title_bg": "#ddf5dd", "label": "欧州"},
    "ASIA": {"edge": "#d62728", "title_bg": "#ffd9d9", "label": "アジア"},
    "FX":   {"edge": "#9467bd", "title_bg": "#efe1ff", "label": "為替"},
}

TARGETS = [
    # 日本
    {"name": "日経平均", "region": "JP", "candidates": ["^N225"]},
    {"name": "日経平均CFD(候補)", "region": "JP", "candidates": ["JPN225", "JP225", "^JP225"]},
    {"name": "日経平均先物(ミニ含む候補)", "region": "JP", "candidates": ["MNI=F", "NIY=F", "NKD=F"]},
    {"name": "TOPIX", "region": "JP", "candidates": ["998405.T"]},
    {"name": "東証グロース250(ETF代替)", "region": "JP", "candidates": ["2516.T"]},

    # 米国
    {"name": "ダウ平均", "region": "US", "candidates": ["^DJI"]},
    {"name": "NASDAQ総合", "region": "US", "candidates": ["^IXIC"]},
    {"name": "S&P500", "region": "US", "candidates": ["^GSPC"]},
    {"name": "半導体指数(SOX)", "region": "US", "candidates": ["^SOX"]},
    {"name": "NYSE FANG+指数", "region": "US", "candidates": ["^NYFANG"]},

    # 欧州
    {"name": "英FTSE100", "region": "EU", "candidates": ["^FTSE"]},
    {"name": "独DAX", "region": "EU", "candidates": ["^GDAXI"]},
    {"name": "仏CAC40(※CAC100代替)", "region": "EU", "candidates": ["^FCHI"]},

    # アジア
    {"name": "香港ハンセン", "region": "ASIA", "candidates": ["^HSI"]},
    {"name": "中国 上海総合", "region": "ASIA", "candidates": ["000001.SS"]},
    {"name": "インド NIFTY50", "region": "ASIA", "candidates": ["^NSEI"]},

    # 為替（別枠）
    {"name": "ドル円(USD/JPY)", "region": "FX", "candidates": ["USDJPY=X"]},
]

# =========================
# yfinance取得（Streamlit用にキャッシュ）
# =========================
@st.cache_data(ttl=120, show_spinner=False)
def fetch_daily(symbol: str, lookback_days: int) -> pd.DataFrame:
    try:
        end_utc = datetime.now(timezone.utc)
        start_utc = end_utc - timedelta(days=lookback_days)
        hist = yf.Ticker(symbol).history(
            start=start_utc, end=end_utc, interval="1d", auto_adjust=AUTO_ADJUST
        )
        if hist is None or hist.empty:
            return pd.DataFrame()
        if hist.index.tz is None:
            hist.index = hist.index.tz_localize("UTC")
        hist = hist.tz_convert(JST)
        return hist.dropna(subset=["Close"])
    except Exception:
        return pd.DataFrame()

@st.cache_data(ttl=60, show_spinner=False)
def fetch_intraday_1m(symbol: str) -> pd.DataFrame:
    try:
        intra = yf.Ticker(symbol).history(period="1d", interval="1m")
        if intra is None or intra.empty:
            return pd.DataFrame()
        if intra.index.tz is None:
            intra.index = intra.index.tz_localize("UTC")
        intra = intra.tz_convert(JST)
        return intra.dropna(subset=["Close"])
    except Exception:
        return pd.DataFrame()

def get_quote_fallback(symbol: str):
    tk = yf.Ticker(symbol)
    try:
        fi = tk.fast_info
        for k in ["last_price", "regular_market_price"]:
            v = fi.get(k)
            if v is not None:
                return float(v)
    except Exception:
        pass
    try:
        info = tk.info
        for k in ["regularMarketPrice", "currentPrice"]:
            v = info.get(k)
            if v is not None:
                return float(v)
    except Exception:
        pass
    return None

def choose_symbol(candidates, lookback_days):
    for sym in candidates:
        d = fetch_daily(sym, lookback_days)
        if not d.empty and len(d) >= 2:
            return sym, d
    return None, pd.DataFrame()

def compute_info(symbol: str, daily: pd.DataFrame, region: str):
    close = daily["Close"].dropna()
    prev_close = float(close.iloc[-2])
    last_close = float(close.iloc[-1])

    now_jst = datetime.now(JST)
    intra = fetch_intraday_1m(symbol)

    now_price = None
    if not intra.empty:
        try:
            now_price = float(intra["Close"].dropna().iloc[-1])
        except Exception:
            now_price = None
    if now_price is None:
        q = get_quote_fallback(symbol)
        if q is not None:
            now_price = q
    if now_price is None:
        now_price = last_close

    mode = "LIVE" if (not intra.empty) else "CLOSE"

    open_price = None
    pct_open = None
    if region == "JP" and JAPAN_OPEN_BASIS_ONLY:
        if is_jpx_session_open(now_jst) and (not intra.empty):
            try:
                open_price = float(intra["Open"].dropna().iloc[0])
            except Exception:
                open_price = None
            if open_price not in (None, 0):
                pct_open = (now_price / open_price - 1.0) * 100.0

    pct_prev = (now_price / prev_close - 1.0) * 100.0

    return {
        "mode": mode,
        "prev_close": prev_close,
        "last_close": last_close,
        "open": open_price,
        "now": now_price,
        "chg_open_pct": pct_open,
        "chg_prev_pct": pct_prev,
    }

def style_axes(ax, region: str):
    stl = REGION_STYLE.get(region, {})
    edge = stl.get("edge", "#333333")
    title_bg = stl.get("title_bg", "#f2f2f2")
    for spine in ax.spines.values():
        spine.set_edgecolor(edge)
        spine.set_linewidth(2.0)
    return title_bg, edge

def make_dashboard_figure(items, title, plot_last_n: int):
    n = len(items)
    rows = (n + 1) // 2
    fig, axes = plt.subplots(rows, 2, figsize=(DASH_FIGSIZE_W, rows * ROW_HEIGHT))
    axes = axes.flatten() if hasattr(axes, "flatten") else [axes]
    fig.suptitle(title, fontsize=14, y=1.02)

    for i, it in enumerate(items):
        ax = axes[i]
        close = it["daily"]["Close"].tail(plot_last_n)
        ax.plot(close.index, close.values)

        ax.text(
            0.98, 0.98, it["info_text"],
            transform=ax.transAxes,
            ha="right", va="top",
            fontsize=8,
            bbox=dict(boxstyle="round", alpha=0.85, pad=0.3)
        )

        title_bg, edge = style_axes(ax, it["region"])
        ax.set_title(
            f'{it["name"]} ({it["symbol"]})',
            fontsize=10,
            bbox=dict(facecolor=title_bg, edgecolor=edge, boxstyle="round,pad=0.25")
        )

        ax.set_xlabel("Date (JST)", fontsize=8)
        ax.set_ylabel("Price / Index", fontsize=8)
        ax.tick_params(axis="x", labelsize=X_LABELSIZE)
        ax.grid(True)
        ax.margins(x=0.03)

    for j in range(n, len(axes)):
        axes[j].axis("off")

    plt.tight_layout()
    return fig

def make_fx_figure(fx_item, plot_last_n: int):
    daily = fx_item["daily"]
    close = daily["Close"].tail(plot_last_n)

    fig, ax = plt.subplots(figsize=(DASH_FIGSIZE_W, 3.2))
    ax.plot(close.index, close.values)

    title_bg, edge = style_axes(ax, fx_item["region"])
    ax.set_title(
        f'{fx_item["name"]} ({fx_item["symbol"]})',
        fontsize=12,
        bbox=dict(facecolor=title_bg, edgecolor=edge, boxstyle="round,pad=0.25")
    )

    ax.text(
        0.98, 0.98, fx_item["info_text"],
        transform=ax.transAxes,
        ha="right", va="top",
        fontsize=10,
        bbox=dict(boxstyle="round", alpha=0.85, pad=0.3)
    )

    ax.set_xlabel("Date (JST)")
    ax.set_ylabel("FX Rate")
    ax.tick_params(axis="x", labelsize=8)
    ax.grid(True)
    ax.margins(x=0.03)
    plt.tight_layout()
    return fig

def run():
    st.set_page_config(page_title="Market Dashboard", layout="wide")
    st.title("Market Dashboard")
    now_jst = datetime.now(JST)
    st.caption(f"Run at (JST): {now_jst:%Y-%m-%d %H:%M:%S}")

    with st.sidebar:
        st.subheader("設定")
        lookback_days = st.number_input("取得期間（日）", 30, 1000, DEFAULT_LOOKBACK_DAYS, 10)
        plot_last_n = st.number_input("表示する直近営業日数", 10, 200, DEFAULT_PLOT_LAST_N, 5)
        if st.button("更新"):
            st.cache_data.clear()
            st.rerun()

    indices_items = []
    fx_item = None
    region_order = {"JP": 0, "US": 1, "EU": 2, "ASIA": 3, "FX": 99}

    with st.spinner("データ取得中..."):
        for t in TARGETS:
            name, region = t["name"], t["region"]
            sym, daily = choose_symbol(t["candidates"], lookback_days)
            if sym is None or daily.empty:
                continue

            info = compute_info(sym, daily, region)

            lines = [f"Mode: {info['mode']}"]
            if (region == "JP") and (info["open"] is not None) and (info["chg_open_pct"] is not None):
                lines.append(f"Open: {info['open']:,.2f}")
                lines.append(f"Now : {info['now']:,.2f}")
                lines.append(f"Chg(Open): {info['chg_open_pct']:+.2f}%")
                lines.append(f"Chg(Prev): {info['chg_prev_pct']:+.2f}%")
            else:
                lines.append(f"Prev: {info['prev_close']:,.2f}")
                lines.append(f"Now : {info['now']:,.2f}")
                lines.append(f"Chg(Prev): {info['chg_prev_pct']:+.2f}%")

            item = {
                "name": name,
                "symbol": sym,
                "region": region,
                "daily": daily,
                "info_text": "\n".join(lines),
                "order": region_order.get(region, 99),
            }

            if region == "FX":
                fx_item = item
            else:
                indices_items.append(item)

    indices_items = sorted(indices_items, key=lambda x: x["order"])
    legend = " / ".join([f'{REGION_STYLE[k]["label"]}' for k in ["JP", "US", "EU", "ASIA"]])

    if indices_items:
        fig = make_dashboard_figure(indices_items, f"Market Dashboard（{legend}）", plot_last_n)
        st.pyplot(fig, clear_figure=True)
    else:
        st.warning("指数データが取得できませんでした（ティッカーが取れない可能性あり）")

    st.divider()

    if fx_item is not None:
        fig_fx = make_fx_figure(fx_item, plot_last_n)
        st.pyplot(fig_fx, clear_figure=True)
    else:
        st.warning("為替データが取得できませんでした")

run()
