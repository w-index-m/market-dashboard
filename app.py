# -*- coding: utf-8 -*-

import os
import time
import logging
import warnings
from datetime import datetime, timezone, timedelta

import pytz
import pandas as pd
import yfinance as yf

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm

import streamlit as st
from yfinance.exceptions import YFRateLimitError


# =========================================================
# 基本設定
# =========================================================
JST = pytz.timezone("Asia/Tokyo")

# Streamlit Cloud向け：呼び出し回数抑制（TTL長め）
TTL_INTRADAY = 180  # ← 60 → 180
TTL_DAILY = 180

# ログ/警告を静かに
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
logging.getLogger("urllib3").setLevel(logging.CRITICAL)
warnings.filterwarnings("ignore", message="Glyph .* missing from font")
warnings.filterwarnings("ignore", category=UserWarning)

# =========================================================
# 日本語フォント（fonts/ 配下を優先）
#   fonts/NotoSansCJKjp-Regular.otf などを置く想定
# =========================================================
def setup_japanese_font() -> bool:
    candidates = [
        os.path.join("fonts", "NotoSansCJKjp-Regular.otf"),
        os.path.join("fonts", "NotoSansJP-Regular.otf"),
        os.path.join("fonts", "IPAexGothic.ttf"),
        os.path.join("fonts", "ipaexg.ttf"),
    ]
    for fp in candidates:
        if os.path.exists(fp):
            try:
                fm.fontManager.addfont(fp)
                prop = fm.FontProperties(fname=fp)
                matplotlib.rcParams["font.family"] = prop.get_name()
                return True
            except Exception:
                pass

    # フォールバック
    matplotlib.rcParams["font.family"] = "DejaVu Sans"
    return False


HAS_JP_FONT = setup_japanese_font()

# =========================================================
# 取得対象（候補ティッカーは “1個固定”）
#   ※ここを増やすと呼び出しが増えてレート制限リスクが上がる
# =========================================================
TARGETS = [
    # 日本
    {"name": "日経平均", "region": "JP", "symbol": "^N225", "flag": "🇯🇵"},
    {"name": "TOPIX", "region": "JP", "symbol": "998405.T", "flag": "🇯🇵"},
    {"name": "グロース250(ETF)", "region": "JP", "symbol": "2516.T", "flag": "🇯🇵"},

    # 米国
    {"name": "ダウ平均", "region": "US", "symbol": "^DJI", "flag": "🇺🇸"},
    {"name": "NASDAQ総合", "region": "US", "symbol": "^IXIC", "flag": "🇺🇸"},
    {"name": "S&P500", "region": "US", "symbol": "^GSPC", "flag": "🇺🇸"},
    {"name": "半導体(SOX)", "region": "US", "symbol": "^SOX", "flag": "🇺🇸"},

    # 欧州
    {"name": "英FTSE100", "region": "EU", "symbol": "^FTSE", "flag": "🇬🇧"},
    {"name": "独DAX", "region": "EU", "symbol": "^GDAXI", "flag": "🇩🇪"},
    {"name": "仏CAC40", "region": "EU", "symbol": "^FCHI", "flag": "🇫🇷"},

    # アジア
    {"name": "香港ハンセン", "region": "ASIA", "symbol": "^HSI", "flag": "🇭🇰"},
    {"name": "上海総合", "region": "ASIA", "symbol": "000001.SS", "flag": "🇨🇳"},
    {"name": "インドNIFTY50", "region": "ASIA", "symbol": "^NSEI", "flag": "🇮🇳"},

    # 為替
    {"name": "ドル円", "region": "FX", "symbol": "USDJPY=X", "flag": "💱"},
]


