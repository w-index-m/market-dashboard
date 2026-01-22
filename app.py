# -*- coding: utf-8 -*-
"""
Market Dashboard (Streamlit)
- 長期チャートは表示せず、「当日(動いている) もしくは 最終取引日」だけを一覧表示
- 日本市場は取引時間中だけ「寄り付き基準 (Open→Now)」に自動切替
- それ以外は「前日比 (PrevClose→Now)」
- 地域（日本→米国→欧州→アジア）で色分け、為替は別枠

注意:
- yfinance は指数/先物/CFDのティッカーが環境差で取れないことがあるため、取れたものだけ表示します。
"""

import logging
import warnings
from datetime import datetime, timedelta, timezone

import pytz
import yfinance as yf
import pandas as pd
import streamlit as st

# =========================
# うるさい表示を抑止
# =========================
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
logging.getLogger("urllib3").setLevel(logging.CRITICAL)
warnings.filterwarnings("ignore", category=UserWarning)

JST = pytz.timezone("Asia/Tokyo")

# =========================
# 自動更新（任意）
# streamlit-autorefresh を requirements.txt に入れると動きます
# =========================
def try_autorefresh(interval_ms: int):
    try:
        from streamlit_autorefresh import st_autorefresh  # type: ignore
        st_autorefresh(interval=interval_ms, key="market_autorefresh")
    except Exception:
        pass

# =========================
# JPX取引時間（簡易）
# 前場: 09:00-11:30 / 後場: 12:30-15:30 (JST)
# =========================
def is_jpx_session_open(now_jst: datetime) -> bool:
    if now_jst.weekday() >= 5:
        return False
    t = now_jst.time()
    morning = (t >= datetime.strptime("09:00", "%H:%M").time()) and (t <= datetime.strptime("11:30", "%H:%M").time())
    afternoon = (t >= datetime.strptime("12:30", "%H:%M").time()) and (t <= datetime.strptime("15:30", "%H:%M").time())
    return morning or afternoon

# =========================
# 表示スタイル（地域色）
# =========================
REGION_STYLE = {
    "JP":   {"bg": "#dbe9ff", "label": "日本"},
    "US":   {"bg": "#ffe7cc", "label": "米国"},
    "EU":   {"bg": "#ddf5dd", "label": "欧州"},
    "ASIA": {"bg": "#ffd9d9", "label": "アジア"},
    "FX":   {"bg": "#efe1ff", "label": "為替"},
}

# =========================
# 取得対象
# - CAC100指定→取得安定のためCAC40で代替（名称に明記）
# - グロース250は指数ティッカーが安定しないためETF(2516.T)で代替
# =========================
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

REGION_ORDER = {"JP": 0, "US": 1, "EU": 2, "ASIA": 3, "FX": 99}

# =========================
# yfinance取得（Streamlitキャッシュ）
# =========================
@st.cache_data(ttl=120, show_spinner=False)
def fetch_daily(symbol: str, lookback_days: int) -> pd.DataFrame:
    try:
        end_utc = datetime.now(timezone.utc)
        start_utc = end_utc - timedelta(days=lookback_days)
        hist = yf.Ticker(symbol).history(start=start_utc, end=end_utc, interval="1d", auto_adjust=False)
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
        for k in ("last_price", "regular_market_price"):
            v = fi.get(k)
            if v is not None:
                return float(v)
    except Exception:
        pass
    try:
        info = tk.info
        for k in ("regularMarketPrice", "currentPrice"):
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

def compute_latest_row(name: str, region: str, symbol: str, daily: pd.DataFrame, japan_open_basis_only: bool):
    """当日(動いている) or 最終取引日 の1行データに集約"""
    close = daily["Close"].dropna()
    prev_close = float(close.iloc[-2])
    last_close = float(close.iloc[-1])
    last_date = close.index[-1]

    now_jst = datetime.now(JST)
    intra = fetch_intraday_1m(symbol)

    # Now値
    now_price = None
    now_time = None
    if not intra.empty:
        try:
            now_price = float(intra["Close"].dropna().iloc[-1])
            now_time = intra.index[-1]
        except Exception:
            now_price = None
            now_time = None

    if now_price is None:
        q = get_quote_fallback(symbol)
        if q is not None:
            now_price = q

    if now_price is None:
        now_price = last_close

    mode = "LIVE" if (not intra.empty) else "CLOSE"
    basis = "前日比"
    base_price = prev_close

    # 日本だけ寄り付き基準（取引時間中 & intradayが取れている時）
    if region == "JP" and japan_open_basis_only and is_jpx_session_open(now_jst) and (not intra.empty):
        try:
            open_price = float(intra["Open"].dropna().iloc[0])
        except Exception:
            open_price = None
        if open_price not in (None, 0):
            basis = "寄り付き比"
            base_price = open_price

    chg_pct = (now_price / base_price - 1.0) * 100.0 if base_price not in (None, 0) else None

    # 表示日付：LIVEなら当日（最終分の時刻）、そうでなければ最終取引日
    shown_dt = now_time if (mode == "LIVE" and now_time is not None) else last_date

    return {
        "地域": REGION_STYLE[region]["label"],
        "名称": name,
        "ティッカー": symbol,
        "日時": shown_dt.strftime("%Y-%m-%d %H:%M") if mode == "LIVE" else shown_dt.strftime("%Y-%m-%d"),
        "現在値": now_price,
        "基準": basis,
        "変化率(%)": chg_pct,
        "モード": mode,
        "order": REGION_ORDER.get(region, 99),
        "region": region,
    }

