# --- 必要ライブラリ（Colab用）---
# -*- coding: utf-8 -*-


import yfinance as yf
import pandas as pd
import matplotlib.pyplot as plt
import pytz
import logging
import warnings
from datetime import datetime, timedelta, timezone

# =========================================================
# うるさい表示を抑止
# =========================================================
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
logging.getLogger("urllib3").setLevel(logging.CRITICAL)
warnings.filterwarnings("ignore", message="Glyph .* missing from font")
warnings.filterwarnings("ignore", category=UserWarning)

# =========================================================
# 設定
# =========================================================
LOOKBACK_DAYS = 220
PLOT_LAST_N = 60

# 2列レイアウト（指数用）
DASH_FIGSIZE_W = 12
ROW_HEIGHT = 2.9

# X軸日付フォント（重なり対策）
X_LABELSIZE = 7

AUTO_ADJUST = False

# 日本だけ寄り付き基準（取引時間中に限る）
JAPAN_OPEN_BASIS_ONLY = True

JST = pytz.timezone("Asia/Tokyo")

# =========================================================
# JPX取引時間（簡易）
# 前場: 09:00-11:30 / 後場: 12:30-15:30 (JST)
# =========================================================
def is_jpx_session_open(now_jst: datetime) -> bool:
    if now_jst.weekday() >= 5:
        return False
    t = now_jst.time()
    morning = (t >= datetime.strptime("09:00", "%H:%M").time()) and (t <= datetime.strptime("11:30", "%H:%M").time())
    afternoon = (t >= datetime.strptime("12:30", "%H:%M").time()) and (t <= datetime.strptime("15:30", "%H:%M").time())
    return morning or afternoon

# =========================================================
# 表示グループ（色分け）
# =========================================================
REGION_STYLE = {
    "JP":   {"edge": "#1f77b4", "title_bg": "#dbe9ff", "label": "日本"},
    "US":   {"edge": "#ff7f0e", "title_bg": "#ffe7cc", "label": "米国"},
    "EU":   {"edge": "#2ca02c", "title_bg": "#ddf5dd", "label": "欧州"},
    "ASIA": {"edge": "#d62728", "title_bg": "#ffd9d9", "label": "アジア"},
    "FX":   {"edge": "#9467bd", "title_bg": "#efe1ff", "label": "為替"},
}

# =========================================================
# 取得対象
#  - 日経CFD/先物ミニは環境差が大きいので候補複数。取れたら採用、取れなければ黙ってスキップ。
#  - CAC100指定→取得安定のためCAC40で代替（名称に明記）
#  - グロース250は指数ティッカーが安定しないためETF(2516.T)で代替
# =========================================================
TARGETS = [
    # 日本
    {"name": "日経平均", "region": "JP", "candidates": ["^N225"], "type": "INDEX"},
    {"name": "日経平均CFD(候補)", "region": "JP", "candidates": ["JPN225", "JP225", "^JP225"], "type": "INDEX"},
    {"name": "日経平均先物(ミニ含む候補)", "region": "JP", "candidates": ["MNI=F", "NIY=F", "NKD=F"], "type": "FUT"},
    {"name": "TOPIX", "region": "JP", "candidates": ["998405.T"], "type": "INDEX"},
    {"name": "東証グロース250(ETF代替)", "region": "JP", "candidates": ["2516.T"], "type": "INDEX"},

    # 米国
    {"name": "ダウ平均", "region": "US", "candidates": ["^DJI"], "type": "INDEX"},
    {"name": "NASDAQ総合", "region": "US", "candidates": ["^IXIC"], "type": "INDEX"},
    {"name": "S&P500", "region": "US", "candidates": ["^GSPC"], "type": "INDEX"},
    {"name": "半導体指数(SOX)", "region": "US", "candidates": ["^SOX"], "type": "INDEX"},
    {"name": "NYSE FANG+指数", "region": "US", "candidates": ["^NYFANG"], "type": "INDEX"},

    # 欧州
    {"name": "英FTSE100", "region": "EU", "candidates": ["^FTSE"], "type": "INDEX"},
    {"name": "独DAX", "region": "EU", "candidates": ["^GDAXI"], "type": "INDEX"},
    {"name": "仏CAC40(※CAC100代替)", "region": "EU", "candidates": ["^FCHI"], "type": "INDEX"},

    # アジア
    {"name": "香港ハンセン", "region": "ASIA", "candidates": ["^HSI"], "type": "INDEX"},
    {"name": "中国 上海総合", "region": "ASIA", "candidates": ["000001.SS"], "type": "INDEX"},
    {"name": "インド NIFTY50", "region": "ASIA", "candidates": ["^NSEI"], "type": "INDEX"},

    # 為替（別枠）
    {"name": "ドル円(USD/JPY)", "region": "FX", "candidates": ["USDJPY=X"], "type": "FX"},
]