# =========================================================
# yfinance取得（レート制限を “落ちずに扱う”）
# =========================================================
@st.cache_data(ttl=TTL_INTRADAY, show_spinner=False)
def fetch_intraday(symbol: str) -> pd.DataFrame:
    """
    できるだけ短期（当日）を取りたい。
    取れなければ 5d へフォールバック。
    レート制限時は _RATE_LIMIT 列で返す。
    """
    tk = yf.Ticker(symbol)

    def _try(period: str, interval: str) -> pd.DataFrame:
        try:
            df = tk.history(period=period, interval=interval)
        except YFRateLimitError:
            return pd.DataFrame({"_RATE_LIMIT": [1]})
        except Exception:
            return pd.DataFrame()

        if df is None or df.empty:
            return pd.DataFrame()

        # tz整備
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        df = df.tz_convert(JST)

        # close必須
        if "Close" not in df.columns:
            return pd.DataFrame()
        df = df.dropna(subset=["Close"])
        return df

    # まずは当日（1d）
    for interval in ["1m", "2m", "5m"]:
        df = _try("1d", interval)
        if "_RATE_LIMIT" in df.columns:
            return df
        if not df.empty and len(df) >= 10:
            return df

    # ダメなら 5日
    for interval in ["5m", "15m", "30m"]:
        df = _try("5d", interval)
        if "_RATE_LIMIT" in df.columns:
            return df
        if not df.empty and len(df) >= 10:
            return df

    return pd.DataFrame()


@st.cache_data(ttl=TTL_DAILY, show_spinner=False)
def fetch_daily_last(symbol: str) -> pd.Series:
    """
    intraday が死んでる時の保険：
    直近の終値だけでも出す（1mo/1d）
    レート制限時は _RATE_LIMIT を返す
    """
    tk = yf.Ticker(symbol)
    try:
        df = tk.history(period="1mo", interval="1d")
    except YFRateLimitError:
        return pd.Series({"_RATE_LIMIT": 1})
    except Exception:
        return pd.Series(dtype=float)

    if df is None or df.empty or "Close" not in df.columns:
        return pd.Series(dtype=float)

    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df = df.tz_convert(JST)
    s = df["Close"].dropna()
    return s


# =========================================================
# 当日開始比（PrevCloseを使わない）
# =========================================================
def compute_from_start(series_close: pd.Series):
    """
    series_close: 時系列のClose
    - base: 当日の最初（または取得期間の最初）
    - last: 最新
    """
    s = series_close.dropna()
    if s.empty or len(s) < 2:
        return None

    base = float(s.iloc[0])
    last = float(s.iloc[-1])

    chg = last - base
    pct = (last / base - 1.0) * 100.0

    return {"base": base, "last": last, "chg": chg, "pct": pct}


# =========================================================
# 小さいタイル用チャート（短期）
#   上げ: 緑 / 下げ: 赤
# =========================================================
def make_tile_chart(close: pd.Series, pct: float, title: str):
    fig, ax = plt.subplots(figsize=(3.3, 1.8), dpi=160)

    s = close.dropna()
    x = s.index
    y = s.values

    ax.plot(x, y, linewidth=1.2)

    # baseライン
    base = float(s.iloc[0])
    ax.axhline(base, linewidth=0.8, alpha=0.5)

    # ざっくり塗り（baseより上/下）
    # 連続塗りは面倒なので、全体傾向で色分け
    if pct >= 0:
        ax.fill_between(x, y, base, alpha=0.18)
    else:
        ax.fill_between(x, y, base, alpha=0.18)

    # 余白削る
    ax.margins(x=0)
    ax.grid(True, linewidth=0.4, alpha=0.4)
    ax.set_title(title, fontsize=9, pad=6)

    # 軸ラベルは省略（タイル密度優先）
    ax.tick_params(axis="x", labelsize=6)
    ax.tick_params(axis="y", labelsize=6)

    # y軸の桁が大きいと見づらいので、指数はざっくり
    ax.yaxis.get_offset_text().set_size(6)

    # 枠色：上げ緑 / 下げ赤
    edge = "#2ca02c" if pct >= 0 else "#d62728"
    for spine in ax.spines.values():
        spine.set_edgecolor(edge)
        spine.set_linewidth(1.6)

    plt.tight_layout()
    return fig