def style_table(df: pd.DataFrame):
    def color_pct(v):
        try:
            if pd.isna(v):
                return ""
            if float(v) > 0:
                return "color: #d62728; font-weight: 600;"  # 上げ: 赤
            if float(v) < 0:
                return "color: #1f77b4; font-weight: 600;"  # 下げ: 青
            return "color: #444;"
        except Exception:
            return ""

    # 地域ごとの背景色
    def bg_region(row):
        reg = row.get("region", "")
        bg = REGION_STYLE.get(reg, {}).get("bg", "#ffffff")
        return [f"background-color: {bg};" for _ in row.index]

    show_cols = ["地域", "名称", "現在値", "変化率(%)", "基準", "日時", "モード", "ティッカー"]
    view = df[show_cols].copy()

    sty = view.style.apply(bg_region, axis=1).format({
        "現在値": "{:,.2f}",
        "変化率(%)": "{:+.2f}",
    }).applymap(color_pct, subset=["変化率(%)"])

    return sty

def main():
    st.set_page_config(page_title="Market Dashboard", layout="wide")
    st.title("Market Dashboard（当日/最終日のみ）")
    now_jst = datetime.now(JST)
    st.caption(f"更新時刻 (JST): {now_jst:%Y-%m-%d %H:%M:%S}")

    with st.sidebar:
        st.subheader("設定")
        lookback_days = st.number_input("取得期間（日）", 30, 1000, 220, 10)
        japan_open_basis_only = st.toggle("日本市場は取引時間中だけ「寄り付き基準」", value=True)
        refresh_sec = st.selectbox("自動更新", [0, 30, 60, 120, 300], index=2, format_func=lambda x: "OFF" if x == 0 else f"{x} 秒")
        st.caption("※ 自動更新を使う場合は requirements.txt に `streamlit-autorefresh` を追加してください。")
        if st.button("手動更新"):
            st.cache_data.clear()
            st.rerun()

    if refresh_sec and refresh_sec > 0:
        try_autorefresh(refresh_sec * 1000)

    rows = []
    with st.spinner("データ取得中..."):
        for t in TARGETS:
            name, region = t["name"], t["region"]
            sym, daily = choose_symbol(t["candidates"], lookback_days)
            if sym is None or daily.empty:
                continue
            rows.append(compute_latest_row(name, region, sym, daily, japan_open_basis_only))

    if not rows:
        st.error("データが取得できませんでした（ティッカーが取れない or yfinance側の制限の可能性）")
        st.stop()

    df = pd.DataFrame(rows).sort_values(["order"]).reset_index(drop=True)

    # 2段構成：指数（地域別混在） → 為替別枠
    df_fx = df[df["region"] == "FX"].copy()
    df_idx = df[df["region"] != "FX"].copy()

    legend = " → ".join([REGION_STYLE[k]["label"] for k in ("JP", "US", "EU", "ASIA")])
    st.subheader(f"指数（{legend}）")

    st.dataframe(
        style_table(df_idx),
        use_container_width=True,
        hide_index=True,
        height=min(36 + 35 * (len(df_idx) + 1), 800),
    )

    st.divider()
    st.subheader("為替")

    if not df_fx.empty:
        st.dataframe(
            style_table(df_fx),
            use_container_width=True,
            hide_index=True,
            height=150,
        )
    else:
        st.info("為替データが取得できませんでした")

    st.caption("※表示は yfinance の取得結果に依存します。特に CFD / 先物 は取得できない場合があります。")

main()