# =========================================================
# yfinance取得（例外は握りつぶして空を返す）
# =========================================================
def fetch_daily(symbol: str) -> pd.DataFrame:
    try:
        end_utc = datetime.now(timezone.utc)
        start_utc = end_utc - timedelta(days=LOOKBACK_DAYS)

        hist = yf.Ticker(symbol).history(
            start=start_utc, end=end_utc,
            interval="1d", auto_adjust=AUTO_ADJUST
        )
        if hist is None or hist.empty:
            return pd.DataFrame()

        if hist.index.tz is None:
            hist.index = hist.index.tz_localize("UTC")
        hist = hist.tz_convert(JST)

        hist = hist.dropna(subset=["Close"])
        return hist
    except Exception:
        return pd.DataFrame()

def fetch_intraday_1m(symbol: str) -> pd.DataFrame:
    try:
        intra = yf.Ticker(symbol).history(period="1d", interval="1m")
        if intra is None or intra.empty:
            return pd.DataFrame()
        if intra.index.tz is None:
            intra.index = intra.index.tz_localize("UTC")
        intra = intra.tz_convert(JST)
        intra = intra.dropna(subset=["Close"])
        return intra
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

def choose_symbol(candidates):
    for sym in candidates:
        d = fetch_daily(sym)
        if not d.empty and len(d) >= 2:
            return sym, d
    return None, pd.DataFrame()

# =========================================================
# 計算
#  - 日本：取引時間中のみ寄り付き基準（Open→Now）＋前日比併記
#  - その他：基本は前日比（PrevClose→Now）。intraday取れたらNowを最新値にする程度。
# =========================================================
def compute_info(symbol: str, daily: pd.DataFrame, region: str):
    close = daily["Close"].dropna()
    prev_close = float(close.iloc[-2])
    last_close = float(close.iloc[-1])

    now_jst = datetime.now(JST)
    intra = fetch_intraday_1m(symbol)

    # "Now" を作る（intradayが取れれば最新Close、ダメならquote、さらにダメならlast_close）
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

    # モード判定（見た目用）
    mode = "LIVE" if (not intra.empty) else "CLOSE"

    # 日本だけ寄り付き基準（取引時間中かつintraday有り）
    open_price = None
    pct_open = None

    if region == "JP" and JAPAN_OPEN_BASIS_ONLY:
        if is_jpx_session_open(now_jst) and (not intra.empty):
            try:
                open_price = float(intra["Open"].dropna().iloc[0])
            except Exception:
                open_price = None
            if open_price is not None and open_price != 0:
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

# =========================================================
# 描画（2列ダッシュボード）
#  - 地域で枠線＆タイトル背景色を変える
# =========================================================
def style_axes(ax, region: str):
    st = REGION_STYLE.get(region, {})
    edge = st.get("edge", "#333333")
    title_bg = st.get("title_bg", "#f2f2f2")

    # 枠線
    for spine in ax.spines.values():
        spine.set_edgecolor(edge)
        spine.set_linewidth(2.0)

    # タイトル背景（bbox）
    return title_bg, edge