# =========================================================
# Streamlit UI
# =========================================================
def run():
    st.set_page_config(page_title="Market Dashboard (Short)", layout="wide")

    st.title("Market Dashboard（短期）")
    now = datetime.now(JST)
    st.caption(f"Run at (JST): {now:%Y-%m-%d %H:%M:%S}")

    if not HAS_JP_FONT:
        st.info("日本語フォントが見つからないため、文字化けする場合は fonts/ に日本語フォントを置いてください。")

    with st.sidebar:
        st.subheader("表示設定（短期）")
        cols = st.number_input("横に並べる枚数", min_value=2, max_value=6, value=4, step=1)
        st.caption("※多くすると取得回数増ではなく“表示密度”が変わるだけです")
        if st.button("キャッシュクリア & 更新"):
            st.cache_data.clear()
            st.rerun()

    # レート制限の時は “落ちずに” メッセージ出して終了
    # （一回制限来ると連続更新で死ぬので止める）
    rate_limited = False

    # タイル生成
    tile_data = []

    with st.spinner("短期データ取得中...（レート制限回避のため頻繁更新は控えめに）"):
        for t in TARGETS:
            name = t["name"]
            symbol = t["symbol"]
            flag = t.get("flag", "")
            title = f"{flag} {name} ({symbol})"

            intra = fetch_intraday(symbol)

            if "_RATE_LIMIT" in intra.columns:
                rate_limited = True
                break

            if not intra.empty and "Close" in intra.columns:
                close = intra["Close"].dropna()
                info = compute_from_start(close)
                if info is None:
                    continue
                tile_data.append((title, close, info))
                continue

            # intradayが取れない時は daily で代替（直近の日だけでも表示）
            daily_close = fetch_daily_last(symbol)
            if isinstance(daily_close, pd.Series) and "_RATE_LIMIT" in daily_close.index:
                rate_limited = True
                break
            if daily_close is None or daily_close.empty:
                continue

            # dailyは日足なので「最後の2点」でも変化は見せる
            close = daily_close.tail(10)
            info = compute_from_start(close)
            if info is None:
                continue
            tile_data.append((title + " (daily)", close, info))

    if rate_limited:
        st.error("Yahoo Finance 側のレート制限に当たりました。数分待ってから更新してください。")
        st.stop()

    if not tile_data:
        st.warning("データが取得できませんでした（ティッカー・通信・レート制限の可能性）")
        return

    # タイル表示
    n = len(tile_data)
    rows = (n + cols - 1) // cols

    idx = 0
    for r in range(rows):
        ccols = st.columns(cols)
        for c in range(cols):
            if idx >= n:
                break

            title, close, info = tile_data[idx]
            pct = info["pct"]
            chg = info["chg"]
            last = info["last"]

            # ヘッダ（数値）
            sign_color = "#2ca02c" if pct >= 0 else "#d62728"
            pct_text = f"{pct:+.2f}%"
            chg_text = f"{chg:+,.2f}"

            with ccols[c]:
                st.markdown(
                    f"""
                    <div style="border:1px solid #eee; border-radius:10px; padding:10px;">
                      <div style="font-size:13px; font-weight:700; margin-bottom:6px;">{title}</div>
                      <div style="font-size:22px; font-weight:800; color:{sign_color}; line-height:1.0;">{pct_text}</div>
                      <div style="font-size:12px; color:#666; margin-top:3px;">
                        Now: {last:,.2f}　Chg(from start): {chg_text}
                      </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

                fig = make_tile_chart(close, pct, "")
                st.pyplot(fig, clear_figure=True)

                # 最終時刻
                try:
                    last_ts = close.index[-1].to_pydatetime()
                    st.caption(f"Last tick: {last_ts:%m/%d %H:%M} JST")
                except Exception:
                    pass

            idx += 1

    st.caption("※短期表示のみ（当日 or 直近）。頻繁に更新するとレート制限に当たりやすいです。")


run()
