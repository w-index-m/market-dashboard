# -*- coding: utf-8 -*-

import os
import logging
import warnings
from datetime import datetime, timezone, timedelta

import pytz
import yfinance as yf
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm

import streamlit as st

# =========================
# 基本設定
# =========================
JST = pytz.timezone("Asia/Tokyo")

# yfinanceログ抑止
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
logging.getLogger("urllib3").setLevel(logging.CRITICAL)
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", message="Glyph .* missing from font")

# =========================
# 日本語フォント設定（fonts/同梱優先）
# =========================
def setup_japanese_font():
    candidates = [
        os.path.join("fonts", "NotoSansCJKjp-Regular.otf"),
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

# =========================
# 「世界株価.com風」スタイル（CSS）
# =========================
def inject_css():
    st.markdown(
        """
<style>
/* 全体余白を詰める */
.block-container { padding-top: 1.0rem; padding-bottom: 1.0rem; }

/* 見出し */
.section-title{
  font-weight: 800;
  margin: 18px 0 8px 0;
  font-size: 18px;
}

/* カード */
.card{
  border: 1px solid rgba(0,0,0,0.08);
  border-radius: 10px;
  padding: 10px 12px 10px 12px;
  background: #fff;
  box-shadow: 0 1px 6px rgba(0,0,0,0.05);
}
.card.up   { background: rgba(0, 200, 0, 0.06); }
.card.down { background: rgba(255, 0, 0, 0.06); }
.card.na   { background: rgba(120, 120, 120, 0.06); }

.row-top{
  display:flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
}
.name{
  font-weight: 800;
  font-size: 14px;
  line-height: 1.1;
}
.sym{
  font-size: 11px;
  color: rgba(0,0,0,0.55);
  margin-top: 2px;
}
.badge{
  font-size: 10px;
  padding: 2px 6px;
  border-radius: 999px;
  border: 1px solid rgba(0,0,0,0.12);
  color: rgba(0,0,0,0.6);
  white-space: nowrap;
}

.big{
  font-size: 28px;
  font-weight: 900;
  margin: 6px 0 2px 0;
  line-height: 1.0;
}
.big.up   { color: #0a8f2a; }
.big.down { color: #c62828; }
.big.na   { color: rgba(0,0,0,0.45); }

.sub{
  font-size: 12px;
  color: rgba(0,0,0,0.6);
  margin-bottom: 6px;
}

.foot{
  font-size: 11px;
  color: rgba(0,0,0,0.45);
  margin-top: 6px;
}
</style>
        """,
        unsafe_allow_html=True
    )

# =========================
# 対象（世界株価風：よく見るやつ）
# =========================
TARGETS = [
    ("日本", [
        {"name": "日経平均", "symbol": "^N225"},
        {"name": "TOPIX", "symbol": "998405.T"},
        {"name": "グロース250（ETF）", "symbol": "2516.T"},
        {"name": "日経VI", "symbol": "^JNIV"},   # 取れない場合あり
    ]),
    ("米国", [
        {"name": "ダウ平均", "symbol": "^DJI"},
        {"name": "NASDAQ", "symbol": "^IXIC"},
        {"name": "S&P500", "symbol": "^GSPC"},
        {"name": "半導体（SOX）", "symbol": "^SOX"},
        {"name": "恐怖指数（VIX）", "symbol": "^VIX"},
    ]),
    ("欧州", [
        {"name": "英FTSE100", "symbol": "^FTSE"},
        {"name": "独DAX", "symbol": "^GDAXI"},
        {"name": "仏CAC40", "symbol": "^FCHI"},
    ]),
    ("為替", [
        {"name": "ドル円", "symbol": "USDJPY=X"},
        {"name": "ユーロ円", "symbol": "EURJPY=X"},
    ]),
]

# =========================
# yfinance取得（レート制限対策：キャッシュTTL長め・足は5m）
# =========================
@st.cache_data(ttl=180, show_spinner=False)
def fetch_intraday(symbol: str) -> pd.DataFrame:
    try:
        # 1日 5分足（1mよりレート制限に強い）
        df = yf.Ticker(symbol).history(period="1d", interval="5m")
        if df is None or df.empty:
            return pd.DataFrame()
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        df = df.tz_convert(JST)
        return df.dropna(subset=["Close"])
    except Exception:
        return pd.DataFrame()

@st.cache_data(ttl=180, show_spinner=False)
def fetch_daily(symbol: str) -> pd.DataFrame:
    try:
        end_utc = datetime.now(timezone.utc)
        start_utc = end_utc - timedelta(days=10)
        df = yf.Ticker(symbol).history(start=start_utc, end=end_utc, interval="1d")
        if df is None or df.empty:
            return pd.DataFrame()
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        df = df.tz_convert(JST)
        return df.dropna(subset=["Close"])
    except Exception:
        return pd.DataFrame()

# =========================
# スパークライン（世界株価風：上昇=緑塗り、下落=赤塗り）
# =========================
def make_sparkline(close: pd.Series, baseline: float):
    fig, ax = plt.subplots(figsize=(3.6, 1.35))  # 小さめ固定（カード用）
    ax.plot(close.index, close.values, linewidth=1.6)

    # 塗り（baseline以上=緑、以下=赤）
    y = close.values
    ax.fill_between(close.index, y, baseline, where=(y >= baseline), alpha=0.20)
    ax.fill_between(close.index, y, baseline, where=(y < baseline), alpha=0.20)

    # ベースライン
    ax.axhline(baseline, linewidth=1.0, alpha=0.5)

    ax.set_xticks([])
    ax.set_yticks([])
    for sp in ax.spines.values():
        sp.set_visible(False)

    fig.tight_layout(pad=0.2)
    return fig

# =========================
# 1銘柄の表示データを作る（例外キャッチで落とさない）
# =========================
def build_item(name: str, symbol: str):
    intra = fetch_intraday(symbol)
    if not intra.empty and len(intra) >= 2:
        close = intra["Close"]
        now = float(close.iloc[-1])
        base = float(intra["Open"].iloc[0]) if "Open" in intra.columns and pd.notna(intra["Open"].iloc[0]) else float(close.iloc[0])
        chg = now - base
        pct = (now / base - 1.0) * 100.0 if base != 0 else None
        last_tick = close.index[-1].strftime("%m/%d %H:%M JST")
        mode = "INTRADAY"
        fig = make_sparkline(close, base)
        return {
            "name": name, "symbol": symbol, "mode": mode,
            "now": now, "base": base, "chg": chg, "pct": pct,
            "last_tick": last_tick, "fig": fig, "ok": True
        }

    # intraday取れない（市場休場/制限/ティッカー不安定等）→ dailyの直近で代替
    daily = fetch_daily(symbol)
    if not daily.empty and len(daily) >= 2:
        close = daily["Close"].tail(2)
        base = float(close.iloc[0])
        now = float(close.iloc[1])
        chg = now - base
        pct = (now / base - 1.0) * 100.0 if base != 0 else None
        last_tick = close.index[-1].strftime("%m/%d JST")
        mode = "DAILY"
        fig = make_sparkline(close, base)
        return {
            "name": name, "symbol": symbol, "mode": mode,
            "now": now, "base": base, "chg": chg, "pct": pct,
            "last_tick": last_tick, "fig": fig, "ok": True
        }

    return {"name": name, "symbol": symbol, "ok": False}

# =========================
# カード描画
# =========================
def render_card(item):
    if not item.get("ok"):
        st.markdown(
            f"""
<div class="card na">
  <div class="row-top">
    <div>
      <div class="name">{item["name"]}</div>
      <div class="sym">{item["symbol"]}</div>
    </div>
    <div class="badge">N/A</div>
  </div>
  <div class="big na">N/A</div>
  <div class="sub">取得できませんでした</div>
</div>
            """,
            unsafe_allow_html=True
        )
        return

    pct = item["pct"]
    chg = item["chg"]
    direction = "up" if (pct is not None and pct >= 0) else "down"
    pct_str = f"{pct:+.2f}%" if pct is not None else "N/A"
    now_str = f"{item['now']:,.2f}"
    chg_str = f"{chg:+,.2f}"

    st.markdown(
        f"""
<div class="card {direction}">
  <div class="row-top">
    <div>
      <div class="name">{item["name"]}</div>
      <div class="sym">{item["symbol"]}</div>
    </div>
    <div class="badge">{item["mode"]}</div>
  </div>

  <div class="big {direction}">{pct_str}</div>
  <div class="sub">Now: {now_str}　Chg: {chg_str}</div>
</div>
        """,
        unsafe_allow_html=True
    )

    st.pyplot(item["fig"], clear_figure=True, use_container_width=True)

    st.markdown(f"""<div class="foot">Last tick: {item["last_tick"]}</div>""", unsafe_allow_html=True)

# =========================
# メイン
# =========================
def main():
    st.set_page_config(page_title="Market Dashboard", layout="wide")
    inject_css()

    st.title("Market Dashboard")
    now_jst = datetime.now(JST)
    st.caption(f"Run at (JST): {now_jst:%Y-%m-%d %H:%M:%S} / Font: {FONT_NAME}")

    with st.sidebar:
        st.subheader("操作")
        if st.button("キャッシュクリア＆更新"):
            st.cache_data.clear()
            st.rerun()
        st.caption("※ 5分足 + TTL=180秒（レート制限対策）")

    # 4列固定（世界株価っぽく詰める）
    cols_per_row = 4

    for section, items in TARGETS:
        st.markdown(f"""<div class="section-title">{section}</div>""", unsafe_allow_html=True)

        # グリッド表示
        cols = st.columns(cols_per_row, gap="medium")
        for idx, it in enumerate(items):
            c = cols[idx % cols_per_row]
            with c:
                item = build_item(it["name"], it["symbol"])
                render_card(item)

        st.divider()

if __name__ == "__main__":
    main()