def plot_dashboard(items, title):
    """
    items: list of dict
      dict keys: name, symbol, region, daily, info_text
    """
    if not items:
        print(f"{title}: 表示できるデータがありません")
        return

    n = len(items)
    rows = (n + 1) // 2

    fig, axes = plt.subplots(rows, 2, figsize=(DASH_FIGSIZE_W, rows * ROW_HEIGHT))
    if rows == 1:
        axes = [axes[0], axes[1]] if isinstance(axes, (list, tuple)) else axes.flatten()
    else:
        axes = axes.flatten()

    fig.suptitle(title, fontsize=14, y=1.02)

    for i, it in enumerate(items):
        ax = axes[i]
        close = it["daily"]["Close"].tail(PLOT_LAST_N)

        ax.plot(close.index, close.values)

        # 情報ボックス
        ax.text(
            0.98, 0.98, it["info_text"],
            transform=ax.transAxes,
            ha="right", va="top",
            fontsize=8,
            bbox=dict(boxstyle="round", alpha=0.85, pad=0.3)
        )

        # 地域別スタイル
        title_bg, edge = style_axes(ax, it["region"])

        ax.set_title(f'{it["name"]} ({it["symbol"]})', fontsize=10,
                     bbox=dict(facecolor=title_bg, edgecolor=edge, boxstyle="round,pad=0.25"))

        ax.set_xlabel("Date (JST)", fontsize=8)
        ax.set_ylabel("Price / Index", fontsize=8)

        # ★ 日付フォント小さく（重なり対策）
        ax.tick_params(axis="x", labelsize=X_LABELSIZE)

        ax.grid(True)
        ax.margins(x=0.03)

    # 余った枠を消す
    for j in range(i + 1, len(axes)):
        axes[j].axis("off")

    plt.tight_layout()
    plt.show()

def plot_fx_box(fx_item):
    """
    為替を別枠で大きめに表示（1枚だけ）
    """
    if fx_item is None:
        print("為替: 表示できるデータがありません")
        return

    daily = fx_item["daily"]
    close = daily["Close"].tail(PLOT_LAST_N)

    fig, ax = plt.subplots(figsize=(DASH_FIGSIZE_W, 3.2))
    ax.plot(close.index, close.values)

    title_bg, edge = style_axes(ax, fx_item["region"])
    ax.set_title(f'{fx_item["name"]} ({fx_item["symbol"]})', fontsize=12,
                 bbox=dict(facecolor=title_bg, edgecolor=edge, boxstyle="round,pad=0.25"))

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
    plt.show()

# =========================================================
# 実行：取得 → グルーピング → 描画
# =========================================================
print(f"Run at (JST): {datetime.now(JST):%Y-%m-%d %H:%M:%S}")

indices_items = []
fx_item = None

# 地域順序（日本→米国→欧州→アジア）
region_order = {"JP": 0, "US": 1, "EU": 2, "ASIA": 3, "FX": 99}

for t in TARGETS:
    name, region = t["name"], t["region"]
    sym, daily = choose_symbol(t["candidates"])

    # 取れないものは黙ってスキップ（画面を汚さない）
    if sym is None or daily.empty:
        continue

    info = compute_info(sym, daily, region)

    # 表示テキスト（日本だけ寄り付き基準が出る）
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

    info_text = "\n".join(lines)

    item = {
        "name": name,
        "symbol": sym,
        "region": region,
        "daily": daily,
        "info_text": info_text,
        "order": region_order.get(region, 99),
    }

    if region == "FX":
        fx_item = item
    else:
        indices_items.append(item)

# 日本→米国→欧州→アジアの順に並べる（同地域内はTARGETS順を維持したいので stable sort）
indices_items = sorted(indices_items, key=lambda x: x["order"])

# セクションタイトル（色分けの凡例っぽく）
legend = " / ".join([f'{REGION_STYLE[k]["label"]}' for k in ["JP","US","EU","ASIA"]])
plot_dashboard(indices_items, f"Market Dashboard（{legend}）")

# 為替は別枠

plot_fx_box(fx_item)


