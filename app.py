# -*- coding: utf-8 -*-
"""
Market Dashboard (Streamlit) - 改善版 v2.6

変更点 (v2.5 → v2.6):
1. 日本版Fear & Greed Indexにも3年/1年/3ヶ月/全期間タブを追加（米国版と同仕様）
2. 日経VI（ボラティリティ指数）セクションを新規追加
   - ^N225の実現ボラティリティから算出
   - 3年/1年/3ヶ月/全期間タブ表示
3. VIX指数（米国版）に3年/1年/3ヶ月/全期間タブを追加
4. 日経平均予測スコアセクションを新規追加
   - VIX、原油、ドル円、モメンタム、RSI等の複合シグナルから
     「明日」「今週」の上昇/下降確率を算出・表示
"""

import os
import logging
import warnings
import re
import html
import io
import datetime as dt
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import wraps
import time

import pytz
import pandas as pd
import yfinance as yf
import requests
from bs4 import BeautifulSoup
import feedparser

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.font_manager as fm
import numpy as np

import streamlit as st

# Plotly（インタラクティブチャート）
try:
    import plotly.graph_objects as go
    import plotly.express as px
    PLOTLY_AVAILABLE = True
except ImportError:
    PLOTLY_AVAILABLE = False

# ── アクセス解析モジュール ────────────────────────────────
try:
    from analytics import track_pageview, render_analytics_dashboard, inject_client_info_collector
    from analytics import _sheets_ws as _anl_sheets_ws, _secret as _anl_secret
    ANALYTICS_AVAILABLE = True
except ImportError:
    ANALYTICS_AVAILABLE = False
    def track_pageview(*a, **kw):
        pass
    def render_analytics_dashboard():
        pass
    def _anl_sheets_ws(tab, headers):
        return None
    def _anl_secret(key, default=""):
        return default
    def inject_client_info_collector():
        pass

# streamlit-analytics2 は削除済み（Streamlit互換性バグのため）
ANALYTICS2_AVAILABLE = False



# PDFテキスト抽出
try:
    import pdfplumber
    PDFPLUMBER_AVAILABLE = True
except ImportError:
    PDFPLUMBER_AVAILABLE = False

try:
    import google.generativeai as genai
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False


# ===========================
# 基本設定
# ===========================
JST = pytz.timezone("Asia/Tokyo")

# ページ設定
st.set_page_config(
    page_title="Market Dashboard | リアルタイム株価・センチメント・Fear&Greed",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={
        "Get Help": "https://github.com/",
        "Report a bug": None,
        "About": "Market Dashboard — AI Sentiment Index (Claude Edition) | リアルタイム市場データ",
    }
)

# ===========================
# ① 国ブロック（セキュリティリスク高い国をブロック）
# ===========================
BLOCKED_COUNTRIES = {"CN", "RU", "KP"}   # 中国・ロシア・北朝鮮

def check_country_block() -> bool:
    """
    ipinfo.io でアクセス元国を判定し、ブロック対象なら True を返す。
    判定はセッション内で1回だけ実行しキャッシュする。
    """
    if st.session_state.get("_country_checked"):
        return st.session_state.get("_country_blocked", False)

    st.session_state["_country_checked"] = True
    country = "??"
    try:
        ip = ""
        try:
            forwarded = st.context.headers.get("x-forwarded-for", "")
            if forwarded:
                ip = forwarded.split(",")[0].strip()
        except Exception:
            pass

        token = get_env_var("IPINFO_TOKEN", "")
        url = f"https://ipinfo.io/{ip}/json" if ip else "https://ipinfo.io/json"
        if token:
            url += f"?token={token}"
        r = requests.get(url, timeout=4, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200:
            country = r.json().get("country", "??")
    except Exception:
        pass

    blocked = country in BLOCKED_COUNTRIES
    st.session_state["_country_blocked"] = blocked
    st.session_state["_visitor_country"] = country
    if blocked:
        logger.warning(f"[BLOCK] country={country} blocked")
    return blocked

# ===========================
# ② SEO / OGP メタタグ注入
# ===========================
_SEO_META = """
<!-- Google Search Console 所有権確認 -->
<meta name="google-site-verification" content="Q8ES9p_0ajIKz1P0jSWTLC1lYGoke5raHQUyTJTaU0M" />

<meta name="description"
  content="Market Dashboard — 日米株式・為替・コモディティのリアルタイム価格、AI Sentiment Index (Claude Edition)、Fear&Greed Index、NAAIM Exposure Index、日経平均予測スコアを一画面で確認できる無料マーケット情報ツール。">
<meta name="keywords"
  content="Market Dashboard, 株価, リアルタイム, Fear Greed Index, センチメント, AI Sentiment Index, Claude, 日経平均, S&P500, VIX, NAAIM, 為替, 投資, 市場分析">
<meta name="robots" content="index, follow">
<meta name="author" content="Market Dashboard">
<meta name="theme-color" content="#1f77b4">

<!-- OGP (Open Graph) -->
<meta property="og:type"        content="website">
<meta property="og:title"       content="Market Dashboard | リアルタイム株価・AIセンチメント・Fear&Greed">
<meta property="og:description" content="日米株式・為替・コモディティのリアルタイム価格と AI Sentiment Index (Claude Edition) を一画面で。Fear&Greed Index・NAAIM・日経予測スコアも収録。">
<meta property="og:image"       content="https://market-dashboard.streamlit.app/app/static/og_image.png">
<meta property="og:url"         content="https://market-dashboard.streamlit.app/">
<meta property="og:site_name"   content="Market Dashboard">
<meta property="og:locale"      content="ja_JP">

<!-- Twitter Card -->
<meta name="twitter:card"        content="summary_large_image">
<meta name="twitter:title"       content="Market Dashboard | リアルタイム株価・AIセンチメント">
<meta name="twitter:description" content="日米株式・為替・AI Sentiment Index (Claude Edition)・Fear&Greed Index をリアルタイムで確認。">
<meta name="twitter:image"       content="https://market-dashboard.streamlit.app/app/static/og_image.png">

<!-- Canonical URL -->
<link rel="canonical" href="https://market-dashboard.streamlit.app/">

<!-- 構造化データ (JSON-LD) -->
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "WebApplication",
  "name": "Market Dashboard",
  "description": "リアルタイム株価・AI Sentiment Index・Fear&Greed Indexを提供する市場情報ダッシュボード",
  "url": "https://market-dashboard.streamlit.app/",
  "applicationCategory": "FinanceApplication",
  "operatingSystem": "Web",
  "inLanguage": ["ja", "en"],
  "offers": {"@type": "Offer", "price": "0", "priceCurrency": "JPY"}
}
</script>
"""

def inject_seo_meta():
    """
    SEO・OGPメタタグ + GTM を複数手法で注入。
    st.markdown + components.html を併用して
    Googlebotにできるだけ見えやすくする。
    """
    # 手法①: st.markdown（通常のDOM注入）
    st.markdown(_SEO_META, unsafe_allow_html=True)

    # 手法②: GTM + meta を親フレームheadに直接inject
    st.html(
        """
        <!-- Google Tag Manager -->
        <script>(function(w,d,s,l,i){w[l]=w[l]||[];w[l].push({'gtm.start':
        new Date().getTime(),event:'gtm.js'});var f=d.getElementsByTagName(s)[0],
        j=d.createElement(s),dl=l!='dataLayer'?'&l='+l:'';j.async=true;j.src=
        'https://www.googletagmanager.com/gtm.js?id='+i+dl;f.parentNode.insertBefore(j,f);
        })(window,document,'script','dataLayer','GTM-MSPS3KGR');</script>
        <!-- End Google Tag Manager -->

        <script>
        (function() {
            // GTM スニペットを親フレーム <head> に注入
            try {
                if (!window.parent.document.querySelector('script[src*="GTM-MSPS3KGR"]')) {
                    var gtmScript = document.createElement('script');
                    gtmScript.async = true;
                    gtmScript.src = 'https://www.googletagmanager.com/gtm.js?id=GTM-MSPS3KGR';
                    window.parent.document.head.appendChild(gtmScript);

                    // dataLayer 初期化
                    window.parent.dataLayer = window.parent.dataLayer || [];
                    window.parent.dataLayer.push({'gtm.start': new Date().getTime(), event:'gtm.js'});
                }
            } catch(e) {}

            // google-site-verification を親フレームheadに注入
            try {
                if (!window.parent.document.querySelector('meta[name="google-site-verification"]')) {
                    var gv = document.createElement('meta');
                    gv.name    = 'google-site-verification';
                    gv.content = 'Q8ES9p_0ajIKz1P0jSWTLC1lYGoke5raHQUyTJTaU0M';
                    window.parent.document.head.appendChild(gv);
                }
            } catch(e) {}

            // その他メタタグを親フレームheadに注入
            var metas = [
                ['description', 'Market Dashboard — 日米株式・為替・コモディティのリアルタイム価格、AI Sentiment Index (Claude Edition)、Fear&Greed Index、NAAIM Exposure Index'],
                ['robots', 'index, follow'],
            ];
            metas.forEach(function(pair) {
                try {
                    if (!window.parent.document.querySelector('meta[name="' + pair[0] + '"]')) {
                        var m = document.createElement('meta');
                        m.name    = pair[0];
                        m.content = pair[1];
                        window.parent.document.head.appendChild(m);
                    }
                } catch(e) {}
            });
        })();
        </script>

        <!-- GTM noscript（bodyタグ直後用） -->
        <noscript>
          <iframe src="https://www.googletagmanager.com/ns.html?id=GTM-MSPS3KGR"
                  height="0" width="0" style="display:none;visibility:hidden"></iframe>
        </noscript>
        """)

# ログ設定
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 不要なログを抑止
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
logging.getLogger("urllib3").setLevel(logging.CRITICAL)
warnings.filterwarnings("ignore", message="Glyph .* missing from font")
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", message="More than 20 figures")
import matplotlib
matplotlib.rcParams['figure.max_open_warning'] = 50

# ===========================
# 環境変数・設定
# ===========================
def get_env_var(key: str, default: str = "") -> str:
    """環境変数を安全に取得"""
    try:
        if key in st.secrets:
            return str(st.secrets[key])
    except Exception:
        pass
    return os.getenv(key, default)

# API Keys
GA_MEASUREMENT_ID  = get_env_var("GA_MEASUREMENT_ID", "")
TIINGO_API_KEY     = get_env_var("TIINGO_API_KEY", "")
DEEPL_API_KEY      = get_env_var("DEEPL_API_KEY", "")
GEMINI_API_KEY     = get_env_var("GEMINI_API_KEY", "")
GROQ_API_KEY       = get_env_var("GROQ_API_KEY", "")
OPENROUTER_API_KEY = get_env_var("OPENROUTER_API_KEY", "")
FINNHUB_API_KEY    = get_env_var("FINNHUB_API_KEY", "")
ALPHA_VANTAGE_KEY  = get_env_var("ALPHA_VANTAGE_KEY", "")
FMP_API_KEY        = get_env_var("FMP_API_KEY", "")
FRED_API_KEY       = get_env_var("FRED_API_KEY", "")

# Gemini設定
if GENAI_AVAILABLE and GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel("gemini-1.5-flash")
        logger.info("✅ Gemini initialized: gemini-1.5-flash")
    except Exception as e:
        logger.warning(f"Gemini initialization failed: {e}")
        try:
            model = genai.GenerativeModel("gemini-1.5-flash-8b")
            logger.info("✅ Gemini initialized (fallback): gemini-1.5-flash-8b")
        except Exception:
            model = None
else:
    model = None

# TDnet設定
TDNET_LIST_URL = "https://www.release.tdnet.info/inbs/I_list_001_{yyyymmdd}.html"
TDNET_BASE = "https://www.release.tdnet.info"
MAX_PDF_TEXT_CHARS = 12000
DEFAULT_TIMEOUT = 20
UA = "Mozilla/5.0 (MarketDashboard/2.6)"

# Geminiモデル候補（2025年現在の有効なモデル名順）
MODEL_FALLBACKS = [
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-1.5-flash",
    "gemini-1.5-flash-8b",
    "gemini-1.5-pro",
]

# ===========================
# キャッシュ設定
# ===========================
TTL_DAILY = 3600
TTL_INTRADAY = 300
TTL_RSS = 1800
TTL_MARKET_NEWS = 60 * 60 * 4
TTL_CHART = 600

# リトライ設定
MAX_RETRIES = 3
RETRY_DELAY = 1

# カラー設定
GREEN = "#1a7f37"
RED = "#d1242f"
BG_UP = "rgba(26,127,55,0.08)"
BG_DN = "rgba(209,36,47,0.08)"
BG_NEUTRAL = "rgba(0,0,0,0.03)"
LINE_NEUTRAL = "#1f77b4"

# ===========================
# ユーティリティ関数
# ===========================
def retry_on_failure(max_retries: int = MAX_RETRIES, delay: float = RETRY_DELAY):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        time.sleep(delay * (attempt + 1))
            logger.error(f"{func.__name__} failed after {max_retries} attempts: {last_exception}")
            raise last_exception
        return wrapper
    return decorator

def sanitize_html(text: str) -> str:
    return html.escape(str(text))


def is_ipad_or_ios_safari() -> bool:
    """iPad / iOS Safari を判定（JSリロードをスキップするため）"""
    ua = ""
    try:
        ua = str(st.context.headers.get("user-agent", ""))
    except Exception:
        pass
    ua_l = ua.lower()
    if "ipad" in ua_l or "iphone" in ua_l:
        return True
    if "safari" in ua_l and "chrome" not in ua_l and "crios" not in ua_l and "fxios" not in ua_l:
        if "macintosh" in ua_l or "mobile" in ua_l:
            return True
    return False


# ── 言語ユーティリティ ────────────────────────────────────────────
def _detect_lang() -> str:
    """HTTP Accept-Language ヘッダー + IP国コードで言語を自動判定。'ja' or 'en' を返す。"""
    try:
        accept = str(st.context.headers.get("accept-language", "")).lower()
        if accept.startswith("ja"):
            return "ja"
    except Exception:
        pass
    # セッションに既に country が保存されていれば流用
    country = st.session_state.get("_anl_country", "")
    if not country:
        try:
            country = fetch_ip_info_server_side().get("country", "")
        except Exception:
            country = ""
    return "ja" if country == "JP" else "en"


def t(ja: str, en: str) -> str:
    """現在のセッション言語に合わせたテキストを返す。"""
    return en if st.session_state.get("lang") == "en" else ja


def _lang_prompt_suffix() -> str:
    """AI プロンプトに追加する言語指示を返す。"""
    if st.session_state.get("lang") == "en":
        return "\n\nIMPORTANT: Respond entirely in English. Do not use Japanese."
    return ""


def _lang_key(base: str) -> str:
    """セッション state のキーを言語ごとに分ける。"""
    return f"{base}_{st.session_state.get('lang', 'ja')}"


def fetch_ip_info_server_side() -> dict:
    """
    サーバー側でipinfo.ioを呼んでIP・地域を取得。
    iPad/iOS Safari でJSリロードができない場合のフォールバック。
    """
    try:
        # st.context.headersからX-Forwarded-Forを取得
        ip = ""
        try:
            forwarded = st.context.headers.get("x-forwarded-for", "")
            if forwarded:
                ip = forwarded.split(",")[0].strip()
        except Exception:
            pass

        token = ""
        try:
            token = str(st.secrets.get("IPINFO_TOKEN", ""))
        except Exception:
            pass

        url = f"https://ipinfo.io/{ip}/json" if ip else "https://ipinfo.io/json"
        if token:
            url += f"?token={token}"

        r = requests.get(url, timeout=4, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200:
            data = r.json()
            return {
                "country": data.get("country", "??"),
                "city":    data.get("city",    "??"),
                "ip":      data.get("ip",       ""),
            }
    except Exception as e:
        logger.debug(f"fetch_ip_info_server_side: {e}")
    return {"country": "??", "city": "??", "ip": ""}

def validate_symbol(symbol: str) -> bool:
    if not symbol or len(symbol) > 20:
        return False
    return bool(re.match(r'^[A-Z0-9\.\-\^=]+$', symbol.upper()))

def is_gemini_quota_error(e: Exception) -> bool:
    err_str = str(e).lower()
    return any(keyword in err_str for keyword in [
        "429", "quota", "resource_exhausted", "rate limit",
        "too many requests", "limit exceeded"
    ])

# ===========================
# Google Analytics
# ===========================
def inject_ga():
    if not GA_MEASUREMENT_ID or not GA_MEASUREMENT_ID.startswith("G-"):
        return
    # height=1 にしないとiframeが描画されずスクリプトが実行されない
    st.html(
        f"""
        <script async src="https://www.googletagmanager.com/gtag/js?id={sanitize_html(GA_MEASUREMENT_ID)}"></script>
        <script>
          window.dataLayer = window.dataLayer || [];
          function gtag(){{dataLayer.push(arguments);}}
          gtag('js', new Date());
          gtag('config', '{sanitize_html(GA_MEASUREMENT_ID)}', {{
            'send_page_view': true,
            'transport_type': 'beacon'
          }});
        </script>
        """)
# inject_ga()  # disabled by patch

def track_page_view():
    if not GA_MEASUREMENT_ID:
        return
    st.html(
        """
        <script>
        (function() {
            function sendGA() {
                if (typeof gtag !== 'undefined') {
                    gtag('event', 'page_view', {
                        page_title: document.title,
                        page_location: window.location.href
                    });
                } else {
                    setTimeout(sendGA, 500);
                }
            }
            sendGA();
        })();
        </script>
        """)
# track_page_view()  # disabled by patch

# ===========================
# フォント設定
# ===========================
def setup_japanese_font() -> str:
    candidates = [
        "fonts/NotoSansCJKjp-Regular.otf",
        "fonts/NotoSansJP-Regular.otf",
        "fonts/IPAexGothic.ttf",
        "fonts/ipaexg.ttf",
    ]
    for fp in candidates:
        if os.path.exists(fp):
            try:
                fm.fontManager.addfont(fp)
                prop = fm.FontProperties(fname=fp)
                font_name = prop.get_name()
                matplotlib.rcParams["font.family"] = font_name
                return font_name
            except Exception as e:
                logger.debug(f"Font loading failed: {fp} - {e}")
    matplotlib.rcParams["font.family"] = "DejaVu Sans"
    return "DejaVu Sans"

FONT_NAME = setup_japanese_font()

# ===========================
# Yahoo Chartリンク生成
# ===========================
def get_yahoo_chart_url(symbol: str, market: str = "US") -> str:
    import urllib.parse
    if symbol.endswith(".T"):
        safe = urllib.parse.quote(symbol, safe="-=.")
        return f"https://finance.yahoo.co.jp/quote/{safe}"
    if symbol.startswith("^"):
        safe = urllib.parse.quote(symbol, safe="-=^.")
        return f"https://finance.yahoo.com/quote/{safe}"
    if "=F" in symbol or "=X" in symbol:
        safe = urllib.parse.quote(symbol, safe="-=.")
        return f"https://finance.yahoo.com/quote/{safe}"
    if symbol.endswith(".SS") or symbol.endswith(".SZ"):
        safe = urllib.parse.quote(symbol, safe="-=.")
        return f"https://finance.yahoo.com/quote/{safe}"
    safe = urllib.parse.quote(symbol, safe="-=.")
    return f"https://finance.yahoo.com/quote/{safe}"

# ===========================
# 市場データ定義
# ===========================
MARKETS = {
    "日本": [
        {"name": "日経平均", "symbol": "^N225", "flag": "JP"},
        {"name": "TOPIX（ETF）", "symbol": "1306.T", "flag": "JP"},
        {"name": "グロース250（ETF）", "symbol": "2516.T", "flag": "JP"},
    ],
    "先物・CFD（時間外取引対応）": [
        {"name": "日経平均先物(CME大型)", "symbol": "NKD=F",  "flag": "JP",
         "note": "CME 日経225先物 ドル建て。日曜17:00(JST)から取引。"},
        {"name": "日経平均CFD(参考)",    "symbol": "JP225=X", "flag": "JP",
         "note": "yfinance CFD参考値。取得できない場合はNKD=Fで代替表示。",
         "fallback_symbol": "NKD=F"},
        {"name": "NYダウ先物(サンデー)", "symbol": "YM=F",   "flag": "US",
         "note": "CME E-mini Dow先物。日曜17:00(ET)から取引開始。"},
        {"name": "S&P500先物(サンデー)", "symbol": "ES=F",   "flag": "US",
         "note": "CME E-mini S&P500先物。最も流動性が高い株価指数先物。"},
        {"name": "ナスダック先物(サンデー)", "symbol": "NQ=F", "flag": "US",
         "note": "CME E-mini Nasdaq100先物。テック株動向の先行指標。"},
    ],
    "日本（個別株）": [
        {"name": "フジクラ", "symbol": "5803.T", "flag": "JP", "provider": "tiingo"},
        {"name": "三菱重工", "symbol": "7011.T", "flag": "JP", "provider": "tiingo"},
        {"name": "三菱商事", "symbol": "8058.T", "flag": "JP"},
        {"name": "ＩＨＩ", "symbol": "7013.T", "flag": "JP"},
        {"name": "伊藤忠商事", "symbol": "8001.T", "flag": "JP"},
        {"name": "三菱UFJ", "symbol": "8306.T", "flag": "JP"},
        {"name": "トヨタ自動車", "symbol": "7203.T", "flag": "JP"},
        {"name": "ソニーG", "symbol": "6758.T", "flag": "JP"},
        {"name": "任天堂", "symbol": "7974.T", "flag": "JP"},
        {"name": "フジクラ ADR", "symbol": "FJIKY", "flag": "US"},
        {"name": "三重 ADR", "symbol": "MHVIY", "flag": "US"},
        {"name": "IHI ADR", "symbol": "IHICY", "flag": "US"},
    ],
    "アジア": [
        {"name": "香港ハンセン", "symbol": "^HSI", "flag": "HK"},
        {"name": "中国 上海総合", "symbol": "000001.SS", "flag": "CN"},
        {"name": "インド NIFTY50", "symbol": "^NSEI", "flag": "IN"},
        {"name": "韓国 KOSPI", "symbol": "^KS11", "flag": "KR"},
        {"name": "台湾 加権", "symbol": "^TWII", "flag": "TW"},
    ],
    "欧州": [
        {"name": "英FTSE100", "symbol": "^FTSE", "flag": "UK"},
        {"name": "独DAX", "symbol": "^GDAXI", "flag": "DE"},
        {"name": "仏CAC40", "symbol": "^FCHI", "flag": "FR"},
    ],
    "米国": [
        {"name": "ダウ平均", "symbol": "^DJI", "flag": "US"},
        {"name": "NASDAQ", "symbol": "^IXIC", "flag": "US"},
        {"name": "S&P500", "symbol": "^GSPC", "flag": "US"},
        {"name": "半導体（SOX）", "symbol": "^SOX", "flag": "US"},
        {"name": "恐怖指数（VIX）", "symbol": "^VIX", "flag": "US"},
        {"name": "Russell2000", "symbol": "^RUT", "flag": "US"},
        {"name": "NASDAQ100", "symbol": "^NDX", "flag": "US", "rt_symbol": "NQ=F"},
        {"name": "FANG+", "symbol": "^NYFANG", "flag": "US"},
    ],
    "米国（債券）": [
        {"name": "米5年金利", "symbol": "^FVX", "flag": "US"},
        {"name": "米10年金利", "symbol": "^TNX", "flag": "US"},
        {"name": "米30年金利", "symbol": "^TYX", "flag": "US"},
        {"name": "米国債先物(30Y)", "symbol": "ZB=F", "flag": "US"},
    ],
    "全世界株式": [
        {"name": "全世界株式(VT)", "symbol": "VT", "flag": "WORLD"},
        {"name": "全世界株式(ACWI)", "symbol": "ACWI", "flag": "WORLD"},
    ],
    "Magnificent 7": [
        {"name": "Apple", "symbol": "AAPL", "flag": "US"},
        {"name": "Microsoft", "symbol": "MSFT", "flag": "US"},
        {"name": "Alphabet", "symbol": "GOOGL", "flag": "US"},
        {"name": "Amazon", "symbol": "AMZN", "flag": "US"},
        {"name": "NVIDIA", "symbol": "NVDA", "flag": "US"},
        {"name": "Meta", "symbol": "META", "flag": "US"},
        {"name": "Tesla", "symbol": "TSLA", "flag": "US"},
    ],
    "米国（個別株）": [
        {"name": "Broadcom", "symbol": "AVGO", "flag": "US"},
        {"name": "Micron", "symbol": "MU", "flag": "US"},
        {"name": "Corning", "symbol": "GLW", "flag": "US"},
        {"name": "Coherent", "symbol": "COHR", "flag": "US"},
        {"name": "Netflix", "symbol": "NFLX", "flag": "US"},
        {"name": "Palantir", "symbol": "PLTR", "flag": "US"},
        {"name": "Vertiv", "symbol": "VRT", "flag": "US"},
        {"name": "Arista", "symbol": "ANET", "flag": "US"},
        {"name": "Constellation", "symbol": "CEG", "flag": "US"},
        {"name": "SuperMicro", "symbol": "SMCI", "flag": "US"},
        {"name": "Intel", "symbol": "INTC", "flag": "US"},
        {"name": "Berkshire", "symbol": "BRK-B", "flag": "US"},
    ],
    "為替": [
        {"name": "ドル円", "symbol": "USDJPY=X", "flag": "FX"},
        {"name": "ユーロ円", "symbol": "EURJPY=X", "flag": "FX"},
        {"name": "ユーロドル", "symbol": "EURUSD=X", "flag": "FX"},
    ],
    "コモディティ": [
        {"name": "ゴールド", "symbol": "GC=F", "flag": "CMD"},
        {"name": "プラチナ", "symbol": "PL=F", "flag": "CMD"},
        {"name": "原油（WTI）", "symbol": "CL=F", "flag": "CMD"},
    ],
    "暗号資産": [
        {"name": "ビットコイン", "symbol": "BTC-USD", "flag": "CRYPTO"},
    ],
}

# ===========================
# Groq API（フォールバック第2候補）
# ===========================
def summarize_with_groq(prompt: str, max_tokens: int = 1500, temperature: float = 0.3) -> Tuple[str, str]:
    if not GROQ_API_KEY:
        return "⚠️ GROQ_API_KEY が設定されていません", ""
    GROQ_MODELS = [
        "llama-3.3-70b-versatile",
        "llama-3.1-8b-instant",
        "gemma2-9b-it",
    ]
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    for model_name in GROQ_MODELS:
        payload = {
            "model": model_name,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        try:
            resp = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers=headers, json=payload, timeout=30,
            )
            if resp.status_code == 429:
                continue
            if resp.status_code == 404:
                continue
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"].strip()
            if text:
                return text, model_name
        except requests.exceptions.Timeout:
            continue
        except Exception as e:
            logger.error(f"Groq error ({model_name}): {e}")
            continue
    return "⚠️ Groq: 全モデルで応答を取得できませんでした", ""


# ===========================
# OpenRouter API（フォールバック第3候補）
# ===========================
def summarize_with_openrouter(prompt: str, max_tokens: int = 1500, temperature: float = 0.3) -> Tuple[str, str]:
    if not OPENROUTER_API_KEY:
        return "⚠️ OPENROUTER_API_KEY が設定されていません", ""
    OPENROUTER_MODELS = [
        "meta-llama/llama-3.2-11b-vision-instruct:free",
        "meta-llama/llama-3.3-70b-instruct:free",
        "meta-llama/llama-3.1-8b-instruct:free",
        "google/gemma-2-9b-it:free",
        "mistralai/mistral-7b-instruct:free",
        "deepseek/deepseek-r1:free",
        "qwen/qwen-2.5-72b-instruct:free",
    ]
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://market-dashboard.streamlit.app",
        "X-Title": "Market Dashboard",
    }
    for model_name in OPENROUTER_MODELS:
        payload = {
            "model": model_name,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        try:
            resp = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers, json=payload, timeout=45,
            )
            if resp.status_code == 429:
                continue
            if resp.status_code in (404, 400):
                continue
            if resp.status_code in (401, 403):
                return "⚠️ OpenRouter認証エラー。OPENROUTER_API_KEY を確認してください。", ""
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"].strip()
            if text:
                return text, model_name
        except requests.exceptions.Timeout:
            continue
        except Exception as e:
            logger.error(f"OpenRouter error ({model_name}): {e}")
            continue
    return "⚠️ OpenRouter: 全モデルで応答を取得できませんでした", ""


# ===========================
# AI呼び出し統合関数
# ===========================
def call_ai_with_fallback(prompt: str, max_output_tokens: int = 1500, temperature: float = 0.3) -> Tuple[str, str]:
    if GENAI_AVAILABLE and GEMINI_API_KEY:
        last_error_msg = ""
        quota_exceeded = False
        for model_name in MODEL_FALLBACKS:
            try:
                genai.configure(api_key=GEMINI_API_KEY)
                gemini_model = genai.GenerativeModel(model_name)
                response = gemini_model.generate_content(
                    prompt,
                    generation_config=genai.types.GenerationConfig(
                        max_output_tokens=max_output_tokens,
                        temperature=temperature,
                    )
                )
                if hasattr(response, "text") and response.text:
                    return response.text.strip(), f"Gemini ({model_name})"
                else:
                    last_error_msg = f"{model_name}: 空レスポンス"
                    continue
            except Exception as e:
                err_str = str(e)
                if is_gemini_quota_error(e):
                    quota_exceeded = True
                    last_error_msg = f"quota超過: {err_str[:120]}"
                    break
                elif any(k in err_str for k in ["404", "not found", "deprecated", "does not exist"]):
                    last_error_msg = f"{model_name}: 利用不可"
                    continue
                elif any(k in err_str for k in ["401", "403", "API_KEY", "invalid api key"]):
                    return (f"⚠️ Gemini認証エラー。GEMINI_API_KEY を確認してください。\n詳細: {err_str[:200]}", "none")
                else:
                    last_error_msg = f"{model_name}: {err_str[:120]}"
                    break

        if quota_exceeded or last_error_msg:
            if GROQ_API_KEY:
                result, groq_model = summarize_with_groq(prompt, max_tokens=max_output_tokens, temperature=temperature)
                if groq_model:
                    return result, f"Groq ({groq_model}) ※Gemini quota超過"
            if OPENROUTER_API_KEY:
                result, or_model = summarize_with_openrouter(prompt, max_tokens=max_output_tokens, temperature=temperature)
                if or_model:
                    return result, f"OpenRouter ({or_model}) ※Gemini/Groq失敗"
            return ("⚠️ Gemini quota超過・Groq失敗・OpenRouter未設定。", "none")

        return (f"⚠️ Gemini APIエラー。\n詳細: {last_error_msg}", "none")

    if GROQ_API_KEY:
        result, groq_model = summarize_with_groq(prompt, max_tokens=max_output_tokens, temperature=temperature)
        if groq_model:
            return result, f"Groq ({groq_model})"

    if OPENROUTER_API_KEY:
        result, or_model = summarize_with_openrouter(prompt, max_tokens=max_output_tokens, temperature=temperature)
        if or_model:
            return result, f"OpenRouter ({or_model})"

    return ("⚠️ AI APIが設定されていません。", "none")


def _call_ai_for_trading(
    prompt: str,
    model_pref: str = "auto",
    max_output_tokens: int = 900,
    temperature: float = 0.3,
) -> tuple:
    """トレーディング分析用AI呼び出し。model_pref でプロバイダーを指定できる。
    model_pref: "auto" | "gemini" | "groq" | "openrouter"
    Returns: (text: str, model_label: str)
    """
    if model_pref == "groq":
        text, model = summarize_with_groq(prompt, max_tokens=max_output_tokens, temperature=temperature)
        if model:
            return text, f"Groq ({model})"
        # Groq 失敗時は OpenRouter へ
        text, model = summarize_with_openrouter(prompt, max_tokens=max_output_tokens, temperature=temperature)
        return (text, f"OpenRouter ({model}) ※Groq失敗") if model else ("⚠️ Groq/OpenRouter 失敗", "none")

    elif model_pref == "openrouter":
        text, model = summarize_with_openrouter(prompt, max_tokens=max_output_tokens, temperature=temperature)
        if model:
            return text, f"OpenRouter ({model})"
        # OpenRouter 失敗時は Groq へ
        text, model = summarize_with_groq(prompt, max_tokens=max_output_tokens, temperature=temperature)
        return (text, f"Groq ({model}) ※OpenRouter失敗") if model else ("⚠️ OpenRouter/Groq 失敗", "none")

    elif model_pref == "gemini":
        # Gemini のみを試行、失敗時はそのままエラーを返す（他へは落とさない）
        if GENAI_AVAILABLE and GEMINI_API_KEY:
            for model_name in MODEL_FALLBACKS:
                try:
                    genai.configure(api_key=GEMINI_API_KEY)
                    gm = genai.GenerativeModel(model_name)
                    resp = gm.generate_content(
                        prompt,
                        generation_config=genai.types.GenerationConfig(
                            max_output_tokens=max_output_tokens,
                            temperature=temperature,
                        )
                    )
                    if hasattr(resp, "text") and resp.text:
                        return resp.text.strip(), f"Gemini ({model_name})"
                except Exception as e:
                    if is_gemini_quota_error(e):
                        return "⚠️ Gemini quota超過。モデルを切り替えてください。", "none"
                    continue
        return "⚠️ Gemini APIエラー", "none"

    else:  # auto
        return call_ai_with_fallback(prompt, max_output_tokens, temperature)


# ===========================
# 関連ダッシュボードリンク HTML
# ===========================
DASHBOARD_LINKS_HTML = """
<div style="
    background: linear-gradient(135deg, #e8eaf6 0%, #e8f5e9 100%);
    border: 1px solid #c5cae9;
    border-radius: 12px;
    padding: 16px 20px;
    margin-top: 16px;
    margin-bottom: 4px;
">
    <div style="
        display: flex;
        align-items: center;
        gap: 8px;
        font-weight: 700;
        font-size: 13px;
        color: #3949ab;
        margin-bottom: 12px;
        letter-spacing: 0.3px;
    ">
        🔗 関連ダッシュボード
    </div>
    <div style="display: flex; gap: 10px; flex-wrap: wrap; align-items: center;">
        <a href="https://usstock-metrics.streamlit.app/" target="_blank" rel="noopener noreferrer" style="
            display: inline-flex;
            align-items: center;
            gap: 7px;
            background: linear-gradient(135deg, #1565c0, #1976d2);
            color: #ffffff;
            padding: 10px 20px;
            border-radius: 8px;
            text-decoration: none;
            font-size: 13px;
            font-weight: 700;
            letter-spacing: 0.2px;
            box-shadow: 0 3px 10px rgba(21,101,192,0.40);
        ">
            🇺🇸&nbsp; USStockMetrics
        </a>
        <a href="https://jstock-metrics.streamlit.app/" target="_blank" rel="noopener noreferrer" style="
            display: inline-flex;
            align-items: center;
            gap: 7px;
            background: linear-gradient(135deg, #c62828, #e53935);
            color: #ffffff;
            padding: 10px 20px;
            border-radius: 8px;
            text-decoration: none;
            font-size: 13px;
            font-weight: 700;
            letter-spacing: 0.2px;
            box-shadow: 0 3px 10px rgba(198,40,40,0.40);
        ">
            🇯🇵&nbsp; JStockMetrics
        </a>
    </div>
    <div style="font-size: 11px; color: #777; margin-top: 10px; line-height: 1.5;">
        各ダッシュボードで詳細な銘柄分析・指標をご覧いただけます
    </div>
</div>
"""

def _build_nav_html(lang: str = "ja") -> str:
    """ページ内ナビゲーションバーの HTML を言語に合わせて生成する。"""
    _css = """<style>
.nav-btn {
    display:inline-flex !important;align-items:center;gap:5px;
    padding:5px 12px;border-radius:6px;text-decoration:none !important;
    font-size:12px;font-weight:700;white-space:nowrap;cursor:pointer;
    border:1px solid #444;
    background:#2a2a2a !important;color:#ffffff !important;
    transition:background 0.15s;
}
.nav-btn:hover { background:#3d3d3d !important;color:#ffffff !important; }
.nav-btn:visited { color:#ffffff !important; }
.ext-btn {
    display:inline-flex;align-items:center;gap:6px;
    padding:6px 14px;border-radius:7px;text-decoration:none !important;
    font-size:12px;font-weight:700;white-space:nowrap;
}
</style>"""
    if lang == "en":
        label_prefix = "📍 On this page"
        links = [
            ("#market-snapshot", "📊 Market"),
            ("#eco-calendar",    "📅 Eco Calendar"),
            ("#macro",           "🌐 Macro"),
            ("#bear-risk",       "🐻 Bear Risk"),
            ("#momentum",        "🚀 Momentum"),
            ("?page=trading",    "💹 Trading"),
            ("#optical-semi",    "📡 Optical vs Semi"),
            ("#earnings-forecast", "📊 Index Forecast"),
            ("#fear-greed",      "😱 Fear&amp;Greed"),
            ("#sector",          "🔄 Sectors"),
            ("#nikkei-pred",     "🔮 Nikkei Forecast"),
            ("#us-pred",         "🎯 US Forecast"),
        ]
    else:
        label_prefix = "📍 このページ内"
        links = [
            ("#market-snapshot", "📊 マーケット"),
            ("#eco-calendar",    "📅 経済イベント"),
            ("#macro",           "🌐 マクロ指標"),
            ("#bear-risk",       "🐻 弱気判定"),
            ("#momentum",        "🚀 モメンタム"),
            ("?page=trading",    "💹 売買"),
            ("#optical-semi",    "📡 光通信vs半導体"),
            ("#earnings-forecast", "📊 指数予測"),
            ("#fear-greed",      "😱 Fear&amp;Greed"),
            ("#sector",          "🔄 セクター"),
            ("#nikkei-pred",     "🔮 日経予測"),
            ("#us-pred",         "🎯 米国予測"),
        ]
    nav_items = "".join(f'<a class="nav-btn" href="{href}">{label}</a>' for href, label in links)
    return f"""{_css}
<div style="background:#111111;border:1px solid #333;border-radius:10px;
     padding:10px 16px;margin-bottom:8px;">
  <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
    <span style="font-size:11px;color:#888;font-weight:700;white-space:nowrap;">{label_prefix}</span>
    {nav_items}
    <span style="flex:1"></span>
    <a href="https://usstock-metrics.streamlit.app/" target="_blank" rel="noopener noreferrer"
       class="ext-btn"
       style="background:linear-gradient(135deg,#1565c0,#1976d2);color:#fff !important;
              box-shadow:0 2px 6px rgba(21,101,192,0.4);">
      🇺🇸&nbsp;USStockMetrics
    </a>
    <a href="https://jstock-metrics.streamlit.app/" target="_blank" rel="noopener noreferrer"
       class="ext-btn"
       style="background:linear-gradient(135deg,#c62828,#e53935);color:#fff !important;
              box-shadow:0 2px 6px rgba(198,40,40,0.4);">
      🇯🇵&nbsp;JStockMetrics
    </a>
  </div>
</div>"""


DASHBOARD_LINKS_TOP_HTML = _build_nav_html("ja")


# ===========================
# Market Board用ニュース表示
# ===========================
@st.cache_data(ttl=TTL_MARKET_NEWS, show_spinner=False)
def fetch_and_summarize_market_news(market: str = "日本", _cache_key: str = "", lang: str = "ja") -> Optional[str]:
    if not GENAI_AVAILABLE or not GEMINI_API_KEY:
        if not GROQ_API_KEY:
            return "__USED_API__:none\n⚠️ AI APIが設定されていません。"

    _en = (lang == "en")
    try:
        if market == "日本":
            feeds = [
                "https://news.yahoo.co.jp/rss/topics/stocks.xml",
                "https://news.yahoo.co.jp/rss/topics/business.xml",
                "https://www3.nhk.or.jp/rss/news/cat6.xml",
                "https://assets.wor.jp/rss/rdf/minkabufx/stock.rdf",
                "https://webapi.yanoshin.jp/webapi/tdnet/list/recent.rss",
            ]
            if _en:
                prompt_template = """You are a senior analyst specializing in the Japanese stock market.
Analyze {article_count} news items and produce a concise English-language investment report.

## 📊 Today's Market Overview
## 🔥 Key Sectors & Themes
## 📈 Notable Individual Stocks
## 🌍 Macro & External Factors
## ⚠️ Risks & Cautions
## 🔮 What to Watch Tomorrow

Sources ({article_count} items):\n{sources}"""
            else:
                prompt_template = """あなたは日本株式市場の専門アナリストです。
以下のニュースソース（{article_count}件）を精査し、投資判断に役立つ詳細なレポートを作成してください。

## 📊 本日の市場概況
## 🔥 注目セクター・テーマ
## 📈 個別銘柄トピック
## 🌍 マクロ・外部環境
## ⚠️ リスク・注意事項
## 🔮 明日以降の注目ポイント

情報ソース（{article_count}件）:\n{sources}"""
        else:
            feeds = [
                "https://www.cnbc.com/id/100003114/device/rss/rss.html",
                "https://feeds.bloomberg.com/markets/news.rss",
                "https://www.cnbc.com/id/10001147/device/rss/rss.html",
                "https://www.cnbc.com/id/20409666/device/rss/rss.html",
                "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
            ]
            if _en:
                prompt_template = """You are a senior Wall Street market analyst.
Analyze {article_count} news items and produce a comprehensive English-language report.

## 📊 Today's US Market Overview
## 🔥 Key Sectors & Themes
## 📈 Notable Stocks
## 🏦 Macro & Fed Policy
## 🌍 Geopolitical & External Risks
## ⚠️ Risk Factors
## 🔮 What to Watch Tomorrow

Sources ({article_count} items):\n{sources}"""
            else:
                prompt_template = """You are a senior Wall Street market analyst.
Analyze {article_count} news items and produce a comprehensive Japanese-language report.

## 📊 本日の米国市場概況
## 🔥 注目セクター・テーマ
## 📈 個別銘柄トピック
## 🏦 マクロ・金融政策
## 🌍 地政学・外部リスク
## ⚠️ リスク・注意事項
## 🔮 明日以降の注目ポイント

情報ソース（{article_count}件）:\n{sources}"""

        collected: list[dict] = []
        seen_titles: set[str] = set()
        for feed_url in feeds:
            try:
                articles = fetch_rss_feed(feed_url, max_items=20, translate=False)
                for article in articles[:15]:
                    title = (article.get("title") or "").strip()
                    if not title or title in seen_titles:
                        continue
                    seen_titles.add(title)
                    collected.append(article)
            except Exception as e:
                logger.warning(f"Failed to fetch feed {feed_url}: {e}")
                continue

        if not collected:
            return "⚠️ ニュース情報を取得できませんでした。"

        sources_text = ""
        for i, article in enumerate(collected, 1):
            title = article.get("title", "")
            summary = (article.get("summary") or "").strip()
            sources_text += f"{i}. {title}\n"
            if summary:
                sources_text += f"   → {summary[:300]}\n"

        article_count = len(collected)
        prompt = prompt_template.format(
            sources=sources_text[:12000],
            article_count=article_count,
        )
        summary, used_api = call_ai_with_fallback(prompt=prompt, max_output_tokens=2500, temperature=0.25)
        return f"__USED_API__:{used_api}\n{summary}"

    except Exception as e:
        logger.error(f"Error in fetch_and_summarize_market_news: {e}")
        return f"⚠️ エラー: {str(e)[:200]}"


def _parse_summary_result(raw: Optional[str]) -> Tuple[str, str]:
    if not raw:
        return "", ""
    if raw.startswith("__USED_API__:"):
        lines = raw.split("\n", 1)
        used_api = lines[0].replace("__USED_API__:", "").strip()
        summary = lines[1] if len(lines) > 1 else ""
        return used_api, summary
    return "", raw


def render_market_news_board(translate_mode: bool = True):
    st.header("📰 Market News Board")
    st.caption("Gemini優先で要約 → quota超過時はGroq → OpenRouter の順で自動切替")

    if not GENAI_AVAILABLE or not GEMINI_API_KEY:
        if not GROQ_API_KEY:
            st.warning("⚠️ AI APIが設定されていません。")
            return
        else:
            st.info("ℹ️ Gemini APIキー未設定のためGroqを使用します")

    tab_jp, tab_us = st.tabs(["🇯🇵 日本株市場", "🇺🇸 米国株市場"])
    _news_lang = st.session_state.get("lang", "ja")
    cache_key = f"{int(time.time() / TTL_MARKET_NEWS)}_{_news_lang}"

    with tab_jp:
        st.subheader(t("🔍 日本株式市場の最新動向（AI要約）",
                       "🔍 Japanese Stock Market — AI Summary"))
        with st.spinner(t("日本市場のニュースをAIで分析中...", "Analyzing Japan market news with AI...")):
            raw_jp = fetch_and_summarize_market_news("日本", _cache_key=cache_key, lang=_news_lang)
        used_api_jp, summary_jp = _parse_summary_result(raw_jp)
        if summary_jp:
            if summary_jp.startswith("⚠️"):
                st.error(summary_jp)
            else:
                st.markdown(summary_jp)
                if used_api_jp:
                    st.caption(f"{'📡 情報源' if _news_lang != 'en' else '📡 Source'}: Yahoo!News Japan | 🤖 AI: {used_api_jp}")
        else:
            st.warning(t("日本市場のニュース要約を取得できませんでした。",
                         "Could not load Japan market news summary."))

        with st.expander("📋 元ニュース一覧を表示", expanded=False):
            feeds = [
                ("Yahoo!ファイナンス（株式）", "https://news.yahoo.co.jp/rss/topics/stocks.xml"),
                ("Yahoo!ニュース（経済）", "https://news.yahoo.co.jp/rss/topics/business.xml"),
            ]
            for feed_name, feed_url in feeds:
                articles = fetch_rss_feed(feed_url, max_items=5, translate=False)
                if articles:
                    st.markdown(f"**{feed_name}**")
                    for i, article in enumerate(articles, 1):
                        if article["link"]:
                            st.markdown(f"{i}. [{article['title']}]({article['link']})")
                        else:
                            st.markdown(f"{i}. {article['title']}")
                    st.divider()

    with tab_us:
        st.subheader(t("🔍 米国株式市場の最新動向（AI要約）",
                       "🔍 US Stock Market — AI Summary"))
        with st.spinner(t("米国市場のニュースをAIで分析中...", "Analyzing US market news with AI...")):
            raw_us = fetch_and_summarize_market_news("米国", _cache_key=cache_key, lang=_news_lang)
        used_api_us, summary_us = _parse_summary_result(raw_us)
        if summary_us:
            if summary_us.startswith("⚠️"):
                st.error(summary_us)
            else:
                st.markdown(summary_us)
                if used_api_us:
                    st.caption(f"{'📡 情報源' if _news_lang != 'en' else '📡 Source'}: CNBC, Bloomberg | 🤖 AI: {used_api_us}")
        else:
            st.warning(t("米国市場のニュース要約を取得できませんでした。",
                         "Could not load US market news summary."))

        with st.expander("📋 元ニュース一覧を表示（英語）", expanded=False):
            feeds = [
                ("CNBC Markets", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
                ("Bloomberg Markets", "https://feeds.bloomberg.com/markets/news.rss"),
            ]
            for feed_name, feed_url in feeds:
                articles = fetch_rss_feed(feed_url, max_items=5, translate=False)
                if articles:
                    st.markdown(f"**{feed_name}**")
                    for i, article in enumerate(articles, 1):
                        if article["link"]:
                            st.markdown(f"{i}. [{article['title']}]({article['link']})")
                        else:
                            st.markdown(f"{i}. {article['title']}")
                    st.divider()


# ===========================
# TDnet関連機能
# ===========================
def make_gemini_model() -> Tuple[object, str]:
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY が未設定です")
    genai.configure(api_key=GEMINI_API_KEY)
    try:
        model = genai.GenerativeModel("gemini-1.5-flash")
        test_response = model.generate_content("test", generation_config=genai.types.GenerationConfig(max_output_tokens=5))
        return model, "gemini-1.5-flash"
    except Exception as e:
        raise RuntimeError(f"gemini-1.5-flash の初期化に失敗: {str(e)}")

@st.cache_data(ttl=600, show_spinner=False)
def fetch_tdnet_list_html(yyyymmdd: str) -> str:
    url = TDNET_LIST_URL.format(yyyymmdd=yyyymmdd)
    r = requests.get(url, timeout=DEFAULT_TIMEOUT, headers={"User-Agent": UA})
    r.raise_for_status()
    r.encoding = r.apparent_encoding
    return r.text

def extract_items_from_tdnet_html(html_content: str) -> List[Dict]:
    soup = BeautifulSoup(html_content, "html.parser")
    items: List[Dict] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or ".pdf" not in href.lower():
            continue
        if href.startswith("http://") or href.startswith("https://"):
            pdf_url = href
        elif href.startswith("//"):
            pdf_url = "https:" + href
        elif href.startswith("/"):
            pdf_url = TDNET_BASE + href
        else:
            pdf_url = TDNET_BASE + "/inbs/" + href
        pdf_url = re.sub(r'([^:])//+', r'\1/', pdf_url)
        title = a.get_text(strip=True) or "（タイトル不明）"
        parent_text = a.parent.get_text(" ", strip=True) if a.parent else ""
        if a.parent and a.parent.parent:
            parent_text += " " + a.parent.parent.get_text(" ", strip=True)
        around = (parent_text + " " + title).strip()
        code = None
        m_code = re.search(r"\b(\d{4})\b", around)
        if m_code:
            code = m_code.group(1)
        time_str = None
        m_time = re.search(r"\b([0-2]?\d:[0-5]\d)\b", around)
        if m_time:
            time_str = m_time.group(1)
        items.append({"title": title, "pdf_url": pdf_url, "code": code, "time": time_str, "around": around[:200]})
    uniq = {it["pdf_url"]: it for it in items}
    return list(uniq.values())

@st.cache_data(ttl=600, show_spinner=False)
def fetch_tdnet_items_for_date(yyyymmdd: str) -> List[Dict]:
    rss_url = "https://webapi.yanoshin.jp/webapi/tdnet/list/recent.rss"
    try:
        import urllib.request
        req = urllib.request.Request(rss_url, headers={'User-Agent': UA})
        with urllib.request.urlopen(req, timeout=15) as response:
            feed_data = response.read()
        feed = feedparser.parse(feed_data)
        items = []
        target_date = dt.datetime.strptime(yyyymmdd, "%Y%m%d").date()
        for entry in feed.entries:
            pub_date = None
            if hasattr(entry, 'published_parsed'):
                pub_date = dt.datetime(*entry.published_parsed[:6]).date()
            if pub_date and pub_date != target_date:
                continue
            title = entry.get("title", "")
            link = entry.get("link", "")
            pdf_url = link if ".pdf" in link.lower() else ""
            if not pdf_url:
                description = entry.get("description", "") or entry.get("summary", "")
                soup = BeautifulSoup(description, "html.parser")
                for a in soup.find_all("a", href=True):
                    if ".pdf" in a["href"].lower():
                        pdf_url = a["href"]
                        break
            if not pdf_url:
                continue
            code = None
            m_code = re.search(r"\b(\d{4})\b", title)
            if m_code:
                code = m_code.group(1)
            time_str = None
            if pub_date:
                time_str = dt.datetime(*entry.published_parsed[:6]).strftime("%H:%M")
            items.append({"title": title, "pdf_url": pdf_url, "code": code, "time": time_str, "around": title})
        if items:
            return items
    except Exception as e:
        logger.warning(f"RSS fetch failed: {e}, falling back to HTML scraping")
    try:
        html_content = fetch_tdnet_list_html(yyyymmdd)
        return extract_items_from_tdnet_html(html_content)
    except Exception as e:
        logger.error(f"Both RSS and HTML fetch failed: {e}")
        return []

@st.cache_data(ttl=3600, show_spinner=False)
def download_pdf_bytes(pdf_url: str) -> bytes:
    r = requests.get(pdf_url, timeout=DEFAULT_TIMEOUT, headers={"User-Agent": UA})
    r.raise_for_status()
    return r.content

@st.cache_data(ttl=3600, show_spinner=False)
def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    if not PDFPLUMBER_AVAILABLE:
        return ""
    text_parts = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            t = page.extract_text() or ""
            if t.strip():
                text_parts.append(t)
    return "\n".join(text_parts).strip()

def gemini_summarize_tdnet(pdf_text: str, title: str) -> Tuple[str, str]:
    trimmed = pdf_text[:MAX_PDF_TEXT_CHARS]
    prompt = f"""
あなたは日本の上場企業の開示資料（TDnet）の読み取り担当です。
以下の資料テキストから要点を抽出し、次のフォーマットでまとめてください。

【出力フォーマット】
- 概要: （増益/減益/上方修正/下方修正など）
- 主要数値: （売上/営利/経常/純利の変化）
- 理由: （要因を1〜3点）
- 注意点: （特損/為替/会計変更など）
- 株価への影響: （ポジティブ/ニュートラル/ネガティブ）

【資料タイトル】\n{title}
【テキスト】\n{trimmed}
""".strip()
    summary, used_api = call_ai_with_fallback(prompt=prompt, max_output_tokens=1500, temperature=0.3)
    return summary, used_api


# ===========================
# RSSフィード設定
# ===========================
RSS_FEEDS = {
    # ── 国内一般経済 ──────────────────────────────────
    "Yahoo!ニュース（経済）":    {"url": "https://news.yahoo.co.jp/rss/topics/business.xml",        "translate": False, "max_items": 20},
    "NHKニュース（経済）":      {"url": "https://www3.nhk.or.jp/rss/news/cat6.xml",                 "translate": False, "max_items": 20},
    # ── 調査報道・経済週刊誌 ─────────────────────────
    "東洋経済オンライン":        {"url": "https://toyokeizai.net/list/feed/rss",                     "translate": False, "max_items": 20},
    "文春オンライン":            {"url": "https://bunshun.jp/list/feed/rss",                         "translate": False, "max_items": 20},
    # ── 株式・マーケット ─────────────────────────────
    "みんかぶ":                  {"url": "https://assets.wor.jp/rss/rdf/minkabufx/stock.rdf",       "translate": False, "max_items": 20},
    "東証TDnet":                 {"url": "https://webapi.yanoshin.jp/webapi/tdnet/list/recent.rss",  "translate": False, "max_items": 20},
    # ── 一次報道・中立系（英語） ─────────────────────
    "AP News":                   {"url": "https://feeds.apnews.com/rss/apf-topnews",                 "translate": True,  "max_items": 20},
    "BBC World":                 {"url": "https://feeds.bbci.co.uk/news/world/rss.xml",              "translate": True,  "max_items": 20},
    "NPR News":                  {"url": "https://feeds.npr.org/1001/rss.xml",                       "translate": True,  "max_items": 20},
    # ── 経済・市場特化（英語） ───────────────────────
    "ブルームバーグ":            {"url": "https://feeds.bloomberg.com/markets/news.rss",             "translate": True,  "max_items": 20},
    "Reuters Business":          {"url": "https://feeds.reuters.com/reuters/businessNews",            "translate": True,  "max_items": 20},
    "MarketWatch":               {"url": "https://feeds.content.dowjones.io/public/rss/mw_bulletins", "translate": True, "max_items": 20},
    "CNBC":                      {"url": "https://www.cnbc.com/id/100003114/device/rss/rss.html",    "translate": True,  "max_items": 20},
    # ── ファクトチェック専門 ─────────────────────────
    "FactCheck.org":             {"url": "https://www.factcheck.org/feed/",                          "translate": True,  "max_items": 20},
    "Snopes":                    {"url": "https://www.snopes.com/feed/",                             "translate": True,  "max_items": 20},
    # ── 米議員株トレード監視 ──────────────────────────
    "議員トレード(QuiverQuant)": {"url": "https://www.quiverquant.com/news/category/congress_trades_automated/feed/", "translate": True, "max_items": 20},
    # ── 光ケーブル・光通信（AI インフラ） ────────────────
    "光通信(COHR)":  {"url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=COHR&region=US&lang=en-US", "translate": True, "max_items": 20},
    "光通信(LITE)":  {"url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=LITE&region=US&lang=en-US", "translate": True, "max_items": 20},
    "光通信(GLW)":   {"url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=GLW&region=US&lang=en-US",  "translate": True, "max_items": 20},
    "光通信(AAOI)":  {"url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=AAOI&region=US&lang=en-US", "translate": True, "max_items": 20},
}

# ===========================
# DeepL翻訳
# ===========================
@st.cache_data(ttl=TTL_RSS, show_spinner=False)
def translate_text(text: str, target_lang: str = "JA") -> str:
    if not text or len(text.strip()) == 0:
        return text
    if DEEPL_API_KEY:
        try:
            api_url = "https://api-free.deepl.com/v2/translate"
            if not DEEPL_API_KEY.endswith(":fx"):
                api_url = "https://api.deepl.com/v2/translate"
            response = requests.post(
                api_url,
                data={"auth_key": DEEPL_API_KEY, "text": text[:5000], "target_lang": target_lang},
                timeout=10,
            )
            if response.status_code == 200:
                return response.json()["translations"][0]["text"]
        except Exception as e:
            logger.debug(f"DeepL translation failed: {e}")
    prompt = f"以下を自然な日本語に翻訳してください（翻訳結果のみ出力）:\n{text[:2000]}"
    try:
        result, used_api = call_ai_with_fallback(prompt, max_output_tokens=800, temperature=0.1)
        if result and not result.startswith("⚠️"):
            return result.strip()
    except Exception as e:
        logger.debug(f"AI translation failed: {e}")
    return text

# ===========================
# RSSフィード取得
# ===========================
@st.cache_data(ttl=TTL_RSS, show_spinner=False)
def fetch_rss_feed(feed_url: str, max_items: int = 10, translate: bool = False) -> List[Dict[str, Any]]:
    try:
        import urllib.request
        req = urllib.request.Request(feed_url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
        with urllib.request.urlopen(req, timeout=15) as response:
            feed_data = response.read()
        feed = feedparser.parse(feed_data)
        if not feed.entries:
            return []
        articles = []
        for entry in feed.entries[:max_items]:
            title = entry.get("title", "No title")
            summary = entry.get("summary", entry.get("description", ""))
            link = entry.get("link", "")
            published = entry.get("published", entry.get("updated", ""))
            summary = html.unescape(summary)
            summary = re.sub(r'<[^>]+>', '', summary).strip()
            if translate and (DEEPL_API_KEY or model):
                title = translate_text(title)
                if summary:
                    summary = translate_text(summary[:500])
            pub_date = ""
            if published:
                try:
                    from email.utils import parsedate_to_datetime
                    d = parsedate_to_datetime(published)
                    pub_date = d.astimezone(JST).strftime("%m/%d %H:%M")
                except Exception:
                    pub_date = published[:16] if len(published) > 16 else published
            articles.append({"title": title, "summary": summary[:200] if summary else "", "link": link, "published": pub_date})
        return articles
    except Exception as e:
        logger.warning(f"RSS feed error for {feed_url}: {type(e).__name__}")
        return []

# ===========================
# データ取得: Tiingo
# ===========================
@st.cache_data(ttl=TTL_DAILY, show_spinner=False)
@retry_on_failure(max_retries=2, delay=1)
def fetch_daily_tiingo(symbol: str, days: int = 20) -> pd.DataFrame:
    if not TIINGO_API_KEY:
        return pd.DataFrame()
    end_utc = datetime.now(timezone.utc)
    start_utc = end_utc - pd.Timedelta(days=days)
    candidates = [symbol]
    if symbol.endswith(".T"):
        code = symbol.replace(".T", "")
        candidates += [code, f"tse:{code}"]
    for tk in candidates:
        try:
            url = f"https://api.tiingo.com/tiingo/daily/{tk}/prices"
            params = {"startDate": start_utc.date().isoformat(), "endDate": end_utc.date().isoformat(), "token": TIINGO_API_KEY}
            r = requests.get(url, params=params, timeout=15)
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
        except Exception as e:
            logger.debug(f"Tiingo fetch failed for {tk}: {e}")
            continue
    return pd.DataFrame()

# ===========================
# データ取得: Yahoo Finance
# ===========================
@st.cache_data(ttl=TTL_DAILY, show_spinner=False)
@retry_on_failure(max_retries=2, delay=1)
def fetch_daily_yahoo(symbol: str, days: int = 20) -> pd.DataFrame:
    end_utc = datetime.now(timezone.utc)
    start_utc = end_utc - pd.Timedelta(days=days)
    df = yf.Ticker(symbol).history(start=start_utc, end=end_utc, interval="1d", auto_adjust=False)
    if df is None or df.empty:
        return pd.DataFrame()
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    return df.tz_convert(JST).dropna(subset=["Close"])

@st.cache_data(ttl=TTL_DAILY, show_spinner=False)
def fetch_daily_stooq(symbol: str, days: int = 20) -> pd.DataFrame:
    try:
        from pandas_datareader import data as pdr
        stooq_symbol = symbol
        if symbol.upper().endswith(".T"):
            stooq_symbol = symbol[:-2] + ".JP"
        end_dt = datetime.now(timezone.utc).date()
        start_dt = (datetime.now(timezone.utc) - pd.Timedelta(days=days)).date()
        df = pdr.DataReader(stooq_symbol, "stooq", start=start_dt, end=end_dt)
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.sort_index()
        if df.index.tz is None:
            df.index = pd.DatetimeIndex(df.index).tz_localize("Asia/Tokyo")
        else:
            df.index = df.index.tz_convert(JST)
        df = df.rename(columns={"Open": "Open", "High": "High", "Low": "Low", "Close": "Close", "Volume": "Volume"})
        df = df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
        return df
    except ImportError:
        return pd.DataFrame()
    except Exception as e:
        logger.debug(f"stooq fetch failed for {symbol}: {e}")
        return pd.DataFrame()


def fetch_daily(symbol: str, days: int = 20, provider: str = "yahoo") -> pd.DataFrame:
    if provider == "tiingo":
        df_t = fetch_daily_tiingo(symbol, days=days)
        if not df_t.empty and len(df_t["Close"].dropna()) >= 2:
            return df_t
        df_s = fetch_daily_stooq(symbol, days=days)
        if not df_s.empty and len(df_s["Close"].dropna()) >= 2:
            return df_s
    return fetch_daily_yahoo(symbol, days=days)

# ===========================
# データ取得: イントラデイ
# ===========================
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
        except Exception as e:
            logger.debug(f"Intraday fetch failed: {symbol} @ {interval}: {e}")
            continue
    return pd.DataFrame()

def safe_last_price(df: pd.DataFrame) -> Optional[float]:
    try:
        s = df["Close"].dropna()
        return float(s.iloc[-1]) if not s.empty else None
    except (KeyError, IndexError, ValueError):
        return None

def compute_card(symbol: str, rt_symbol: Optional[str] = None, provider: str = "yahoo") -> Dict[str, Any]:
    try:
        daily = fetch_daily(symbol, days=15, provider=provider)
        if daily.empty or len(daily["Close"].dropna()) < 2:
            return {"ok": False, "reason": "前日終値取得失敗"}
        closes = daily["Close"].dropna()
        prev_close = float(closes.iloc[-2])
        latest_close = float(closes.iloc[-1])
        daily_last_ts = closes.index[-1]
        intraday_sym = rt_symbol or symbol
        intra = fetch_intraday(intraday_sym)
        if not intra.empty:
            now = safe_last_price(intra)
            last_ts = intra.index[-1]
            mode = "INTRADAY"
            series = intra["Close"]
        else:
            now = latest_close
            last_ts = daily_last_ts
            mode = "CLOSE"
            series = closes.tail(30)
        if now is None or prev_close == 0:
            return {"ok": False, "reason": "価格計算失敗"}
        chg = now - prev_close
        pct = (now / prev_close - 1.0) * 100.0
        return {
            "ok": True, "mode": mode,
            "interval": intra.attrs.get("interval", "1d") if mode == "INTRADAY" else "1d",
            "now": now, "base": prev_close, "chg": chg, "pct": pct,
            "series": series, "last_ts": last_ts,
            "date_label": last_ts.strftime("%Y-%m-%d"),
            "rt_used": bool(rt_symbol),
        }
    except Exception as e:
        logger.error(f"compute_card error: {symbol} - {e}")
        return {"ok": False, "reason": f"エラー: {str(e)}"}

# ===========================
# スパークラインチャート（10分キャッシュ）
# ===========================
@st.cache_data(ttl=TTL_CHART, show_spinner=False)
def make_sparkline_cached(series_values: tuple, series_index_str: tuple, base: float, mode: str) -> bytes:
    import io as _io
    fig, ax = plt.subplots(figsize=(5.6, 1.75))
    if not series_values:
        ax.text(0.5, 0.5, "N/A", ha="center", va="center", fontsize=12)
        ax.axis("off")
    else:
        x = pd.to_datetime(list(series_index_str), utc=True).tz_convert(JST)
        y = np.array(series_values)
        ax.axhline(base, linewidth=1, alpha=0.6, color="black", linestyle="--")
        ax.plot(x, y, linewidth=1.8, color=LINE_NEUTRAL, alpha=0.95)
        ax.fill_between(x, y, base, where=(y >= base), alpha=0.12, color=GREEN)
        ax.fill_between(x, y, base, where=(y < base), alpha=0.12, color=RED)
        if mode == "INTRADAY":
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=JST))
        else:
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d", tz=JST))
        ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=3, maxticks=6))
        ax.tick_params(axis="x", labelsize=9, rotation=0)
        ax.tick_params(axis="y", labelsize=9)
        ax.margins(x=0.01)
        for spine in ax.spines.values():
            spine.set_alpha(0.2)
        ax.grid(True, axis="y", alpha=0.15)
    plt.tight_layout()
    buf = _io.BytesIO()
    fig.savefig(buf, format="png", dpi=100)
    plt.close(fig)
    buf.seek(0)
    return buf.read()

def make_sparkline(series: pd.Series, base: float, mode: str, up: bool):
    import io as _io
    if series is None or series.empty:
        fig, ax = plt.subplots(figsize=(5.6, 1.75))
        ax.text(0.5, 0.5, "N/A", ha="center", va="center", fontsize=12)
        ax.axis("off")
        plt.tight_layout()
        buf = _io.BytesIO()
        fig.savefig(buf, format="png", dpi=100)
        plt.close(fig)
        buf.seek(0)
        return buf.read()
    values_tuple = tuple(float(v) for v in series.values)
    index_tuple = tuple(str(idx) for idx in series.index)
    return make_sparkline_cached(values_tuple, index_tuple, float(base), mode)

# ===========================
# Fear & Greed Gauge（10分キャッシュ）
# ===========================
@st.cache_data(ttl=TTL_CHART, show_spinner=False)
def make_fear_greed_gauge_cached(score: float) -> bytes:
    import io as _io
    fig, ax = plt.subplots(figsize=(3, 1.5))
    theta = np.linspace(0, np.pi, 200)
    ax.plot(np.cos(theta), np.sin(theta), linewidth=10, color="#eeeeee")
    zones = [(0, 25, "#d1242f"), (25, 45, "#ff8800"), (45, 55, "#999999"), (55, 75, "#1a7f37"), (75, 100, "#008000")]
    for start, end, color in zones:
        angle = np.linspace(np.pi * (1 - start/100), np.pi * (1 - end/100), 100)
        ax.plot(np.cos(angle), np.sin(angle), linewidth=10, color=color)
    needle_angle = np.pi * (1 - score/100)
    ax.plot([0, 0.8*np.cos(needle_angle)], [0, 0.8*np.sin(needle_angle)], linewidth=2, color="black")
    ax.text(0, -0.15, f"{int(score)}", fontsize=18, fontweight="bold", ha="center")
    ax.set_aspect("equal")
    ax.axis("off")
    plt.tight_layout()
    buf = _io.BytesIO()
    fig.savefig(buf, format="png", dpi=100)
    plt.close(fig)
    buf.seek(0)
    return buf.read()

def make_fear_greed_gauge(score: float):
    return make_fear_greed_gauge_cached(float(round(score, 2)))

# ===========================
# Fear & Greed Index取得
# ===========================
@st.cache_data(ttl=TTL_INTRADAY, show_spinner=False)
def fetch_fear_greed_index() -> Optional[Dict[str, Any]]:
    try:
        START_DATE = "2022-01-01"
        url = f"https://production.dataviz.cnn.io/index/fearandgreed/graphdata/{START_DATE}"
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        data = r.json()
        fg_current = data.get("fear_and_greed", {})
        score = fg_current.get("score")
        rating = fg_current.get("rating")
        timestamp = fg_current.get("timestamp")
        historical = data.get("fear_and_greed_historical", {}).get("data", [])
        if score is not None:
            hist_df = pd.DataFrame()
            if historical:
                hist_df = pd.DataFrame(historical)
                if not hist_df.empty and 'x' in hist_df.columns:
                    hist_df['date'] = pd.to_datetime(hist_df['x'], unit='ms')
                    hist_df['score'] = hist_df['y']
                    hist_df = hist_df[['date', 'score']].sort_values('date')
            return {
                "score": float(score),
                "rating": rating or "Neutral",
                "timestamp": timestamp,
                "historical": hist_df,
                "previous_close": float(historical[-2]['y']) if len(historical) >= 2 else None,
            }
    except Exception as e:
        logger.error(f"Fear & Greed fetch error: {e}")
    return None

# ===========================
# CNN F&G 7指標 個別取得
# ===========================
@st.cache_data(ttl=TTL_INTRADAY, show_spinner=False)
def fetch_fg_components() -> Dict[str, Any]:
    """
    CNN Fear & Greed Index の7構成指標をYahoo Financeから自力計算する。

    ① Market Momentum      : S&P500 vs 125日移動平均
    ② Stock Price Strength : NYSE 52週高値/安値 净差（^NYA代替）
    ③ Stock Price Breadth  : McClellan Volume Summation Index（ADV/DEC出来高比）
    ④ Put/Call Options     : CBOE Put/Call Ratio (^PCALL or ^PC)
    ⑤ Market Volatility    : VIX 水準と傾向
    ⑥ Safe Haven Demand    : 債券(TLT) vs 株式(SPY) 20日リターン差
    ⑦ Junk Bond Demand     : HY-IG スプレッド（HYG vs LQD 利回り比）
    """
    result: Dict[str, Any] = {}
    end   = datetime.now(timezone.utc)
    start = end - timedelta(days=600)

    def _hist(sym: str) -> pd.DataFrame:
        try:
            df = yf.Ticker(sym).history(start=start, end=end, interval="1d", auto_adjust=False)
            if df is None or df.empty:
                return pd.DataFrame()
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC")
            return df.tz_convert(JST)
        except Exception as e:
            logger.debug(f"fg_comp _hist({sym}): {e}")
            return pd.DataFrame()

    # ──────────────────────────────────────────────
    # ① Market Momentum: S&P500 vs 125日移動平均
    # ──────────────────────────────────────────────
    try:
        sp = _hist("^GSPC")
        if not sp.empty and len(sp) >= 130:
            close = sp["Close"].dropna()
            ma125 = close.rolling(125).mean()
            current  = float(close.iloc[-1])
            ma_val   = float(ma125.iloc[-1])
            pct_diff = (current / ma_val - 1) * 100

            # スコア変換: +5%以上=Greed, -5%以下=Fear
            score = float(np.clip(50 + pct_diff * 5, 0, 100))

            # 過去の MA125 差分履歴（チャート用）
            hist_pct = ((close / ma125 - 1) * 100).dropna()
            hist_score = np.clip(50 + hist_pct * 5, 0, 100)
            hist_df = pd.DataFrame({
                "date":  pd.to_datetime(hist_pct.index).tz_localize(None)
                         if hist_pct.index.tz is not None
                         else pd.to_datetime(hist_pct.index),
                "score": hist_score.values,
                "sp500": close.loc[hist_pct.index].values,
                "ma125": ma125.loc[hist_pct.index].values,
            })
            result["momentum"] = {
                "score": score,
                "sp500": current,
                "ma125": ma_val,
                "pct_diff": pct_diff,
                "hist_df": hist_df,
                "label": "Above MA125 (Greed)" if pct_diff > 0 else "Below MA125 (Fear)",
            }
    except Exception as e:
        logger.warning(f"FG momentum error: {e}")

    # ──────────────────────────────────────────────
    # ② Stock Price Strength: 52週高値/安値 净差
    #    NYSE 銘柄数が取れないため ^NYA の近似（1y高値比較）
    # ──────────────────────────────────────────────
    try:
        # 主要ETFバスケットで High/Low比率を近似
        # IWM(小型)・SPY(大型)・MDY(中型) の52週高値/安値比で代替
        highs_total = lows_total = 0
        for sym in ["SPY", "IWM", "MDY", "QQQ", "DIA"]:
            df = _hist(sym)
            if df.empty or len(df) < 252:
                continue
            c   = df["Close"].dropna()
            hi  = float(c.rolling(252).max().iloc[-1])
            lo  = float(c.rolling(252).min().iloc[-1])
            mid = (hi + lo) / 2
            if float(c.iloc[-1]) >= mid:
                highs_total += 1
            else:
                lows_total  += 1

        net = highs_total - lows_total   # -5 〜 +5
        score = float(np.clip(50 + net * 10, 0, 100))

        # 過去200日分の近似スコア（SPY 1y高値比率）
        sp2 = _hist("SPY")
        if not sp2.empty and len(sp2) >= 252:
            c2 = sp2["Close"].dropna()
            hi252 = c2.rolling(252).max()
            lo252 = c2.rolling(252).min()
            ratio = ((c2 - lo252) / (hi252 - lo252).replace(0, np.nan)).dropna()
            hist_score2 = np.clip(ratio * 100, 0, 100)
            hist_df2 = pd.DataFrame({
                "date":  pd.to_datetime(ratio.index).tz_localize(None)
                         if ratio.index.tz is not None
                         else pd.to_datetime(ratio.index),
                "score": hist_score2.values,
            })
        else:
            hist_df2 = pd.DataFrame()

        result["strength"] = {
            "score": score,
            "new_highs": highs_total,
            "new_lows":  lows_total,
            "net": net,
            "hist_df": hist_df2,
            "label": f"Net +{net} (Greed)" if net > 0 else f"Net {net} (Fear)",
        }
    except Exception as e:
        logger.warning(f"FG strength error: {e}")

    # ──────────────────────────────────────────────
    # ③ Stock Price Breadth: McClellan Volume Summation
    #    [Fix 1] SPY/QQQ/IWM/DIA/MDY 5本の日次騰落を合算して
    #            ADV-DEC を擬似的に算出 → MSI 計算の感度を改善
    # ──────────────────────────────────────────────
    try:
        import functools as _functools
        BREADTH_SYMS = ["SPY", "QQQ", "IWM", "DIA", "MDY"]
        net_series_list = []
        for _sym_b in BREADTH_SYMS:
            _df_b = _hist(_sym_b)
            if _df_b.empty or len(_df_b) < 40:
                continue
            _c_b = _df_b["Close"].dropna()
            _ret_b = _c_b.pct_change().dropna()
            # +1(上昇) / -1(下落) / 0(変化なし)
            _net_b = _ret_b.apply(lambda x: 1.0 if x > 0 else (-1.0 if x < 0 else 0.0))
            net_series_list.append(_net_b)

        if net_series_list:
            # 共通日付で合算し件数で正規化 → -1〜+1
            combined = _functools.reduce(
                lambda a, b: a.add(b, fill_value=0), net_series_list
            )
            combined = combined / len(net_series_list)

            ema19  = combined.ewm(span=19, adjust=False).mean()
            ema39  = combined.ewm(span=39, adjust=False).mean()
            mco    = ema19 - ema39        # McClellan Oscillator
            msi    = mco.cumsum()         # Summation Index

            msi_norm = float(msi.iloc[-1])
            # MSIはcumsumで値域が不定なため、過去レンジのパーセンタイルで正規化
            msi_min, msi_max = float(msi.min()), float(msi.max())
            if msi_max > msi_min:
                score_b = float(np.clip((msi_norm - msi_min) / (msi_max - msi_min) * 100, 0, 100))
                hist_scores_b = np.clip((msi.values - msi_min) / (msi_max - msi_min) * 100, 0, 100)
            else:
                score_b = 50.0
                hist_scores_b = np.full(len(msi), 50.0)

            hist_msi = pd.DataFrame({
                "date":  pd.to_datetime(msi.index).tz_localize(None)
                         if msi.index.tz is not None
                         else pd.to_datetime(msi.index),
                "score": hist_scores_b,
                "msi":   msi.values,
            })
            result["breadth"] = {
                "score": score_b,
                "msi":   msi_norm,
                "hist_df": hist_msi,
                "label": "Breadth Strong (Greed)" if score_b > 50 else "Breadth Weak (Fear)",
            }
        else:
            # フォールバック: S&P500 単体
            sp3 = _hist("^GSPC")
            if not sp3.empty and len(sp3) >= 40:
                close3 = sp3["Close"].dropna()
                ret    = close3.pct_change().dropna()
                net_adv = (ret > 0).astype(float) - (ret < 0).astype(float)
                ema19  = net_adv.ewm(span=19, adjust=False).mean()
                ema39  = net_adv.ewm(span=39, adjust=False).mean()
                mco    = ema19 - ema39
                msi    = mco.cumsum()
                msi_norm = float(msi.iloc[-1])
                msi_min2, msi_max2 = float(msi.min()), float(msi.max())
                if msi_max2 > msi_min2:
                    score_b  = float(np.clip((msi_norm - msi_min2) / (msi_max2 - msi_min2) * 100, 0, 100))
                    hist_sc2 = np.clip((msi.values - msi_min2) / (msi_max2 - msi_min2) * 100, 0, 100)
                else:
                    score_b, hist_sc2 = 50.0, np.full(len(msi), 50.0)
                hist_msi = pd.DataFrame({
                    "date":  pd.to_datetime(msi.index).tz_localize(None)
                             if msi.index.tz is not None
                             else pd.to_datetime(msi.index),
                    "score": hist_sc2,
                    "msi":   msi.values,
                })
                result["breadth"] = {
                    "score": score_b,
                    "msi":   msi_norm,
                    "hist_df": hist_msi,
                    "label": "Breadth Strong (Greed)" if score_b > 50 else "Breadth Weak (Fear)",
                }
    except Exception as e:
        logger.warning(f"FG breadth error: {e}")

    # ──────────────────────────────────────────────
    # ④ Put/Call Options: CBOE Put/Call Ratio
    #    [Fix A] ^CPC/^CPCE 取得不可時は VXX/UVXY で代替
    # ──────────────────────────────────────────────
    try:
        pc_df = _hist("^CPC")
        if pc_df.empty:
            pc_df = _hist("^CPCE")

        if not pc_df.empty and len(pc_df) >= 5:
            # ── 正規ルート: CBOE Put/Call Ratio ──────────────
            pc_c   = pc_df["Close"].dropna()
            pc_val = float(pc_c.iloc[-1])
            pc_ma10 = float(pc_c.rolling(10).mean().iloc[-1]) if len(pc_c) >= 10 else pc_val
            score_pc = float(np.clip(100 - (pc_val - 0.5) / 0.6 * 100, 0, 100))
            hist_pc_score = np.clip(100 - (pc_c - 0.5) / 0.6 * 100, 0, 100)
            hist_df_pc = pd.DataFrame({
                "date":  pd.to_datetime(pc_c.index).tz_localize(None)
                         if pc_c.index.tz is not None
                         else pd.to_datetime(pc_c.index),
                "score": hist_pc_score.values,
                "pc":    pc_c.values,
            })
            result["put_call"] = {
                "score":   score_pc,
                "ratio":   pc_val,
                "ma10":    pc_ma10,
                "hist_df": hist_df_pc,
                "source":  "CBOE P/C",
                "label":   f"P/C={pc_val:.2f} " + (
                    "Bearish (Fear)" if pc_val > 1.0 else "Bullish (Greed)"),
            }
        else:
            # ── 代替ルート: VXX（ボラETF）の価格変化でPut需要を近似 ──
            # VXX上昇 = Putヘッジ需要増 = Fear
            vxx_df = _hist("VXX")
            if vxx_df.empty:
                vxx_df = _hist("UVXY")  # 2倍レバレッジ版
            if not vxx_df.empty and len(vxx_df) >= 20:
                vxx_c   = vxx_df["Close"].dropna()
                vxx_val = float(vxx_c.iloc[-1])
                vxx_ma20 = float(vxx_c.rolling(20).mean().iloc[-1])
                # VXX が MA20 より高い → Fear（Put需要高）
                vxx_ratio = vxx_val / vxx_ma20 if vxx_ma20 > 0 else 1.0
                score_pc  = float(np.clip(100 - (vxx_ratio - 0.9) / 0.3 * 100, 0, 100))
                # 履歴スコア
                vxx_ma20_s = vxx_c.rolling(20).mean()
                ratio_s    = (vxx_c / vxx_ma20_s).dropna()
                hist_score_s = np.clip(100 - (ratio_s - 0.9) / 0.3 * 100, 0, 100)
                hist_df_pc = pd.DataFrame({
                    "date":  pd.to_datetime(ratio_s.index).tz_localize(None)
                             if ratio_s.index.tz is not None
                             else pd.to_datetime(ratio_s.index),
                    "score": hist_score_s.values,
                    "pc":    ratio_s.values,
                })
                result["put_call"] = {
                    "score":   score_pc,
                    "ratio":   vxx_ratio,
                    "ma10":    vxx_ma20,
                    "hist_df": hist_df_pc,
                    "source":  "VXX/MA20(代替)",
                    "label":   f"VXX ratio={vxx_ratio:.3f} " + (
                        "Bearish (Fear)" if vxx_ratio > 1.05 else "Bullish (Greed)"),
                }
    except Exception as e:
        logger.warning(f"FG put/call error: {e}")

    # ──────────────────────────────────────────────
    # ⑤ Market Volatility: VIX
    # ──────────────────────────────────────────────
    try:
        vix_df = _hist("^VIX")
        if not vix_df.empty and len(vix_df) >= 50:
            vix_c  = vix_df["Close"].dropna()
            vix_now = float(vix_c.iloc[-1])
            vix_ma50 = float(vix_c.rolling(50).mean().iloc[-1])
            # VIX < 12 = Extreme Greed, > 30 = Extreme Fear
            score_v = float(np.clip(100 - (vix_now - 10) / 25 * 100, 0, 100))
            hist_v_score = np.clip(100 - (vix_c - 10) / 25 * 100, 0, 100)
            hist_df_v = pd.DataFrame({
                "date":  pd.to_datetime(vix_c.index).tz_localize(None)
                         if vix_c.index.tz is not None
                         else pd.to_datetime(vix_c.index),
                "score": hist_v_score.values,
                "vix":   vix_c.values,
            })
            result["volatility"] = {
                "score":  score_v,
                "vix":    vix_now,
                "ma50":   vix_ma50,
                "hist_df": hist_df_v,
                "label":  f"VIX={vix_now:.1f} " + ("Extreme Fear" if vix_now > 30
                           else "Fear" if vix_now > 20
                           else "Neutral" if vix_now > 15
                           else "Greed"),
            }
    except Exception as e:
        logger.warning(f"FG volatility error: {e}")

    # ──────────────────────────────────────────────
    # ⑥ Safe Haven Demand: TLT vs SPY 20日リターン差
    # ──────────────────────────────────────────────
    try:
        tlt = _hist("TLT")   # 米国長期国債ETF
        spy = _hist("SPY")   # S&P500 ETF
        if not tlt.empty and not spy.empty and len(tlt) >= 25 and len(spy) >= 25:
            tlt_c = tlt["Close"].dropna()
            spy_c = spy["Close"].dropna()
            # 共通日付
            common = tlt_c.index.intersection(spy_c.index)
            if len(common) >= 22:
                tlt_ret20 = float(tlt_c.loc[common[-1]] / tlt_c.loc[common[-22]] - 1) * 100
                spy_ret20 = float(spy_c.loc[common[-1]] / spy_c.loc[common[-22]] - 1) * 100
                spread = tlt_ret20 - spy_ret20   # 正=債券優位=Fear
                # spread < -5 = Greed, > +5 = Fear
                score_sh = float(np.clip(50 - spread * 5, 0, 100))

                # 履歴
                tlt_r = tlt_c.loc[common].pct_change(20).dropna()
                spy_r = spy_c.loc[common].pct_change(20).dropna()
                common2 = tlt_r.index.intersection(spy_r.index)
                spread_hist = (tlt_r.loc[common2] - spy_r.loc[common2]) * 100
                hist_sh = pd.DataFrame({
                    "date":  pd.to_datetime(spread_hist.index).tz_localize(None)
                             if spread_hist.index.tz is not None
                             else pd.to_datetime(spread_hist.index),
                    "score": np.clip(50 - spread_hist.values * 5, 0, 100),
                    "spread": spread_hist.values,
                })
                result["safe_haven"] = {
                    "score":     score_sh,
                    "tlt_ret20": tlt_ret20,
                    "spy_ret20": spy_ret20,
                    "spread":    spread,
                    "hist_df":   hist_sh,
                    "label":     ("Bonds outperform (Fear)" if spread > 0
                                  else "Stocks outperform (Greed)"),
                }
    except Exception as e:
        logger.warning(f"FG safe haven error: {e}")

    # ──────────────────────────────────────────────
    # ⑦ Junk Bond Demand: HYG vs LQD スプレッド
    # ──────────────────────────────────────────────
    try:
        hyg = _hist("HYG")   # iShares HY Corporate Bond ETF
        lqd = _hist("LQD")   # iShares IG Corporate Bond ETF
        if not hyg.empty and not lqd.empty and len(hyg) >= 20 and len(lqd) >= 20:
            hyg_c = hyg["Close"].dropna()
            lqd_c = lqd["Close"].dropna()
            common_j = hyg_c.index.intersection(lqd_c.index)
            if len(common_j) >= 20:
                # HYG/LQD 比率: 高い=JunkBond需要強=リスクオン=Greed
                ratio_jb  = hyg_c.loc[common_j] / lqd_c.loc[common_j]
                ratio_now = float(ratio_jb.iloc[-1])
                ratio_ma20 = float(ratio_jb.rolling(20).mean().iloc[-1])
                spread_jb = (ratio_now / ratio_ma20 - 1) * 100
                # spread > 0 = HYG/LQD上昇中=Greed
                score_jb = float(np.clip(50 + spread_jb * 10, 0, 100))

                hist_score_jb = np.clip(
                    50 + (ratio_jb / ratio_jb.rolling(20).mean() - 1) * 1000, 0, 100
                )
                hist_df_jb = pd.DataFrame({
                    "date":  pd.to_datetime(ratio_jb.index).tz_localize(None)
                             if ratio_jb.index.tz is not None
                             else pd.to_datetime(ratio_jb.index),
                    "score": hist_score_jb.values,
                    "ratio": ratio_jb.values,
                })
                result["junk_bond"] = {
                    "score":     score_jb,
                    "ratio":     ratio_now,
                    "ma20":      ratio_ma20,
                    "spread":    spread_jb,
                    "hist_df":   hist_df_jb,
                    "label":     ("HYG/LQD↑ Risk-On (Greed)" if spread_jb > 0
                                  else "HYG/LQD↓ Risk-Off (Fear)"),
                }
    except Exception as e:
        logger.warning(f"FG junk bond error: {e}")

    return result


def _draw_component_chart(comp: Dict[str, Any], key: str, color: str = "#1976d2") -> None:
    """
    7指標のうち1つのコンポーネントを小型チャートで描画する。
    [Fix B] figsize を (6, 2.2) に統一。
    [Fix C] Y軸ラベルを英語に統一（フォント文字化け対策）。
    """
    hist_df = comp.get("hist_df")
    if hist_df is None or hist_df.empty:
        st.caption("No chart data")
        return
    df = hist_df.copy()
    df["date"] = pd.to_datetime(df["date"])
    if df["date"].dt.tz is not None:
        df["date"] = df["date"].dt.tz_localize(None)
    df = df.sort_values("date").tail(365)

    # ★ figsize を全指標で統一
    fig, ax = plt.subplots(figsize=(6, 2.2))
    ax.plot(df["date"], df["score"], linewidth=1.5, color=color)
    ax.fill_between(df["date"], df["score"], 50, alpha=0.12, color=color)
    ax.axhspan(0,  25, alpha=0.07, color="red")
    ax.axhspan(25, 45, alpha=0.07, color="orange")
    ax.axhspan(55, 75, alpha=0.07, color="lightgreen")
    ax.axhspan(75, 100, alpha=0.07, color="green")
    ax.axhline(50, linewidth=0.8, color="gray", linestyle="--", alpha=0.5)
    ax.set_ylim(0, 100)
    # ★ 英語ラベルに統一
    ax.set_ylabel("Score", fontsize=8, fontfamily="DejaVu Sans")
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
    plt.xticks(rotation=30, fontsize=7)
    ax.yaxis.set_tick_params(labelsize=7)
    ax.grid(True, alpha=0.25)
    # ★ 最新スコアをアノテーション
    last_score = float(df["score"].iloc[-1])
    last_date  = df["date"].iloc[-1]
    ax.annotate(f"{last_score:.0f}",
                xy=(last_date, last_score),
                xytext=(6, 0), textcoords="offset points",
                fontsize=8, fontweight="bold", color=color,
                va="center", fontfamily="DejaVu Sans")
    plt.tight_layout(pad=0.3)
    st.pyplot(fig, clear_figure=True)


# ===========================
# 日本版Fear & Greed Index
# ===========================
@st.cache_data(ttl=TTL_INTRADAY, show_spinner=False)
def fetch_japan_fear_greed_index() -> Optional[Dict[str, Any]]:
    try:
        end_date = datetime.now(timezone.utc)
        # ★ 3年タブに対応するため1200日（約3.3年）分取得
        start_date = end_date - timedelta(days=1200)
        nikkei = yf.Ticker("^N225").history(start=start_date, end=end_date, interval="1d", auto_adjust=False)
        if nikkei.empty:
            return None
        if nikkei.index.tz is None:
            nikkei.index = nikkei.index.tz_localize("UTC")
        nikkei = nikkei.tz_convert(JST)
        nikkei['returns'] = nikkei['Close'].pct_change()

        # ★ MA50計算（十分なデータがある前提）
        nikkei['MA50'] = nikkei['Close'].rolling(window=50).mean()

        # ★ 現在スコア（最新日）
        recent_30d = nikkei.tail(30)
        volatility_30d = recent_30d['returns'].std() * 100
        momentum_30d = (nikkei['Close'].iloc[-1] / nikkei['Close'].iloc[-30] - 1) * 100
        price_vs_ma = (nikkei['Close'].iloc[-1] / nikkei['MA50'].iloc[-1] - 1) * 100
        vol_score = max(0, min(100, 100 - (volatility_30d - 1) * 50))
        momentum_score = max(0, min(100, 50 + momentum_30d * 2.5))
        ma_score = max(0, min(100, 50 + price_vs_ma * 10))
        score = vol_score * 0.4 + momentum_score * 0.4 + ma_score * 0.2

        # ★ 前日スコア
        prev_nikkei = nikkei.iloc[:-1]
        prev_30d = prev_nikkei.tail(30)
        prev_volatility = prev_30d['returns'].std() * 100
        prev_momentum = (prev_nikkei['Close'].iloc[-1] / prev_nikkei['Close'].iloc[-30] - 1) * 100
        prev_price_vs_ma = (prev_nikkei['Close'].iloc[-1] / prev_nikkei['MA50'].iloc[-1] - 1) * 100
        prev_vol_score = max(0, min(100, 100 - (prev_volatility - 1) * 50))
        prev_momentum_score = max(0, min(100, 50 + prev_momentum * 2.5))
        prev_ma_score = max(0, min(100, 50 + prev_price_vs_ma * 10))
        prev_score = prev_vol_score * 0.4 + prev_momentum_score * 0.4 + prev_ma_score * 0.2

        # ★ 履歴スコアを全データ範囲で計算（MA50が有効な点から）
        hist_scores = []
        n = len(nikkei)
        for idx in range(50, n):  # MA50が有効なインデックスから開始
            window_data = nikkei.iloc[max(0, idx-30):idx]
            if len(window_data) < 20:
                continue
            window_vol = window_data['returns'].std() * 100
            window_momentum = (
                (nikkei['Close'].iloc[idx] / nikkei['Close'].iloc[max(0, idx-30)] - 1) * 100
            )
            ma50_val = nikkei['MA50'].iloc[idx]
            window_ma_diff = (
                (nikkei['Close'].iloc[idx] / ma50_val - 1) * 100
                if not pd.isna(ma50_val) else 0
            )
            h_vol_score = max(0, min(100, 100 - (window_vol - 1) * 50))
            h_momentum_score = max(0, min(100, 50 + window_momentum * 2.5))
            h_ma_score = max(0, min(100, 50 + window_ma_diff * 10))
            h_score = h_vol_score * 0.4 + h_momentum_score * 0.4 + h_ma_score * 0.2

            date_val = nikkei.index[idx]
            # timezone → naive
            if hasattr(date_val, 'tz') and date_val.tz is not None:
                date_val = date_val.replace(tzinfo=None)
            hist_scores.append({'date': pd.Timestamp(date_val), 'score': h_score})

        hist_df = pd.DataFrame(hist_scores)

        # ★ 最新スコアが確実に末尾に入るよう補正（最終行を現在スコアで上書き）
        if not hist_df.empty:
            last_date = nikkei.index[-1]
            if hasattr(last_date, 'tz') and last_date.tz is not None:
                last_date = last_date.replace(tzinfo=None)
            last_date = pd.Timestamp(last_date)
            # 最終行が最新日付であれば上書き、なければ追加
            if hist_df['date'].iloc[-1].date() == last_date.date():
                hist_df.loc[hist_df.index[-1], 'score'] = score
            else:
                hist_df = pd.concat([
                    hist_df,
                    pd.DataFrame([{'date': last_date, 'score': score}])
                ], ignore_index=True)

        rating = get_fear_greed_label(score)[0]
        return {
            "score": float(score), "rating": rating,
            "timestamp": nikkei.index[-1].isoformat(),
            "historical": hist_df, "previous_close": float(prev_score),
            "volatility": volatility_30d, "momentum": momentum_30d, "ma_diff": price_vs_ma,
        }
    except Exception as e:
        logger.error(f"Japan Fear & Greed fetch error: {e}", exc_info=True)
        return None

def get_fear_greed_label(score: float) -> Tuple[str, str]:
    if score <= 25:
        return "極度の恐怖", "#d1242f"
    elif score <= 45:
        return "恐怖", "#ff8800"
    elif score <= 55:
        return "中立", "#999999"
    elif score <= 75:
        return "貪欲", "#1a7f37"
    else:
        return "極度の貪欲", "#008000"


# ===========================
# ★ 共通チャート描画ヘルパー（タブ切り替え用）
# ===========================
def draw_trend_chart(
    hist_df: pd.DataFrame,
    title: str,
    ylabel: str = "Score",
    y_min: float = 0,
    y_max: float = 100,
    add_fg_bands: bool = False,
    color: str = None,
    show_stats: bool = True,
):
    """汎用トレンドチャート（Fear&Greed / VI / VIX共通）— Plotlyインタラクティブ対応"""
    if hist_df is None or hist_df.empty:
        st.warning("データがありません")
        return

    df_work = hist_df.copy()
    df_work["date"] = pd.to_datetime(df_work["date"])
    if df_work["date"].dt.tz is not None:
        df_work["date"] = df_work["date"].dt.tz_localize(None)
    df_work = df_work.sort_values("date").reset_index(drop=True)

    line_color = color or LINE_NEUTRAL

    tab_3y, tab_1y, tab_3m, tab_all = st.tabs(["3年（デフォルト）", "1年", "3ヶ月", "全期間"])

    def _draw(df_slice: pd.DataFrame, date_fmt: str, locator):
        if df_slice.empty:
            st.info("この期間のデータがありません")
            return

        if PLOTLY_AVAILABLE:
            # ── Plotly インタラクティブチャート ──────────────────
            def _zone(s):
                if s >= 75: return "Extreme Greed 🤑"
                if s >= 55: return "Greed 😊"
                if s >= 45: return "Neutral 😐"
                if s >= 25: return "Fear 😟"
                return "Extreme Fear 😱"

            df_slice = df_slice.copy()
            df_slice["zone"] = df_slice["score"].apply(_zone)
            df_slice["tooltip"] = df_slice.apply(
                lambda r: f"{r['date'].strftime('%Y-%m-%d')}<br>"
                          f"スコア: <b>{r['score']:.1f}</b><br>"
                          f"{r['zone']}",
                axis=1,
            )

            fig = go.Figure()

            # Fear&Greedゾーン背景
            if add_fg_bands:
                fg_bands = [
                    (75, 100, "rgba(0,128,0,0.08)",    "Extreme Greed"),
                    (55,  75, "rgba(144,238,144,0.08)","Greed"),
                    (45,  55, "rgba(128,128,128,0.06)","Neutral"),
                    (25,  45, "rgba(255,165,0,0.08)",  "Fear"),
                    (0,   25, "rgba(255,0,0,0.08)",    "Extreme Fear"),
                ]
                for y0, y1, fc, nm in fg_bands:
                    fig.add_hrect(
                        y0=y0, y1=y1, fillcolor=fc, line_width=0,
                        annotation_text=nm, annotation_position="right",
                        annotation_font_size=9, annotation_font_color="#888",
                    )
                fig.add_hline(
                    y=50, line_dash="dash",
                    line_color="rgba(128,128,128,0.4)", line_width=1,
                )

            # メインライン
            fig.add_trace(go.Scatter(
                x=df_slice["date"],
                y=df_slice["score"],
                mode="lines+markers",
                line=dict(color=line_color, width=2),
                marker=dict(size=3, color=line_color),
                name=ylabel,
                hovertemplate="%{customdata}<extra></extra>",
                customdata=df_slice["tooltip"],
            ))

            # 最新値マーカー
            last_row = df_slice.iloc[-1]
            fig.add_trace(go.Scatter(
                x=[last_row["date"]],
                y=[last_row["score"]],
                mode="markers+text",
                marker=dict(size=10, color=line_color,
                            line=dict(width=2, color="white")),
                text=[f"{last_row['score']:.0f}"],
                textposition="middle right",
                textfont=dict(size=12, color=line_color),
                showlegend=False,
                hoverinfo="skip",
            ))

            fig.update_layout(
                title=dict(text=title, font=dict(size=12)),
                yaxis=dict(range=[y_min, y_max], title=ylabel,
                           gridcolor="rgba(200,200,200,0.3)"),
                xaxis=dict(gridcolor="rgba(200,200,200,0.2)"),
                plot_bgcolor="white",
                paper_bgcolor="white",
                hovermode="x unified",
                hoverlabel=dict(bgcolor="white", font_size=12),
                margin=dict(l=50, r=80, t=40, b=40),
                height=360,
                showlegend=False,
            )
            st.plotly_chart(fig, width="stretch")

        else:
            # ── matplotlibフォールバック ──────────────────────────
            fig, ax = plt.subplots(figsize=(12, 4))
            ax.plot(df_slice["date"], df_slice["score"],
                    linewidth=1.8, color=line_color,
                    marker="o", markersize=1.5)
            if add_fg_bands:
                ax.axhspan(0,  25, alpha=0.10, color="red")
                ax.axhspan(25, 45, alpha=0.10, color="orange")
                ax.axhspan(45, 55, alpha=0.10, color="gray")
                ax.axhspan(55, 75, alpha=0.10, color="lightgreen")
                ax.axhspan(75, 100, alpha=0.10, color="green")
                ax.axhline(50, linewidth=0.8, color="gray", linestyle="--", alpha=0.5)
            last_val  = df_slice["score"].iloc[-1]
            last_date = df_slice["date"].iloc[-1]
            ax.annotate(
                f"{last_val:.0f}",
                xy=(last_date, last_val),
                xytext=(8, 0), textcoords="offset points",
                fontsize=10, fontweight="bold", color=line_color, va="center",
            )
            ax.set_ylabel(ylabel, fontsize=11, fontfamily="DejaVu Sans")
            ax.set_ylim(y_min, y_max)
            ax.grid(True, alpha=0.3)
            ax.xaxis.set_major_formatter(mdates.DateFormatter(date_fmt))
            ax.xaxis.set_major_locator(locator)
            plt.xticks(rotation=45)
            plt.tight_layout()
            st.pyplot(fig, clear_figure=True)

        if show_stats:
            st.caption(
                f"データ点数: {len(df_slice)}日 | "
                f"平均: {df_slice['score'].mean():.1f} | "
                f"最高: {df_slice['score'].max():.1f} | "
                f"最低: {df_slice['score'].min():.1f}"
            )

    cutoff_3y  = pd.Timestamp.now() - pd.DateOffset(years=3)
    cutoff_1y  = pd.Timestamp.now() - pd.DateOffset(years=1)
    cutoff_3m  = pd.Timestamp.now() - pd.DateOffset(months=3)

    with tab_3m:
        _draw(df_work[df_work['date'] >= cutoff_3m], '%m/%d', mdates.WeekdayLocator(interval=2))
    with tab_1y:
        _draw(df_work[df_work['date'] >= cutoff_1y], '%Y/%m', mdates.MonthLocator(interval=1))
    with tab_3y:
        _draw(df_work[df_work['date'] >= cutoff_3y], '%Y/%m', mdates.MonthLocator(interval=3))
    with tab_all:
        _draw(df_work, '%Y/%m', mdates.YearLocator())
        if not df_work.empty:
            first_date = df_work['date'].iloc[0].strftime('%Y-%m-%d')
            last_date  = df_work['date'].iloc[-1].strftime('%Y-%m-%d')
            st.caption(f"📅 取得期間: {first_date} 〜 {last_date}（計{len(df_work)}日分）")


# ===========================
# ★ VIX / 日経VI 履歴データ取得（長期）
# ===========================
@st.cache_data(ttl=TTL_DAILY, show_spinner=False)
def fetch_long_history(symbol: str, years: int = 4) -> pd.DataFrame:
    """指定シンボルの長期日足データを取得"""
    try:
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=365 * years + 30)
        df = yf.Ticker(symbol).history(start=start_date, end=end_date, interval="1d", auto_adjust=False)
        if df is None or df.empty:
            return pd.DataFrame()
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        df = df.tz_convert(JST)
        df = df[["Close"]].dropna()
        df = df.reset_index().rename(columns={"Date": "date", "Close": "score", "Datetime": "date"})
        if "date" not in df.columns:
            df = df.reset_index()
            df.columns = ["date", "score"]
        df["date"] = pd.to_datetime(df["date"])
        if df["date"].dt.tz is not None:
            df["date"] = df["date"].dt.tz_localize(None)
        return df[["date", "score"]].sort_values("date").reset_index(drop=True)
    except Exception as e:
        logger.error(f"fetch_long_history error ({symbol}): {e}")
        return pd.DataFrame()




# ===========================
# ★ 予測スコア履歴管理 [Fix 2]
# ===========================
import matplotlib.ticker as _mticker

_PRED_HISTORY_KEY = "prediction_score_history"
_PRED_HISTORY_MAXDAYS = 90   # 最大90日分保持


def save_prediction_score(prob_up_tomorrow: float, composite: float):
    """今回の予測スコアを session_state に蓄積する（同日は上書き）"""
    today_str = datetime.now(JST).strftime("%Y-%m-%d")
    if _PRED_HISTORY_KEY not in st.session_state:
        st.session_state[_PRED_HISTORY_KEY] = []
    history = st.session_state[_PRED_HISTORY_KEY]
    history = [e for e in history if e["date"] != today_str]
    history.append({
        "date":      today_str,
        "prob_up":   round(float(prob_up_tomorrow), 2),
        "composite": round(float(composite), 4),
    })
    history = sorted(history, key=lambda x: x["date"])[-_PRED_HISTORY_MAXDAYS:]
    st.session_state[_PRED_HISTORY_KEY] = history


@st.cache_data(ttl=TTL_DAILY, show_spinner=False)
def build_prediction_history_from_market(days: int = 90) -> pd.DataFrame:
    """
    過去N日分の市場データから予測スコア(Prob.Up)をベクトル演算で一括生成。
    ループなし・高速・キャッシュ対応。
    戻り値: DataFrame columns=[date, prob_up, composite]
    """
    try:
        end   = datetime.now(timezone.utc)
        start = end - timedelta(days=days + 30)

        def _get(sym):
            try:
                df = yf.Ticker(sym).history(
                    start=start, end=end, interval="1d", auto_adjust=False
                )
                if df is None or df.empty:
                    return pd.Series(dtype=float, name=sym)
                if df.index.tz is None:
                    df.index = df.index.tz_localize("UTC")
                s = df.tz_convert(JST)["Close"].dropna()
                s.index = s.index.tz_localize(None)
                return s.rename(sym)
            except Exception:
                return pd.Series(dtype=float, name=sym)

        sp  = _get("^GSPC")
        fx  = _get("USDJPY=X")
        vix = _get("^VIX")
        sox = _get("^SOX")

        if sp.empty or len(sp) < 10:
            logger.warning("[pred_hist] S&P500データ取得失敗")
            return pd.DataFrame()

        # ── ベクトル演算でシグナルを一括計算 ──────────────
        df = pd.DataFrame({"sp": sp, "fx": fx, "vix": vix, "sox": sox})
        df = df.sort_index().ffill()

        # 騰落率・変化量（全行一括）
        df["sp_ret1"]  = df["sp"].pct_change(1)  * 100
        df["sp_ret5"]  = df["sp"].pct_change(5)  * 100
        df["fx_ret1"]  = df["fx"].pct_change(1)  * 100
        df["sox_ret1"] = df["sox"].pct_change(1) * 100
        df["vix_chg"]  = df["vix"].diff(1)

        # tanh正規化シグナル
        df["sig_sp1"]  = np.tanh(df["sp_ret1"]  / 1.5)
        df["sig_sp5"]  = np.tanh(df["sp_ret5"]  / 4.0)
        df["sig_fx"]   = np.tanh(df["fx_ret1"]  / 0.8)
        df["sig_sox"]  = np.tanh(df["sox_ret1"] / 2.0)

        # VIX: 通常は変化量逆張り、VIX>30は水準逆張り
        sig_vix_normal = np.tanh(-df["vix_chg"] / 2.0)
        sig_vix_high   = np.tanh((35 - df["vix"]) / 10.0)
        df["sig_vix"]  = np.where(df["vix"] > 30, sig_vix_high, sig_vix_normal)

        # 重み付き合成（重み: sp1=3.0, sp5=1.5, fx=2.5, vix=2.0, sox=2.5）
        W_SP1, W_SP5, W_FX, W_VIX, W_SOX = 3.0, 1.5, 2.5, 2.0, 2.5
        W_TOTAL = W_SP1 + W_SP5 + W_FX + W_VIX + W_SOX

        df["composite"] = (
            df["sig_sp1"]  * W_SP1 +
            df["sig_sp5"]  * W_SP5 +
            df["sig_fx"]   * W_FX  +
            df["sig_vix"]  * W_VIX +
            df["sig_sox"]  * W_SOX
        ) / W_TOTAL

        # -1〜+1 → 15〜85% にマッピング
        df["prob_up"] = (50.0 + df["composite"] * 30.0).clip(15, 85).round(2)

        # 過去days日分だけ返す
        cutoff = pd.Timestamp.now() - pd.Timedelta(days=days)
        df = df[df.index >= cutoff].dropna(subset=["prob_up"])

        result = df[["prob_up", "composite"]].reset_index()
        result.columns = ["date", "prob_up", "composite"]
        result["date"] = pd.to_datetime(result["date"]).dt.strftime("%Y-%m-%d")
        result["composite"] = result["composite"].round(4)

        logger.info(f"[pred_hist] 生成成功: {len(result)}日分")
        return result

    except Exception as e:
        logger.error(f"build_prediction_history_from_market error: {e}", exc_info=True)
        return pd.DataFrame()


@st.cache_data(ttl=TTL_DAILY, show_spinner=False)
def fetch_nikkei_for_comparison(days: int = 120) -> pd.DataFrame:
    """日経平均の日足データを取得（比較チャート用）"""
    try:
        end   = datetime.now(timezone.utc)
        start = end - timedelta(days=days)
        df = yf.Ticker("^N225").history(
            start=start, end=end, interval="1d", auto_adjust=False
        )
        if df is None or df.empty:
            return pd.DataFrame()
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        df = df.tz_convert(JST)
        df = df[["Close"]].dropna().reset_index()
        df.columns = ["date", "nikkei"]
        df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
        return df.sort_values("date").reset_index(drop=True)
    except Exception as e:
        logger.error(f"fetch_nikkei_for_comparison error: {e}")
        return pd.DataFrame()


def render_prediction_history():
    """
    予測スコア推移 × 日経平均実績の比較チャート（Plotlyインタラクティブ対応）
    """
    st.markdown("#### 📉 予測スコア推移 × 日経平均 実績比較")
    st.caption(
        "予測スコア（翌日Prob. Up）の推移と実際の日経平均を重ねて表示します。"
        "ホバー/タップで日付・スコアを確認できます。"
        "スコアはダッシュボードを開くたびに蓄積されます（サーバー再起動でリセット）。"
    )

    # ── 過去データを市場データから自動生成して補完 ────────────
    session_history = st.session_state.get(_PRED_HISTORY_KEY, [])

    # キャッシュをクリアして再取得（デバッグ：確実に実行させる）
    market_df = pd.DataFrame()
    try:
        market_df = build_prediction_history_from_market(days=90)
    except Exception as _me:
        logger.error(f"[pred_hist] market_df取得エラー: {_me}")

    # デバッグ情報をexpanderで表示
    with st.expander("🔧 予測履歴デバッグ情報", expanded=False):
        st.write(f"market_df型: {type(market_df)}")
        st.write(f"market_df件数: {len(market_df) if isinstance(market_df, pd.DataFrame) else 'N/A'}")
        st.write(f"market_df.empty: {market_df.empty if isinstance(market_df, pd.DataFrame) else 'N/A'}")
        if isinstance(market_df, pd.DataFrame) and not market_df.empty:
            st.dataframe(market_df.head(5))
        st.write(f"session_history件数: {len(session_history)}")

    # DataFrameをリストに変換してsessionとマージ（session優先）
    market_list = []
    if isinstance(market_df, pd.DataFrame) and len(market_df) > 0:
        market_list = market_df.to_dict("records")

    session_dates = {e["date"] for e in session_history}
    merged = list(session_history) + [
        e for e in market_list if e["date"] not in session_dates
    ]
    history = sorted(merged, key=lambda x: x["date"])

    nk_df = fetch_nikkei_for_comparison(days=120)

    # ── 日経平均のみ表示（予測データ不足時）──────────────────
    if len(history) < 2:
        st.info(
            f"📊 予測スコアの推移データが不足しています（現在 {len(history)} 日分 / market:{len(market_list)}件）。"
            "\n複数回・別の日にダッシュボードを開くと履歴が積み上がります。"
        )
        if not nk_df.empty and PLOTLY_AVAILABLE:
            nk_df["tooltip"] = nk_df.apply(
                lambda r: f"{r['date'].strftime('%Y-%m-%d')}<br>日経平均: <b>{r['nikkei']:,.0f}</b>円",
                axis=1,
            )
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=nk_df["date"], y=nk_df["nikkei"],
                mode="lines", line=dict(color="#1565c0", width=2),
                name="日経平均",
                hovertemplate="%{customdata}<extra></extra>",
                customdata=nk_df["tooltip"],
            ))
            fig.update_layout(
                title="日経平均（実績）",
                yaxis=dict(title="Nikkei 225", tickformat=",.0f",
                           gridcolor="rgba(200,200,200,0.3)"),
                xaxis=dict(gridcolor="rgba(200,200,200,0.2)"),
                plot_bgcolor="white", paper_bgcolor="white",
                hovermode="x unified",
                hoverlabel=dict(bgcolor="white", font_size=12),
                height=300, margin=dict(l=60, r=20, t=40, b=40),
            )
            st.plotly_chart(fig, width="stretch")
            st.caption("↑ 日経平均実績のみ表示中（予測スコア蓄積後に比較グラフが表示されます）")
        return

    # DataFrameに変換
    hist_df = pd.DataFrame(history)
    hist_df["date"] = pd.to_datetime(hist_df["date"])
    hist_df = hist_df.sort_values("date").reset_index(drop=True)

    # 統計情報
    col_s1, col_s2, col_s3, col_s4 = st.columns(4)
    col_s1.metric("蓄積日数",     f"{len(hist_df)}日")
    col_s2.metric("平均Prob. Up", f"{hist_df['prob_up'].mean():.1f}%")
    col_s3.metric("最高スコア",   f"{hist_df['prob_up'].max():.1f}%")
    col_s4.metric("最低スコア",   f"{hist_df['prob_up'].min():.1f}%")

    # ヒット率計算
    hit_count = total_check = 0
    if not nk_df.empty:
        for i in range(len(hist_df) - 1):
            pred_date = hist_df["date"].iloc[i]
            prob      = hist_df["prob_up"].iloc[i]
            next_date = hist_df["date"].iloc[i + 1]
            nk_pred   = nk_df[nk_df["date"] <= pred_date]
            nk_next   = nk_df[nk_df["date"] <= next_date]
            if nk_pred.empty or nk_next.empty:
                continue
            p0 = float(nk_pred["nikkei"].iloc[-1])
            p1 = float(nk_next["nikkei"].iloc[-1])
            if (p1 > p0) == (prob > 50):
                hit_count += 1
            total_check += 1

    if PLOTLY_AVAILABLE:
        # ── Plotly 2軸インタラクティブチャート ───────────────
        hist_df["direction"] = hist_df["prob_up"].apply(
            lambda x: "📈 強気" if x > 55 else ("📉 弱気" if x < 45 else "➡️ 中立")
        )
        hist_df["tooltip"] = hist_df.apply(
            lambda r: (
                f"{r['date'].strftime('%Y-%m-%d')}<br>"
                f"Prob. Up: <b>{r['prob_up']:.1f}%</b><br>"
                f"{r['direction']}"
            ),
            axis=1,
        )

        fig = go.Figure()

        # 50%ライン
        fig.add_hline(
            y=50, line_dash="dash",
            line_color="rgba(128,128,128,0.5)", line_width=1,
        )
        fig.add_hline(
            y=55, line_dash="dot",
            line_color="rgba(76,175,80,0.4)", line_width=1,
        )
        fig.add_hline(
            y=45, line_dash="dot",
            line_color="rgba(244,67,54,0.4)", line_width=1,
        )

        # 塗りつぶし（強気=緑 / 弱気=赤）
        fig.add_trace(go.Scatter(
            x=hist_df["date"],
            y=hist_df["prob_up"].clip(lower=50),
            fill="tonexty", fillcolor="rgba(76,175,80,0.12)",
            line=dict(width=0), showlegend=False, hoverinfo="skip",
            yaxis="y1",
        ))
        fig.add_trace(go.Scatter(
            x=hist_df["date"], y=[50] * len(hist_df),
            line=dict(width=0), showlegend=False, hoverinfo="skip",
            yaxis="y1",
        ))

        # 予測スコアライン（左軸）
        fig.add_trace(go.Scatter(
            x=hist_df["date"],
            y=hist_df["prob_up"],
            mode="lines+markers",
            line=dict(color="#9c27b0", width=2.5),
            marker=dict(size=6, color="#9c27b0",
                        line=dict(width=1.5, color="white")),
            name="翌日Prob. Up（予測）",
            hovertemplate="%{customdata}<extra></extra>",
            customdata=hist_df["tooltip"],
            yaxis="y1",
        ))

        # 日経平均ライン（右軸）
        if not nk_df.empty:
            date_min = hist_df["date"].min() - pd.Timedelta(days=3)
            nk_plot  = nk_df[nk_df["date"] >= date_min].copy()
            if not nk_plot.empty:
                nk_plot["tooltip"] = nk_plot.apply(
                    lambda r: (
                        f"{r['date'].strftime('%Y-%m-%d')}<br>"
                        f"日経平均: <b>{r['nikkei']:,.0f}</b>円"
                    ),
                    axis=1,
                )
                fig.add_trace(go.Scatter(
                    x=nk_plot["date"],
                    y=nk_plot["nikkei"],
                    mode="lines",
                    line=dict(color="#1565c0", width=1.8),
                    name="日経平均（実績）",
                    hovertemplate="%{customdata}<extra></extra>",
                    customdata=nk_plot["tooltip"],
                    yaxis="y2",
                    opacity=0.8,
                ))

        # レイアウト（2軸）
        hit_text = (
            f"翌日予測ヒット率: {hit_count/total_check*100:.0f}% "
            f"({hit_count}/{total_check})"
            if total_check > 0 else ""
        )
        fig.update_layout(
            title=dict(
                text=f"予測Prob. Up × 日経平均実績　{hit_text}",
                font=dict(size=12),
            ),
            yaxis=dict(
                title="Prob. Up (%)",
                title_font=dict(color="#9c27b0"),
                tickfont=dict(color="#9c27b0"),
                range=[25, 80],
                gridcolor="rgba(200,200,200,0.3)",
                side="left",
            ),
            yaxis2=dict(
                title="Nikkei 225",
                title_font=dict(color="#1565c0"),
                tickfont=dict(color="#1565c0"),
                tickformat=",.0f",
                overlaying="y",
                side="right",
                gridcolor="rgba(0,0,0,0)",
            ),
            xaxis=dict(gridcolor="rgba(200,200,200,0.2)"),
            plot_bgcolor="white",
            paper_bgcolor="white",
            hovermode="x unified",
            hoverlabel=dict(bgcolor="white", font_size=12),
            legend=dict(
                orientation="h", yanchor="bottom",
                y=1.02, xanchor="left", x=0,
            ),
            height=420,
            margin=dict(l=60, r=70, t=60, b=40),
        )
        st.plotly_chart(fig, width="stretch")

    else:
        # ── matplotlibフォールバック ──────────────────────────
        fig, ax1 = plt.subplots(figsize=(13, 5))
        color_score = "#9c27b0"
        ax1.set_ylabel("Prob. Up (%)", color=color_score, fontsize=11)
        ax1.plot(hist_df["date"], hist_df["prob_up"],
                 color=color_score, linewidth=2.2,
                 marker="o", markersize=5, label="翌日Prob. Up（予測）", zorder=5)
        ax1.fill_between(hist_df["date"], hist_df["prob_up"], 50,
                         where=(hist_df["prob_up"] >= 50),
                         alpha=0.15, color="#4caf50")
        ax1.fill_between(hist_df["date"], hist_df["prob_up"], 50,
                         where=(hist_df["prob_up"] < 50),
                         alpha=0.15, color="#f44336")
        ax1.axhline(50, linewidth=1.0, color="gray", linestyle="--", alpha=0.6)
        ax1.set_ylim(30, 75)
        if not nk_df.empty:
            ax2 = ax1.twinx()
            date_min = hist_df["date"].min() - pd.Timedelta(days=3)
            nk_plot  = nk_df[nk_df["date"] >= date_min]
            if not nk_plot.empty:
                ax2.plot(nk_plot["date"], nk_plot["nikkei"],
                         color="#1565c0", linewidth=1.8, alpha=0.75, label="日経平均（実績）")
                ax2.set_ylabel("Nikkei 225 Price", color="#1565c0", fontsize=11)
        ax1.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
        ax1.xaxis.set_major_locator(mdates.WeekdayLocator(interval=1))
        plt.xticks(rotation=45, fontsize=9)
        ax1.grid(True, alpha=0.25)
        plt.tight_layout()
        st.pyplot(fig, clear_figure=True)

    # 詳細テーブル
    with st.expander("📋 予測スコア履歴テーブル", expanded=False):
        disp = hist_df[["date", "prob_up", "composite"]].copy()
        disp["date"] = pd.to_datetime(disp["date"]).dt.strftime("%Y-%m-%d")
        disp = disp.rename(columns={
            "date":      "日付",
            "prob_up":   "Prob. Up(%)",
            "composite": "総合スコア",
        })
        disp["方向性"] = disp["Prob. Up(%)"].apply(
            lambda x: "📈 強気" if x > 55 else ("📉 弱気" if x < 45 else "➡️ 中立")
        )
        st.dataframe(disp, width="stretch", hide_index=True)

@st.cache_data(ttl=TTL_DAILY, show_spinner=False)
def fetch_nikkei_vi_history(years: int = 4) -> pd.DataFrame:
    """
    日経VI（日本版恐怖指数）の履歴を取得。
    ^JNIV が取得できない場合は ^N225 の実現ボラティリティで代替。
    """
    # まず ^JNIV を試す
    df = fetch_long_history("^JNIV", years=years)
    if not df.empty:
        df.attrs["source"] = "^JNIV"
        return df

    # フォールバック: 日経平均の実現ボラティリティ（20日）
    try:
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=365 * years + 60)
        nk = yf.Ticker("^N225").history(start=start_date, end=end_date, interval="1d", auto_adjust=False)
        if nk is None or nk.empty:
            return pd.DataFrame()
        if nk.index.tz is None:
            nk.index = nk.index.tz_localize("UTC")
        nk = nk.tz_convert(JST)
        ret = nk["Close"].pct_change()
        # 20日ローリング実現ボラ (年率換算 × 100 → VI相当値)
        rv = ret.rolling(20).std() * np.sqrt(252) * 100
        rv = rv.dropna()
        dates = rv.index
        # timezone aware → naive
        if dates.tz is not None:
            dates = dates.tz_localize(None)
        df = pd.DataFrame({"date": pd.to_datetime(dates), "score": rv.values})
        df.attrs["source"] = "RV(^N225)"
        return df.sort_values("date").reset_index(drop=True)
    except Exception as e:
        logger.error(f"Nikkei VI fallback error: {e}")
        return pd.DataFrame()


# ===========================
# ★ 日経平均 予測スコア エンジン v2（9カテゴリ完全版）
# ===========================

# カテゴリ定義（表示用）
SIGNAL_CATEGORIES = {
    "① グローバル市場連動": {
        "icon": "🌏",
        "desc": "S&P500・NASDAQ・SOXは日経と高い相関。特にSOX→半導体→日本電機株→日経のルートが重要。",
        "color": "#1565c0",
    },
    "② 為替（USD/JPY）": {
        "icon": "💱",
        "desc": "日本株は円安＝株高の影響が強い。輸出企業のEPS直接影響・外国人投資家フロー変化。",
        "color": "#2e7d32",
    },
    "③ 先物市場": {
        "icon": "📊",
        "desc": "日本は先物主導市場。CME日経先物と現物の乖離（ギャップ）が翌朝の寄り付きを決定する。",
        "color": "#6a1b9a",
    },
    "④ 金利": {
        "icon": "📈",
        "desc": "AIバブル以降、米10年金利↑→ハイテク↓の影響が強まっている。日米金利差も要チェック。",
        "color": "#e65100",
    },
    "⑤ VIX（恐怖指数）": {
        "icon": "😱",
        "desc": "VIX>25でBear警戒、VIX<15でBull基調。変化率も重要（急低下=買いシグナル）。",
        "color": "#b71c1c",
    },
    "⑥ コモディティ": {
        "icon": "🛢️",
        "desc": "原油↑→インフレ→金利↑→株↓。ゴールド↑→リスクオフの代替シグナル。",
        "color": "#795548",
    },
    "⑦ 海外ETFフロー": {
        "icon": "💰",
        "desc": "iShares MSCI Japan ETF(EWJ)の出来高・価格変化は外国人の日本株需要を示す先行指標。",
        "color": "#00695c",
    },
    "⑧ テクニカル分析": {
        "icon": "📐",
        "desc": "RSI・MACD・ボリンジャーバンド・移動平均乖離。RSI<30→逆張り買い、RSI>70→過熱圏。",
        "color": "#4527a0",
    },
    "⑨ 市場センチメント": {
        "icon": "🧠",
        "desc": "AIニュース要約スコア（Bloomberg/Nikkei/Reuters）。曜日効果・季節性も含む。",
        "color": "#ad1457",
    },
}


def _safe_hist(symbol: str, start, end) -> pd.DataFrame:
    """yfinance履歴を安全に取得"""
    try:
        df = yf.Ticker(symbol).history(start=start, end=end, interval="1d", auto_adjust=False)
        if df is None or df.empty:
            return pd.DataFrame()
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        return df.tz_convert(JST)
    except Exception as e:
        logger.debug(f"_safe_hist({symbol}): {e}")
        return pd.DataFrame()


def _calc_rsi(series: pd.Series, period: int = 14) -> float:
    """RSI計算"""
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    val = rsi.iloc[-1]
    return float(val) if not pd.isna(val) else 50.0


def _calc_macd(series: pd.Series) -> Tuple[float, float]:
    """MACD / Signal 計算、(macd_val, signal_val) を返す"""
    ema12 = series.ewm(span=12, adjust=False).mean()
    ema26 = series.ewm(span=26, adjust=False).mean()
    macd  = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    return float(macd.iloc[-1]), float(signal.iloc[-1])


def _calc_bb_pct(series: pd.Series, window: int = 20) -> float:
    """ボリンジャーバンド %B（0=下限, 1=上限）"""
    ma   = series.rolling(window).mean()
    std  = series.rolling(window).std()
    upper = ma + 2 * std
    lower = ma - 2 * std
    denom = (upper - lower).iloc[-1]
    if denom == 0:
        return 0.5
    bb_pct = (series.iloc[-1] - lower.iloc[-1]) / denom
    return float(np.clip(bb_pct, 0, 1))


@st.cache_data(ttl=TTL_DAILY, show_spinner=False)
def compute_nikkei_prediction() -> Dict[str, Any]:
    """
    9カテゴリ・25シグナル の重み付き合成で
    日経平均翌日・今週のProb. Upを推定する。

    カテゴリ:
      ① グローバル市場連動（S&P500, NASDAQ, SOX）
      ② 為替（USDJPY リターン・モメンタム・ボラティリティ）
      ③ 先物市場（日経先物CME vs 現物ギャップ）
      ④ 金利（米10年・日本10年・金利差）
      ⑤ VIX（水準・変化・恐怖度）
      ⑥ コモディティ（原油・ゴールド）
      ⑦ 海外ETFフロー（EWJ 価格・出来高）
      ⑧ テクニカル（RSI・MACD・BB・MA乖離）
      ⑨ 市場センチメント（曜日効果・季節性）
    """
    # ── シグナル収集構造 ──────────────────────────
    # details_by_cat: {カテゴリ名: [signal_dict, ...]}
    details_by_cat: Dict[str, List[Dict]] = {k: [] for k in SIGNAL_CATEGORIES}
    all_details: List[Dict] = []
    score_sum   = 0.0
    weight_sum  = 0.0

    def add_signal(category: str, name: str, value: float,
                   weight: float, desc: str, raw: str = ""):
        nonlocal score_sum, weight_sum
        clamped = float(np.clip(value, -1.0, 1.0))
        score_sum  += clamped * weight
        weight_sum += weight
        icon = "🟢" if clamped > 0.15 else ("🔴" if clamped < -0.15 else "⚪")
        row = {
            "判定": icon, "カテゴリ": category, "シグナル": name,
            "値": f"{clamped:+.3f}", "重み": f"{weight:.1f}",
            "生データ": raw, "説明": desc,
        }
        details_by_cat[category].append(row)
        all_details.append(row)

    try:
        end   = datetime.now(timezone.utc)
        start = end - timedelta(days=420)

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # ① グローバル市場連動
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        cat = "① グローバル市場連動"

        # S&P500 前日・5日リターン
        sp_df = _safe_hist("^GSPC", start, end)
        sp_val = sp_ret1 = sp_ret5 = None
        if not sp_df.empty and len(sp_df) >= 6:
            sp_c = sp_df["Close"].dropna()
            sp_val  = float(sp_c.iloc[-1])
            sp_ret1 = (sp_c.iloc[-1] / sp_c.iloc[-2] - 1) * 100
            sp_ret5 = (sp_c.iloc[-1] / sp_c.iloc[-6] - 1) * 100
            add_signal(cat, "S&P500 前日騰落",
                       np.tanh(sp_ret1 / 1.5), 3.0,
                       "NY終値と翌朝日経は高相関", f"{sp_ret1:+.2f}%")
            add_signal(cat, "S&P500 5日モメンタム",
                       np.tanh(sp_ret5 / 4.0), 1.5,
                       "中期トレンドの方向感", f"{sp_ret5:+.2f}%")

        # NASDAQ 前日騰落
        nq_df = _safe_hist("^IXIC", start, end)
        nq_val = None
        if not nq_df.empty and len(nq_df) >= 2:
            nq_c   = nq_df["Close"].dropna()
            nq_val = float(nq_c.iloc[-1])
            nq_ret1 = (nq_c.iloc[-1] / nq_c.iloc[-2] - 1) * 100
            add_signal(cat, "NASDAQ 前日騰落",
                       np.tanh(nq_ret1 / 1.5), 2.5,
                       "ハイテク比率が高い日本株と連動", f"{nq_ret1:+.2f}%")

        # SOX（Philadelphia Semiconductor Index）
        sox_df = _safe_hist("^SOX", start, end)
        sox_val = None
        if not sox_df.empty and len(sox_df) >= 2:
            sox_c   = sox_df["Close"].dropna()
            sox_val = float(sox_c.iloc[-1])
            sox_ret1 = (sox_c.iloc[-1] / sox_c.iloc[-2] - 1) * 100
            add_signal(cat, "SOX 前日騰落",
                       np.tanh(sox_ret1 / 2.0), 2.5,
                       "SOX→半導体→日本電機株→日経 の波及経路",
                       f"{sox_ret1:+.2f}%")

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # ② 為替（USD/JPY）
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        cat = "② 為替（USD/JPY）"

        fx_df  = _safe_hist("USDJPY=X", start, end)
        fx_val = None
        if not fx_df.empty and len(fx_df) >= 22:
            fx_c   = fx_df["Close"].dropna()
            fx_val = float(fx_c.iloc[-1])

            # リターン（円安プラス）
            fx_ret1 = (fx_c.iloc[-1] / fx_c.iloc[-2] - 1) * 100
            fx_ret5 = (fx_c.iloc[-1] / fx_c.iloc[-6] - 1) * 100
            add_signal(cat, "USDJPY 前日変化",
                       np.tanh(fx_ret1 / 0.8), 2.5,
                       "円安→輸出企業益・外国人フロー流入", f"{fx_ret1:+.3f}%")
            add_signal(cat, "USDJPY 5日モメンタム",
                       np.tanh(fx_ret5 / 1.5), 2.0,
                       "円安トレンドの持続性", f"{fx_ret5:+.3f}%")

            # ボラティリティ（高ボラ=不確実性→弱気）
            fx_vol20 = fx_c.pct_change().rolling(20).std().iloc[-1] * 100
            add_signal(cat, "USDJPY ボラティリティ20日",
                       np.tanh(-(fx_vol20 - 0.5) / 0.3), 1.0,
                       "為替ボラ高＝不確実性増大→弱気",
                       f"{fx_vol20:.3f}%")

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # ③ 先物市場
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        cat = "③ 先物市場"

        nkd_df  = _safe_hist("NKD=F", start, end)   # CME 日経225先物（ドル建て）
        nk_df   = _safe_hist("^N225", start, end)    # 日経現物
        nkd_val = gap_pct = None

        if not nkd_df.empty and not nk_df.empty:
            nkd_c = nkd_df["Close"].dropna()
            nk_c  = nk_df["Close"].dropna()
            nkd_val = float(nkd_c.iloc[-1])

            # CME先物 vs 現物 乖離率（% 換算、ドル→円は省略してpct比較）
            nkd_ret1 = (nkd_c.iloc[-1] / nkd_c.iloc[-2] - 1) * 100 if len(nkd_c) >= 2 else 0
            nk_ret1  = (nk_c.iloc[-1]  / nk_c.iloc[-2]  - 1) * 100  if len(nk_c)  >= 2 else 0
            gap_pct  = nkd_ret1 - nk_ret1   # CMEが現物より強い→強気
            add_signal(cat, "CME先物 vs 現物乖離",
                       np.tanh(gap_pct / 1.5), 3.0,
                       "CME日経-現物ギャップ>0→翌朝窓開け上昇示唆",
                       f"CME:{nkd_ret1:+.2f}% 現物:{nk_ret1:+.2f}% gap:{gap_pct:+.2f}%")

            # 日経先物 5日モメンタム
            if len(nkd_c) >= 6:
                nkd_mom5 = (nkd_c.iloc[-1] / nkd_c.iloc[-6] - 1) * 100
                add_signal(cat, "CME先物 5日モメンタム",
                           np.tanh(nkd_mom5 / 4.0), 1.5,
                           "先物からの需給動向", f"{nkd_mom5:+.2f}%")
        else:
            # CME取得不可の場合は日経現物のみ
            nk_df  = _safe_hist("^N225", start, end)
            nk_c   = nk_df["Close"].dropna() if not nk_df.empty else pd.Series(dtype=float)

        # 日経現物がまだなければ取得
        if "nk_c" not in dir() or (isinstance(nk_c, pd.Series) and nk_c.empty):
            nk_df = _safe_hist("^N225", start, end)
            if nk_df.empty or len(nk_df) < 25:
                return {"ok": False, "reason": "日経平均データ不足"}
            nk_c = nk_df["Close"].dropna()
        elif nk_c.empty:
            return {"ok": False, "reason": "日経平均データ不足"}

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # ④ 金利
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        cat = "④ 金利"

        # 米10年金利
        tnx_df  = _safe_hist("^TNX", start, end)
        tnx_val = None
        if not tnx_df.empty and len(tnx_df) >= 6:
            tnx_c   = tnx_df["Close"].dropna()
            tnx_val = float(tnx_c.iloc[-1])
            tnx_ch1 = float(tnx_c.iloc[-1]) - float(tnx_c.iloc[-2])
            tnx_ch5 = float(tnx_c.iloc[-1]) - float(tnx_c.iloc[-6])
            add_signal(cat, "米10年金利 前日変化",
                       np.tanh(-tnx_ch1 / 0.10), 2.0,
                       "金利↑→ハイテクバリュエーション↓", f"{tnx_ch1:+.3f}%pt")
            add_signal(cat, "米10年金利 5日変化",
                       np.tanh(-tnx_ch5 / 0.25), 1.5,
                       "金利トレンド変化の方向性", f"{tnx_ch5:+.3f}%pt")

        # 日本10年金利（^TNX相当がないためJGB ETF代替: 2621.T or ^N225代理）
        jgb_df  = _safe_hist("^JGB", start, end)
        jgb_val = None
        if not jgb_df.empty and len(jgb_df) >= 2:
            jgb_c   = jgb_df["Close"].dropna()
            jgb_val = float(jgb_c.iloc[-1])
            jgb_ch1 = float(jgb_c.iloc[-1]) - float(jgb_c.iloc[-2])
            add_signal(cat, "日本10年金利 前日変化",
                       np.tanh(-jgb_ch1 / 0.05), 1.5,
                       "日銀政策変化への感応度", f"{jgb_ch1:+.3f}%pt")

        # 日米金利差（米−日）変化
        if tnx_val is not None and jgb_val is not None:
            spread = tnx_val - jgb_val
            add_signal(cat, "日米金利差（米−日）",
                       np.tanh((spread - 3.0) / 1.0), 1.0,
                       "金利差拡大→円安→株高のチェーン",
                       f"{spread:.3f}%pt")

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # ⑤ VIX（恐怖指数）
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        cat = "⑤ VIX（恐怖指数）"

        vix_df  = _safe_hist("^VIX", start, end)
        vix_val = None
        if not vix_df.empty and len(vix_df) >= 5:
            vix_c   = vix_df["Close"].dropna()
            vix_val = float(vix_c.iloc[-1])

            # 水準（<15=強気, 15-25=中立, >25=弱気）
            add_signal(cat, "VIX 水準",
                       np.tanh(-(vix_val - 20) / 8), 3.0,
                       "VIX<15=Bull, VIX>25=Bear",
                       f"VIX={vix_val:.2f}")

            # 前日変化率
            vix_ch1 = (vix_c.iloc[-1] / vix_c.iloc[-2] - 1) * 100
            add_signal(cat, "VIX 前日変化",
                       np.tanh(-vix_ch1 / 8), 2.0,
                       "VIX急低下→リスクオン回帰", f"{vix_ch1:+.2f}%")

            # 3日変化
            vix_ch3 = (vix_c.iloc[-1] / vix_c.iloc[-4] - 1) * 100
            add_signal(cat, "VIX 3日変化",
                       np.tanh(-vix_ch3 / 12), 1.5,
                       "短期トレンドの反転確認用", f"{vix_ch3:+.2f}%")

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # ⑥ コモディティ
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        cat = "⑥ コモディティ"

        # 原油WTI（前日・5日）
        oil_df  = _safe_hist("CL=F", start, end)
        oil_val = None
        if not oil_df.empty and len(oil_df) >= 6:
            oil_c   = oil_df["Close"].dropna()
            oil_val = float(oil_c.iloc[-1])
            oil_ret1 = (oil_c.iloc[-1] / oil_c.iloc[-2] - 1) * 100
            oil_ret5 = (oil_c.iloc[-1] / oil_c.iloc[-6] - 1) * 100
            # 原油↑→インフレ懸念→金利↑→株↓（逆相関）
            add_signal(cat, "原油 前日変化",
                       np.tanh(-oil_ret1 / 3), 1.0,
                       "原油↑→インフレ→金利↑→株↓", f"{oil_ret1:+.2f}%")
            add_signal(cat, "原油 5日モメンタム",
                       np.tanh(-oil_ret5 / 5), 0.8,
                       "中期コストプッシュ圧力", f"{oil_ret5:+.2f}%")

        # ゴールド（リスクオフ指標）
        gold_df  = _safe_hist("GC=F", start, end)
        gold_val = None
        if not gold_df.empty and len(gold_df) >= 2:
            gold_c   = gold_df["Close"].dropna()
            gold_val = float(gold_c.iloc[-1])
            gold_ret1 = (gold_c.iloc[-1] / gold_c.iloc[-2] - 1) * 100
            # 金↑はリスクオフ→株弱気シグナル
            add_signal(cat, "ゴールド 前日変化",
                       np.tanh(-gold_ret1 / 1.5), 0.8,
                       "金↑=リスクオフ→株弱気傾向",
                       f"{gold_ret1:+.2f}%")

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # ⑦ 海外ETFフロー（EWJ: iShares MSCI Japan ETF）
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        cat = "⑦ 海外ETFフロー"

        ewj_df  = _safe_hist("EWJ", start, end)
        ewj_val = None
        if not ewj_df.empty and len(ewj_df) >= 6:
            ewj_c    = ewj_df["Close"].dropna()
            ewj_vol  = ewj_df["Volume"].dropna()
            ewj_val  = float(ewj_c.iloc[-1])
            ewj_ret1 = (ewj_c.iloc[-1] / ewj_c.iloc[-2] - 1) * 100
            ewj_ret5 = (ewj_c.iloc[-1] / ewj_c.iloc[-6] - 1) * 100
            add_signal(cat, "EWJ 前日騰落",
                       np.tanh(ewj_ret1 / 1.5), 2.0,
                       "外国人の日本株ETF需給を直接反映",
                       f"{ewj_ret1:+.2f}%")
            add_signal(cat, "EWJ 5日モメンタム",
                       np.tanh(ewj_ret5 / 3.0), 1.5,
                       "外国人フローの方向感", f"{ewj_ret5:+.2f}%")

            # 出来高変化（急増=強い関心）
            if len(ewj_vol) >= 6:
                vol_avg5 = float(ewj_vol.iloc[-6:-1].mean())
                vol_ratio = float(ewj_vol.iloc[-1]) / vol_avg5 if vol_avg5 > 0 else 1.0
                # 出来高増加は方向性を強化するがシグナル中立
                ewj_price_vol_signal = ewj_ret1 / 1.5 * min(vol_ratio, 2.0) / 2.0
                add_signal(cat, "EWJ 出来高×価格変化",
                           np.tanh(ewj_price_vol_signal), 1.0,
                           "出来高増×上昇=強いBull、増×下落=強いBear",
                           f"出来高比:{vol_ratio:.2f}x")

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # ⑧ テクニカル分析（日経平均）
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        cat = "⑧ テクニカル分析"

        # nk_cはすでに取得済み
        if len(nk_c) >= 26:
            # RSI14（逆張り）
            rsi_val = _calc_rsi(nk_c, 14)
            rsi_sig = -(rsi_val - 50) / 50
            add_signal(cat, "RSI14",
                       np.tanh(rsi_sig * 1.5), 1.5,
                       f"RSI<30→+1 RSI>70→-1（逆張り）",
                       f"RSI={rsi_val:.1f}")

            # MACD
            macd_val, macd_sig = _calc_macd(nk_c)
            macd_cross = macd_val - macd_sig
            # MACD>Signal=強気
            nk_price = float(nk_c.iloc[-1])
            macd_norm = macd_cross / (nk_price * 0.005) if nk_price > 0 else 0
            add_signal(cat, "MACD クロス",
                       np.tanh(macd_norm), 1.5,
                       "MACD>Signal=強気、MACD<Signal=弱気",
                       f"MACD:{macd_val:.0f} Sig:{macd_sig:.0f}")

            # ボリンジャーバンド %B
            bb_pct = _calc_bb_pct(nk_c, 20)
            # %B < 0.2 → 売られすぎ=逆張り強気
            # %B > 0.8 → 買われすぎ=逆張り弱気
            bb_sig = -(bb_pct - 0.5) * 2
            add_signal(cat, "ボリンジャーバンド %B",
                       np.tanh(bb_sig), 1.0,
                       "%B<0.2=売られすぎ(強気) %B>0.8=買われすぎ(弱気)",
                       f"%B={bb_pct:.2f}")

            # 移動平均乖離（MA25・MA50・MA200）
            for window, weight, label in [(25, 1.5, "MA25"), (50, 1.5, "MA50"), (200, 1.0, "MA200")]:
                if len(nk_c) >= window:
                    ma_val  = float(nk_c.rolling(window).mean().iloc[-1])
                    ma_diff = (float(nk_c.iloc[-1]) / ma_val - 1) * 100
                    # MA上方=トレンド強気、過大乖離（>15%）は過熱として減衰
                    if ma_diff > 15:
                        sig = np.tanh((30 - ma_diff) / 10)
                    elif ma_diff < -15:
                        sig = np.tanh((-30 - ma_diff) / -10)
                    else:
                        sig = np.tanh(ma_diff / 8)
                    add_signal(cat, f"{label}乖離",
                               sig, weight,
                               f"現値vs{label}（±15%超で過熱/過売り判定）",
                               f"{ma_diff:+.2f}%")

            # 5日モメンタム（テクニカル補強）
            if len(nk_c) >= 6:
                mom5 = (float(nk_c.iloc[-1]) / float(nk_c.iloc[-6]) - 1) * 100
                add_signal(cat, "5日モメンタム",
                           np.tanh(mom5 / 3), 1.5,
                           "短期トレンド追随", f"{mom5:+.2f}%")

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # ⑨ 市場センチメント
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        cat = "⑨ 市場センチメント"

        # 曜日効果（過去の統計的傾向）
        today_wd = datetime.now(JST).weekday()  # 0=月, 4=金
        wd_names  = ["Mon", "Tue", "Wed", "Thu", "Fri"]
        if not nk_df.empty and today_wd < 5:
            nk_wd = nk_df.copy()
            nk_wd["wd"]  = nk_wd.index.weekday
            nk_wd["ret"] = nk_wd["Close"].pct_change()
            wd_data = nk_wd[nk_wd["wd"] == today_wd]["ret"].dropna()
            if len(wd_data) >= 10:
                wr = float((wd_data > 0).mean())
                add_signal(cat, f"曜日効果({wd_names[today_wd]})",
                           (wr - 0.5) * 2, 0.5,
                           f"{wd_names[today_wd]}曜日の勝率（過去{len(wd_data)}回）",
                           f"勝率{wr:.1%}")

        # 月末・月初効果（月初5日以内=買い傾向）
        today_dom = datetime.now(JST).day
        if today_dom <= 5:
            add_signal(cat, "月初効果",
                       0.3, 0.5,
                       "月初5日間は機関投資家の新規資金流入傾向",
                       f"{today_dom}日")
        elif today_dom >= 25:
            add_signal(cat, "月末効果",
                       -0.2, 0.3,
                       "月末はリバランス売り傾向",
                       f"{today_dom}日")

        # Fear & Greed Index（CNN）の簡易取得
        try:
            fg_url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata/2024-01-01"
            fg_r = requests.get(fg_url, timeout=8,
                                headers={"User-Agent": "Mozilla/5.0"})
            if fg_r.status_code == 200:
                fg_score = float(fg_r.json()["fear_and_greed"]["score"])
                # F&G: 50中立、高いほど強気
                add_signal(cat, "Fear & Greed Index",
                           np.tanh((fg_score - 50) / 25), 1.5,
                           "CNN F&G: 75+=Extreme Greed, 25-=Extreme Fear",
                           f"{fg_score:.0f}/100")
        except Exception:
            pass

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 総合スコア計算
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        if weight_sum <= 0:
            return {"ok": False, "reason": "シグナル計算失敗"}

        composite = score_sum / weight_sum  # -1.0 〜 +1.0

        # シグモイド変換
        prob_up_tomorrow = float(1 / (1 + np.exp(-composite * 1.5)) * 100)
        prob_down_tomorrow = 100 - prob_up_tomorrow
        prob_up_week = float(1 / (1 + np.exp(-composite * 1.0)) * 100)
        prob_down_week = 100 - prob_up_week

        n_signals = len(all_details)

        # スナップショット
        snapshot = {
            "日経平均": f"{float(nk_c.iloc[-1]):,.0f}" if not nk_c.empty else "N/A",
            "S&P500":  f"{sp_val:,.0f}"   if sp_val  is not None else "N/A",
            "SOX":     f"{sox_val:,.0f}"  if sox_val is not None else "N/A",
            "VIX":     f"{vix_val:.2f}"   if vix_val is not None else "N/A",
            "USD/JPY": f"{fx_val:.2f}"    if fx_val  is not None else "N/A",
            "原油WTI": f"{oil_val:.1f}"   if oil_val is not None else "N/A",
            "EWJ":     f"{ewj_val:.2f}"   if ewj_val is not None else "N/A",
            "米10年金利": f"{tnx_val:.3f}%" if tnx_val is not None else "N/A",
        }

        # カテゴリ別集計
        cat_scores: Dict[str, float] = {}
        for cat_name, rows in details_by_cat.items():
            if not rows:
                continue
            c_sum = sum(float(r["値"]) * float(r["重み"]) for r in rows)
            c_wt  = sum(float(r["重み"]) for r in rows)
            cat_scores[cat_name] = c_sum / c_wt if c_wt > 0 else 0.0

        save_prediction_score(prob_up_tomorrow, composite)
        return {
            "ok": True,
            "composite": composite,
            "prob_up_tomorrow":   prob_up_tomorrow,
            "prob_down_tomorrow": prob_down_tomorrow,
            "prob_up_week":       prob_up_week,
            "prob_down_week":     prob_down_week,
            "n_signals":    n_signals,
            "details":      all_details,
            "details_by_cat": details_by_cat,
            "cat_scores":   cat_scores,
            "snapshot":     snapshot,
            "updated_at":   datetime.now(JST).strftime("%Y-%m-%d %H:%M JST"),
        }

    except Exception as e:
        logger.error(f"compute_nikkei_prediction error: {e}", exc_info=True)
        return {"ok": False, "reason": f"計算エラー: {str(e)[:300]}"}


def render_prediction_gauge(prob_up: float) -> bytes:
    """
    予測確率ゲージ描画。
    日本語テキストはmatplotlibでは文字化けするため、
    数値（%）のみ描画し、ラベルは呼び出し側でHTMLで表示する。
    """
    import io as _io
    fig, ax = plt.subplots(figsize=(3.5, 2.0))
    colors_grad = ["#d1242f", "#ff8800", "#aaaaaa", "#4caf50", "#1a7f37"]
    thresholds = [0, 35, 45, 55, 65, 100]
    for i in range(len(colors_grad)):
        t_start = np.pi * (1 - thresholds[i+1]/100)
        t_end   = np.pi * (1 - thresholds[i]/100)
        t_range = np.linspace(t_start, t_end, 60)
        ax.plot(np.cos(t_range), np.sin(t_range), linewidth=14, color=colors_grad[i])
    needle_angle = np.pi * (1 - prob_up/100)
    ax.plot([0, 0.75*np.cos(needle_angle)], [0, 0.75*np.sin(needle_angle)],
            linewidth=2.5, color="black", zorder=10)
    ax.add_patch(plt.Circle((0, 0), 0.05, color="black", zorder=11))
    color = "#1a7f37" if prob_up > 55 else ("#d1242f" if prob_up < 45 else "#888888")
    # スコア数値のみ（ASCII）— 日本語は使わない
    ax.text(0, -0.22, f"{prob_up:.1f}%", fontsize=22, fontweight="bold",
            ha="center", va="top", color=color, fontfamily="DejaVu Sans")
    # 目盛りラベル（英語）
    ax.text(-1.05, -0.05, "0", fontsize=8, ha="center", color="#888", fontfamily="DejaVu Sans")
    ax.text( 1.05, -0.05, "100", fontsize=8, ha="center", color="#888", fontfamily="DejaVu Sans")
    ax.text( 0,    1.05,  "50", fontsize=8, ha="center", color="#888", fontfamily="DejaVu Sans")
    ax.set_xlim(-1.25, 1.25)
    ax.set_ylim(-0.45, 1.2)
    ax.set_aspect("equal")
    ax.axis("off")
    fig.patch.set_facecolor("white")
    plt.tight_layout(pad=0.1)
    buf = _io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def _draw_category_radar(cat_scores: Dict[str, float]) -> bytes:
    """カテゴリ別スコアのレーダーチャートをPNG bytes で返す"""
    import io as _io
    cats  = list(cat_scores.keys())
    vals  = [float(np.clip(v, -1, 1)) for v in cat_scores.values()]
    # -1〜+1 を 0〜1 に正規化して面積表示
    vals_norm = [(v + 1) / 2 for v in vals]
    n = len(cats)
    if n < 3:
        return b""

    angles = [i * 2 * np.pi / n for i in range(n)] + [0]
    vals_plot = vals_norm + [vals_norm[0]]

    fig, ax = plt.subplots(figsize=(5, 5), subplot_kw=dict(polar=True))
    ax.set_theta_offset(np.pi / 2)
    ax.set_theta_direction(-1)

    ax.plot(angles, vals_plot, linewidth=2, color="#1976d2")
    ax.fill(angles, vals_plot, alpha=0.22, color="#1976d2")

    # 基準線（中立=0.5）
    ax.plot(angles, [0.5] * (n + 1), linestyle="--", linewidth=0.8,
            color="gray", alpha=0.6)

    # ラベル（短縮形、mojibake回避のため英語に）
    short_labels = [
        "① Global", "② FX", "③ Futures",
        "④ Rates", "⑤ VIX", "⑥ Commodity",
        "⑦ ETF Flow", "⑧ Technical", "⑨ Sentiment",
    ]
    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(short_labels[:n], fontsize=8, fontfamily="DejaVu Sans")
    ax.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
    ax.set_yticklabels(["Bear", "", "Neut", "", "Bull"],
                       fontsize=7, color="gray", fontfamily="DejaVu Sans")
    ax.set_ylim(0, 1)
    ax.grid(True, alpha=0.3)
    fig.patch.set_facecolor("white")
    plt.tight_layout()
    buf = _io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def _cat_score_bar_html(score: float) -> str:
    """カテゴリスコアを横バーHTMLで表示（-1〜+1）"""
    pct  = int((score + 1) / 2 * 100)   # 0〜100%
    color = "#1a7f37" if score > 0.1 else ("#d1242f" if score < -0.1 else "#888")
    label = "強気" if score > 0.15 else ("弱気" if score < -0.15 else "中立")
    return f"""
    <div style="margin:3px 0;">
      <div style="display:flex;align-items:center;gap:8px;">
        <div style="flex:1;background:#eee;border-radius:4px;height:10px;overflow:hidden;">
          <div style="width:{pct}%;height:100%;background:{color};
               border-radius:4px;transition:width 0.3s;"></div>
        </div>
        <span style="font-size:11px;color:{color};font-weight:700;
              white-space:nowrap;min-width:28px;">{label}</span>
        <span style="font-size:10px;color:#999;min-width:38px;">{score:+.2f}</span>
      </div>
    </div>"""


def render_nikkei_prediction():
    """日経平均予測スコアセクション — 9カテゴリ完全版"""
    st.markdown('<a id="nikkei-pred"></a>', unsafe_allow_html=True)
    st.header(t("🔮 日経平均 方向性予測スコア", "🔮 Nikkei 225 Directional Forecast"))
    st.markdown(
        '<div style="background:linear-gradient(135deg,#e3f2fd,#f3e5f5);'
        'border-left:4px solid #1976d2;border-radius:6px;padding:10px 16px;'
        'font-size:13px;color:#333;margin-bottom:12px;">'
        + t('<strong>9カテゴリ・25シグナル</strong> の重み付き複合スコアで方向性確率を推定します。<br>'
            'グローバル市場・為替・先物・金利・VIX・コモディティ・ETFフロー・テクニカル・センチメント',
            '<strong>9 categories · 25 signals</strong> — weighted composite score for directional probability.<br>'
            'Global markets · FX · Futures · Rates · VIX · Commodities · ETF flows · Technical · Sentiment') +
        '</div>',
        unsafe_allow_html=True
    )

    with st.spinner("25シグナル取得・分析中..."):
        pred = compute_nikkei_prediction()

    if not pred.get("ok"):
        st.error(f"⚠️ 予測計算に失敗しました: {pred.get('reason', '不明')}")
        return

    prob_up_t    = pred["prob_up_tomorrow"]
    prob_down_t  = pred["prob_down_tomorrow"]
    prob_up_w    = pred["prob_up_week"]
    prob_down_w  = pred["prob_down_week"]
    composite    = pred["composite"]
    details      = pred["details"]
    details_cat  = pred["details_by_cat"]
    cat_scores   = pred["cat_scores"]
    snapshot     = pred["snapshot"]
    updated_at   = pred["updated_at"]
    n_signals    = pred["n_signals"]

    # ─── 現在値スナップショット ───────────────────
    snap_keys = list(snapshot.keys())
    snap_cols = st.columns(len(snap_keys))
    for col, k in zip(snap_cols, snap_keys):
        col.metric(k, snapshot[k])

    st.divider()

    # ─── ゲージ + レーダー + 総合シグナル ────────
    col_t, col_w, col_radar, col_conf = st.columns([1, 1, 1.2, 0.9])

    with col_t:
        color_t = "#1a7f37" if prob_up_t > 55 else ("#d1242f" if prob_up_t < 45 else "#888")
        st.markdown(
            '<div style="text-align:center;font-size:15px;font-weight:700;'
            'margin-bottom:4px;">📅 明日の予測</div>',
            unsafe_allow_html=True
        )
        st.image(render_prediction_gauge(prob_up_t), width="stretch")
        st.markdown(
            f'<div style="text-align:center;margin-top:2px;">'
            f'<div style="font-size:12px;color:#666;margin-bottom:3px;">Prob. Up</div>'
            f'<span style="color:{color_t};font-weight:900;font-size:22px;">'
            f'{prob_up_t:.1f}%</span>'
            f'<span style="color:#999;font-size:13px;"> / </span>'
            f'<span style="color:#d1242f;font-size:15px;">下降 {prob_down_t:.1f}%</span>'
            f'</div>',
            unsafe_allow_html=True
        )

    with col_w:
        color_w = "#1a7f37" if prob_up_w > 55 else ("#d1242f" if prob_up_w < 45 else "#888")
        st.markdown(
            '<div style="text-align:center;font-size:15px;font-weight:700;'
            'margin-bottom:4px;">📆 今週の予測</div>',
            unsafe_allow_html=True
        )
        st.image(render_prediction_gauge(prob_up_w), width="stretch")
        st.markdown(
            f'<div style="text-align:center;margin-top:2px;">'
            f'<div style="font-size:12px;color:#666;margin-bottom:3px;">Prob. Up</div>'
            f'<span style="color:{color_w};font-weight:900;font-size:22px;">'
            f'{prob_up_w:.1f}%</span>'
            f'<span style="color:#999;font-size:13px;"> / </span>'
            f'<span style="color:#d1242f;font-size:15px;">下降 {prob_down_w:.1f}%</span>'
            f'</div>',
            unsafe_allow_html=True
        )

    with col_radar:
        st.markdown(
            '<div style="text-align:center;font-size:14px;font-weight:700;'
            'margin-bottom:4px;">📡 カテゴリ別レーダー</div>',
            unsafe_allow_html=True
        )
        if cat_scores:
            radar_bytes = _draw_category_radar(cat_scores)
            if radar_bytes:
                st.image(radar_bytes, width="stretch")

    with col_conf:
        direction = "強気 📈" if composite > 0.1 else ("弱気 📉" if composite < -0.1 else "中立 ➡️")
        st.metric("方向性", direction)
        st.metric("総合スコア", f"{composite:+.3f}",
                  help="-1.0（強弱気）〜 +1.0（強強気）")
        st.metric("有効シグナル数", f"{n_signals}個")
        st.caption(f"更新: {updated_at}")

    st.divider()

    # ─── 免責事項 ─────────────────────────────────
    st.markdown(
        '<div style="background:#fff3cd;border:1px solid #ffc107;border-radius:8px;'
        'padding:8px 14px;font-size:12px;color:#856404;margin-bottom:8px;">'
        '⚠️ <strong>免責事項</strong>: 本予測は過去データの統計的傾向に基づく参考情報です。'
        '実際の株価は様々な要因により大きく異なります。投資判断は自己責任でお願いします。'
        '</div>',
        unsafe_allow_html=True
    )

    # ─── カテゴリ別スコアバー ─────────────────────
    st.markdown("#### 📊 カテゴリ別シグナル強度")
    cat_col1, cat_col2 = st.columns(2)
    cat_items = list(SIGNAL_CATEGORIES.items())
    half = (len(cat_items) + 1) // 2

    for col_idx, col in enumerate([cat_col1, cat_col2]):
        with col:
            for cat_name, cat_meta in cat_items[col_idx * half:(col_idx + 1) * half]:
                cat_s = cat_scores.get(cat_name, 0.0)
                color = cat_meta["color"]
                icon  = cat_meta["icon"]
                bar_html = _cat_score_bar_html(cat_s)
                st.markdown(
                    f'<div style="border:1px solid #e0e0e0;border-radius:8px;'
                    f'padding:8px 12px;margin-bottom:8px;'
                    f'border-left:3px solid {color};">'
                    f'<div style="font-size:13px;font-weight:700;color:{color};'
                    f'margin-bottom:4px;">{icon} {cat_name}</div>'
                    f'<div style="font-size:11px;color:#777;margin-bottom:5px;">'
                    f'{cat_meta["desc"]}</div>'
                    f'{bar_html}'
                    f'</div>',
                    unsafe_allow_html=True
                )

    # ─── シグナル詳細テーブル（カテゴリ別タブ）────
    st.markdown("#### 🔍 シグナル詳細")
    tab_labels = ["全シグナル"] + [
        f"{SIGNAL_CATEGORIES[k]['icon']} {k}"
        for k in SIGNAL_CATEGORIES if k in details_cat and details_cat[k]
    ]
    tabs = st.tabs(tab_labels)

    with tabs[0]:
        if details:
            df_all = pd.DataFrame(details)
            st.dataframe(
                df_all[["判定", "カテゴリ", "シグナル", "値", "重み", "生データ", "説明"]],
                width="stretch", hide_index=True,
            )

    tab_idx = 1
    for cat_name, cat_meta in SIGNAL_CATEGORIES.items():
        rows = details_cat.get(cat_name, [])
        if not rows:
            continue
        with tabs[tab_idx]:
            st.markdown(
                f'<div style="font-size:12px;color:#555;margin-bottom:8px;">'
                f'{cat_meta["desc"]}</div>',
                unsafe_allow_html=True
            )
            df_cat = pd.DataFrame(rows)
            st.dataframe(
                df_cat[["判定", "シグナル", "値", "重み", "生データ", "説明"]],
                width="stretch", hide_index=True,
            )
        tab_idx += 1

    # ─── 算出方法 ─────────────────────────────────
    with st.expander("ℹ️ 9カテゴリ・算出方法の詳細", expanded=False):
        st.markdown("""
### シグナルエンジン v2 — 9カテゴリ体系

| # | カテゴリ | 主要シグナル | 重み帯 | 根拠 |
|---|---------|------------|--------|------|
| ① | グローバル市場連動 | S&P500・NASDAQ・SOX 前日/5日 | 1.5〜3.0 | 日経はNY終値に翌朝高連動。SOX→半導体→電機株→日経の波及 |
| ② | 為替 USD/JPY | 前日変化・5日モメンタム・ボラ | 1.0〜2.5 | 円安=輸出企業EPS増・外国人フロー増 |
| ③ | 先物市場 | CME先物vs現物ギャップ・5日モメンタム | 1.5〜3.0 | 先物主導市場。ギャップ>0→翌朝窓開け上昇 |
| ④ | 金利 | 米10年前日/5日変化・日米金利差 | 1.0〜2.0 | 金利↑→ハイテクPER圧縮→日経↓ |
| ⑤ | VIX | 水準・前日変化・3日変化 | 1.5〜3.0 | VIX>25=Bear警戒、急低下=買いシグナル |
| ⑥ | コモディティ | 原油前日/5日・ゴールド前日 | 0.8〜1.0 | 原油↑→インフレ→金利↑→株↓ |
| ⑦ | 海外ETFフロー | EWJ価格・5日・出来高×価格 | 1.0〜2.0 | 外国人の日本株ETF需給を直接反映 |
| ⑧ | テクニカル | RSI14・MACD・BB%B・MA25/50/200乖離・5日モメンタム | 1.0〜1.5 | 逆張り・トレンド追随の複合判定 |
| ⑨ | センチメント | 曜日効果・月末月初・F&G Index | 0.3〜1.5 | 統計的アノマリーと市場心理 |

**確率変換:** シグモイド関数 `P = 1 / (1 + e^(-composite × k))`  
明日 k=2.8、今週 k=1.6（不確実性考慮で保守的）
        """)

    render_prediction_history()

    render_quant_analysis()







# ===========================
# ★ 米国株予測シグナルカテゴリ定義
# ===========================

# =====================================================
# ★ 米国株クオンツ拡張
# =====================================================

@st.cache_data(ttl=TTL_DAILY, show_spinner=False)
def run_backtest_us(symbol: str = "^GSPC", lookback_years: int = 3) -> Dict[str, Any]:
    """
    S&P500/NASDAQ/ダウを対象にバックテストを実行。
    米国固有のシグナル（セクター・DXY・Put/Call）を特徴量として使用。
    """
    try:
        end   = datetime.now(timezone.utc)
        start = end - timedelta(days=365 * lookback_years + 60)

        def _g(sym):
            try:
                df = yf.Ticker(sym).history(
                    start=start, end=end, interval="1d", auto_adjust=False)
                if df is None or df.empty:
                    return pd.Series(dtype=float)
                if df.index.tz is None:
                    df.index = df.index.tz_localize("UTC")
                return df.tz_convert(JST)["Close"].dropna()
            except Exception:
                return pd.Series(dtype=float)

        tgt  = _g(symbol)
        sp   = _g("^GSPC")
        nq   = _g("^IXIC")
        vix  = _g("^VIX")
        tnx  = _g("^TNX")
        xlk  = _g("XLK")   # テック
        xlf  = _g("XLF")   # 金融
        xle  = _g("XLE")   # エネルギー
        oil  = _g("CL=F")
        dxy  = _g("DX=F")
        spy  = _g("SPY")
        iwm  = _g("IWM")

        if len(tgt) < 100:
            return {"ok": False, "reason": "データ不足"}

        features = []
        labels   = []
        base = tgt if symbol != "^GSPC" else sp

        for i in range(50, len(base) - 1):
            date  = base.index[i]
            feats = {}

            def _feat(ser, days, scale, default=0.0):
                """直近のリターンを計算してtanhで正規化"""
                try:
                    idx = ser.index.get_indexer([date], method="nearest")[0]
                    if idx >= days:
                        ret = (float(ser.iloc[idx]) / float(ser.iloc[idx-days]) - 1) * 100
                        return float(np.tanh(ret / scale))
                except Exception:
                    pass
                return default

            def _level(ser, target_val, scale, default=0.0):
                """水準シグナル"""
                try:
                    idx = ser.index.get_indexer([date], method="nearest")[0]
                    return float(np.tanh(-(float(ser.iloc[idx]) - target_val) / scale))
                except Exception:
                    return default

            # S&P500 モメンタム（1日・5日・20日）
            feats["sp_ret1"]  = _feat(sp,  1,  1.5)
            feats["sp_ret5"]  = _feat(sp,  5,  4.0)
            feats["sp_ret20"] = _feat(sp, 20,  8.0)

            # NASDAQ モメンタム（1日）
            feats["nq_ret1"]  = _feat(nq,  1,  1.5)

            # VIX 水準・変化
            feats["vix_level"] = _level(vix, 20, 8)
            feats["vix_ret1"]  = _feat(vix,  1, 8,  0.0)
            feats["vix_ret3"]  = _feat(vix,  3, 12, 0.0)

            # 金利変化
            feats["tnx_ch1"] = _feat(tnx, 1, 0.1)
            feats["tnx_ch5"] = _feat(tnx, 5, 0.3)

            # セクター相対強度（XLK vs SPY）
            xlk_r5 = _feat(xlk, 5, 3.0)
            spy_r5 = _feat(spy, 5, 3.0)
            feats["xlk_rel"] = float(np.tanh((xlk_r5 - spy_r5) * 5))

            # XLF相対強度（金融強い=景気拡大=強気）
            xlf_r5 = _feat(xlf, 5, 3.0)
            feats["xlf_rel"] = float(np.tanh((xlf_r5 - spy_r5) * 5))

            # 原油（インフレ代替）
            feats["oil_ret1"] = _feat(oil, 1, 3.0)

            # DXY（ドル強い=リスクオフ）
            feats["dxy_ret5"] = _feat(dxy, 5, 2.0)

            # 小型 vs 大型（IWM vs SPY）リスクオン指標
            iwm_r5 = _feat(iwm, 5, 3.0)
            feats["iwm_rel"] = float(np.tanh((iwm_r5 - spy_r5) * 5))

            # テクニカル（S&P500）
            sp_slice = sp.iloc[max(0, i-25):i+1]
            if len(sp_slice) >= 15:
                feats["sp_rsi"] = float(np.tanh(
                    -(_calc_rsi(sp_slice, 14) - 50) / 50 * 1.5))
            else:
                feats["sp_rsi"] = 0.0

            if len(sp_slice) >= 22:
                feats["sp_bb"] = float(np.tanh(
                    -(_calc_bb_pct(sp_slice, 20) - 0.5) * 2))
            else:
                feats["sp_bb"] = 0.0

            # 翌日ラベル
            actual_up = int(base.iloc[i+1] > base.iloc[i])
            features.append(feats)
            labels.append(actual_up)

        if len(features) < 50:
            return {"ok": False, "reason": "特徴量データ不足"}

        feat_names = list(features[0].keys())
        X = np.array([[f.get(k, 0.0) for k in feat_names] for f in features])
        y = np.array(labels)

        # 単体シグナルのヒット率
        signal_stats = {}
        for j, fname in enumerate(feat_names):
            col = X[:, j]
            pred_up   = (col > 0).astype(int)
            pred_down = (col < 0).astype(int)
            n_up      = pred_up.sum()
            n_down    = pred_down.sum()
            hit_up    = float((pred_up   * y).sum()   / n_up)   if n_up   > 0 else 0.5
            hit_down  = float((pred_down * (1-y)).sum() / n_down) if n_down > 0 else 0.5
            hit_total = float(((pred_up * y).sum() + (pred_down * (1-y)).sum()) /
                               max(n_up + n_down, 1))
            signal_stats[fname] = {
                "hit_rate":  round(hit_total * 100, 1),
                "hit_up":    round(hit_up    * 100, 1),
                "hit_down":  round(hit_down  * 100, 1),
                "n_signals": int(n_up + n_down),
            }

        # 合成スコア
        composite_scores = X.mean(axis=1)
        pred_comp = (composite_scores > 0).astype(int)
        if SKLEARN_AVAILABLE:
            from sklearn.metrics import accuracy_score as _acc
            overall_hit = float(_acc(y, pred_comp))
        else:
            overall_hit = float((pred_comp == y).mean())

        # 仮想P&L
        base_arr = base.values
        returns = []
        for i in range(50, min(len(base_arr)-1, 50+len(composite_scores))):
            idx = i - 50
            if idx >= len(composite_scores):
                break
            daily_ret = base_arr[i+1] / base_arr[i] - 1
            returns.append(daily_ret if composite_scores[idx] > 0 else -daily_ret)

        returns = np.array(returns)
        sharpe  = float(returns.mean() / returns.std() * np.sqrt(252)) if returns.std() > 0 else 0.0
        cumret  = np.cumprod(1 + returns)
        max_dd  = float(((cumret - np.maximum.accumulate(cumret)) /
                         np.maximum.accumulate(cumret)).min() * 100) if len(cumret) > 0 else 0.0
        total_ret = float((cumret[-1] - 1) * 100) if len(cumret) > 0 else 0.0

        # ローリングヒット率
        window = 60
        dates_arr = base.index[50:]
        rolling_hits, rolling_dates = [], []
        for i in range(window, len(pred_comp)):
            rolling_hits.append(
                float((pred_comp[i-window:i] == y[i-window:i]).mean()) * 100)
            if i < len(dates_arr):
                rolling_dates.append(dates_arr[i])

        hit_df = pd.DataFrame({
            "date":     pd.to_datetime(rolling_dates),
            "hit_rate": rolling_hits,
        }) if rolling_dates else pd.DataFrame()

        return {
            "ok":              True,
            "symbol":          symbol,
            "overall_hit":     round(overall_hit * 100, 1),
            "sharpe":          round(sharpe, 3),
            "max_dd":          round(max_dd, 1),
            "total_ret":       round(total_ret, 1),
            "n_samples":       len(y),
            "up_rate":         round(float(y.mean()) * 100, 1),
            "signal_stats":    signal_stats,
            "feat_names":      feat_names,
            "hit_df":          hit_df,
            "X":               X,
            "y":               y,
            "composite_scores": composite_scores,
        }

    except Exception as e:
        logger.error(f"run_backtest_us error: {e}", exc_info=True)
        return {"ok": False, "reason": str(e)[:200]}


def optimize_weights_ml_us(bt_result: Dict) -> Dict[str, Any]:
    """米国株ML重み最適化（Walk-forward検証）"""
    if not bt_result.get("ok") or not SKLEARN_AVAILABLE:
        return {"ok": False, "reason": "データ不足またはscikit-learn未インストール"}
    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
        from sklearn.model_selection import TimeSeriesSplit
        from sklearn.metrics import accuracy_score

        X, y = bt_result["X"], bt_result["y"]
        feat_names = bt_result["feat_names"]
        if len(X) < 100:
            return {"ok": False, "reason": "サンプル不足"}

        scaler   = StandardScaler()
        X_scaled = scaler.fit_transform(X)
        tscv     = TimeSeriesSplit(n_splits=5)
        fold_scores, fold_details = [], []

        for fold, (tr, te) in enumerate(tscv.split(X_scaled)):
            lr = LogisticRegression(C=0.1, max_iter=500, random_state=42)
            lr.fit(X_scaled[tr], y[tr])
            sc = accuracy_score(y[te], lr.predict(X_scaled[te]))
            fold_scores.append(sc)
            fold_details.append({"fold": fold+1, "train_n": len(tr),
                                  "test_n": len(te), "accuracy": round(sc*100, 1)})

        final_lr = LogisticRegression(C=0.1, max_iter=500, random_state=42)
        final_lr.fit(X_scaled, y)
        coefs = final_lr.coef_[0]

        xgb_coefs = None
        if XGB_AVAILABLE:
            try:
                import xgboost as xgb
                xm = xgb.XGBClassifier(n_estimators=100, max_depth=3,
                                        learning_rate=0.05, random_state=42,
                                        eval_metric="logloss", verbosity=0)
                xm.fit(X_scaled, y)
                xgb_coefs = xm.feature_importances_
            except Exception:
                pass

        label_map = {
            "sp_ret1": "S&P500 1D", "sp_ret5": "S&P500 5D",
            "sp_ret20": "S&P500 20D", "nq_ret1": "NASDAQ 1D",
            "vix_level": "VIX Level", "vix_ret1": "VIX 1D Chg",
            "vix_ret3": "VIX 3D Chg", "tnx_ch1": "US10Y 1D",
            "tnx_ch5": "US10Y 5D", "xlk_rel": "XLK Rel Str",
            "xlf_rel": "XLF Rel Str", "oil_ret1": "Crude 1D",
            "dxy_ret5": "DXY 5D", "iwm_rel": "IWM Rel Str",
            "sp_rsi": "S&P500 RSI14", "sp_bb": "S&P500 BB%B",
        }
        rows = []
        for i, fname in enumerate(feat_names):
            row = {"Signal": label_map.get(fname, fname),
                   "LR Coef": round(float(coefs[i]), 4),
                   "Importance": round(abs(float(coefs[i])), 4),
                   "Direction": "Bullish" if coefs[i] > 0 else "Bearish"}
            if xgb_coefs is not None:
                row["XGB Importance"] = round(float(xgb_coefs[i]), 4)
            rows.append(row)

        importance_df = pd.DataFrame(rows).sort_values("Importance", ascending=False)
        latest_x = X_scaled[-1:]
        optimized_prob = float(final_lr.predict_proba(latest_x)[0][1] * 100)

        return {
            "ok":             True,
            "cv_mean":        round(float(np.mean(fold_scores)) * 100, 1),
            "cv_std":         round(float(np.std(fold_scores))  * 100, 1),
            "importance_df":  importance_df,
            "fold_details":   fold_details,
            "optimized_prob": optimized_prob,
            "has_xgb":        XGB_AVAILABLE and xgb_coefs is not None,
            "label_map":      label_map,
        }
    except Exception as e:
        logger.error(f"optimize_weights_ml_us error: {e}", exc_info=True)
        return {"ok": False, "reason": str(e)[:200]}


@st.cache_data(ttl=TTL_INTRADAY, show_spinner=False)
def compute_ensemble_us(target: str = "SP500") -> Dict[str, Any]:
    """
    米国株アンサンブル予測（4モデル）
    短期 / 中期 / トレンド / Fed政策
    """
    symbol_map = {"SP500": "^GSPC", "NASDAQ": "^NDX", "DOW": "^DJI"}
    sym = symbol_map.get(target, "^GSPC")

    try:
        end   = datetime.now(timezone.utc)
        start = end - timedelta(days=420)

        def _h(s):
            try:
                df = yf.Ticker(s).history(
                    start=start, end=end, interval="1d", auto_adjust=False)
                if df is None or df.empty:
                    return pd.Series(dtype=float)
                if df.index.tz is None:
                    df.index = df.index.tz_localize("UTC")
                return df.tz_convert(JST)["Close"].dropna()
            except Exception:
                return pd.Series(dtype=float)

        main = _h(sym)
        sp   = _h("^GSPC")
        nq   = _h("^IXIC")
        vix  = _h("^VIX")
        tnx  = _h("^TNX")
        xlk  = _h("XLK")
        xlf  = _h("XLF")
        xle  = _h("XLE")
        spy  = _h("SPY")
        iwm  = _h("IWM")
        oil  = _h("CL=F")
        dxy  = _h("DX=F")

        if len(main) < 30:
            return {"ok": False, "reason": "データ不足"}

        def _r(ser, n):
            if len(ser) >= n+1:
                return (float(ser.iloc[-1]) / float(ser.iloc[-n]) - 1) * 100
            return 0.0

        def _rel(s1, s2, n=5):
            return _r(s1, n) - _r(s2, n)

        # ── 短期モデル（40%）: モメンタム・VIX・先物 ──────
        short = {}
        if len(sp) >= 2:
            short["sp_ret1"]  = (np.tanh(_r(sp, 1) / 1.5), 3.0)
        if len(nq) >= 2:
            short["nq_ret1"]  = (np.tanh(_r(nq, 1) / 1.5), 2.0)
        if len(vix) >= 4:
            short["vix_lv"]   = (np.tanh(-(float(vix.iloc[-1]) - 20) / 8), 2.5)
            short["vix_ch1"]  = (np.tanh(-_r(vix, 1) / 8), 2.0)
        # 先物乖離
        fut_map = {"SP500": "ES=F", "NASDAQ": "NQ=F", "DOW": "YM=F"}
        fut = _h(fut_map.get(target, "ES=F"))
        if len(fut) >= 2 and len(sp) >= 2:
            gap = _r(fut, 1) - _r(sp, 1)
            short["fut_gap"] = (np.tanh(gap / 1.5), 2.5)

        # ── 中期モデル（30%）: マクロ・セクター・テクニカル ──
        mid = {}
        if len(tnx) >= 6:
            tnx_ch5 = float(tnx.iloc[-1]) - float(tnx.iloc[-6])
            mid["tnx_ch5"] = (np.tanh(-tnx_ch5 / 0.25), 2.0)
        if len(sp) >= 22:
            mid["sp_mom20"] = (np.tanh(_r(sp, 20) / 8), 2.0)
        if len(xlk) >= 6 and len(spy) >= 6:
            mid["xlk_rel"]  = (np.tanh(_rel(xlk, spy) / 3), 1.5)
        if len(xlf) >= 6 and len(spy) >= 6:
            mid["xlf_rel"]  = (np.tanh(_rel(xlf, spy) / 3), 1.5)
        if len(iwm) >= 6 and len(spy) >= 6:
            mid["iwm_rel"]  = (np.tanh(_rel(iwm, spy) / 3), 1.5)
        if len(sp) >= 26:
            macd_v, macd_s = _calc_macd(sp)
            sp_p = float(sp.iloc[-1])
            mid["macd"] = (np.tanh((macd_v - macd_s) / (sp_p * 0.005 or 1)), 1.5)
        if len(sp) >= 15:
            mid["rsi14"] = (np.tanh(-(_calc_rsi(sp, 14) - 50) / 50 * 1.5), 1.5)

        # ── トレンドモデル（20%）: MA・長期傾向 ─────────────
        trend = {}
        for w, wt in [(50, 1.5), (200, 2.0)]:
            if len(sp) >= w:
                ma   = float(sp.rolling(w).mean().iloc[-1])
                diff = (float(sp.iloc[-1]) / ma - 1) * 100
                sig  = np.tanh(-diff/10) if abs(diff) > 10 else np.tanh(diff/5)
                trend[f"ma{w}"] = (sig, wt)
        if len(sp) >= 130:
            ma125 = float(sp.rolling(125).mean().iloc[-1])
            diff  = (float(sp.iloc[-1]) / ma125 - 1) * 100
            trend["ma125"] = (np.tanh(diff * 5), 2.0)
        if len(dxy) >= 6:
            trend["dxy"] = (np.tanh(-_r(dxy, 5) / 2), 1.0)

        # ── Fed政策モデル（10%）: 金利水準・イールドカーブ ──
        fed = {}
        if len(tnx) >= 20:
            tnx_v   = float(tnx.iloc[-1])
            tnx_ma20 = float(tnx.rolling(20).mean().iloc[-1])
            fed["tnx_level"]  = (np.tanh(-(tnx_v - 3.5) / 1.0), 2.0)
            fed["tnx_vs_ma"]  = (np.tanh(-(tnx_v / tnx_ma20 - 1) * 100 / 3), 1.5)
        irx = _h("^IRX")
        if len(irx) >= 2 and len(tnx) >= 2:
            spread = float(tnx.iloc[-1]) - float(irx.iloc[-1]) / 10
            fed["yield_curve"] = (np.tanh(spread / 1.0), 2.0)

        # アンサンブル合成
        w_short = 0.40; w_mid = 0.30; w_trend = 0.20; w_fed = 0.10

        def _ws(sigs):
            if not sigs:
                return 0.0
            s = sum(float(np.clip(v, -1, 1)) * w for _, (v, w) in sigs.items())
            tw = sum(w for _, (_, w) in sigs.items())
            return float(s / tw) if tw > 0 else 0.0

        sc_short  = _ws(short)
        sc_mid    = _ws(mid)
        sc_trend  = _ws(trend)
        sc_fed    = _ws(fed)
        sc_ens    = sc_short*w_short + sc_mid*w_mid + sc_trend*w_trend + sc_fed*w_fed

        def _prob(sc, k=2.5):
            return float(1 / (1 + np.exp(-sc * k)) * 100)

        p_short  = _prob(sc_short,  2.8)
        p_mid    = _prob(sc_mid,    1.6)
        p_trend  = _prob(sc_trend,  1.2)
        p_fed    = _prob(sc_fed,    1.0)
        p_ens    = _prob(sc_ens,    2.5)

        votes_up   = sum([p_short > 50, p_mid > 50, p_trend > 50, p_fed > 50])
        consensus  = "強気 📈" if votes_up >= 3 else ("弱気 📉" if votes_up <= 1 else "中立 ➡️")
        confidence = "高" if votes_up == 4 or votes_up == 0 else (
                     "中" if votes_up == 3 or votes_up == 1 else "低")

        return {
            "ok":            True,
            "target":        target,
            "prob_ensemble": round(p_ens, 1),
            "ensemble_score": round(sc_ens, 4),
            "votes_up":      votes_up,
            "consensus":     consensus,
            "confidence":    confidence,
            "models": {
                "短期（1日）":    {"score": round(sc_short, 4), "prob": round(p_short, 1),
                                   "weight": w_short, "n": len(short),
                                   "desc": "S&P500/NASDAQ・VIX・先物乖離",
                                   "color": "#1976d2"},
                "中期（1週）":    {"score": round(sc_mid, 4),   "prob": round(p_mid, 1),
                                   "weight": w_mid,   "n": len(mid),
                                   "desc": "セクター・テクニカル・20日モメンタム",
                                   "color": "#2e7d32"},
                "トレンド":       {"score": round(sc_trend, 4), "prob": round(p_trend, 1),
                                   "weight": w_trend, "n": len(trend),
                                   "desc": "MA50/200・長期傾向・DXY",
                                   "color": "#6a1b9a"},
                "Fed政策":        {"score": round(sc_fed, 4),   "prob": round(p_fed, 1),
                                   "weight": w_fed,   "n": len(fed),
                                   "desc": "金利水準・イールドカーブ",
                                   "color": "#e65100"},
            },
            "updated_at": datetime.now(JST).strftime("%Y-%m-%d %H:%M JST"),
        }

    except Exception as e:
        logger.error(f"compute_ensemble_us error: {e}", exc_info=True)
        return {"ok": False, "reason": str(e)[:200]}


def _render_bt_kpi(bt: Dict):
    """バックテストKPIカード（共通）"""
    k1, k2, k3, k4, k5 = st.columns(5)
    hc = "#1a7f37" if bt["overall_hit"] > 55 else ("#d1242f" if bt["overall_hit"] < 48 else "#888")
    sc = "#1a7f37" if bt["sharpe"] > 0.5 else ("#d1242f" if bt["sharpe"] < 0 else "#888")
    dc = "#d1242f" if bt["max_dd"] < -20 else ("#ff8800" if bt["max_dd"] < -10 else "#888")
    rc = "#1a7f37" if bt["total_ret"] > 0 else "#d1242f"
    for col, val, label, color, fmt in [
        (k1, bt["overall_hit"],  "ヒット率",       hc, f"{bt['overall_hit']}%"),
        (k2, bt["sharpe"],       "シャープレシオ", sc, f"{bt['sharpe']:.2f}"),
        (k3, bt["max_dd"],       "最大DD",         dc, f"{bt['max_dd']:.1f}%"),
        (k4, bt["total_ret"],    "仮想リターン",   rc, f"{bt['total_ret']:+.1f}%"),
        (k5, bt["n_samples"],    "サンプル数",     "#333", f"{bt['n_samples']}日"),
    ]:
        col.markdown(
            f'<div style="text-align:center;padding:10px;background:#f8f9fa;border-radius:8px;">'
            f'<div style="font-size:11px;color:#666;">{label}</div>'
            f'<div style="font-size:26px;font-weight:900;color:{color};">{fmt}</div></div>',
            unsafe_allow_html=True)


def _render_rolling_hit_chart(hit_df: pd.DataFrame, overall_hit: float):
    """60日ローリングヒット率チャート（共通）"""
    if hit_df.empty:
        return
    import matplotlib.dates as mdates
    fig, ax = plt.subplots(figsize=(12, 3.5))
    ax.plot(hit_df["date"], hit_df["hit_rate"], color="#1976d2", linewidth=1.8)
    ax.axhline(50, color="gray", linestyle="--", alpha=0.6)
    ax.axhline(overall_hit, color="#e91e63", linestyle=":", linewidth=1.5,
               label=f"平均 {overall_hit}%")
    ax.fill_between(hit_df["date"], hit_df["hit_rate"], 50,
                    where=(hit_df["hit_rate"] >= 50), alpha=0.15, color="#1a7f37")
    ax.fill_between(hit_df["date"], hit_df["hit_rate"], 50,
                    where=(hit_df["hit_rate"] < 50), alpha=0.15, color="#d1242f")
    ax.set_ylabel("Hit Rate (%)", fontsize=10)
    ax.set_ylim(30, 75)
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.25)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y/%m"))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    plt.xticks(rotation=45)
    plt.tight_layout()
    st.pyplot(fig, clear_figure=True)


def _render_importance_chart(importance_df: pd.DataFrame):
    """特徴量重要度チャート（共通）"""
    if importance_df is None or importance_df.empty:
        return
    imp = importance_df.sort_values("LR Coef")
    fig, ax = plt.subplots(figsize=(10, max(3, len(imp)*0.45)))
    colors = ["#1a7f37" if v > 0 else "#d1242f" for v in imp["LR Coef"]]
    labels = imp.get("Label", imp.get("ラベル", imp["シグナル"]))
    ax.barh(labels, imp["LR Coef"], color=colors, height=0.6)
    ax.axvline(0, color="black", linewidth=0.8)
    ax.set_xlabel("LR Coefficient", fontsize=9)
    ax.grid(True, axis="x", alpha=0.3)
    plt.tight_layout()
    st.pyplot(fig, clear_figure=True)


def _render_ensemble_result(ens: Dict):
    """アンサンブル結果表示（共通）"""
    votes = ens["votes_up"]
    total_models = len(ens["models"])
    prob_ens = ens["prob_ensemble"]
    prob_color = "#1a7f37" if prob_ens > 55 else ("#d1242f" if prob_ens < 45 else "#888")
    border_color = "#1a7f37" if votes >= total_models // 2 + 1 else "#d1242f"

    st.markdown(
        f'<div style="background:#f8f9fa;border:2px solid {border_color};'
        f'border-radius:12px;padding:16px 20px;margin-bottom:16px;">'
        f'<div style="display:flex;align-items:center;gap:24px;flex-wrap:wrap;">'
        f'<div style="text-align:center;">'
        f'<div style="font-size:11px;color:#666;">アンサンブルProb. Up</div>'
        f'<div style="font-size:36px;font-weight:900;color:{prob_color};">{prob_ens:.1f}%</div>'
        f'</div>'
        f'<div style="text-align:center;">'
        f'<div style="font-size:11px;color:#666;">総合判定</div>'
        f'<div style="font-size:22px;font-weight:700;">{ens["consensus"]}</div>'
        f'</div>'
        f'<div style="text-align:center;">'
        f'<div style="font-size:11px;color:#666;">多数決</div>'
        f'<div style="font-size:20px;font-weight:700;">{votes}/{total_models}モデル強気</div>'
        f'</div>'
        f'<div style="text-align:center;">'
        f'<div style="font-size:11px;color:#666;">信頼度</div>'
        f'<div style="font-size:20px;font-weight:700;">{"🔥高" if ens["confidence"] == "高" else "📊中" if ens["confidence"] == "中" else "⚠️低"}</div>'
        f'</div>'
        f'</div></div>',
        unsafe_allow_html=True)

    # モデル別カード
    cols = st.columns(len(ens["models"]))
    for col, (mname, mdata) in zip(cols, ens["models"].items()):
        p = mdata["prob"]
        pc = "#1a7f37" if p > 55 else ("#d1242f" if p < 45 else "#888")
        with col:
            st.markdown(
                f'<div style="border:2px solid {mdata["color"]};border-radius:10px;'
                f'padding:10px;text-align:center;">'
                f'<div style="font-size:12px;font-weight:700;color:{mdata["color"]};">{mname}</div>'
                f'<div style="font-size:10px;color:#888;margin:2px 0;">{mdata["desc"]}</div>'
                f'<div style="font-size:26px;font-weight:900;color:{pc};">{p:.1f}%</div>'
                f'<div style="font-size:10px;color:#999;">重み:{mdata["weight"]:.0%} / {mdata["n"]}シグナル</div>'
                f'</div>',
                unsafe_allow_html=True)

    st.image(render_prediction_gauge(prob_ens), width=280)
    st.caption(f"更新: {ens['updated_at']}")


def render_us_quant_analysis(target: str = "SP500"):
    """米国株クオンツ分析セクション（ボタン式遅延読み込み）"""
    st.markdown("---")
    st.markdown(
        f"""<div style="background:linear-gradient(135deg,#e3f2fd,#fce4ec);
        border-left:4px solid #1976d2;border-radius:6px;padding:10px 16px;
        font-size:13px;color:#333;margin-bottom:12px;">
        <strong>🔬 米国株クオンツ分析</strong>:
        バックテスト・ML最適化・4モデルアンサンブルで予測精度を検証します
        </div>""",
        unsafe_allow_html=True)

    key = f"show_quant_us_{target}"
    if key not in st.session_state:
        st.session_state[key] = False

    if not st.session_state[key]:
        if st.button(f"🔬 クオンツ分析を実行（{target}）",
                     key=f"run_quant_us_{target}", type="secondary",
                     width="stretch"):
            st.session_state[key] = True
            st.rerun()
        st.caption("※ バックテスト・ML最適化・アンサンブルを実行します（初回30〜60秒）")
        return

    tab_bt, tab_ml, tab_ens = st.tabs(
        ["① バックテスト", "② ML最適化", "③ アンサンブル（4モデル）"])

    sym_map = {"SP500": "^GSPC", "NASDAQ": "^NDX", "DOW": "^DJI"}
    sym = sym_map.get(target, "^GSPC")

    with tab_bt:
        st.markdown(f"#### 📊 バックテスト（{target}・過去3年）")
        st.caption("米国固有シグナル（セクター・DXY・Put/Call）を含む16特徴量で検証")
        with st.spinner("バックテスト実行中..."):
            bt = run_backtest_us(sym, lookback_years=3)
        if not bt.get("ok"):
            st.error(f"バックテスト失敗: {bt.get('reason')}")
        else:
            _render_bt_kpi(bt)
            st.caption("※ 仮想P&L: スコア>0でロング、<0でショート（手数料なし）")
            _render_rolling_hit_chart(bt.get("hit_df", pd.DataFrame()), bt["overall_hit"])
            with st.expander("🔍 シグナル別ヒット率", expanded=False):
                label_map = {
                    "sp_ret1": "S&P500 1D", "sp_ret5": "S&P500 5D",
                    "sp_ret20": "S&P500 20D", "nq_ret1": "NASDAQ 1D",
                    "vix_level": "VIX Level", "vix_ret1": "VIX 1D",
                    "vix_ret3": "VIX 3D", "tnx_ch1": "US10Y 1D",
                    "tnx_ch5": "US10Y 5D", "xlk_rel": "XLK Rel Str",
                    "xlf_rel": "XLF Rel Str", "oil_ret1": "Crude 1D",
                    "dxy_ret5": "DXY 5D", "iwm_rel": "IWM Rel Str",
                    "sp_rsi": "S&P500 RSI", "sp_bb": "S&P500 BB%B",
                }
                rows = [{"Signal": label_map.get(k, k),
                         "Hit Rate": f"{v['hit_rate']}%",
                         "Up HIT": f"{v['hit_up']}%",
                         "Down HIT": f"{v['hit_down']}%",
                         "Count": v["n_signals"]}
                        for k, v in sorted(bt.get("signal_stats", {}).items(),
                                           key=lambda x: x[1]["hit_rate"], reverse=True)]
                if rows:
                    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

    with tab_ml:
        st.markdown("#### 🤖 ML Weight Optimization")
        st.caption("Logistic Regression / Walk-forward 5-fold validation")
        if not SKLEARN_AVAILABLE:
            st.warning("requirements.txtに `scikit-learn` を追加してください")
        else:
            with st.spinner("ML最適化実行中..."):
                bt2 = run_backtest_us(sym, lookback_years=3)
                if not bt2.get("ok"):
                    st.error("バックテストデータが必要です")
                else:
                    ml = optimize_weights_ml_us(bt2)
            if ml.get("ok"):
                cv_color = "#1a7f37" if ml["cv_mean"] > 54 else ("#d1242f" if ml["cv_mean"] < 50 else "#888")
                c1, c2, c3 = st.columns(3)
                c1.markdown(
                    f'<div style="text-align:center;padding:12px;background:#f8f9fa;border-radius:8px;">'
                    f'<div style="font-size:11px;color:#666;">CV Accuracy</div>'
                    f'<div style="font-size:26px;font-weight:900;color:{cv_color};">{ml["cv_mean"]}%</div>'
                    f'<div style="font-size:11px;color:#999;">±{ml["cv_std"]}%</div></div>',
                    unsafe_allow_html=True)
                c2.markdown(
                    f'<div style="text-align:center;padding:12px;background:#f8f9fa;border-radius:8px;">'
                    f'<div style="font-size:11px;color:#666;">ML Optimized</div>'
                    f'<div style="font-size:26px;font-weight:900;color:#1976d2;">{ml["optimized_prob"]:.1f}%</div>'
                    f'<div style="font-size:11px;color:#999;">Prob. Up</div></div>',
                    unsafe_allow_html=True)
                c3.markdown(
                    f'<div style="text-align:center;padding:12px;background:#f8f9fa;border-radius:8px;">'
                    f'<div style="font-size:11px;color:#666;">モデル</div>'
                    f'<div style="font-size:16px;font-weight:700;">Logistic{"+ XGB" if ml["has_xgb"] else ""}</div>'
                    f'<div style="font-size:11px;color:#999;">Walk-forward 5-fold</div></div>',
                    unsafe_allow_html=True)

                imp_df = ml.get("importance_df")
                if imp_df is not None and not imp_df.empty:
                    st.markdown("**📊 Signal Importance**")
                    lm = ml.get("label_map", {})
                    imp_df["Label"] = imp_df["シグナル"].map(lambda x: lm.get(x, x))
                    _render_importance_chart(imp_df)
            else:
                st.error(f"ML最適化失敗: {ml.get('reason')}")

    with tab_ens:
        st.markdown("#### 🎯 アンサンブル予測（4モデル）")
        st.caption("短期/中期/トレンド/Fed政策 の4モデル多数決")
        with st.spinner("4モデル計算中..."):
            ens = compute_ensemble_us(target)
        if not ens.get("ok"):
            st.error(f"アンサンブル計算失敗: {ens.get('reason')}")
        else:
            _render_ensemble_result(ens)


US_SIGNAL_CATEGORIES = {
    "① マクロ・景気": {
        "icon": "🏦", "color": "#1565c0",
        "desc": "Treasury利回りカーブ・景気先行指標。逆イールド深化=景気後退リスク。",
    },
    "② 金利・FED政策": {
        "icon": "📈", "color": "#e65100",
        "desc": "米10年・2年金利変化。利上げ停止・利下げ期待=強気。急騰=弱気。",
    },
    "③ VIX恐怖指数": {
        "icon": "😱", "color": "#b71c1c",
        "desc": "VIX<15=Greed, VIX>25=Fear。急低下=リスクオン回帰のシグナル。",
    },
    "④ セクターローテ": {
        "icon": "🔄", "color": "#6a1b9a",
        "desc": "XLK(テック)・XLF(金融)・XLE(エネルギー)の相対強度で市場の方向性を確認。",
    },
    "⑤ モメンタム": {
        "icon": "🚀", "color": "#2e7d32",
        "desc": "S&P500・NASDAQの短期・中期モメンタム。トレンドフォロー系シグナル。",
    },
    "⑥ 市場の幅": {
        "icon": "📊", "color": "#00695c",
        "desc": "上昇銘柄数÷下落銘柄数。指数上昇でも幅が狭いと天井サイン。",
    },
    "⑦ コモディティ・ドル": {
        "icon": "🛢️", "color": "#795548",
        "desc": "原油↑→インフレ懸念。ゴールド↑→リスクオフ。DXY↑→新興国・資源株↓。",
    },
    "⑧ テクニカル分析": {
        "icon": "📐", "color": "#4527a0",
        "desc": "RSI・MACD・ボリンジャーバンド・MA乖離。S&P500ベースで算出。",
    },
    "⑨ センチメント": {
        "icon": "🧠", "color": "#ad1457",
        "desc": "Fear&Greed Index・Put/Call比率・VXX出来高。市場心理の総合指標。",
    },
}


@st.cache_data(ttl=TTL_DAILY, show_spinner=False)
def compute_us_prediction(target: str = "SP500") -> Dict[str, Any]:
    """
    米国株（S&P500 / NASDAQ100 / ダウ）の翌日・今週方向性予測。
    target: "SP500" | "NASDAQ" | "DOW"
    """
    target_map = {
        "SP500":  {"symbol": "^GSPC", "name": "S&P500",    "futures": "ES=F"},
        "NASDAQ": {"symbol": "^NDX",  "name": "NASDAQ100", "futures": "NQ=F"},
        "DOW":    {"symbol": "^DJI",  "name": "ダウ平均",  "futures": "YM=F"},
    }
    tgt = target_map.get(target, target_map["SP500"])

    details_by_cat: Dict[str, List[Dict]] = {k: [] for k in US_SIGNAL_CATEGORIES}
    all_details: List[Dict] = []
    score_sum  = 0.0
    weight_sum = 0.0

    def add_signal(category: str, name: str, value: float,
                   weight: float, desc: str, raw: str = ""):
        nonlocal score_sum, weight_sum
        clamped = float(np.clip(value, -1.0, 1.0))
        score_sum  += clamped * weight
        weight_sum += weight
        icon = "🟢" if clamped > 0.15 else ("🔴" if clamped < -0.15 else "⚪")
        row = {
            "判定": icon, "カテゴリ": category, "シグナル": name,
            "値": f"{clamped:+.3f}", "重み": f"{weight:.1f}",
            "生データ": raw, "説明": desc,
        }
        details_by_cat[category].append(row)
        all_details.append(row)

    try:
        end   = datetime.now(timezone.utc)
        start = end - timedelta(days=420)

        def _h(sym):
            try:
                df = yf.Ticker(sym).history(
                    start=start, end=end, interval="1d", auto_adjust=False)
                if df is None or df.empty:
                    return pd.DataFrame()
                if df.index.tz is None:
                    df.index = df.index.tz_localize("UTC")
                return df.tz_convert(JST)
            except Exception as e:
                logger.debug(f"us_pred _h({sym}): {e}")
                return pd.DataFrame()

        # ── ターゲット取得 ──────────────────────────────────
        main_df = _h(tgt["symbol"])
        if main_df.empty or len(main_df) < 30:
            return {"ok": False, "reason": f"{tgt['name']}データ不足"}
        main_c = main_df["Close"].dropna()
        main_val = float(main_c.iloc[-1])

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # ① マクロ・景気：Yield Curve（10Y-2Y）
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        cat = "① マクロ・景気"
        tnx = _h("^TNX"); tyx = _h("^TYX"); fvx = _h("^FVX")
        tnx_val = tyx_val = fvx_val = None

        if not tnx.empty and len(tnx) >= 5:
            tnx_c   = tnx["Close"].dropna()
            tnx_val = float(tnx_c.iloc[-1])
            # 2年金利代替（^IRX = 13週T-Bill）
            irx_df = _h("^IRX")
            if not irx_df.empty:
                irx_c = irx_df["Close"].dropna()
                irx_val = float(irx_c.iloc[-1])
                # ^IRX・^TNX はともに同単位（例: 5.25 = 5.25%）なので割り算不要
                spread_2_10 = tnx_val - irx_val
                add_signal(cat, "イールドカーブ(10Y-2Y近似)",
                           np.tanh(spread_2_10 / 1.0), 2.0,
                           "逆イールド（spread<0）=景気後退懸念→弱気",
                           f"10Y:{tnx_val:.3f}% 短期:{irx_val:.3f}% spread:{spread_2_10:+.3f}%")

            # 10年金利の5日変化
            if len(tnx_c) >= 6:
                tnx_ch5 = float(tnx_c.iloc[-1]) - float(tnx_c.iloc[-6])
                add_signal(cat, "米10年金利5日変化",
                           np.tanh(-tnx_ch5 / 0.3), 1.5,
                           "金利急上昇=バリュエーション圧迫→弱気",
                           f"{tnx_ch5:+.3f}%pt")

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # ② 金利・FED政策
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        cat = "② 金利・FED政策"
        if not tnx.empty and len(tnx) >= 20:
            tnx_c   = tnx["Close"].dropna()
            tnx_val = float(tnx_c.iloc[-1])
            tnx_ch1 = float(tnx_c.iloc[-1]) - float(tnx_c.iloc[-2])
            tnx_ma20 = float(tnx_c.rolling(20).mean().iloc[-1])

            add_signal(cat, "米10年金利 前日変化",
                       np.tanh(-tnx_ch1 / 0.1), 2.5,
                       "金利↑→ハイテクPER圧縮", f"{tnx_ch1:+.3f}%pt")
            # 金利水準（4%超=高め=弱気、3%以下=緩和的=強気）
            add_signal(cat, "米10年金利 水準",
                       np.tanh(-(tnx_val - 3.5) / 1.0), 1.5,
                       "4%超=株式バリュエーション圧迫",
                       f"{tnx_val:.3f}%")
            # MA20との比較（上昇トレンド中か）
            tnx_vs_ma = (tnx_val / tnx_ma20 - 1) * 100
            add_signal(cat, "金利 vs MA20",
                       np.tanh(-tnx_vs_ma / 3), 1.0,
                       "金利がMA20上方乖離=株に逆風",
                       f"{tnx_vs_ma:+.2f}%")

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # ③ VIX恐怖指数
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        cat = "③ VIX恐怖指数"
        vix_df = _h("^VIX")
        vix_val = None
        if not vix_df.empty and len(vix_df) >= 10:
            vix_c   = vix_df["Close"].dropna()
            vix_val = float(vix_c.iloc[-1])
            vix_ch1 = (vix_c.iloc[-1] / vix_c.iloc[-2] - 1) * 100
            vix_ch3 = (vix_c.iloc[-1] / vix_c.iloc[-4] - 1) * 100

            add_signal(cat, "VIX 水準",
                       np.tanh(-(vix_val - 20) / 8), 3.0,
                       "VIX<15=Bull, VIX>25=Bear",
                       f"VIX={vix_val:.2f}")
            add_signal(cat, "VIX 前日変化",
                       np.tanh(-vix_ch1 / 8), 2.0,
                       "VIX急低下=リスクオン", f"{vix_ch1:+.2f}%")
            add_signal(cat, "VIX 3日変化",
                       np.tanh(-vix_ch3 / 12), 1.5,
                       "短期トレンド確認", f"{vix_ch3:+.2f}%")

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # ④ セクターローテーション
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        cat = "④ セクターローテ"
        sp_df = _h("^GSPC")
        sector_signals = []
        for sym, name, is_growth in [
            ("XLK", "テック",     True),
            ("XLF", "金融",       True),
            ("XLE", "エネルギー", False),
            ("XLV", "ヘルスケア", True),
            ("XLU", "公益",       False),
        ]:
            sec_df = _h(sym)
            if sec_df.empty or len(sec_df) < 6:
                continue
            sec_c  = sec_df["Close"].dropna()
            sec_r5 = (sec_c.iloc[-1] / sec_c.iloc[-6] - 1) * 100
            # SP500との相対強度
            if not sp_df.empty and len(sp_df) >= 6:
                sp_c  = sp_df["Close"].dropna()
                sp_r5 = (sp_c.iloc[-1] / sp_c.iloc[-6] - 1) * 100
                rel   = sec_r5 - sp_r5
                # グロースセクター強い=強気、ディフェンシブ強い=弱気
                sign  = 1.0 if is_growth else -1.0
                add_signal(cat, f"{name} 相対強度",
                           np.tanh(sign * rel / 3), 0.8,
                           f"{name}がS&P500をアウトパフォーム",
                           f"{sec_r5:+.2f}% vs SP500:{sp_r5:+.2f}%")

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # ⑤ モメンタム
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        cat = "⑤ モメンタム"
        # S&P500
        if not sp_df.empty and len(sp_df) >= 25:
            sp_c   = sp_df["Close"].dropna()
            sp_r1  = (sp_c.iloc[-1] / sp_c.iloc[-2]  - 1) * 100
            sp_r5  = (sp_c.iloc[-1] / sp_c.iloc[-6]  - 1) * 100
            sp_r20 = (sp_c.iloc[-1] / sp_c.iloc[-21] - 1) * 100
            add_signal(cat, "S&P500 前日騰落",
                       np.tanh(sp_r1 / 1.5), 2.5,
                       "前日の勢いが翌日に持続しやすい", f"{sp_r1:+.2f}%")
            add_signal(cat, "S&P500 5日モメンタム",
                       np.tanh(sp_r5 / 4.0), 2.0,
                       "短期トレンド方向性", f"{sp_r5:+.2f}%")
            add_signal(cat, "S&P500 20日モメンタム",
                       np.tanh(sp_r20 / 8.0), 1.5,
                       "中期トレンド方向性", f"{sp_r20:+.2f}%")

        # NASDAQ（テック代表）
        nq_df = _h("^IXIC")
        if not nq_df.empty and len(nq_df) >= 3:
            nq_c  = nq_df["Close"].dropna()
            nq_r1 = (nq_c.iloc[-1] / nq_c.iloc[-2] - 1) * 100
            add_signal(cat, "NASDAQ 前日騰落",
                       np.tanh(nq_r1 / 1.5), 2.0,
                       "ハイテク株の先行指標", f"{nq_r1:+.2f}%")

        # 先物乖離（ES=F vs ^GSPC）
        fut_df = _h(tgt["futures"])
        if not fut_df.empty and not sp_df.empty and len(fut_df) >= 2:
            fut_c   = fut_df["Close"].dropna()
            fut_r1  = (fut_c.iloc[-1] / fut_c.iloc[-2] - 1) * 100
            sp_r1_f = (sp_df["Close"].dropna().iloc[-1] /
                       sp_df["Close"].dropna().iloc[-2] - 1) * 100
            gap = fut_r1 - sp_r1_f
            add_signal(cat, f"先物 vs 現物 乖離({tgt['futures']})",
                       np.tanh(gap / 1.5), 2.5,
                       "先物が現物より強い=翌朝窓開け上昇示唆",
                       f"先物:{fut_r1:+.2f}% 現物:{sp_r1_f:+.2f}%")

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # ⑥ 市場の幅（Breadth）
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        cat = "⑥ 市場の幅"
        import functools as _ft
        breadth_syms = ["SPY", "QQQ", "IWM", "DIA", "MDY"]
        net_list = []
        for bsym in breadth_syms:
            bdf = _h(bsym)
            if bdf.empty or len(bdf) < 40:
                continue
            bc  = bdf["Close"].dropna().pct_change().dropna()
            net_list.append(bc.apply(lambda x: 1.0 if x > 0 else (-1.0 if x < 0 else 0.0)))

        if net_list:
            combined = _ft.reduce(lambda a, b: a.add(b, fill_value=0), net_list)
            combined = combined / len(net_list)
            ema19 = combined.ewm(span=19, adjust=False).mean()
            ema39 = combined.ewm(span=39, adjust=False).mean()
            msi   = (ema19 - ema39).cumsum()
            msi_v = float(msi.iloc[-1])
            # 累積和は値域が不定なためパーセンタイルランクで正規化（±1に張り付き防止）
            msi_pct = float((msi <= msi_v).mean()) * 2 - 1  # -1〜+1
            add_signal(cat, "McClellan Summation Index",
                       np.tanh(msi_pct * 1.5), 2.0,
                       "市場全体の騰落広がり（過去データ内パーセンタイル）",
                       f"MSI={msi_v:.3f} PctRank={((msi<=msi_v).mean()*100):.0f}%")

        # SPY vs IWM（大型 vs 小型）
        spy_df = _h("SPY"); iwm_df = _h("IWM")
        if not spy_df.empty and not iwm_df.empty and len(spy_df) >= 6 and len(iwm_df) >= 6:
            spy_r5 = (spy_df["Close"].dropna().iloc[-1] /
                      spy_df["Close"].dropna().iloc[-6] - 1) * 100
            iwm_r5 = (iwm_df["Close"].dropna().iloc[-1] /
                      iwm_df["Close"].dropna().iloc[-6] - 1) * 100
            # 小型株が強い=リスクオン=強気
            add_signal(cat, "小型株(IWM) vs 大型株(SPY)",
                       np.tanh((iwm_r5 - spy_r5) / 3), 1.5,
                       "小型株アウトパフォーム=リスクオン",
                       f"IWM:{iwm_r5:+.2f}% SPY:{spy_r5:+.2f}%")

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # ⑦ コモディティ・ドル
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        cat = "⑦ コモディティ・ドル"
        # 原油
        oil_df = _h("CL=F")
        if not oil_df.empty and len(oil_df) >= 6:
            oil_c   = oil_df["Close"].dropna()
            oil_r1  = (oil_c.iloc[-1] / oil_c.iloc[-2] - 1) * 100
            oil_r5  = (oil_c.iloc[-1] / oil_c.iloc[-6] - 1) * 100
            add_signal(cat, "原油 前日変化",
                       np.tanh(-oil_r1 / 3), 1.0,
                       "原油↑→インフレ→金利↑→株↓", f"{oil_r1:+.2f}%")
            add_signal(cat, "原油 5日モメンタム",
                       np.tanh(-oil_r5 / 5), 0.8,
                       "中期コストプッシュ圧力", f"{oil_r5:+.2f}%")

        # ゴールド
        gold_df = _h("GC=F")
        if not gold_df.empty and len(gold_df) >= 2:
            gold_c  = gold_df["Close"].dropna()
            gold_r1 = (gold_c.iloc[-1] / gold_c.iloc[-2] - 1) * 100
            add_signal(cat, "ゴールド 前日変化",
                       np.tanh(-gold_r1 / 1.5), 0.8,
                       "金↑=リスクオフ→株弱気", f"{gold_r1:+.2f}%")

        # DXY（ドル指数）
        dxy_df = _h("DX=F")
        if not dxy_df.empty and len(dxy_df) >= 6:
            dxy_c  = dxy_df["Close"].dropna()
            dxy_r5 = (dxy_c.iloc[-1] / dxy_c.iloc[-6] - 1) * 100
            # ドル強い→新興国・資源株↓、米国株には中立〜弱い影響
            add_signal(cat, "DXY(ドル指数) 5日変化",
                       np.tanh(-dxy_r5 / 2), 0.8,
                       "ドル高=グローバルリスクオフ傾向",
                       f"{dxy_r5:+.2f}%")

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # ⑧ テクニカル分析
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        cat = "⑧ テクニカル分析"
        if len(main_c) >= 26:
            rsi_val  = _calc_rsi(main_c, 14)
            rsi_sig  = -(rsi_val - 50) / 50
            add_signal(cat, "RSI14",
                       np.tanh(rsi_sig * 1.5), 1.5,
                       f"RSI<30→逆張り強気 RSI>70→過熱",
                       f"RSI={rsi_val:.1f}")

            macd_val, macd_sig_val = _calc_macd(main_c)
            macd_cross = macd_val - macd_sig_val
            macd_norm  = macd_cross / (main_val * 0.005) if main_val > 0 else 0
            add_signal(cat, "MACD クロス",
                       np.tanh(macd_norm), 1.5,
                       "MACD>Signal=強気", f"{macd_val:.1f}/{macd_sig_val:.1f}")

            bb_pct = _calc_bb_pct(main_c, 20)
            add_signal(cat, "ボリンジャーバンド %B",
                       np.tanh(-(bb_pct - 0.5) * 2), 1.0,
                       "%B<0.2=売られすぎ(強気)", f"%B={bb_pct:.2f}")

            for window, weight, label in [(50, 1.5, "MA50"), (200, 1.5, "MA200")]:
                if len(main_c) >= window:
                    ma_val  = float(main_c.rolling(window).mean().iloc[-1])
                    ma_diff = (main_val / ma_val - 1) * 100
                    # MA上方 = トレンドフォロー強気、ただし乖離過大（>15%）は過熱として減衰
                    if ma_diff > 15:
                        sig = np.tanh((30 - ma_diff) / 10)   # 過熱域：乖離拡大で弱まる
                    elif ma_diff < -15:
                        sig = np.tanh((-30 - ma_diff) / -10) # 売られすぎ：逆張り強気
                    else:
                        sig = np.tanh(ma_diff / 8)            # 通常域：トレンド方向に線形
                    add_signal(cat, f"{label}乖離",
                               sig, weight,
                               f"現値vs{label}（±15%超で過熱/過売り判定）",
                               f"{ma_diff:+.2f}%")

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # ⑨ センチメント
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        cat = "⑨ センチメント"
        # Fear & Greed Index
        try:
            fg_r = requests.get(
                "https://production.dataviz.cnn.io/index/fearandgreed/graphdata/2024-01-01",
                timeout=6, headers={"User-Agent": "Mozilla/5.0"})
            if fg_r.status_code == 200:
                fg_score = float(fg_r.json()["fear_and_greed"]["score"])
                add_signal(cat, "Fear & Greed Index",
                           np.tanh((fg_score - 50) / 25), 2.0,
                           "75+=Extreme Greed, 25-=Extreme Fear",
                           f"{fg_score:.0f}/100")
        except Exception:
            pass

        # VXX（ボラETF）
        vxx_df = _h("VXX")
        if not vxx_df.empty and len(vxx_df) >= 20:
            vxx_c    = vxx_df["Close"].dropna()
            vxx_ma20 = float(vxx_c.rolling(20).mean().iloc[-1])
            vxx_ratio = float(vxx_c.iloc[-1]) / vxx_ma20 if vxx_ma20 > 0 else 1.0
            add_signal(cat, "VXX vs MA20",
                       np.tanh(-(vxx_ratio - 1.0) / 0.15), 1.5,
                       "VXX高=Put需要高=Fear", f"ratio={vxx_ratio:.3f}")

        # 曜日効果
        today_wd = datetime.now(JST).weekday()
        wd_names = ["Mon", "Tue", "Wed", "Thu", "Fri"]
        if not sp_df.empty and today_wd < 5:
            sp_wd = sp_df.copy()
            sp_wd["wd"]  = sp_wd.index.weekday
            sp_wd["ret"] = sp_wd["Close"].pct_change()
            wd_data = sp_wd[sp_wd["wd"] == today_wd]["ret"].dropna()
            if len(wd_data) >= 10:
                wr = float((wd_data > 0).mean())
                add_signal(cat, f"曜日効果({wd_names[today_wd]})",
                           (wr - 0.5) * 2, 0.5,
                           f"S&P500の{wd_names[today_wd]}曜日勝率",
                           f"{wr:.1%}")

        # ── 総合スコア ──────────────────────────────────
        if weight_sum <= 0:
            return {"ok": False, "reason": "シグナル不足"}

        composite = score_sum / weight_sum
        # scale 2.8→1.5 / 1.6→1.0 に調整（composite=±1 で 82%/73% 程度に抑制）
        prob_up_t = float(1 / (1 + np.exp(-composite * 1.5)) * 100)
        prob_up_w = float(1 / (1 + np.exp(-composite * 1.0)) * 100)

        # カテゴリ別スコア
        cat_scores: Dict[str, float] = {}
        for cn, rows in details_by_cat.items():
            if not rows:
                continue
            cs = sum(float(r["値"]) * float(r["重み"]) for r in rows)
            cw = sum(float(r["重み"]) for r in rows)
            cat_scores[cn] = cs / cw if cw > 0 else 0.0

        sp_val  = float(sp_df["Close"].dropna().iloc[-1]) if not sp_df.empty else None
        nq_val  = float(nq_df["Close"].dropna().iloc[-1]) if not nq_df.empty and len(nq_df) >= 1 else None

        snapshot = {
            tgt["name"]:   f"{main_val:,.2f}",
            "S&P500":      f"{sp_val:,.0f}"   if sp_val   else "N/A",
            "NASDAQ":      f"{nq_val:,.0f}"   if nq_val   else "N/A",
            "VIX":         f"{vix_val:.2f}"   if vix_val  else "N/A",
            "米10年金利":  f"{tnx_val:.3f}%"  if tnx_val  else "N/A",
        }

        return {
            "ok": True,
            "target": target,
            "target_name": tgt["name"],
            "composite": composite,
            "prob_up_tomorrow":   prob_up_t,
            "prob_down_tomorrow": 100 - prob_up_t,
            "prob_up_week":       prob_up_w,
            "prob_down_week":     100 - prob_up_w,
            "n_signals":    len(all_details),
            "details":      all_details,
            "details_by_cat": details_by_cat,
            "cat_scores":   cat_scores,
            "snapshot":     snapshot,
            "updated_at":   datetime.now(JST).strftime("%Y-%m-%d %H:%M JST"),
        }

    except Exception as e:
        logger.error(f"compute_us_prediction error: {e}", exc_info=True)
        return {"ok": False, "reason": f"計算エラー: {str(e)[:200]}"}


def render_us_prediction():
    """米国株 方向性予測スコアセクション"""
    st.markdown('<a id="us-pred"></a>', unsafe_allow_html=True)
    st.header(t("🇺🇸 米国株 方向性予測スコア", "🇺🇸 US Stocks Directional Forecast"))
    st.markdown(
        '<div style="background:linear-gradient(135deg,#e3f2fd,#fce4ec);'
        'border-left:4px solid #1976d2;border-radius:6px;padding:10px 16px;'
        'font-size:13px;color:#333;margin-bottom:12px;">'
        + t('<strong>9カテゴリ・複合シグナル</strong> で S&P500 / NASDAQ100 / ダウ の方向性確率を推定します。<br>'
            'マクロ・金利・VIX・セクター・モメンタム・市場の幅・コモディティ・テクニカル・センチメント',
            '<strong>9 categories · composite signals</strong> — directional probability for S&P500 / NASDAQ100 / Dow.<br>'
            'Macro · Rates · VIX · Sector · Momentum · Breadth · Commodities · Technical · Sentiment') +
        '</div>',
        unsafe_allow_html=True,
    )

    tab_sp, tab_nq, tab_dj = st.tabs(
        ["📊 S&P500", "💻 NASDAQ100", "🏭 ダウ平均"]
    )

    for tab, target in [(tab_sp, "SP500"), (tab_nq, "NASDAQ"), (tab_dj, "DOW")]:
        with tab:
            with st.spinner(f"{target} シグナル取得・分析中..."):
                pred = compute_us_prediction(target)

            if not pred.get("ok"):
                st.error(f"⚠️ {pred.get('reason', '不明')}")
                continue

            prob_up_t   = pred["prob_up_tomorrow"]
            prob_down_t = pred["prob_down_tomorrow"]
            prob_up_w   = pred["prob_up_week"]
            prob_down_w = pred["prob_down_week"]
            composite   = pred["composite"]
            cat_scores  = pred["cat_scores"]
            snapshot    = pred["snapshot"]
            details     = pred["details"]
            details_cat = pred["details_by_cat"]
            n_signals   = pred["n_signals"]
            updated_at  = pred["updated_at"]

            # スナップショット
            snap_keys = list(snapshot.keys())
            snap_cols = st.columns(len(snap_keys))
            for col, k in zip(snap_cols, snap_keys):
                col.metric(k, snapshot[k])

            st.divider()

            col_t, col_w, col_radar, col_conf = st.columns([1, 1, 1.2, 0.9])

            with col_t:
                color_t = "#1a7f37" if prob_up_t > 55 else ("#d1242f" if prob_up_t < 45 else "#888")
                st.markdown('<div style="text-align:center;font-size:15px;font-weight:700;margin-bottom:4px;">📅 明日の予測</div>', unsafe_allow_html=True)
                st.image(render_prediction_gauge(prob_up_t), width="stretch")
                st.markdown(
                    f'<div style="text-align:center;">'
                    f'<div style="font-size:12px;color:#666;">Prob. Up</div>'
                    f'<span style="color:{color_t};font-weight:900;font-size:22px;">{prob_up_t:.1f}%</span>'
                    f'<span style="color:#999;"> / </span>'
                    f'<span style="color:#d1242f;font-size:15px;">下降 {prob_down_t:.1f}%</span>'
                    f'</div>', unsafe_allow_html=True)

            with col_w:
                color_w = "#1a7f37" if prob_up_w > 55 else ("#d1242f" if prob_up_w < 45 else "#888")
                st.markdown('<div style="text-align:center;font-size:15px;font-weight:700;margin-bottom:4px;">📆 今週の予測</div>', unsafe_allow_html=True)
                st.image(render_prediction_gauge(prob_up_w), width="stretch")
                st.markdown(
                    f'<div style="text-align:center;">'
                    f'<div style="font-size:12px;color:#666;">Prob. Up</div>'
                    f'<span style="color:{color_w};font-weight:900;font-size:22px;">{prob_up_w:.1f}%</span>'
                    f'<span style="color:#999;"> / </span>'
                    f'<span style="color:#d1242f;font-size:15px;">下降 {prob_down_w:.1f}%</span>'
                    f'</div>', unsafe_allow_html=True)

            with col_radar:
                st.markdown('<div style="text-align:center;font-size:14px;font-weight:700;margin-bottom:4px;">📡 カテゴリ別レーダー</div>', unsafe_allow_html=True)
                if cat_scores:
                    radar_bytes = _draw_us_radar(cat_scores)
                    if radar_bytes:
                        st.image(radar_bytes, width="stretch")

            with col_conf:
                direction = "強気 📈" if composite > 0.1 else ("弱気 📉" if composite < -0.1 else "中立 ➡️")
                st.metric("方向性", direction)
                st.metric("総合スコア", f"{composite:+.3f}")
                st.metric("Count", f"{n_signals}個")
                st.caption(f"更新: {updated_at}")

            st.divider()

            # カテゴリ別スコアバー
            st.markdown("#### 📊 カテゴリ別シグナル強度")
            cat_col1, cat_col2 = st.columns(2)
            cat_items = list(US_SIGNAL_CATEGORIES.items())
            half = (len(cat_items) + 1) // 2
            for col_idx, col in enumerate([cat_col1, cat_col2]):
                with col:
                    for cat_name, cat_meta in cat_items[col_idx * half:(col_idx + 1) * half]:
                        cat_s = cat_scores.get(cat_name, 0.0)
                        color = cat_meta["color"]
                        icon  = cat_meta["icon"]
                        bar_html = _cat_score_bar_html(cat_s)
                        st.markdown(
                            f'<div style="border:1px solid #e0e0e0;border-radius:8px;'
                            f'padding:8px 12px;margin-bottom:8px;border-left:3px solid {color};">'
                            f'<div style="font-size:13px;font-weight:700;color:{color};margin-bottom:4px;">'
                            f'{icon} {cat_name}</div>'
                            f'<div style="font-size:11px;color:#777;margin-bottom:5px;">'
                            f'{cat_meta["desc"]}</div>{bar_html}</div>',
                            unsafe_allow_html=True)

            # シグナル詳細テーブル
            st.markdown("#### 🔍 シグナル詳細")
            with st.expander("全シグナル一覧", expanded=False):
                if details:
                    st.dataframe(
                        pd.DataFrame(details)[["判定","カテゴリ","シグナル","値","重み","生データ","説明"]],
                        width="stretch", hide_index=True)

            st.markdown(
                '<div style="background:#fff3cd;border:1px solid #ffc107;border-radius:8px;'
                'padding:8px 14px;font-size:12px;color:#856404;">'
                '⚠️ 本予測は参考情報です。投資判断は自己責任でお願いします。'
                '</div>', unsafe_allow_html=True)

            render_us_quant_analysis(target)


def _draw_us_radar(cat_scores: Dict[str, float]) -> bytes:
    """米国版レーダーチャート"""
    import io as _io
    cats  = list(cat_scores.keys())
    vals  = [float(np.clip(v, -1, 1)) for v in cat_scores.values()]
    vals_norm = [(v + 1) / 2 for v in vals]
    n = len(cats)
    if n < 3:
        return b""
    angles = [i * 2 * np.pi / n for i in range(n)] + [0]
    vals_plot = vals_norm + [vals_norm[0]]

    fig, ax = plt.subplots(figsize=(5, 5), subplot_kw=dict(polar=True))
    ax.set_theta_offset(np.pi / 2)
    ax.set_theta_direction(-1)
    ax.plot(angles, vals_plot, linewidth=2, color="#1976d2")
    ax.fill(angles, vals_plot, alpha=0.22, color="#1976d2")
    ax.plot(angles, [0.5] * (n + 1), linestyle="--", linewidth=0.8, color="gray", alpha=0.6)
    short_labels = ["① Macro", "② Rates", "③ VIX",
                    "④ Sector", "⑤ Momentum", "⑥ Breadth",
                    "⑦ Commodity", "⑧ Technical", "⑨ Sentiment"]
    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(short_labels[:n], fontsize=8, fontfamily="DejaVu Sans")
    ax.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
    ax.set_yticklabels(["Bear","","Neut","","Bull"], fontsize=7, color="gray", fontfamily="DejaVu Sans")
    ax.set_ylim(0, 1)
    ax.grid(True, alpha=0.3)
    fig.patch.set_facecolor("white")
    plt.tight_layout()
    buf = _io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    buf.seek(0)
    return buf.read()



# ===========================
# カードCSS（レスポンシブ対応）
# ===========================


# =====================================================
# ★ 高度解析モジュール A/B/C/D
# =====================================================

# ── セクター定義 ─────────────────────────────────────
SECTORS = {
    "XLK":  {"name": "Technology",      "jp": "IT・テクノロジー",  "color": "#1565c0"},
    "XLF":  {"name": "Financials",      "jp": "金融",              "color": "#2e7d32"},
    "XLV":  {"name": "Health Care",     "jp": "ヘルスケア",        "color": "#c62828"},
    "XLY":  {"name": "Cons.Discret.",   "jp": "一般消費財",        "color": "#f57f17"},
    "XLP":  {"name": "Cons.Staples",    "jp": "生活必需品",        "color": "#6a1b9a"},
    "XLE":  {"name": "Energy",          "jp": "エネルギー",        "color": "#4e342e"},
    "XLI":  {"name": "Industrials",     "jp": "資本財・産業",      "color": "#00695c"},
    "XLB":  {"name": "Materials",       "jp": "素材",              "color": "#827717"},
    "XLRE": {"name": "Real Estate",     "jp": "不動産",            "color": "#ad1457"},
    "XLU":  {"name": "Utilities",       "jp": "公益事業",          "color": "#37474f"},
    "XLC":  {"name": "Comm.Services",   "jp": "通信サービス",      "color": "#0277bd"},
}

# ── マクロレジーム定義 ────────────────────────────────
REGIMES = {
    "Risk-On Expansion":   {"jp": "🟢 Risk-On Expansion",   "color": "#1a7f37", "assets": "Equity up / Bond down / Commodity up"},
    "Risk-On Slowdown":    {"jp": "🟡 Risk-On Slowdown",    "color": "#f9a825", "assets": "Equity flat / Bond up / Gold up"},
    "Risk-Off Recovery":   {"jp": "🔵 Risk-Off Recovery",   "color": "#1565c0", "assets": "Bond up / Gold up / Equity down"},
    "Risk-Off Contraction":{"jp": "🔴 Risk-Off Contraction","color": "#d1242f", "assets": "Cash up / Gold up / Equity down"},
}


# =====================================================
# [A] セクターローテーションマップ
# =====================================================
@st.cache_data(ttl=TTL_DAILY, show_spinner=False)
def compute_sector_rotation() -> Dict[str, Any]:
    """
    RRG（Relative Rotation Graph）方式でセクターの
    モメンタム × 相対強度を計算する。

    x軸: RS比率（SPYに対する相対強度）
    y軸: RSモメンタム（RS比率の変化率）
    バブルサイズ: 過去1ヶ月リターン絶対値
    """
    try:
        end   = datetime.now(timezone.utc)
        start = end - timedelta(days=365)

        # SPY + 11セクターETFをバッチ一括ダウンロード（個別Ticker×12回 → 1回）
        _sr_syms = ["SPY"] + list(SECTORS.keys())
        _sr_raw  = yf.download(
            _sr_syms, start=start, end=end,
            progress=False, auto_adjust=False, group_by="ticker"
        )

        def _sr_close(sym):
            try:
                if isinstance(_sr_raw.columns, pd.MultiIndex):
                    s = _sr_raw[sym]["Close"] if sym in _sr_raw.columns.get_level_values(0) else pd.Series(dtype=float)
                else:
                    s = _sr_raw["Close"] if len(_sr_syms) == 1 else pd.Series(dtype=float)
                s = s.dropna()
                if s.index.tz is None:
                    s.index = s.index.tz_localize("UTC")
                return s.tz_convert(JST)
            except Exception:
                return pd.Series(dtype=float)

        spy_c = _sr_close("SPY")
        if spy_c.empty:
            return {"ok": False, "reason": "SPYデータ取得失敗"}

        results = {}
        for sym, meta in SECTORS.items():
            try:
                sec_c = _sr_close(sym)
                if sec_c.empty:
                    continue

                # 共通日付
                common = sec_c.index.intersection(spy_c.index)
                if len(common) < 60:
                    continue
                sec_aligned = sec_c.loc[common]
                spy_aligned = spy_c.loc[common]

                # RS比率（52週正規化）
                rs_raw = sec_aligned / spy_aligned
                rs_52w_max = rs_raw.rolling(252, min_periods=60).max().iloc[-1]
                rs_52w_min = rs_raw.rolling(252, min_periods=60).min().iloc[-1]
                rs_range = rs_52w_max - rs_52w_min
                rs_ratio = float(
                    (rs_raw.iloc[-1] - rs_52w_min) / rs_range * 100
                    if rs_range > 0 else 50
                )

                # RSモメンタム（RS比率の1週間変化）
                rs_1w_ago = float(
                    (rs_raw.iloc[-6] - rs_52w_min) / rs_range * 100
                    if len(rs_raw) >= 6 and rs_range > 0 else 50
                )
                rs_momentum = rs_ratio - rs_1w_ago

                # 1ヶ月リターン
                ret_1m = float((sec_aligned.iloc[-1] / sec_aligned.iloc[-22] - 1) * 100)                          if len(sec_aligned) >= 22 else 0.0

                # 現在値・前日比
                current = float(sec_aligned.iloc[-1])
                ret_1d  = float((sec_aligned.iloc[-1] / sec_aligned.iloc[-2] - 1) * 100)                           if len(sec_aligned) >= 2 else 0.0

                # 象限判定
                quadrant = (
                    "Leading"   if rs_ratio > 50 and rs_momentum > 0 else
                    "Weakening" if rs_ratio > 50 and rs_momentum <= 0 else
                    "Improving" if rs_ratio <= 50 and rs_momentum > 0 else
                    "Lagging"
                )

                results[sym] = {
                    "name":        meta["jp"],
                    "color":       meta["color"],
                    "rs_ratio":    round(rs_ratio,   2),
                    "rs_momentum": round(rs_momentum, 2),
                    "ret_1m":      round(ret_1m, 2),
                    "ret_1d":      round(ret_1d, 2),
                    "current":     round(current, 2),
                    "quadrant":    quadrant,
                }
            except Exception as e:
                logger.debug(f"sector {sym}: {e}")
                continue

        return {"ok": True, "sectors": results,
                "updated_at": datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")}

    except Exception as e:
        logger.error(f"compute_sector_rotation error: {e}")
        return {"ok": False, "reason": str(e)[:200]}


def render_sector_rotation():
    """セクターローテーションマップ描画"""
    st.markdown('<a id="sector"></a>', unsafe_allow_html=True)
    st.header("🔄 Sector Rotation Map")
    st.markdown(
        '<div style="background:linear-gradient(135deg,#e3f2fd,#e8f5e9);'
        'border-left:4px solid #1976d2;border-radius:6px;padding:10px 16px;'
        'font-size:13px;color:#333;margin-bottom:12px;">'
        + t('<strong>RRG（Relative Rotation Graph）方式</strong>: x軸=相対強度, y軸=モメンタム の2軸で11セクターの位置を可視化。'
            '<br>🟢Leading（強い）→ 🟡Weakening（失速）→ 🔴Lagging（弱い）→ 🔵Improving（回復）のサイクルで回転します。',
            '<strong>RRG (Relative Rotation Graph)</strong>: x-axis = relative strength, y-axis = momentum — plots 11 sectors.'
            '<br>🟢 Leading → 🟡 Weakening → 🔴 Lagging → 🔵 Improving cycle.') +
        '</div>', unsafe_allow_html=True)

    with st.spinner("セクターデータ取得中..."):
        data = compute_sector_rotation()

    if not data.get("ok"):
        st.error(f"取得失敗: {data.get('reason')}")
        return

    sectors = data["sectors"]
    if not sectors:
        st.warning("セクターデータを取得できませんでした")
        return

    # ── RRGチャート ──────────────────────────────────
    fig, ax = plt.subplots(figsize=(10, 4.5))
    fig.patch.set_facecolor("#fafafa")
    ax.set_facecolor("#fafafa")

    # 象限背景
    ax.axhspan(0, 20,  xmin=0.5, xmax=1.0, alpha=0.08, color="#1a7f37")  # Leading
    ax.axhspan(-20, 0, xmin=0.5, xmax=1.0, alpha=0.08, color="#f9a825")  # Weakening
    ax.axhspan(-20, 0, xmin=0.0, xmax=0.5, alpha=0.08, color="#d1242f")  # Lagging
    ax.axhspan(0, 20,  xmin=0.0, xmax=0.5, alpha=0.08, color="#1565c0")  # Improving

    # 象限ラベル
    ax.text(85, 18,  "Leading",   fontsize=11, fontweight="bold",
            color="#1a7f37", ha="right", fontfamily="DejaVu Sans")
    ax.text(85, -18, "Weakening", fontsize=11, fontweight="bold",
            color="#f9a825", ha="right", fontfamily="DejaVu Sans")
    ax.text(15, -18, "Lagging",   fontsize=11, fontweight="bold",
            color="#d1242f", ha="left",  fontfamily="DejaVu Sans")
    ax.text(15, 18,  "Improving", fontsize=11, fontweight="bold",
            color="#1565c0", ha="left",  fontfamily="DejaVu Sans")

    # 中心軸
    ax.axvline(50, color="gray", linewidth=1.2, alpha=0.5)
    ax.axhline(0,  color="gray", linewidth=1.2, alpha=0.5)

    # 各セクターのバブルプロット
    for sym, s in sectors.items():
        x = s["rs_ratio"]
        y = s["rs_momentum"]
        size = max(200, abs(s["ret_1m"]) * 150 + 300)
        color = s["color"]

        ax.scatter(x, y, s=size, color=color, alpha=0.75, zorder=5,
                   edgecolors="white", linewidths=1.5)

        # ラベル
        offset_x = 2 if x < 80 else -2
        ha = "left" if x < 80 else "right"
        # ETFシンボル + リターンをラベルに（ASCII文字のみ）
        label_text = f"{sym}\n{s['ret_1d']:+.1f}%"
        ax.annotate(
            label_text,
            xy=(x, y), xytext=(offset_x, 3),
            textcoords="offset points",
            fontsize=8.5, fontweight="bold", color=color,
            ha=ha, fontfamily="DejaVu Sans",
            bbox=dict(boxstyle="round,pad=0.2", facecolor="white",
                      edgecolor=color, alpha=0.8, linewidth=0.8),
        )

    ax.set_xlim(0, 100)
    ax.set_ylim(-20, 20)
    ax.set_xlabel("RS Ratio (vs SPY)", fontsize=11, fontfamily="DejaVu Sans")
    ax.set_ylabel("RS Momentum", fontsize=11, fontfamily="DejaVu Sans")
    ax.set_title("Sector Rotation RRG Chart",
                 fontsize=14, fontweight="bold", pad=15, fontfamily="DejaVu Sans")
    ax.grid(True, alpha=0.2)
    plt.tight_layout()
    st.pyplot(fig, clear_figure=True)

    # ── 象限別テーブル ───────────────────────────────
    st.markdown("#### 📋 Sector Details")
    quad_order = ["Leading", "Weakening", "Improving", "Lagging"]
    quad_labels = {
        "Leading":   "🟢 Leading (Strong & Continuing)",
        "Weakening": "🟡 Weakening (Losing Momentum)",
        "Improving": "🔵 Improving (Recovering)",
        "Lagging":   "🔴 Lagging (Weak)",
    }
    cols = st.columns(2)
    for i, quad in enumerate(quad_order):
        with cols[i % 2]:
            quad_sectors = {k: v for k, v in sectors.items()
                            if v["quadrant"] == quad}
            if quad_sectors:
                st.markdown(f"**{quad_labels[quad]}**")
                rows = [{"ETF": k, "Sector": v["name"],
                         "1D": f"{v['ret_1d']:+.2f}%",
                         "1M": f"{v['ret_1m']:+.2f}%",
                         "RS Ratio": f"{v['rs_ratio']:.1f}",
                         "Momentum": f"{v['rs_momentum']:+.2f}"}
                        for k, v in sorted(quad_sectors.items(),
                                           key=lambda x: x[1]["rs_ratio"],
                                           reverse=True)]
                st.dataframe(pd.DataFrame(rows),
                             width="stretch", hide_index=True)

    st.caption(f"Updated: {data['updated_at']} | Bubble size = abs(1M return)")


# =====================================================
# [B] マクロ経済レジーム検知
# =====================================================
@st.cache_data(ttl=TTL_DAILY, show_spinner=False)
def detect_macro_regime() -> Dict[str, Any]:
    """
    複数指標からマクロ経済レジームを8段階で判定する。

    判定軸:
      成長軸: イールドカーブ・小型株相対強度・銅/金比率
      リスク軸: VIX水準・クレジットスプレッド・Put/Call
    """
    try:
        end   = datetime.now(timezone.utc)
        start = end - timedelta(days=420)

        def _c(sym):
            try:
                df = yf.Ticker(sym).history(
                    start=start, end=end, interval="1d", auto_adjust=False)
                if df is None or df.empty:
                    return pd.Series(dtype=float)
                if df.index.tz is None:
                    df.index = df.index.tz_localize("UTC")
                return df.tz_convert(JST)["Close"].dropna()
            except Exception:
                return pd.Series(dtype=float)

        tnx  = _c("^TNX")   # 米10年
        irx  = _c("^IRX")   # 米3ヶ月
        vix  = _c("^VIX")
        spy  = _c("SPY")
        iwm  = _c("IWM")    # 小型株
        hyg  = _c("HYG")    # ハイイールド債
        lqd  = _c("LQD")    # 投資適格債
        cop  = _c("CPER")   # 銅ETF
        gld  = _c("GLD")    # ゴールド
        tlt  = _c("TLT")    # 長期国債

        signals = {}
        scores  = {}

        # ── 成長シグナル ──────────────────────────────
        # 1. イールドカーブ（10Y - 3M）
        if len(tnx) >= 2 and len(irx) >= 2:
            common = tnx.index.intersection(irx.index)
            if len(common) >= 20:
                tnx_a = tnx.loc[common]
                irx_a = irx.loc[common] / 10
                spread = float(tnx_a.iloc[-1]) - float(irx_a.iloc[-1])
                spread_ma20 = float((tnx_a - irx_a).rolling(20).mean().iloc[-1])
                signals["yield_curve"]   = spread > 0          # 正=拡張
                signals["yc_improving"]  = spread > spread_ma20 # 改善中
                scores["yield_curve"]    = spread
                scores["yc_trend"]       = spread - spread_ma20

        # 2. 小型株 vs 大型株（IWM/SPY）
        if len(iwm) >= 20 and len(spy) >= 20:
            common2 = iwm.index.intersection(spy.index)
            if len(common2) >= 20:
                ratio = iwm.loc[common2] / spy.loc[common2]
                ratio_ma20 = float(ratio.rolling(20).mean().iloc[-1])
                ratio_now  = float(ratio.iloc[-1])
                signals["small_cap_lead"] = ratio_now > ratio_ma20
                scores["small_cap"]       = (ratio_now / ratio_ma20 - 1) * 100

        # 3. 銅/ゴールド比率（景気先行指標）
        if len(cop) >= 20 and len(gld) >= 20:
            common3 = cop.index.intersection(gld.index)
            if len(common3) >= 20:
                cg_ratio  = cop.loc[common3] / gld.loc[common3]
                cg_ma20   = float(cg_ratio.rolling(20).mean().iloc[-1])
                cg_now    = float(cg_ratio.iloc[-1])
                signals["copper_gold"] = cg_now > cg_ma20
                scores["copper_gold"]  = (cg_now / cg_ma20 - 1) * 100

        # ── リスクシグナル ────────────────────────────
        # 4. VIX水準
        vix_val = None
        if len(vix) >= 20:
            vix_val  = float(vix.iloc[-1])
            vix_ma20 = float(vix.rolling(20).mean().iloc[-1])
            signals["low_vix"]      = vix_val < 20
            signals["vix_falling"]  = vix_val < vix_ma20
            scores["vix"]           = vix_val

        # 5. クレジットスプレッド（HYG/LQD）
        if len(hyg) >= 20 and len(lqd) >= 20:
            common4 = hyg.index.intersection(lqd.index)
            if len(common4) >= 20:
                hl_ratio  = hyg.loc[common4] / lqd.loc[common4]
                hl_ma20   = float(hl_ratio.rolling(20).mean().iloc[-1])
                hl_now    = float(hl_ratio.iloc[-1])
                signals["tight_spread"] = hl_now > hl_ma20   # HYG強=スプレッド縮小=楽観
                scores["credit"]        = (hl_now / hl_ma20 - 1) * 100

        # 6. TLT vs SPY（セーフヘイブン需要）
        if len(tlt) >= 20 and len(spy) >= 20:
            common5 = tlt.index.intersection(spy.index)
            if len(common5) >= 20:
                tlt_ret20 = float(tlt.loc[common5].iloc[-1] /
                                  tlt.loc[common5].iloc[-21] - 1) * 100
                spy_ret20 = float(spy.loc[common5].iloc[-1] /
                                  spy.loc[common5].iloc[-21] - 1) * 100
                signals["stock_leads"] = spy_ret20 > tlt_ret20  # 株>債券=リスクオン
                scores["safe_haven"]   = spy_ret20 - tlt_ret20

        # ── レジーム判定 ─────────────────────────────
        growth_signals = [
            signals.get("yield_curve", False),
            signals.get("yc_improving", False),
            signals.get("small_cap_lead", False),
            signals.get("copper_gold", False),
        ]
        risk_signals = [
            signals.get("low_vix", False),
            signals.get("vix_falling", False),
            signals.get("tight_spread", False),
            signals.get("stock_leads", False),
        ]

        growth_score  = sum(growth_signals) / max(len(growth_signals), 1)
        risk_on_score = sum(risk_signals)   / max(len(risk_signals), 1)

        # 4象限
        is_risk_on    = risk_on_score >= 0.5
        is_expanding  = growth_score  >= 0.5

        if is_risk_on and is_expanding:
            regime = "Risk-On Expansion"
        elif is_risk_on and not is_expanding:
            regime = "Risk-On Slowdown"
        elif not is_risk_on and is_expanding:
            regime = "Risk-Off Recovery"
        else:
            regime = "Risk-Off Contraction"

        # 信頼度（シグナルの一致度）
        all_agree = max(growth_score, 1-growth_score) + max(risk_on_score, 1-risk_on_score)
        confidence = round((all_agree / 2 - 0.5) * 200, 1)  # 0〜100%

        # 推奨アセット配分
        asset_allocation = {
            "Risk-On Expansion":    {"Equity": 70, "Bond": 10, "Commodity": 15, "Cash": 5},
            "Risk-On Slowdown":     {"Equity": 50, "Bond": 25, "Commodity": 15, "Cash": 10},
            "Risk-Off Recovery":    {"Equity": 30, "Bond": 40, "Commodity": 20, "Cash": 10},
            "Risk-Off Contraction": {"Equity": 15, "Bond": 40, "Commodity": 15, "Cash": 30},
        }

        # 歴史的類似局面
        historical_parallels = {
            "Risk-On Expansion":    "2013-2017 (QE rally), 2020H2-2021 (COVID recovery)",
            "Risk-On Slowdown":     "2018 (late rate hike), 2022 H1",
            "Risk-Off Recovery":    "2019 (rate cut pivot), 2023 H1",
            "Risk-Off Contraction": "2008 (Lehman), 2020 Feb-Mar (COVID shock)",
        }

        return {
            "ok":              True,
            "regime":          regime,
            "regime_jp":       REGIMES[regime]["jp"],
            "regime_color":    REGIMES[regime]["color"],
            "assets":          REGIMES[regime]["assets"],
            "growth_score":    round(growth_score * 100, 1),
            "risk_on_score":   round(risk_on_score * 100, 1),
            "confidence":      confidence,
            "signals":         signals,
            "scores":          scores,
            "allocation":      asset_allocation[regime],
            "historical":      historical_parallels[regime],
            "vix":             vix_val,
            "updated_at":      datetime.now(JST).strftime("%Y-%m-%d %H:%M JST"),
        }

    except Exception as e:
        logger.error(f"detect_macro_regime error: {e}")
        return {"ok": False, "reason": str(e)[:200]}


def render_macro_regime():
    """マクロ経済レジーム検知セクション描画"""
    st.header("🌐 Macro Economic Regime Detection")
    st.markdown(
        '<div style="background:linear-gradient(135deg,#f3e5f5,#e8f5e9);'
        'border-left:4px solid #9c27b0;border-radius:6px;padding:10px 16px;'
        'font-size:13px;color:#333;margin-bottom:12px;">'
        '<strong>8シグナル</strong>から成長軸×リスク軸の4象限でマクロ局面を判定。'
        'イールドカーブ・小型株・銅/金比率・VIX・クレジットスプレッドを総合分析します。'
        '</div>', unsafe_allow_html=True)

    # ── Alpha Vantage 経済指標（APIキーあり時） ────────────
    if ALPHA_VANTAGE_KEY:
        with st.expander("📊 Alpha Vantage リアルタイム経済指標", expanded=True):
            with st.spinner("経済指標取得中..."):
                render_av_economic_dashboard()
    else:
        st.caption("💡 ALPHA_VANTAGE_KEY を設定するとFF金利・CPI・GDP等のリアルタイム経済指標が表示されます")

    with st.spinner("マクロ指標取得中..."):
        regime_data = detect_macro_regime()

    if not regime_data.get("ok"):
        st.error(f"取得失敗: {regime_data.get('reason')}")
        return

    regime      = regime_data["regime"]
    regime_jp   = regime_data["regime_jp"]
    color       = regime_data["regime_color"]
    growth_s    = regime_data["growth_score"]
    risk_s      = regime_data["risk_on_score"]
    confidence  = regime_data["confidence"]
    allocation  = regime_data["allocation"]
    signals     = regime_data["signals"]
    scores      = regime_data["scores"]

    # ── メインカード ──────────────────────────────────
    st.markdown(
        f'<div style="background:linear-gradient(135deg,{color}22,{color}11);'
        f'border:3px solid {color};border-radius:16px;padding:20px 28px;'
        f'margin-bottom:20px;">'
        f'<div style="font-size:28px;font-weight:900;color:{color};">{regime_jp}</div>'
        f'<div style="font-size:14px;color:#555;margin-top:6px;">'
        f'推奨アセット: <strong>{regime_data["assets"]}</strong></div>'
        f'<div style="font-size:12px;color:#888;margin-top:4px;">'
        f'過去類似局面: {regime_data["historical"]}</div>'
        f'</div>', unsafe_allow_html=True)

    # ── スコアメーター ────────────────────────────────
    c1, c2, c3 = st.columns(3)
    c1.metric("Growth Score", f"{growth_s:.0f}/100",
              help="Yield Curve / Small Cap / Copper-Gold")
    c2.metric("Risk-On Score", f"{risk_s:.0f}/100",
              help="VIX / Credit Spread / Safe Haven Demand")
    c3.metric("Confidence",   f"{confidence:.0f}%",
              help="Signal agreement rate")

    # ── 4象限チャート ─────────────────────────────────
    fig, ax = plt.subplots(figsize=(8, 3.5))
    fig.patch.set_facecolor("#fafafa")
    ax.set_facecolor("#fafafa")

    # 象限背景
    ax.fill_between([50, 100], [50, 50], [100, 100],
                    color="#1a7f37", alpha=0.08)  # Risk-On Expansion
    ax.fill_between([50, 100], [0, 0],   [50, 50],
                    color="#f9a825", alpha=0.08)  # Risk-On Slowdown
    ax.fill_between([0, 50],  [50, 50], [100, 100],
                    color="#1565c0", alpha=0.08)  # Risk-Off Recovery
    ax.fill_between([0, 50],  [0, 0],   [50, 50],
                    color="#d1242f", alpha=0.08)  # Risk-Off Contraction

    # 象限ラベル
    for (x, y, label, c) in [
        (75, 75, "🟢 Risk-On\nExpansion",    "#1a7f37"),
        (75, 25, "🟡 Risk-On\nSlowdown",     "#f9a825"),
        (25, 75, "🔵 Risk-Off\nRecovery",    "#1565c0"),
        (25, 25, "🔴 Risk-Off\nContraction", "#d1242f"),
    ]:
        ax.text(x, y, label, fontsize=10, ha="center", va="center",
                color=c, fontweight="bold", fontfamily="DejaVu Sans",
                alpha=0.7)

    # 軸線
    ax.axvline(50, color="gray", linewidth=1, alpha=0.5)
    ax.axhline(50, color="gray", linewidth=1, alpha=0.5)

    # 現在位置プロット
    ax.scatter(risk_s, growth_s, s=600, color=color,
               zorder=10, edgecolors="white", linewidths=3)
    ax.annotate("NOW", xy=(risk_s, growth_s), xytext=(5, 5),
                textcoords="offset points", fontsize=11,
                fontweight="bold", color=color,
                fontfamily="DejaVu Sans")

    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.set_xlabel("Risk-On Score ->", fontsize=12, fontfamily="DejaVu Sans")
    ax.set_ylabel("Growth Score ->", fontsize=12, fontfamily="DejaVu Sans")
    ax.set_title("Macro Economic Regime Map", fontsize=14, fontweight="bold", fontfamily="DejaVu Sans")
    ax.grid(True, alpha=0.2)
    plt.tight_layout()
    st.pyplot(fig, clear_figure=True)

    # ── 推奨アセット配分 ─────────────────────────────
    st.markdown("#### 📊 Recommended Asset Allocation")
    alloc_items = list(allocation.items())
    alloc_cols  = st.columns(len(alloc_items))
    alloc_colors = {"Equity": "#1976d2", "Bond": "#2e7d32",
                    "Commodity": "#f57f17", "Cash": "#757575"}
    for col, (asset, pct) in zip(alloc_cols, alloc_items):
        ac = alloc_colors.get(asset, "#888")
        col.markdown(
            f'<div style="text-align:center;padding:12px;'
            f'background:{ac}22;border:2px solid {ac};border-radius:10px;">'
            f'<div style="font-size:12px;color:{ac};font-weight:700;">{asset}</div>'
            f'<div style="font-size:28px;font-weight:900;color:{ac};">{pct}%</div>'
            f'</div>', unsafe_allow_html=True)

    # ── シグナル一覧 ──────────────────────────────────
    with st.expander("🔍 Signal Details", expanded=False):
        signal_rows = [
            {"Signal": "Yield Curve (10Y-3M > 0)",
             "Status": "✅ Normal" if signals.get("yield_curve") else "⚠️ Inverted",
             "Value": f"{scores.get('yield_curve', 0):+.3f}%pt"},
            {"Signal": "YC Trend (vs MA20)",
             "Status": "✅ Improving" if signals.get("yc_improving") else "❌ Worsening",
             "Value": f"{scores.get('yc_trend', 0):+.3f}%pt"},
            {"Signal": "Small Cap Lead (IWM vs SPY)",
             "Status": "✅ Leading" if signals.get("small_cap_lead") else "❌ Lagging",
             "Value": f"{scores.get('small_cap', 0):+.2f}%"},
            {"Signal": "Copper/Gold Ratio",
             "Status": "✅ Rising" if signals.get("copper_gold") else "❌ Falling",
             "Value": f"{scores.get('copper_gold', 0):+.2f}%"},
            {"Signal": "VIX Level (< 20)",
             "Status": "✅ Stable" if signals.get("low_vix") else "⚠️ Elevated",
             "Value": f"VIX={regime_data.get('vix', 0):.1f}"},
            {"Signal": "VIX Trend (< MA20)",
             "Status": "✅ Falling" if signals.get("vix_falling") else "❌ Rising",
             "Value": ""},
            {"Signal": "Credit Spread (HYG/LQD)",
             "Status": "✅ Tight" if signals.get("tight_spread") else "❌ Wide",
             "Value": f"{scores.get('credit', 0):+.2f}%"},
            {"Signal": "Equity vs Bond (SPY > TLT 20D)",
             "Status": "✅ Equity Lead" if signals.get("stock_leads") else "❌ Bond Lead",
             "Value": f"{scores.get('safe_haven', 0):+.2f}%pt"},
        ]
        st.dataframe(pd.DataFrame(signal_rows),
                     width="stretch", hide_index=True)

    st.caption(f"更新: {regime_data['updated_at']}")


# =====================================================
# [C] コリレーションヒートマップ
# =====================================================
@st.cache_data(ttl=TTL_DAILY, show_spinner=False)
def compute_correlation_matrix(period_days: int = 90) -> Dict[str, Any]:
    """複数資産の相関係数行列を計算する"""
    ASSETS = {
        "Nikkei225": "^N225",
        "S&P500":    "^GSPC",
        "NASDAQ":    "^IXIC",
        "ダウ":      "^DJI",
        "VIX":       "^VIX",
        "USD/JPY":   "USDJPY=X",
        "EUR/USD":   "EURUSD=X",
        "US10Y":     "^TNX",
        "CrudeOil":  "CL=F",
        "Gold":      "GC=F",
        "Bitcoin": "BTC-USD",
        "SPY":       "SPY",
        "QQQ":       "QQQ",
        "IWM":       "IWM",
        "TLT":       "TLT",
        "HYG":       "HYG",
        "XLK":       "XLK",
        "XLE":       "XLE",
        "GLD":       "GLD",
        "DXY":       "DX=F",
    }
    try:
        end   = datetime.now(timezone.utc)
        start = end - timedelta(days=period_days + 30)
        prices = {}
        for name, sym in ASSETS.items():
            try:
                df = yf.Ticker(sym).history(
                    start=start, end=end, interval="1d", auto_adjust=False)
                if df is None or df.empty:
                    continue
                if df.index.tz is None:
                    df.index = df.index.tz_localize("UTC")
                c = df.tz_convert(JST)["Close"].dropna()
                if len(c) >= period_days // 2:
                    prices[name] = c
            except Exception:
                continue

        if len(prices) < 3:
            return {"ok": False, "reason": "データ不足"}

        price_df = pd.DataFrame(prices)
        # 各列を個別に日次リターン化してから結合
        ret_dict = {}
        for col in price_df.columns:
            s = price_df[col].dropna()
            if len(s) >= period_days // 2:
                ret_dict[col] = s.pct_change().dropna()
        if len(ret_dict) < 3:
            return {"ok": False, "reason": "リターンデータ不足"}
        ret_df = pd.DataFrame(ret_dict)
        # 直近 period_days 分（ペアワイズで相関計算するのでdropnaしない）
        ret_df  = ret_df.tail(period_days + 10)
        corr_df = ret_df.corr(min_periods=20)  # 最低20日あれば計算

        return {
            "ok":       True,
            "corr_df":  corr_df,
            "n_days":   len(ret_df),
            "assets":   list(corr_df.columns),
            "updated_at": datetime.now(JST).strftime("%Y-%m-%d %H:%M JST"),
        }
    except Exception as e:
        logger.error(f"compute_correlation_matrix error: {e}")
        return {"ok": False, "reason": str(e)[:200]}


def render_correlation_heatmap():
    """コリレーションヒートマップ描画"""
    st.header("📊 Correlation Heatmap")
    st.markdown(
        '<div style="background:linear-gradient(135deg,#fff3e0,#e8eaf6);'
        'border-left:4px solid #ff6f00;border-radius:6px;padding:10px 16px;'
        'font-size:13px;color:#333;margin-bottom:12px;">'
        '<strong>20資産の相関係数</strong>をヒートマップで可視化。'
        '赤=正の相関（同方向）、青=負の相関（逆方向）、白=無相関。'
        '</div>', unsafe_allow_html=True)

    st.info(
        "📘 ヒートマップの見方\n"
        "・各マスは2つの資産の日次リターンの相関係数です。\n"
        "・ +1 に近いほど同じ方向に動きやすく、-1 に近いほど逆方向に動きやすいことを示します。\n"
        "・ 0 付近は連動性が弱い状態です。\n"
        "・ 対角線は自分自身との相関なので常に 1.00 です。\n"
        "・ 例えば株式同士が高い正相関なら同時に動きやすく、株と債券が負相関なら分散効果の参考になります。"
    )

    period = st.radio("計算期間", ["30日", "90日", "1年"],
                      index=1, horizontal=True, key="corr_period")
    period_map = {"30日": 30, "90日": 90, "1年": 252}
    period_days = period_map[period]

    with st.spinner("相関係数計算中..."):
        corr_data = compute_correlation_matrix(period_days)

    if not corr_data.get("ok"):
        st.error(f"取得失敗: {corr_data.get('reason')}")
        return

    corr_df = corr_data["corr_df"]
    n_days  = corr_data["n_days"]

    # ── ヒートマップ ──────────────────────────────────
    fig, ax = plt.subplots(figsize=(14, 12))
    n = len(corr_df)

    # カラーマップ（赤-白-青）
    import matplotlib.colors as mcolors
    cmap = plt.cm.RdBu_r

    im = ax.imshow(corr_df.values, cmap=cmap, vmin=-1, vmax=1, aspect="auto")

    # 軸ラベル
    ax.set_xticks(range(n))
    ax.set_yticks(range(n))
    ax.set_xticklabels(corr_df.columns, rotation=45, ha="right", fontsize=9,
                       fontfamily="DejaVu Sans")
    ax.set_yticklabels(corr_df.columns, fontsize=9,
                       fontfamily="DejaVu Sans")

    # セル内に数値表示
    for i in range(n):
        for j in range(n):
            val = corr_df.values[i, j]
            text_color = "white" if abs(val) > 0.6 else "black"
            ax.text(j, i, f"{val:.2f}", ha="center", va="center",
                    fontsize=7, color=text_color,
                    fontfamily="DejaVu Sans")

    # カラーバー
    cbar = plt.colorbar(im, ax=ax, shrink=0.8)
    cbar.set_label("Correlation", fontsize=10, fontfamily="DejaVu Sans")
    ax.set_title(f"Correlation Matrix (Last {n_days} Days)",
                 fontsize=14, fontweight="bold", pad=15, fontfamily="DejaVu Sans")
    plt.tight_layout()
    st.pyplot(fig, clear_figure=True)

    # ── 注目ペア（高相関・逆相関）──────────────────────
    st.markdown("#### 🔍 Notable Pairs")
    col_pos, col_neg = st.columns(2)

    pairs = []
    assets = list(corr_df.columns)
    for i in range(len(assets)):
        for j in range(i+1, len(assets)):
            val = float(corr_df.values[i, j])
            pairs.append({"Asset A": assets[i], "Asset B": assets[j],
                          "相関係数": round(val, 3)})
    pairs_df = pd.DataFrame(pairs).sort_values("相関係数", ascending=False)

    with col_pos:
        st.markdown("**🔴 High Positive Correlation (Top5)**")
        st.dataframe(pairs_df.head(5), width="stretch", hide_index=True)

    with col_neg:
        st.markdown("**🔵 High Negative Correlation (Top5)**")
        st.dataframe(pairs_df.tail(5).iloc[::-1],
                     width="stretch", hide_index=True)

    st.caption(f"Updated: {corr_data['updated_at']} / Data points: {n_days} days")


# =====================================================
# [D] センチメント複合スコア
# =====================================================
@st.cache_data(ttl=TTL_DAILY, show_spinner=False)
def compute_composite_sentiment() -> Dict[str, Any]:
    """
    2026年版 改善マルチアセット・センチメントスコア（パーセンタイルランク正規化）

    ① F&G Index (CNN実データ)           重み 12%
    ② VIX水準 (パーセンタイルランク)    重み 10%
    ③ VIX期間構造 (VIX3M/VIX)          重み 10%
    ④ Put/Call比率 (改善フォールバック)  重み  8%
    ⑤ SP500価格モメンタム               重み 10%
    ⑥ Safe Haven需要 (株vs債券)         重み  8%
    ⑦ セクター配分                      重み  8%
    ⑧ 信用リスク選好 (HYG/LQD)         重み  8%
    ⑨ ブレッドス (改善版)               重み  6%
    ⑩ 日経225モメンタム (新規)          重み  8%
    ⑪ ドル円リスク (新規)               重み  6%
    ⑫ 日本実現VIX (新規)                重み  6%
    """
    try:
        end   = datetime.now(timezone.utc)
        start = end - timedelta(days=420)

        def _c(sym):
            try:
                df = yf.Ticker(sym).history(
                    start=start, end=end, interval="1d", auto_adjust=False
                )
                if df is None or df.empty:
                    return pd.Series(dtype=float)
                if df.index.tz is None:
                    df.index = df.index.tz_localize("UTC")
                return df.tz_convert(JST)["Close"].dropna()
            except Exception:
                return pd.Series(dtype=float)

        def _score_clip(x):
            return float(np.clip(x, 0, 100))

        def _pct_rank(series: pd.Series, window: int = 252) -> pd.Series:
            """ローリングパーセンタイルランク (0-100)。固定レンジより市場レジーム変化に強い。"""
            if len(series) < 20:
                return pd.Series(dtype=float)
            return series.rolling(window, min_periods=20).rank(pct=True) * 100

        components = {}

        # ── 共通価格を並列一括取得 ──────────────────────────────
        _sym_keys = [
            "^VIX", "^VIX3M", "^GSPC", "TLT", "HYG", "LQD",
            "XLK", "XLU", "^N225", "USDJPY=X", "VXX",
            "SPY", "QQQ", "IWM", "DIA", "MDY", "^CPC", "^CPCE",
        ]
        with ThreadPoolExecutor(max_workers=8) as _p:
            _futs = {s: _p.submit(_c, s) for s in _sym_keys}
        _cd = {s: _futs[s].result() for s in _sym_keys}

        vix_c    = _cd["^VIX"]
        vix3m_c  = _cd["^VIX3M"]
        sp_c     = _cd["^GSPC"]
        tlt_c    = _cd["TLT"]
        hyg_c    = _cd["HYG"]
        lqd_c    = _cd["LQD"]
        xlk_c    = _cd["XLK"]
        xlu_c    = _cd["XLU"]
        n225_c   = _cd["^N225"]
        usdjpy_c = _cd["USDJPY=X"]
        vxx_c    = _cd["VXX"]
        spy_c    = _cd["SPY"]
        qqq_c    = _cd["QQQ"]
        iwm_c    = _cd["IWM"]
        dia_c    = _cd["DIA"]
        mdy_c    = _cd["MDY"]
        cpc_c    = _cd["^CPC"]
        cpce_c   = _cd["^CPCE"]

        # ① F&G Index (CNN実データ + 履歴も同時取得)
        fg_hist_series = pd.Series(dtype=float)
        try:
            r = requests.get(
                "https://production.dataviz.cnn.io/index/fearandgreed/graphdata/2023-01-01",
                timeout=6, headers={"User-Agent": "Mozilla/5.0"}
            )
            if r.status_code == 200:
                data = r.json()
                fg_score = float(data["fear_and_greed"]["score"])
                hist_raw = data.get("fear_and_greed_historical", {}).get("data", [])
                if hist_raw:
                    fg_dates  = [pd.Timestamp(d["x"], unit="ms", tz="UTC").tz_convert(JST).normalize() for d in hist_raw]
                    fg_values = [float(d["y"]) for d in hist_raw]
                    fg_hist_series = pd.Series(fg_values, index=fg_dates).sort_index()
                    fg_hist_series = fg_hist_series[~fg_hist_series.index.duplicated(keep="last")]
                # normalized にパーセンタイルランクを使用（他指標と統一）
                fg_pct = _pct_rank(fg_hist_series)
                fg_normalized = _score_clip(float(fg_pct.iloc[-1])) if (
                    not fg_pct.empty and not pd.isna(fg_pct.iloc[-1])
                ) else fg_score
                fg_zone = (
                    "Extreme Greed" if fg_score > 75 else
                    "Greed"         if fg_score > 55 else
                    "Neutral"       if fg_score > 45 else
                    "Fear"          if fg_score > 25 else
                    "Extreme Fear"
                )
                components["F&G Index"] = {
                    "score": fg_normalized,
                    "normalized": fg_normalized,
                    "weight": 0.12,
                    "label": f"{fg_zone} (raw:{fg_score:.0f} PctRank:{fg_normalized:.0f}%)",
                    "color": "#1a7f37" if fg_normalized > 55 else ("#d1242f" if fg_normalized < 45 else "#888"),
                }
        except Exception:
            pass

        # ② VIX水準 (パーセンタイルランク, 逆転: 高VIX=恐怖=低スコア)
        if not vix_c.empty and len(vix_c) >= 20:
            vix_pct   = _pct_rank(vix_c)
            vix_score = _score_clip(100 - float(vix_pct.iloc[-1]))
            vix_val   = float(vix_c.iloc[-1])
            components["VIX水準"] = {
                "score": vix_score, "normalized": vix_score, "weight": 0.10,
                "label": f"VIX={vix_val:.1f} (過去1年比:{100-vix_score:.0f}%ile低水準)",
                "color": "#1a7f37" if vix_score > 55 else ("#d1242f" if vix_score < 45 else "#888"),
            }

        # ③ VIX期間構造 (VIX3M/VIX, パーセンタイルランク)
        if not vix_c.empty and not vix3m_c.empty:
            common = vix_c.index.intersection(vix3m_c.index)
            if len(common) >= 20:
                ratio_s   = (vix3m_c.loc[common] / vix_c.loc[common].replace(0, np.nan)).dropna()
                ratio_pct = _pct_rank(ratio_s)
                if not ratio_pct.empty and not pd.isna(ratio_pct.iloc[-1]):
                    term_score = _score_clip(float(ratio_pct.iloc[-1]))
                    components["VIX期間構造"] = {
                        "score": term_score, "normalized": term_score, "weight": 0.10,
                        "label": f"VIX3M/VIX={float(ratio_s.iloc[-1]):.2f} (順順鞘={term_score:.0f}%ile)",
                        "color": "#1a7f37" if term_score > 55 else ("#d1242f" if term_score < 45 else "#888"),
                    }

        # ④ Put/Call比率 (事前取得: cpc_c → cpce_c → VXX代替, パーセンタイルランク, 逆転)
        pc_loaded = False
        for pc_s, pc_lbl in [(cpc_c, "P/C(全体)"), (cpce_c, "P/C(株式)")]:
            if not pc_s.empty and len(pc_s) >= 20:
                pc_pct = _pct_rank(pc_s)
                if not pc_pct.empty and not pd.isna(pc_pct.iloc[-1]):
                    pc_score = _score_clip(100 - float(pc_pct.iloc[-1]))
                    components["Put/Call比率"] = {
                        "score": pc_score, "normalized": pc_score, "weight": 0.08,
                        "label": f"{pc_lbl}={float(pc_s.iloc[-1]):.2f} (恐怖度:{100-pc_score:.0f}%ile)",
                        "color": "#1a7f37" if pc_score > 55 else ("#d1242f" if pc_score < 45 else "#888"),
                    }
                    pc_loaded = True
                    break
        if not pc_loaded and not vxx_c.empty and len(vxx_c) >= 20:
            vxx_pct  = _pct_rank(vxx_c)
            if not vxx_pct.empty and not pd.isna(vxx_pct.iloc[-1]):
                pc_score = _score_clip(100 - float(vxx_pct.iloc[-1]))
                components["Put/Call(VXX代替)"] = {
                    "score": pc_score, "normalized": pc_score, "weight": 0.08,
                    "label": f"VXX恐怖度:{100-pc_score:.0f}%ile（低=安心感強い）",
                    "color": "#1a7f37" if pc_score > 55 else ("#d1242f" if pc_score < 45 else "#888"),
                }

        # ⑤ SP500価格モメンタム (パーセンタイルランク)
        if not sp_c.empty and len(sp_c) >= 25:
            mom20     = (sp_c.pct_change(20) * 100).dropna()
            mom_pct   = _pct_rank(mom20)
            if not mom_pct.empty and not pd.isna(mom_pct.iloc[-1]):
                mom_score = _score_clip(float(mom_pct.iloc[-1]))
                components["価格モメンタム"] = {
                    "score": mom_score, "normalized": mom_score, "weight": 0.10,
                    "label": f"S&P500 20日:{float(mom20.iloc[-1]):+.2f}% (強さ上位{mom_score:.0f}%)",
                    "color": "#1a7f37" if mom_score > 55 else ("#d1242f" if mom_score < 45 else "#888"),
                }

        # ⑥ Safe Haven需要 (SP500 vs TLT, パーセンタイルランク)
        if not sp_c.empty and not tlt_c.empty:
            common = sp_c.index.intersection(tlt_c.index)
            if len(common) >= 25:
                spread    = ((sp_c.loc[common].pct_change(20) - tlt_c.loc[common].pct_change(20)) * 100).dropna()
                sh_pct    = _pct_rank(spread)
                if not sh_pct.empty and not pd.isna(sh_pct.iloc[-1]):
                    sh_score = _score_clip(float(sh_pct.iloc[-1]))
                    components["Safe Haven需要"] = {
                        "score": sh_score, "normalized": sh_score, "weight": 0.08,
                        "label": f"株>債券 差:{float(spread.iloc[-1]):+.2f}%pt（株選好度:{sh_score:.0f}%ile）",
                        "color": "#1a7f37" if sh_score > 55 else ("#d1242f" if sh_score < 45 else "#888"),
                    }

        # ⑦ セクター配分 (XLK vs XLU, パーセンタイルランク)
        if not xlk_c.empty and not xlu_c.empty:
            common = xlk_c.index.intersection(xlu_c.index)
            if len(common) >= 15:
                sec_spread = ((xlk_c.loc[common].pct_change(10) - xlu_c.loc[common].pct_change(10)) * 100).dropna()
                sec_pct    = _pct_rank(sec_spread)
                if not sec_pct.empty and not pd.isna(sec_pct.iloc[-1]):
                    sec_score = _score_clip(float(sec_pct.iloc[-1]))
                    components["セクター配分"] = {
                        "score": sec_score, "normalized": sec_score, "weight": 0.08,
                        "label": f"IT vs 公益:{float(sec_spread.iloc[-1]):+.2f}%（攻め度:{sec_score:.0f}%ile）",
                        "color": "#1a7f37" if sec_score > 55 else ("#d1242f" if sec_score < 45 else "#888"),
                    }

        # ⑧ 信用リスク選好 (HYG vs LQD, パーセンタイルランク)
        if not hyg_c.empty and not lqd_c.empty:
            common = hyg_c.index.intersection(lqd_c.index)
            if len(common) >= 25:
                credit    = ((hyg_c.loc[common].pct_change(20) - lqd_c.loc[common].pct_change(20)) * 100).dropna()
                crd_pct   = _pct_rank(credit)
                if not crd_pct.empty and not pd.isna(crd_pct.iloc[-1]):
                    crd_score = _score_clip(float(crd_pct.iloc[-1]))
                    components["信用リスク選好"] = {
                        "score": crd_score, "normalized": crd_score, "weight": 0.08,
                        "label": f"HY債>IG債:{float(credit.iloc[-1]):+.2f}%（リスク選好度:{crd_score:.0f}%ile）",
                        "color": "#1a7f37" if crd_score > 55 else ("#d1242f" if crd_score < 45 else "#888"),
                    }

        # ⑨ ブレッドス (事前取得ETF5本の平均リターン強度をパーセンタイルランク)
        breadth_parts = []
        for s in [spy_c, qqq_c, iwm_c, dia_c, mdy_c]:
            if not s.empty and len(s) >= 15:
                breadth_parts.append(s.pct_change(10) * 100)
        if len(breadth_parts) >= 2:
            avg_br   = pd.concat(breadth_parts, axis=1).dropna().mean(axis=1)
            br_pct   = _pct_rank(avg_br)
            if not br_pct.empty and not pd.isna(br_pct.iloc[-1]):
                br_score = _score_clip(float(br_pct.iloc[-1]))
                components["ブレッドス"] = {
                    "score": br_score, "normalized": br_score, "weight": 0.06,
                    "label": f"主要ETF平均10日:{float(avg_br.iloc[-1]):+.2f}%（市場全体の広がり:{br_score:.0f}%ile）",
                    "color": "#1a7f37" if br_score > 55 else ("#d1242f" if br_score < 45 else "#888"),
                }

        # ⑩ 日経225モメンタム (新規, パーセンタイルランク)
        if not n225_c.empty and len(n225_c) >= 25:
            n225_mom  = (n225_c.pct_change(20) * 100).dropna()
            n225_pct  = _pct_rank(n225_mom)
            if not n225_pct.empty and not pd.isna(n225_pct.iloc[-1]):
                n225_score = _score_clip(float(n225_pct.iloc[-1]))
                components["日経225モメンタム"] = {
                    "score": n225_score, "normalized": n225_score, "weight": 0.08,
                    "label": f"日経225 20日:{float(n225_mom.iloc[-1]):+.2f}%（強さ:{n225_score:.0f}%ile）",
                    "color": "#1a7f37" if n225_score > 55 else ("#d1242f" if n225_score < 45 else "#888"),
                }

        # ⑪ ドル円リスク (新規: 円高=リスクオフ=低スコア, パーセンタイルランク)
        if not usdjpy_c.empty and len(usdjpy_c) >= 20:
            usdjpy_mom = (usdjpy_c.pct_change(10) * 100).dropna()
            usdjpy_pct = _pct_rank(usdjpy_mom)
            if not usdjpy_pct.empty and not pd.isna(usdjpy_pct.iloc[-1]):
                usdjpy_score = _score_clip(float(usdjpy_pct.iloc[-1]))
                components["ドル円リスク"] = {
                    "score": usdjpy_score, "normalized": usdjpy_score, "weight": 0.06,
                    "label": f"USD/JPY={float(usdjpy_c.iloc[-1]):.2f}（円安方向の強さ:{usdjpy_score:.0f}%ile）",
                    "color": "#1a7f37" if usdjpy_score > 55 else ("#d1242f" if usdjpy_score < 45 else "#888"),
                }

        # ⑫ 日本実現VIX (新規: N225の20日実現ボラティリティ, 逆転)
        if not n225_c.empty and len(n225_c) >= 25:
            n225_rvol  = (n225_c.pct_change() * 100).rolling(20).std() * np.sqrt(252)
            rvol_pct   = _pct_rank(n225_rvol.dropna())
            if not rvol_pct.empty and not pd.isna(rvol_pct.iloc[-1]):
                rvol_score = _score_clip(100 - float(rvol_pct.iloc[-1]))
                components["日本実現VIX"] = {
                    "score": rvol_score, "normalized": rvol_score, "weight": 0.06,
                    "label": f"日経ボラ={float(n225_rvol.iloc[-1]):.1f}%（低いほど安定・強気）",
                    "color": "#1a7f37" if rvol_score > 55 else ("#d1242f" if rvol_score < 45 else "#888"),
                }

        if not components:
            return {"ok": False, "reason": "コンポーネントデータ取得失敗"}

        total_weight = sum(c["weight"] for c in components.values())
        composite = sum(c["normalized"] * c["weight"] for c in components.values()) / total_weight

        # ── 過去履歴：全指標をパーセンタイルランクで日次再計算 ────
        hist_df = pd.DataFrame()
        hist_debug_info = {}
        series_map = {}  # try外で初期化しスコープ問題を防ぐ
        try:
            def _to_naive(ser: pd.Series) -> pd.Series:
                if ser.empty:
                    return ser
                idx = ser.index
                if hasattr(idx, "tz") and idx.tz is not None:
                    idx = idx.tz_localize(None)
                ser = ser.copy()
                ser.index = pd.to_datetime(idx).normalize()
                return ser[~ser.index.duplicated(keep="last")]

            def _pr(series: pd.Series, window: int = 252) -> pd.Series:
                if len(series) < 20:
                    return pd.Series(dtype=float)
                return series.rolling(window, min_periods=20).rank(pct=True) * 100

            sp_n      = _to_naive(sp_c)
            vix_n     = _to_naive(vix_c)
            vix3m_n   = _to_naive(vix3m_c)
            tlt_n     = _to_naive(tlt_c)
            hyg_n     = _to_naive(hyg_c)
            lqd_n     = _to_naive(lqd_c)
            xlk_n     = _to_naive(xlk_c)
            xlu_n     = _to_naive(xlu_c)
            n225_n    = _to_naive(n225_c)
            usdjpy_n  = _to_naive(usdjpy_c)
            vxx_n     = _to_naive(vxx_c)
            hist_debug_info["sp_c_len"]  = len(sp_n)
            hist_debug_info["vix_c_len"] = len(vix_n)

            # ① F&G 実データ（パーセンタイルランク、リアルタイムと同じ手法で統一）
            if not fg_hist_series.empty:
                fg_n = _to_naive(fg_hist_series)
                if len(fg_n) >= 20:
                    series_map["fg_actual"] = (_pr(fg_n).clip(0, 100), 0.12)
                    hist_debug_info["fg_actual_len"] = len(fg_n)
            else:
                # VIX逆転 × モメンタム合成（F&Gと相関が高く、純粋モメンタムと差別化）
                if len(sp_n) >= 25 and len(vix_n) >= 20:
                    common_fg = sp_n.index.intersection(vix_n.index)
                    if len(common_fg) >= 20:
                        sp_m  = np.clip((sp_n.loc[common_fg].pct_change(20) * 100 + 10) / 20 * 100, 0, 100)
                        vix_i = np.clip((35 - vix_n.loc[common_fg]) / (35 - 12) * 100, 0, 100)
                        fg_proxy = (sp_m * 0.6 + vix_i * 0.4).dropna()
                        series_map["fg_proxy"] = (fg_proxy, 0.12)

            # ② VIX水準 (パーセンタイルランク, 逆転)
            if len(vix_n) >= 20:
                series_map["vix"] = ((100 - _pr(vix_n)).clip(0, 100), 0.10)

            # ③ VIX期間構造 (パーセンタイルランク)
            if len(vix_n) >= 20 and len(vix3m_n) >= 20:
                common = vix_n.index.intersection(vix3m_n.index)
                if len(common) >= 20:
                    ratio_n = (vix3m_n.loc[common] / vix_n.loc[common].replace(0, np.nan)).dropna()
                    series_map["vix_term"] = (_pr(ratio_n).clip(0, 100), 0.10)

            # ④ Put/Call (リアルタイムと同データ優先, 逆転)
            cpc_n  = _to_naive(cpc_c)
            cpce_n = _to_naive(cpce_c)
            pc_hist_set = False
            for pc_hn in [cpc_n, cpce_n]:
                if len(pc_hn) >= 20:
                    series_map["put_call"] = ((100 - _pr(pc_hn)).clip(0, 100), 0.08)
                    pc_hist_set = True
                    break
            if not pc_hist_set and len(vxx_n) >= 20:
                series_map["put_call"] = ((100 - _pr(vxx_n)).clip(0, 100), 0.08)

            # ⑤ SP500モメンタム (パーセンタイルランク)
            if len(sp_n) >= 25:
                series_map["momentum"] = (_pr((sp_n.pct_change(20) * 100).dropna()).clip(0, 100), 0.10)

            # ⑥ Safe Haven (パーセンタイルランク)
            if len(sp_n) >= 25 and len(tlt_n) >= 25:
                common = sp_n.index.intersection(tlt_n.index)
                if len(common) >= 25:
                    spread_n = ((sp_n.loc[common].pct_change(20) - tlt_n.loc[common].pct_change(20)) * 100).dropna()
                    series_map["safe_haven"] = (_pr(spread_n).clip(0, 100), 0.08)

            # ⑦ セクター (XLK vs XLU, パーセンタイルランク)
            if len(xlk_n) >= 15 and len(xlu_n) >= 15:
                common = xlk_n.index.intersection(xlu_n.index)
                if len(common) >= 15:
                    sec_n = ((xlk_n.loc[common].pct_change(10) - xlu_n.loc[common].pct_change(10)) * 100).dropna()
                    series_map["sector"] = (_pr(sec_n).clip(0, 100), 0.08)

            # ⑧ 信用リスク (パーセンタイルランク)
            if len(hyg_n) >= 25 and len(lqd_n) >= 25:
                common = hyg_n.index.intersection(lqd_n.index)
                if len(common) >= 25:
                    credit_n = ((hyg_n.loc[common].pct_change(20) - lqd_n.loc[common].pct_change(20)) * 100).dropna()
                    series_map["credit"] = (_pr(credit_n).clip(0, 100), 0.08)

            # ⑨ ブレッドス (事前取得ETFを再利用、二重取得なし)
            br_parts_n = [_to_naive(s) for s in [spy_c, qqq_c, iwm_c, dia_c, mdy_c]]
            br_parts_n = [s.pct_change(10) * 100 for s in br_parts_n if len(s) >= 15]
            if len(br_parts_n) >= 2:
                avg_br_n = pd.concat(br_parts_n, axis=1).dropna().mean(axis=1)
                series_map["breadth"] = (_pr(avg_br_n).clip(0, 100), 0.06)

            # ⑩ 日経225モメンタム (パーセンタイルランク)
            if len(n225_n) >= 25:
                series_map["nikkei_mom"] = (_pr((n225_n.pct_change(20) * 100).dropna()).clip(0, 100), 0.08)

            # ⑪ ドル円リスク (パーセンタイルランク)
            if len(usdjpy_n) >= 20:
                series_map["usdjpy"] = (_pr((usdjpy_n.pct_change(10) * 100).dropna()).clip(0, 100), 0.06)

            # ⑫ 日本実現VIX (パーセンタイルランク, 逆転)
            if len(n225_n) >= 25:
                n225_rv_n = (n225_n.pct_change() * 100).rolling(20).std() * np.sqrt(252)
                series_map["jp_rvol"] = ((100 - _pr(n225_rv_n.dropna())).clip(0, 100), 0.06)

            hist_debug_info["series_map_keys"] = list(series_map.keys())

            if series_map:
                total_w = sum(w for _, w in series_map.values())
                df_parts = {
                    name: ser * (w / total_w)
                    for name, (ser, w) in series_map.items()
                }
                hist_combined = pd.DataFrame(df_parts).ffill().bfill().dropna(how="all")
                hist_score    = hist_combined.sum(axis=1).clip(0, 100)
                hist_df = pd.DataFrame({
                    "date":  pd.to_datetime(hist_score.index),
                    "score": hist_score.values,
                }).tail(1100).reset_index(drop=True)
                hist_debug_info["hist_df_len"] = len(hist_df)
            else:
                hist_debug_info["hist_df_len"] = 0

        except Exception as _hist_e:
            hist_debug_info["error"] = str(_hist_e)
            logger.warning(f"sentiment hist error: {_hist_e}")
            try:
                def _tz_naive_fb(ser):
                    if ser.empty: return ser
                    idx = ser.index
                    if hasattr(idx, "tz") and idx.tz is not None:
                        idx = idx.tz_localize(None)
                    s = ser.copy(); s.index = pd.to_datetime(idx).normalize()
                    return s[~s.index.duplicated(keep="last")]
                sp_fb  = _tz_naive_fb(sp_c)  if not sp_c.empty  else pd.Series(dtype=float)
                vix_fb = _tz_naive_fb(vix_c) if not vix_c.empty else pd.Series(dtype=float)
                if len(sp_fb) >= 30 and len(vix_fb) >= 30:
                    common_h = sp_fb.index.intersection(vix_fb.index)
                    sp_mom   = np.clip((sp_fb.loc[common_h].pct_change(20) * 100 + 10) / 20 * 100, 0, 100)
                    vx_norm  = np.clip((35 - vix_fb.loc[common_h]) / (35 - 12) * 100, 0, 100)
                    hist_score = (sp_mom * 0.55 + vx_norm * 0.45).clip(0, 100)
                    hist_df = pd.DataFrame({
                        "date":  pd.to_datetime(common_h),
                        "score": hist_score.values,
                    }).tail(1100).reset_index(drop=True)
                    hist_debug_info["fallback"] = True
                    hist_debug_info["hist_df_len"] = len(hist_df)
            except Exception:
                pass

        # 履歴最終値をcompositeとして使用 → チャートの最終点と表示スコアが必ず一致
        if not hist_df.empty:
            composite = float(hist_df["score"].iloc[-1])

        # ── 米国 / 日本 サブスコア分離計算 ──────────────────────
        _US_KEYS = {"F&G Index", "VIX水準", "VIX期間構造", "Put/Call比率",
                    "Put/Call(VXX代替)", "価格モメンタム", "Safe Haven需要",
                    "セクター配分", "信用リスク選好", "ブレッドス"}
        _JP_KEYS = {"日経225モメンタム", "ドル円リスク", "日本実現VIX"}

        us_comps = {k: v for k, v in components.items() if k in _US_KEYS}
        jp_comps = {k: v for k, v in components.items() if k in _JP_KEYS}

        _us_tw = sum(c["weight"] for c in us_comps.values())
        _jp_tw = sum(c["weight"] for c in jp_comps.values())
        us_composite = (
            sum(c["normalized"] * c["weight"] for c in us_comps.values()) / _us_tw
            if _us_tw > 0 else 50.0
        )
        jp_composite = (
            sum(c["normalized"] * c["weight"] for c in jp_comps.values()) / _jp_tw
            if _jp_tw > 0 else 50.0
        )

        # ── 米国 / 日本 サブ履歴 ─────────────────────────────────
        _US_HIST = {"fg_actual", "fg_proxy", "vix", "vix_term", "put_call",
                    "momentum", "safe_haven", "sector", "credit", "breadth"}
        _JP_HIST = {"nikkei_mom", "usdjpy", "jp_rvol"}
        us_hist_df = pd.DataFrame()
        jp_hist_df = pd.DataFrame()
        if hist_df.empty is False:  # series_map が構築済みの場合のみ
            try:
                us_map = {k: v for k, v in series_map.items() if k in _US_HIST}
                jp_map = {k: v for k, v in series_map.items() if k in _JP_HIST}
                if us_map:
                    _uw = sum(w for _, w in us_map.values())
                    _ud = pd.DataFrame({n: s * (w / _uw) for n, (s, w) in us_map.items()})
                    _us = _ud.ffill().bfill().dropna(how="all").sum(axis=1).clip(0, 100)
                    us_hist_df = pd.DataFrame({"date": pd.to_datetime(_us.index), "score": _us.values}).tail(1100).reset_index(drop=True)
                if jp_map:
                    _jw = sum(w for _, w in jp_map.values())
                    _jd = pd.DataFrame({n: s * (w / _jw) for n, (s, w) in jp_map.items()})
                    _js = _jd.ffill().bfill().dropna(how="all").sum(axis=1).clip(0, 100)
                    jp_hist_df = pd.DataFrame({"date": pd.to_datetime(_js.index), "score": _js.values}).tail(1100).reset_index(drop=True)
            except Exception as _sub_e:
                logger.warning(f"sub-hist error: {_sub_e}")

        sentiment_label = (
            "Extreme Greed 🤑" if composite > 75 else
            "Greed 😊"          if composite > 55 else
            "Neutral 😐"        if composite > 45 else
            "Fear 😟"           if composite > 25 else
            "Extreme Fear 😱"
        )
        sentiment_color = "#1a7f37" if composite > 55 else ("#d1242f" if composite < 45 else "#888")

        return {
            "ok":            True,
            "composite":     round(float(composite), 1),
            "label":         sentiment_label,
            "color":         sentiment_color,
            "components":    components,
            "us_composite":  round(float(us_composite), 1),
            "jp_composite":  round(float(jp_composite), 1),
            "us_components": us_comps,
            "jp_components": jp_comps,
            "hist_df":       hist_df,
            "us_hist_df":    us_hist_df,
            "jp_hist_df":    jp_hist_df,
            "hist_debug":    hist_debug_info,
            "updated_at":    datetime.now(JST).strftime("%Y-%m-%d %H:%M JST"),
        }

    except Exception as e:
        logger.error(f"compute_composite_sentiment error: {e}")
        return {"ok": False, "reason": str(e)[:200]}

# ===========================
# NAAIM Exposure Index
# ===========================
@st.cache_data(ttl=TTL_DAILY, show_spinner=False)
def fetch_naaim_data() -> pd.DataFrame:
    """
    NAAIM Exposure Index 取得。全体を30秒でハードタイムアウト。
    手法1: 公式ページHTMLからXLSXリンクを動的抽出
    手法2: HTMLテーブルをBeautifulSoupで解析
    """
    import re as _re
    from io import BytesIO
    import signal as _signal

    HARD_TIMEOUT = 8   # 秒（この時間を超えたら諦める）
    _deadline = time.time() + HARD_TIMEOUT

    def _over():
        return time.time() > _deadline

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    def _parse_xlsx(content_bytes: bytes) -> pd.DataFrame:
        df = pd.read_excel(BytesIO(content_bytes), engine="openpyxl")
        df.columns = [str(c).strip() for c in df.columns]
        date_col = next(
            (c for c in df.columns if any(k in c.lower() for k in ["date", "week", "ending"])), None
        )
        if date_col:
            df = df.rename(columns={date_col: "Date"})
            df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
            df = df.dropna(subset=["Date"]).sort_values("Date")
        num_col = next(
            (c for c in df.columns if any(k in c.lower() for k in ["number", "mean", "average", "naaim", "exposure"])), None
        )
        if num_col:
            df = df.rename(columns={num_col: "NAAIM"})
        elif len(df.columns) >= 2:
            df = df.rename(columns={df.columns[1]: "NAAIM"})
        if "NAAIM" not in df.columns or "Date" not in df.columns:
            return pd.DataFrame()
        df["NAAIM"] = pd.to_numeric(df["NAAIM"], errors="coerce")
        df = df.dropna(subset=["NAAIM"])
        return df[["Date", "NAAIM"]].tail(156) if not df.empty else pd.DataFrame()

    # ── 手法1: 公式ページからXLSXリンクを動的抽出 ──────────
    if not _over():
        try:
            remaining = max(2, int(_deadline - time.time()))
            page_url = "https://naaim.org/programs/naaim-exposure-index/"
            r = requests.get(page_url, headers=HEADERS, timeout=min(6, remaining))
            if r.status_code == 200:
                patterns = [
                    r'https://naaim\.org/wp-content/uploads/[^\s"\'<>]+\.xlsx',
                    r'https://naaim\.org/wp-content/uploads/[^\s"\'<>]+\.xls',
                    r'"(https://[^"]+naaim[^"]+\.xlsx)"',
                ]
                xlsx_url = None
                for pat in patterns:
                    m = _re.search(pat, r.text, _re.IGNORECASE)
                    if m:
                        xlsx_url = m.group(1) if m.lastindex else m.group(0)
                        break
                if xlsx_url and not _over():
                    remaining2 = max(4, int(_deadline - time.time()))
                    r2 = requests.get(xlsx_url, headers=HEADERS, timeout=min(6, remaining2))
                    if r2.status_code == 200:
                        df = _parse_xlsx(r2.content)
                        if not df.empty:
                            logger.info(f"[NAAIM] 手法1 成功: {len(df)}件")
                            return df
        except Exception as e:
            logger.warning(f"[NAAIM] 手法1 失敗: {e}")

    # ── 手法2: HTMLテーブルをスクレイピング ──────────────────
    if not _over():
        try:
            remaining = max(2, int(_deadline - time.time()))
            page_url = "https://naaim.org/programs/naaim-exposure-index/"
            r = requests.get(page_url, headers=HEADERS, timeout=min(6, remaining))
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "html.parser")
                for tbl in soup.find_all("table"):
                    if _over():
                        break
                    try:
                        df = pd.read_html(str(tbl))[0]
                        df.columns = [str(c).strip() for c in df.columns]
                        date_col = next((c for c in df.columns if any(k in str(c).lower() for k in ["date", "week"])), None)
                        num_col  = next((c for c in df.columns if any(k in str(c).lower() for k in ["number", "naaim", "exposure", "mean"])), None)
                        if date_col and num_col:
                            df = df.rename(columns={date_col: "Date", num_col: "NAAIM"})
                            df["Date"]  = pd.to_datetime(df["Date"],  errors="coerce")
                            df["NAAIM"] = pd.to_numeric(df["NAAIM"], errors="coerce")
                            df = df.dropna(subset=["Date", "NAAIM"]).sort_values("Date")
                            if len(df) >= 5:
                                logger.info(f"[NAAIM] 手法2 成功: {len(df)}件")
                                return df[["Date", "NAAIM"]].tail(156)
                    except Exception:
                        continue
        except Exception as e:
            logger.warning(f"[NAAIM] 手法2 失敗: {e}")

    logger.error("[NAAIM] 全手法で取得失敗 or タイムアウト")
    return pd.DataFrame()


def render_naaim_section():
    """NAAIM Exposure Index セクション描画（並行取得・遅延表示対応）"""
    st.subheader(t("📡 NAAIM Exposure Index（機関投資家エクスポージャー）",
                   "📡 NAAIM Exposure Index (Institutional Positioning)"))
    st.caption(t(
        "全米アクティブ投資マネージャー協会（NAAIM）が毎週水曜公表。"
        "プロの運用者が実際にどれだけ米株ロングを持っているかを示す逆張り指標。"
        "高値圏（>80）は過熱・天井警戒、低値圏（<30）は恐怖・底値候補。",
        "Published weekly by NAAIM (National Association of Active Investment Managers). "
        "Shows how much net long exposure professional managers carry — a contrarian indicator. "
        ">80: overheated / top risk; <30: fear / potential bottom.",
    ))

    # ─────────────────────────────────────────────────────────
    # セッションキャッシュ方式（rerunループなし）:
    #   初回: スレッドで最大10秒バックグラウンド取得。完了したら描画。
    #   2回目以降: session_stateから即描画（待機ゼロ）。
    # ─────────────────────────────────────────────────────────
    import threading as _threading

    _cache_key  = "_naaim_df_cache"
    _status_key = "_naaim_status"   # None | "fetching" | "done" | "error"

    status = st.session_state.get(_status_key)

    if status in ("done", "error"):
        # キャッシュから即取得（待機なし）
        _cached = st.session_state.get(_cache_key)
        df_naaim = _cached if isinstance(_cached, pd.DataFrame) else pd.DataFrame()

    else:
        # 未取得 or fetching → スレッドで取得して最大10秒待つ（1回限り、rerunなし）
        _result = {"df": None, "done": False}

        def _bg_fetch():
            try:
                _result["df"] = fetch_naaim_data()
            except Exception as _e:
                logger.error(f"[NAAIM] thread error: {_e}")
                _result["df"] = pd.DataFrame()
            finally:
                _result["done"] = True

        _t = _threading.Thread(target=_bg_fetch, daemon=True)
        _t.start()

        with st.spinner("NAAIM データを取得中...（最大10秒）"):
            _t.join(timeout=9)   # 最大10秒で強制打ち切り

        df_naaim = _result["df"] if _result["df"] is not None else pd.DataFrame()

        # セッションキャッシュに保存（次回は即表示）
        st.session_state[_cache_key]  = df_naaim
        st.session_state[_status_key] = "done" if not df_naaim.empty else "error"

    if df_naaim.empty:
        st.warning(
            "⚠️ NAAIMデータを取得できませんでした\n\n"
            "原因: NAAIM公式サイトのXLSXリンク構造変更・ネットワークタイムアウトなど。"
            "しばらく待ってから再読み込みしてください。"
        )
        st.markdown(
            '<a href="https://naaim.org/programs/naaim-exposure-index/" '
            'target="_blank" style="font-size:13px;">📎 NAAIM公式ページで確認する</a>',
            unsafe_allow_html=True,
        )
        return

    # 最新値・前週比
    latest  = float(df_naaim["NAAIM"].iloc[-1])
    prev    = float(df_naaim["NAAIM"].iloc[-2]) if len(df_naaim) >= 2 else None
    q4_avg  = float(df_naaim["NAAIM"].tail(13).mean())  # 直近13週≒1四半期
    latest_date = df_naaim["Date"].iloc[-1].strftime("%Y-%m-%d")

    # 判定ロジック（詳細版）
    if latest >= 90:
        level_label  = "🔴 極度の強気（バブル警戒）"
        level_color  = "#b71c1c"
        level_detail = (
            "**90以上：機関投資家がほぼフルポジション。**\n\n"
            "歴史的にこの水準はバブル的な過熱を示すことが多く、"
            "短期的な天井・急落リスクが非常に高い状態です。\n"
            "逆張り売り・利益確定のタイミングとして警戒が必要です。\n"
            "過去に90超から急落した例：2018年1月（VIXショック直前）、2021年末（インフレ懸念前）。"
        )
    elif latest >= 80:
        level_label  = "🟠 過熱（強気過多・天井警戒）"
        level_color  = "#d1242f"
        level_detail = (
            "**80〜90：強気相場の終盤サイン。**\n\n"
            "アクティブマネージャーの多くが高いロングポジションを保有しており、"
            "新規の買い手が少なくなりつつある状態です。\n"
            "相場の上値が重くなりやすく、何らかのネガティブ材料で"
            "急速に調整が入りやすいフェーズです。\n"
            "慎重にポジション管理し、利益確定を検討する水準です。"
        )
    elif latest >= 60:
        level_label  = "🟡 やや強気（中立上限）"
        level_color  = "#f57c00"
        level_detail = (
            "**60〜80：強気優勢だが過熱ではない。**\n\n"
            "機関投資家は積極的にロングを積んでいますが、まだ過熱域ではありません。\n"
            "トレンド継続中であれば押し目買いが機能しやすい水準です。\n"
            "ただし80に近づくにつれて慎重さを増すのが合理的です。"
        )
    elif latest >= 40:
        level_label  = "⚪ 中立"
        level_color  = "#888888"
        level_detail = (
            "**40〜60：中立域。強気でも弱気でもない。**\n\n"
            "機関投資家がほぼ等分にロング・リスクオフ姿勢を持っており、"
            "相場の方向感が定まっていない状態です。\n"
            "レンジ相場になりやすく、方向性が出るまで待ちの姿勢が有効です。\n"
            "50近辺は歴史的な中央値であり、通常の市場環境を示します。"
        )
    elif latest >= 25:
        level_label  = "🟢 弱気（慎重・リスクオフ）"
        level_color  = "#2e7d32"
        level_detail = (
            "**25〜40：機関投資家が慎重姿勢に転換。弱気相場入りの傾向。**\n\n"
            "アクティブマネージャーの多くがロングポジションを減らし、"
            "現金比率を高めている状態です。\n"
            "この水準は弱気相場（ベア相場）の初期〜中盤に多く見られます。\n"
            "逆張り投資家にとっては「仕込み始め」の検討エリアですが、"
            "さらに下落する可能性もあるため、分割買いが有効です。\n"
            "現在値 **{:.1f}** は過去のベア相場（2022年・2020年3月等）でも見られた水準です。".format(latest)
        )
    elif latest >= 10:
        level_label  = "🟢 強い弱気（パニック・底値候補）"
        level_color  = "#1565c0"
        level_detail = (
            "**10〜25：パニック的な売り・強烈な弱気相場。**\n\n"
            "機関投資家が大幅にポジションを削減しており、恐怖感が市場を支配しています。\n"
            "歴史的に見ると、この水準は**相場の底値圏に近い**ことが多く、"
            "逆張りの買い場として機能しやすいです。\n"
            "ただし経済環境が悪化している場合はさらに下落することもあり、"
            "マクロ環境（景気後退の有無）と合わせて判断が必要です。\n"
            "過去の例：2022年9〜10月（FRB利上げ急加速時）、2020年3月（コロナショック）。"
        )
    else:
        level_label  = "🔵 極度の恐怖（歴史的底値圏）"
        level_color  = "#0d47a1"
        level_detail = (
            "**10以下：歴史的に極めてまれな恐怖水準。**\n\n"
            "機関投資家がほぼポジションをゼロもしくはショートに転換しており、"
            "市場全体がパニック状態に近いです。\n"
            "過去のデータではこの水準から**数ヶ月以内に大幅反発**することが多く、"
            "長期投資家にとっては歴史的な買い場になる可能性が高いです。\n"
            "ただし底値を確認するまでは慎重に、分割・積立での参入が推奨されます。\n"
            "過去の例：2008年リーマンショック直後、2011年欧州債務危機時。"
        )

    col1, col2, col3, col4 = st.columns(4)
    col1.metric(
        f"最新値（{latest_date}）",
        f"{latest:.2f}",
        delta=f"{latest - prev:+.2f}" if prev is not None else None,
        delta_color="normal"
    )
    col2.metric("直近13週平均", f"{q4_avg:.2f}")
    col3.metric("水準判定", level_label)
    col4.metric("データ件数", f"{len(df_naaim)}週")

    # 水準解説カード
    border_color = level_color
    st.markdown(
        f'<div style="background:#f8f9fa;border-left:5px solid {border_color};'
        f'border-radius:6px;padding:14px 18px;margin:12px 0;font-size:13px;line-height:1.7;">'
        f'<strong style="color:{border_color};font-size:14px;">現在の市場環境：{level_label}</strong><br><br>'
        + level_detail.replace("\n", "<br>") +
        f'</div>',
        unsafe_allow_html=True,
    )

    # タブで期間切替
    tab_1y, tab_2y, tab_all = st.tabs(["1年", "2年", "全期間"])

    def _draw_naaim(plot_df: pd.DataFrame, title: str):
        if plot_df.empty:
            st.info("期間内データなし")
            return
        fig, ax = plt.subplots(figsize=(12, 4))
        ax.plot(plot_df["Date"], plot_df["NAAIM"],
                color="#1565c0", linewidth=2, label="NAAIM Number")
        # 2週移動平均
        ma2 = plot_df["NAAIM"].rolling(2).mean()
        ax.plot(plot_df["Date"], ma2,
                color="#e91e63", linewidth=1.5, linestyle="--", label="2-Week MA")
        # ゾーン色分け（固定値）
        ax.axhspan(80, 130, alpha=0.07, color="#d1242f")
        ax.axhspan(60, 80, alpha=0.04, color="#f57c00")
        ax.axhspan(0,  25, alpha=0.07, color="#1a7f37")
        ax.axhspan(-50, 0, alpha=0.10, color="#1565c0")
        # 境界線
        for val, lbl, clr in [(80, "80 過熱", "#d1242f"), (25, "25 弱気", "#2e7d32")]:
            ax.axhline(val, color=clr, linestyle="--", alpha=0.5, linewidth=1)
            ax.text(plot_df["Date"].iloc[0], val + 1, lbl,
                    fontsize=8, color=clr, fontfamily="DejaVu Sans")
        ax.axhline(50, color="gray", linestyle=":", alpha=0.5)
        # 最新値アノテーション
        ax.annotate(
            f"{float(plot_df['NAAIM'].iloc[-1]):.1f}",
            xy=(plot_df["Date"].iloc[-1], float(plot_df["NAAIM"].iloc[-1])),
            xytext=(8, 0), textcoords="offset points",
            fontsize=11, fontweight="bold",
            color=level_color, va="center",
            fontfamily="DejaVu Sans"
        )
        ax.set_ylim(-60, 130)
        ax.set_title(title, fontsize=11)
        ax.set_ylabel("NAAIM Number")
        ax.legend(fontsize=9, loc="upper left")
        ax.grid(True, alpha=0.25)
        plt.xticks(rotation=45)
        plt.tight_layout()
        st.pyplot(fig, clear_figure=True)
        st.caption(
            f"最新: {float(plot_df['NAAIM'].iloc[-1]):.2f} | "
            f"平均: {plot_df['NAAIM'].mean():.2f} | "
            f"最高: {plot_df['NAAIM'].max():.2f} | "
            f"最低: {plot_df['NAAIM'].min():.2f}"
        )

    now = pd.Timestamp.now()
    with tab_1y:
        _draw_naaim(
            df_naaim[df_naaim["Date"] >= now - pd.DateOffset(years=1)],
            "NAAIM Exposure Index (1 Year)"
        )
    with tab_2y:
        _draw_naaim(
            df_naaim[df_naaim["Date"] >= now - pd.DateOffset(years=2)],
            "NAAIM Exposure Index (2 Years)"
        )
    with tab_all:
        _draw_naaim(df_naaim, "NAAIM Exposure Index (All)")

    # 水準早見表
    with st.expander("📋 NAAIM水準早見表（目安）", expanded=False):
        st.markdown("""
| 水準 | 判定 | 意味 | 投資行動の目安 |
|------|------|------|----------------|
| **90以上** | 🔴 極度の強気 | バブル的過熱・フルロング | 利確・ヘッジ検討 |
| **80〜90** | 🟠 過熱 | 天井警戒・買い手枯渇 | 慎重・利確優先 |
| **60〜80** | 🟡 やや強気 | トレンド継続中 | 押し目買い有効 |
| **40〜60** | ⚪ 中立 | 方向感なし | 様子見・レンジ想定 |
| **25〜40** | 🟢 弱気 | ベア相場傾向・慎重 | 分割仕込み検討 |
| **10〜25** | 🟢 強い弱気 | パニック・底値候補 | 逆張り買い検討 |
| **10以下** | 🔵 極度の恐怖 | 歴史的底値圏 | 長期買い場の可能性 |

> **注意**: NAAIMはあくまで機関投資家のポジション調査であり、相場予測指標ではありません。
> 他の指標（VIX・F&G・マクロ環境）と合わせて総合判断してください。
        """)



# =====================================================
# ★ 4指標相関分析 + AIコメント
# =====================================================
@st.cache_data(ttl=TTL_DAILY, show_spinner=False)
def compute_4indicator_correlation(days: int = 90) -> Dict[str, Any]:
    """
    CNN Fear&Greed / AI Sentiment / 日経225 / S&P500
    の過去N日分の相関係数を計算して返す。
    既存のfetch_fear_greed_index()の履歴データを活用。
    """
    try:
        end   = datetime.now(timezone.utc)
        start = end - timedelta(days=days + 60)

        def _get_price(sym):
            try:
                df = yf.Ticker(sym).history(
                    start=start, end=end, interval="1d", auto_adjust=False
                )
                if df is None or df.empty:
                    return pd.Series(dtype=float)
                if df.index.tz is None:
                    df.index = df.index.tz_localize("UTC")
                s = df.tz_convert(JST)["Close"].dropna()
                # 日付のみに正規化・重複除去
                s.index = pd.to_datetime(s.index).normalize().tz_localize(None)
                return s[~s.index.duplicated(keep="last")]
            except Exception:
                return pd.Series(dtype=float)

        nk = _get_price("^N225")
        sp = _get_price("^GSPC")

        if nk.empty or sp.empty:
            return {"ok": False, "reason": "価格データ取得失敗"}

        # ── AI Sentiment履歴（ベクトル演算版） ─────────────────
        ai_sent_df = build_prediction_history_from_market(days=days + 30)
        if not isinstance(ai_sent_df, pd.DataFrame) or ai_sent_df.empty:
            return {"ok": False, "reason": "AI Sentimentデータ取得失敗"}

        ai_sent_df = ai_sent_df.copy()
        ai_sent_df["date"] = pd.to_datetime(ai_sent_df["date"]).dt.normalize()
        ai_sent_df = ai_sent_df.drop_duplicates(subset=["date"], keep="last")
        ai_s = ai_sent_df.set_index("date")["prob_up"].rename("AI_Sentiment")
        logger.info(f"[4corr] AI Sentiment: {len(ai_s)}件")

        # ── CNN F&G履歴（既存関数のstart_dateつきAPIを使用） ───
        fg_series = pd.Series(dtype=float)
        try:
            start_date = (datetime.now() - timedelta(days=days + 60)).strftime("%Y-%m-%d")
            r = requests.get(
                f"https://production.dataviz.cnn.io/index/fearandgreed/graphdata/{start_date}",
                timeout=10,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            )
            if r.status_code == 200:
                data = r.json()
                hist = data.get("fear_and_greed_historical", {}).get("data", [])
                if hist:
                    rows = []
                    for h in hist:
                        try:
                            ts = pd.to_datetime(int(h["x"]), unit="ms").normalize()
                            rows.append({"date": ts, "fg": float(h["y"])})
                        except Exception:
                            continue
                    if rows:
                        fg_df = pd.DataFrame(rows)
                        fg_df = fg_df.drop_duplicates(subset=["date"], keep="last")
                        fg_df = fg_df.set_index("date")
                        fg_series = fg_df["fg"].rename("CNN_FearGreed")
                        logger.info(f"[4corr] CNN F&G: {len(fg_series)}件")
        except Exception as e:
            logger.warning(f"[4corr] CNN F&G取得失敗: {e}")

        # ── 全指標をDataFrameに結合 ──────────────────────────
        cutoff = pd.Timestamp.now() - pd.Timedelta(days=days)

        df = pd.DataFrame({"日経平均225": nk, "S&P500": sp})
        df = df[df.index >= cutoff]
        df.index = pd.to_datetime(df.index).normalize()

        # AI Sentiment を left join
        df = df.join(ai_s, how="left")

        # CNN F&G を left join
        if not fg_series.empty:
            fg_series.index = pd.to_datetime(fg_series.index).normalize()
            df = df.join(fg_series, how="left")
        else:
            df["CNN_FearGreed"] = np.nan

        # 前埋め補完（週末・祝日のギャップ対策）
        df = df.ffill().bfill()

        # 必須列でdropna
        df = df.dropna(subset=["日経平均225", "S&P500", "AI_Sentiment"])
        logger.info(f"[4corr] 結合後: {len(df)}行, CNN F&G有効: {df['CNN_FearGreed'].notna().sum()}行")

        if len(df) < 5:
            return {"ok": False, "reason": f"データ不足（{len(df)}日分）"}

        # ── 相関係数計算 ─────────────────────────────────────
        # 価格系→日次リターン、スコア系→変化量
        df_ret = df.copy()
        for col in ["日経平均225", "S&P500"]:
            df_ret[col] = df[col].pct_change() * 100
        for col in ["AI_Sentiment", "CNN_FearGreed"]:
            if col in df_ret.columns:
                df_ret[col] = df[col].diff()

        df_ret = df_ret.dropna(subset=["日経平均225", "S&P500", "AI_Sentiment"])

        # CNN F&Gが全欠損なら列ごと除外して計算
        valid_cols = [c for c in df_ret.columns if df_ret[c].notna().sum() >= 5]
        corr_ret   = df_ret[valid_cols].corr()
        corr_level = df[valid_cols].corr()

        # 最新値
        latest = {}
        for col in df.columns:
            s = df[col].dropna()
            if not s.empty:
                latest[col] = float(s.iloc[-1])

        return {
            "ok":          True,
            "corr_ret":    corr_ret,
            "corr_level":  corr_level,
            "df":          df,
            "df_ret":      df_ret,
            "n_days":      len(df_ret),
            "latest":      latest,
            "valid_cols":  valid_cols,
            "updated_at":  datetime.now(JST).strftime("%Y-%m-%d %H:%M JST"),
        }

    except Exception as e:
        logger.error(f"compute_4indicator_correlation error: {e}", exc_info=True)
        return {"ok": False, "reason": str(e)[:300]}


def render_4indicator_correlation():
    """4指標相関分析 + AIコメント セクション"""
    st.header("🔗 センチメント × 株価 相関分析")
    st.markdown(
        '<div style="background:linear-gradient(135deg,#e8f5e9,#e3f2fd);'
        'border-left:4px solid #1976d2;border-radius:6px;padding:10px 16px;'
        'font-size:13px;color:#333;margin-bottom:14px;">'
        "<strong>4指標</strong>の相互関係を分析。"
        "CNN Fear&Greed Index・AI Sentiment Index・日経平均225・S&P500の"
        "相関係数と時系列推移をインタラクティブに表示します。"
        "</div>",
        unsafe_allow_html=True,
    )

    period = st.radio(
        "分析期間", ["30日", "60日", "90日", "180日"],
        index=2, horizontal=True, key="corr4_period"
    )
    period_days = {"30日": 30, "60日": 60, "90日": 90, "180日": 180}[period]

    with st.spinner("データ取得・相関計算中..."):
        result = compute_4indicator_correlation(days=period_days)

    if not result.get("ok"):
        st.error(f"取得失敗: {result.get('reason')}")
        return

    corr_ret   = result["corr_ret"]
    corr_level = result["corr_level"]
    df         = result["df"]
    df_ret     = result["df_ret"]
    n_days     = result["n_days"]
    latest     = result["latest"]

    # ── 最新値サマリー ─────────────────────────────────────
    cols_latest = st.columns(4)
    labels = {
        "日経平均225":    ("🇯🇵 日経平均", "{:,.0f}"),
        "S&P500":        ("🇺🇸 S&P500",   "{:,.0f}"),
        "AI_Sentiment":  ("🧠 AI Sentiment", "{:.1f}"),
        "CNN_FearGreed": ("😱 CNN F&G",   "{:.1f}"),
    }
    for i, (col_key, (label, fmt)) in enumerate(labels.items()):
        if col_key in latest:
            cols_latest[i].metric(label, fmt.format(latest[col_key]))

    st.markdown(f"*分析期間: 直近 {n_days} 営業日 / 更新: {result['updated_at']}*")
    st.divider()

    # ── タブ構成 ──────────────────────────────────────────
    tab_heat, tab_scatter, tab_ts, tab_ai = st.tabs([
        "🟥 相関ヒートマップ",
        "📐 散布図マトリクス",
        "📈 時系列推移",
        "🤖 AIコメント",
    ])

    col_names_jp = {
        "日経平均225":    "日経225",
        "S&P500":        "S&P500",
        "AI_Sentiment":  "AI Sentiment",
        "CNN_FearGreed": "CNN F&G",
    }

    # ── ① 相関ヒートマップ（Plotly） ──────────────────────
    with tab_heat:
        st.markdown("**変化量ベースの相関係数**（日次リターン・スコア変化量）")

        if PLOTLY_AVAILABLE:
            import plotly.figure_factory as ff

            corr_disp = corr_ret.rename(
                index=col_names_jp, columns=col_names_jp
            )
            z    = corr_disp.values.tolist()
            x    = list(corr_disp.columns)
            y    = list(corr_disp.index)
            text = [[f"{v:.2f}" for v in row] for row in corr_disp.values]

            fig = go.Figure(go.Heatmap(
                z=corr_disp.values,
                x=x, y=y,
                text=text,
                texttemplate="%{text}",
                colorscale="RdBu_r",
                zmid=0, zmin=-1, zmax=1,
                colorbar=dict(title="相関係数"),
                hovertemplate=(
                    "%{y} × %{x}<br>相関係数: <b>%{z:.3f}</b><extra></extra>"
                ),
            ))
            fig.update_layout(
                title=f"相関ヒートマップ（直近{n_days}日・変化量ベース）",
                height=420,
                plot_bgcolor="white",
                paper_bgcolor="white",
                margin=dict(l=100, r=20, t=50, b=80),
                xaxis=dict(tickangle=-30),
            )
            st.plotly_chart(fig, width="stretch")
        else:
            st.dataframe(corr_ret.rename(index=col_names_jp, columns=col_names_jp).round(3),
                         width="stretch")

        # 解釈ガイド
        st.markdown("""
**相関係数の見方:**
- **+0.7以上** 🔴 強い正の相関（同じ方向に動きやすい）
- **+0.3〜0.7** 🟠 やや正の相関
- **-0.3〜+0.3** ⚪ ほぼ無相関
- **-0.3〜-0.7** 🔵 やや負の相関
- **-0.7以下** 💙 強い負の相関（逆方向に動きやすい）
        """)

    # ── ② 散布図 ──────────────────────────────────────────
    with tab_scatter:
        st.markdown("**2指標の散布図（変化量ベース）**")

        pairs_options = [
            ("AI_Sentiment", "日経平均225"),
            ("AI_Sentiment", "S&P500"),
            ("CNN_FearGreed", "日経平均225"),
            ("CNN_FearGreed", "S&P500"),
            ("CNN_FearGreed", "AI_Sentiment"),
            ("日経平均225", "S&P500"),
        ]
        pair_labels = [
            f"{col_names_jp.get(a,a)} × {col_names_jp.get(b,b)}"
            for a, b in pairs_options
        ]
        sel = st.selectbox("表示ペア", pair_labels, key="corr4_pair")
        sel_idx = pair_labels.index(sel)
        col_x, col_y = pairs_options[sel_idx]

        if col_x in df_ret.columns and col_y in df_ret.columns:
            sc_df = df_ret[[col_x, col_y]].dropna().copy()

            if len(sc_df) < 5:
                st.warning(f"⚠️ {sel} のデータが不足しています（{len(sc_df)}件）。別のペアを選択してください。")
            else:
                sc_df["date"] = sc_df.index.strftime("%Y-%m-%d")
                r_val = float(sc_df[[col_x, col_y]].corr().iloc[0, 1])

                if PLOTLY_AVAILABLE:
                    x_arr = sc_df[col_x].values
                    y_arr = sc_df[col_y].values

                    # 回帰直線（データが有効な場合のみ）
                    try:
                        coef   = np.polyfit(x_arr, y_arr, 1)
                        x_line = np.linspace(x_arr.min(), x_arr.max(), 100)
                        y_line = np.polyval(coef, x_line)
                        has_trendline = True
                    except Exception:
                        has_trendline = False

                    fig = go.Figure()
                    fig.add_trace(go.Scatter(
                        x=sc_df[col_x], y=sc_df[col_y],
                        mode="markers",
                        marker=dict(size=6, color="#1976d2", opacity=0.6),
                        hovertemplate=(
                            f"{col_names_jp.get(col_x,col_x)}: %{{x:.2f}}<br>"
                            f"{col_names_jp.get(col_y,col_y)}: %{{y:.2f}}<br>"
                            "日付: %{customdata}<extra></extra>"
                        ),
                        customdata=sc_df["date"],
                        name="実データ",
                    ))
                    if has_trendline:
                        fig.add_trace(go.Scatter(
                            x=x_line, y=y_line,
                            mode="lines",
                            line=dict(color="#e53935", width=2, dash="dash"),
                            name=f"回帰直線 (r={r_val:.2f})",
                            hoverinfo="skip",
                        ))
                    fig.update_layout(
                        title=f"{sel}　相関係数: {r_val:.3f}",
                        xaxis_title=col_names_jp.get(col_x, col_x),
                        yaxis_title=col_names_jp.get(col_y, col_y),
                        plot_bgcolor="white",
                        paper_bgcolor="white",
                        hovermode="closest",
                        height=400,
                        margin=dict(l=60, r=20, t=50, b=50),
                    )
                    st.plotly_chart(fig, width="stretch")
        else:
            st.warning(f"⚠️ 選択したペアの一方または両方のデータがありません: {col_x}, {col_y}")

    # ── ③ 時系列推移 ──────────────────────────────────────
    with tab_ts:
        st.markdown("**4指標の正規化推移（0〜100スケール）**")

        df_norm = df.copy()
        for col in df_norm.columns:
            mn, mx = df_norm[col].min(), df_norm[col].max()
            if mx > mn:
                df_norm[col] = (df_norm[col] - mn) / (mx - mn) * 100

        if PLOTLY_AVAILABLE:
            colors = {
                "日経平均225":    "#1565c0",
                "S&P500":        "#2e7d32",
                "AI_Sentiment":  "#9c27b0",
                "CNN_FearGreed": "#e65100",
            }
            fig = go.Figure()
            for col in df_norm.columns:
                fig.add_trace(go.Scatter(
                    x=df_norm.index,
                    y=df_norm[col],
                    mode="lines",
                    line=dict(color=colors.get(col, "#888"), width=1.8),
                    name=col_names_jp.get(col, col),
                    hovertemplate=(
                        f"{col_names_jp.get(col,col)}<br>"
                        "日付: %{x|%Y-%m-%d}<br>"
                        "正規化値: %{y:.1f}<extra></extra>"
                    ),
                ))
            fig.update_layout(
                title=f"4指標の正規化推移（直近{n_days}日）",
                yaxis=dict(title="正規化スコア（0〜100）",
                           gridcolor="rgba(200,200,200,0.3)"),
                xaxis=dict(gridcolor="rgba(200,200,200,0.2)"),
                plot_bgcolor="white",
                paper_bgcolor="white",
                hovermode="x unified",
                hoverlabel=dict(bgcolor="white", font_size=12),
                legend=dict(orientation="h", y=1.08),
                height=420,
                margin=dict(l=60, r=20, t=60, b=40),
            )
            st.plotly_chart(fig, width="stretch")

    # ── ④ AIコメント ──────────────────────────────────────
    with tab_ai:
        st.markdown("**🤖 相関分析AIコメント**")

        # 相関係数サマリーを文字列化
        corr_summary = []
        cols = list(corr_ret.columns)
        for i in range(len(cols)):
            for j in range(i+1, len(cols)):
                a = col_names_jp.get(cols[i], cols[i])
                b = col_names_jp.get(cols[j], cols[j])
                v = float(corr_ret.iloc[i, j])
                strength = (
                    "強い正の相関" if v > 0.6 else
                    "やや正の相関" if v > 0.3 else
                    "ほぼ無相関"   if v > -0.3 else
                    "やや負の相関" if v > -0.6 else
                    "強い負の相関"
                )
                corr_summary.append(f"・{a} × {b}: {v:.3f}（{strength}）")

        latest_summary = []
        for k, v in latest.items():
            lbl = col_names_jp.get(k, k)
            latest_summary.append(f"・{lbl}: {v:.1f}")

        prompt = f"""あなたは金融アナリストです。以下の市場データの相関分析結果を元に、
投資家向けの日本語コメントを書いてください。

【分析期間】直近{n_days}営業日（{period}）

【現在の各指標水準】
{chr(10).join(latest_summary)}

【相関係数（日次変化量ベース）】
{chr(10).join(corr_summary)}

以下の形式で200〜300字で簡潔にまとめてください：
1. 最も注目すべき相関関係（強い or 意外な関係）
2. 現在の指標水準から読み取れる市場環境
3. 投資家への示唆（1〜2点）

専門用語は使いすぎず、わかりやすく書いてください。""" + _lang_prompt_suffix()

        _corr4_key = _lang_key("corr4_ai_comment")
        _corr4_model_key = _lang_key("corr4_ai_model")
        if st.button(t("🤖 AIコメントを生成", "🤖 Generate AI Comment"), key="corr4_ai_btn", type="primary"):
            with st.spinner(t("AI分析中...", "Analyzing with AI...")):
                try:
                    comment, used_model = call_ai_with_fallback(
                        prompt,
                        max_output_tokens=600,
                        temperature=0.5,
                    )
                    st.session_state[_corr4_key] = comment
                    st.session_state[_corr4_model_key] = used_model
                except Exception as e:
                    st.error(f"AI生成エラー: {e}")

        if _corr4_key in st.session_state:
            st.markdown(
                f'<div style="background:#f8f9ff;border-left:4px solid #1976d2;'
                f'border-radius:6px;padding:16px 20px;font-size:14px;'
                f'line-height:1.8;color:#222;margin-top:12px;">'
                f'{st.session_state[_corr4_key].replace(chr(10),"<br>")}'
                f'</div>',
                unsafe_allow_html=True,
            )
            if st.session_state.get(_corr4_model_key):
                st.caption(f"🤖 AI: {st.session_state[_corr4_model_key]}")

        # 相関係数テーブルも表示
        st.markdown("**相関係数一覧（参考）**")
        corr_disp2 = corr_ret.rename(index=col_names_jp, columns=col_names_jp).round(3)
        st.dataframe(corr_disp2, width="stretch")


@st.cache_data(ttl=3600, show_spinner=False)
def compute_crisis_pattern_similarity() -> Dict[str, Any]:
    """
    現在の市場状態を複数指標で評価し、過去の暴落パターンと比較する。
    VIX単体ではなく:
      ・VIXの上がり方（速度・加速度）
      ・セクターローテーションの方向性
      ・為替（ドル円）の動き
      ・信用スプレッド（HYG vs LQD）
      ・債券 vs 株式のパフォーマンス
      ・10年金利の動向
    を複合的にスコアリングして類似度を算出。
    """
    try:
        end   = datetime.now(timezone.utc)
        start = end - timedelta(days=100)

        def _g(sym):
            try:
                df = yf.Ticker(sym).history(start=start, end=end, interval="1d", auto_adjust=False)
                if df is None or df.empty: return pd.Series(dtype=float)
                if df.index.tz is None: df.index = df.index.tz_localize("UTC")
                return df.tz_convert(JST)["Close"].dropna()
            except: return pd.Series(dtype=float)

        _cps_keys = ["^VIX", "^VIX3M", "^GSPC", "TLT", "HYG", "LQD",
                     "USDJPY=X", "^TNX", "XLK", "XLU", "XLF", "XLP"]
        with ThreadPoolExecutor(max_workers=6) as _p:
            _cf = {s: _p.submit(_g, s) for s in _cps_keys}
        _cg = {s: _cf[s].result() for s in _cps_keys}
        vix    = _cg["^VIX"];   vix3m  = _cg["^VIX3M"]
        sp     = _cg["^GSPC"];  tlt    = _cg["TLT"]
        hyg    = _cg["HYG"];    lqd    = _cg["LQD"]
        usdjpy = _cg["USDJPY=X"]; tnx  = _cg["^TNX"]
        xlk    = _cg["XLK"];    xlu    = _cg["XLU"]
        xlf    = _cg["XLF"];    xlp    = _cg["XLP"]

        def _ret(s, n):
            if len(s) >= n + 1:
                return (float(s.iloc[-1]) / float(s.iloc[-(n+1)]) - 1) * 100
            return None

        # 現在の市場状態ベクトル
        S = {}
        if len(vix) >= 21:
            S["vix_level"]   = float(vix.iloc[-1])
            S["vix_vel_20"]  = _ret(vix, 20)   # 20日でのVIX上昇率
            S["vix_vel_5"]   = _ret(vix, 5)    # 5日でのVIX上昇率（急騰感）
        if len(vix) >= 2 and len(vix3m) >= 2:
            c = vix.index.intersection(vix3m.index)
            if len(c) >= 2:
                S["vix_term"] = float(vix3m.loc[c[-1]]) / float(vix.loc[c[-1]])
                # >1.0=順イールド(安定), <1.0=逆イールド(パニック)
        if len(sp) >= 21:
            S["sp_20d"] = _ret(sp, 20)
            S["sp_5d"]  = _ret(sp, 5)
        if len(tlt) >= 21 and len(sp) >= 21:
            t = _ret(tlt, 20); s = _ret(sp, 20)
            if t is not None and s is not None:
                S["bond_vs_eq"] = t - s   # 正=債券逃避(リスクオフ)
        if len(hyg) >= 21 and len(lqd) >= 21:
            h = _ret(hyg, 20); l = _ret(lqd, 20)
            if h is not None and l is not None:
                S["credit_stress"] = l - h   # 正=信用収縮(LQD > HYG)
        if len(usdjpy) >= 21:
            S["usdjpy_20d"] = _ret(usdjpy, 20)   # 負=円高(リスクオフ)
        if len(tnx) >= 21:
            S["tnx_chg"] = float(tnx.iloc[-1]) - float(tnx.iloc[-21])
            S["tnx_level"] = float(tnx.iloc[-1])
        if len(xlk) >= 21 and len(xlu) >= 21:
            k = _ret(xlk, 20); u = _ret(xlu, 20)
            if k is not None and u is not None:
                S["growth_vs_def"] = k - u   # 負=ディフェンシブ優位
        if len(xlf) >= 21 and len(xlp) >= 21:
            f = _ret(xlf, 20); p = _ret(xlp, 20)
            if f is not None and p is not None:
                S["fin_vs_stap"] = f - p   # 負=生活必需品優位(金融弱い)

        # 過去の危機パターン定義
        # (expected_value, neutral_value, weight, label)
        # neutral=平常時の値, expected=その危機の典型値
        PATTERNS = {
            "リーマンショック\n2008-09": {
                "color": "#dc2626", "emoji": "🏦",
                "period": "2008年9月〜2009年3月",
                "sp_peak_loss": -56.8,
                "description": "金融機関が連鎖破綻。信用市場が完全機能停止。VIXは数ヶ月かけ段階的に上昇→急騰。",
                "key_signals": [
                    "信用スプレッドの急拡大（最重要）",
                    "金融株が市場に先行して崩壊",
                    "VIXは急騰ではなく段階的上昇（数ヶ月）",
                    "円が急騰（ドル円が90円台に）",
                    "公益・生活必需品が相対的に堅調",
                ],
                "sig": {
                    # factor: (expected, neutral, weight)
                    "vix_level":    (55,  15,   2.0),
                    "vix_vel_20":   (80,  0,    2.0),   # 段階的上昇
                    "vix_vel_5":    (15,  0,    1.0),   # 週次は穏やか
                    "vix_term":     (0.75, 1.15, 2.0),  # 逆イールド
                    "sp_20d":       (-18, 1,    3.0),
                    "bond_vs_eq":   (15,  -2,   2.0),   # 国債逃避
                    "credit_stress":(12,  -1,   4.0),   # ★最重要
                    "usdjpy_20d":   (-8,  0,    3.0),   # 円高
                    "growth_vs_def":(-15, 0,    2.0),
                    "fin_vs_stap":  (-20, 0,    3.0),   # 金融崩壊
                },
            },
            "コロナショック\n2020年2-3月": {
                "color": "#7c3aed", "emoji": "🦠",
                "period": "2020年2月〜4月",
                "sp_peak_loss": -33.9,
                "description": "全資産同時暴落。VIXが史上最速で1ヶ月に5倍。FRBの無制限QEでV字回復。",
                "key_signals": [
                    "VIXが数週間で急騰（コロナ最大の特徴）",
                    "全セクターが同時下落（逃げ場なし）",
                    "最初はドル高→その後ドル安",
                    "国債も一時売られた（流動性危機）",
                    "V字回復した唯一の危機",
                ],
                "sig": {
                    "vix_level":    (65,  15,   2.0),
                    "vix_vel_20":   (300, 0,    5.0),   # ★超急騰が最大の特徴
                    "vix_vel_5":    (80,  0,    4.0),   # 数日単位でも急騰
                    "vix_term":     (0.6, 1.15, 3.0),
                    "sp_20d":       (-30, 1,    3.0),
                    "credit_stress":(8,   -1,   2.0),
                    "usdjpy_20d":   (-4,  0,    1.0),
                    "growth_vs_def":(-3,  0,    1.0),   # 全面安で差が小さい
                },
            },
            "ITバブル崩壊\n2001-02": {
                "color": "#0284c7", "emoji": "💻",
                "period": "2001年〜2002年",
                "sp_peak_loss": -49.1,
                "description": "テック株先行崩壊。バリュー・ディフェンシブは堅調。VIXはゆっくり上昇。長期低迷。",
                "key_signals": [
                    "ITテック株の先行下落（成長株崩壊）",
                    "バリュー株・高配当株が相対的に強い",
                    "VIXはゆっくり上昇（急騰なし）",
                    "生活必需品・ヘルスケアが強い",
                    "下落が2年以上続く長期低迷",
                ],
                "sig": {
                    "vix_level":    (35,  15,   1.5),
                    "vix_vel_20":   (25,  0,    1.0),   # ゆっくり
                    "vix_vel_5":    (3,   0,    0.5),   # 週次は穏やか
                    "vix_term":     (0.9, 1.15, 1.0),
                    "sp_20d":       (-8,  1,    2.0),
                    "bond_vs_eq":   (10,  -2,   2.0),
                    "credit_stress":(3,   -1,   1.5),
                    "usdjpy_20d":   (-2,  0,    1.0),
                    "growth_vs_def":(-22, 0,    5.0),   # ★グロース崩壊が最大の特徴
                    "fin_vs_stap":  (-5,  0,    2.0),
                },
            },
            "金利上昇ショック\n2022年": {
                "color": "#b45309", "emoji": "📈",
                "period": "2022年",
                "sp_peak_loss": -24.5,
                "description": "FRBの急利上げで株も国債も同時下落。ドル高・円安急進。エネルギーが独り勝ち。",
                "key_signals": [
                    "株と国債が同時下落（両方に逃げ場なし）",
                    "ドル高・円安が急進（ドル円150円台）",
                    "10年金利が急上昇（1%→4%超）",
                    "VIXは中程度（30-40）で急騰なし",
                    "エネルギー・素材株がアウトパフォーム",
                ],
                "sig": {
                    "vix_level":    (30,  15,   1.5),
                    "vix_vel_20":   (20,  0,    1.0),
                    "vix_term":     (0.92,1.15, 1.0),
                    "sp_20d":       (-8,  1,    2.0),
                    "bond_vs_eq":   (-8,  -2,   4.0),   # ★国債も下落（逆転）
                    "credit_stress":(4,   -1,   1.5),
                    "usdjpy_20d":   (7,   0,    4.0),   # ★円安ドル高
                    "tnx_chg":      (0.6, 0,    4.0),   # ★金利上昇
                    "growth_vs_def":(-10, 0,    2.0),
                },
            },
            "強気相場\n（平常）": {
                "color": "#15803d", "emoji": "🚀",
                "period": "上昇トレンド期",
                "sp_peak_loss": 0,
                "description": "低VIX・信用安定・グロース優位の典型的な強気相場。",
                "key_signals": [
                    "VIXが低水準かつ低下傾向",
                    "グロース・テック株がリード",
                    "信用スプレッドが安定・縮小",
                    "株式 > 債券のリターン",
                    "ドル安・円安が穏やかに推移",
                ],
                "sig": {
                    "vix_level":    (15,  30,   2.0),   # 低VIXが平常
                    "vix_vel_20":   (-15, 5,    2.0),   # VIX低下
                    "vix_term":     (1.2, 0.9,  2.0),   # 順イールド
                    "sp_20d":       (8,   -2,   3.0),
                    "bond_vs_eq":   (-8,  2,    2.0),   # 株>債券
                    "credit_stress":(-3,  1,    2.0),   # 信用良好
                    "growth_vs_def":(10,  -2,   3.0),   # グロース優位
                },
            },
        }

        def _match(current, expected, neutral, weight):
            """0-1のマッチスコアを計算"""
            if current is None: return None
            span = expected - neutral
            if abs(span) < 0.001: return 1.0 if abs(current - expected) < 0.001 else 0.0
            score = (current - neutral) / span
            return float(np.clip(score, 0.0, 1.0)), weight

        # 類似度計算
        similarities = {}
        for name, pat in PATTERNS.items():
            weighted_sum = 0.0
            total_w = 0.0
            matched_factors = []
            mismatched_factors = []

            for factor, (exp, neu, w) in pat["sig"].items():
                if factor not in S or S[factor] is None: continue
                span = exp - neu
                if abs(span) < 0.001: continue
                score = float(np.clip((S[factor] - neu) / span, 0.0, 1.0))
                weighted_sum += score * w
                total_w += w
                factor_jp = {
                    "vix_level": "VIX水準", "vix_vel_20": "VIX上昇速度(20日)",
                    "vix_vel_5": "VIX急騰感(5日)", "vix_term": "VIX期間構造",
                    "sp_20d": "S&P500下落率", "sp_5d": "S&P500短期",
                    "bond_vs_eq": "債券vs株式", "credit_stress": "信用スプレッド",
                    "usdjpy_20d": "ドル円方向", "tnx_chg": "10年金利変化",
                    "tnx_level": "金利水準", "growth_vs_def": "グロースvsディフェンシブ",
                    "fin_vs_stap": "金融vs生活必需品",
                }.get(factor, factor)
                if score >= 0.6:
                    matched_factors.append(f"✅ {factor_jp}({score:.0%}一致)")
                elif score <= 0.2:
                    mismatched_factors.append(f"❌ {factor_jp}(不一致)")

            sim = (weighted_sum / total_w * 100) if total_w > 0 else 0
            similarities[name] = {
                **{k: v for k, v in pat.items() if k != "sig"},
                "score": round(sim, 1),
                "matched": matched_factors[:4],
                "mismatched": mismatched_factors[:2],
            }

        ranked = sorted(similarities.items(), key=lambda x: -x[1]["score"])

        return {
            "ok": True,
            "state": S,
            "ranked": ranked,
            "updated_at": datetime.now(JST).strftime("%Y-%m-%d %H:%M JST"),
        }

    except Exception as e:
        logger.error(f"compute_crisis_pattern_similarity: {e}")
        return {"ok": False, "reason": str(e)[:200]}


@st.cache_data(ttl=1800, show_spinner=False)
def _fetch_summary_prices() -> Dict[str, Any]:
    """サマリー用の主要価格データを取得（キャッシュ30分）。直近15営業日の履歴も含む。"""
    out = {}
    TREND_DAYS = 15  # トレンドチャート用営業日数
    try:
        end   = datetime.now(timezone.utc)
        start = end - timedelta(days=30)  # 休場考慮で多めに取得
        for sym, key in [
            ("^GSPC","sp"), ("^N225","nk"), ("^VIX","vix"),
            ("^TNX","tnx"), ("DX=F","dxy"),
            ("YM=F","dow_f"), ("NQ=F","ndx_f"), ("NKD=F","nk_f"),
        ]:
            try:
                df = yf.Ticker(sym).history(start=start, end=end, interval="1d", auto_adjust=False)
                if df is not None and not df.empty:
                    c = df["Close"].dropna()
                    if len(c) >= 2:
                        out[key]           = float(c.iloc[-1])
                        out[f"{key}_chg1"] = (float(c.iloc[-1]) / float(c.iloc[-2]) - 1) * 100
                    if len(c) >= 6:
                        out[f"{key}_chg5"] = (float(c.iloc[-1]) / float(c.iloc[-6]) - 1) * 100
                    # 直近 TREND_DAYS 日分の履歴を保存
                    c_tail = c.iloc[-TREND_DAYS:]
                    out[f"{key}_dates"]  = [str(d.date()) for d in c_tail.index]
                    out[f"{key}_series"] = [float(v) for v in c_tail.values]
            except Exception:
                pass
    except Exception:
        pass
    return out


# =====================================================
# モメンタムランキング（日経225 / ナスダック）
# =====================================================

_NK225_STOCKS = {
    "7203.T": "トヨタ自動車",    "6758.T": "ソニーグループ",
    "8306.T": "三菱UFJ FG",      "9432.T": "NTT",
    "6367.T": "ダイキン工業",    "8035.T": "東京エレクトロン",
    "6857.T": "アドバンテスト",  "6861.T": "キーエンス",
    "6954.T": "ファナック",      "7974.T": "任天堂",
    "9984.T": "ソフトバンクG",   "4063.T": "東京応化工業",
    "6501.T": "日立製作所",      "4502.T": "武田薬品",
    "9433.T": "KDDI",            "7267.T": "ホンダ",
    "4519.T": "中外製薬",        "8058.T": "三菱商事",
    "8031.T": "三井物産",        "6702.T": "富士通",
    "4568.T": "第一三共",        "6098.T": "リクルートHD",
    "7011.T": "三菱重工業",      "4543.T": "テルモ",
    "7751.T": "キヤノン",        "8801.T": "三井不動産",
    "6971.T": "京セラ",          "4661.T": "オリエンタルランド",
    "7733.T": "オリンパス",      "2413.T": "エムスリー",
}

_NDX_STOCKS = {
    "AAPL": "Apple",        "MSFT": "Microsoft",
    "NVDA": "NVIDIA",       "AMZN": "Amazon",
    "META": "Meta",         "GOOGL": "Alphabet",
    "TSLA": "Tesla",        "AVGO": "Broadcom",
    "COST": "Costco",       "NFLX": "Netflix",
    "AMD":  "AMD",          "ADBE": "Adobe",
    "QCOM": "Qualcomm",     "INTU": "Intuit",
    "CSCO": "Cisco",        "AMGN": "Amgen",
    "ISRG": "Intuitive",    "BKNG": "Booking",
    "PANW": "Palo Alto",    "LRCX": "Lam Research",
    "SNPS": "Synopsys",     "CDNS": "Cadence",
    "MELI": "MercadoLibre", "REGN": "Regeneron",
    "VRTX": "Vertex",       "ADP":  "ADP",
    "CRWD": "CrowdStrike",  "MRVL": "Marvell",
    "ABNB": "Airbnb",       "DXCM": "DexCom",
}

# NASDAQ-100 構成銘柄入れ替え履歴（出典: Nasdaq公式・各種報道）
_NDX100_CHANGES = [
    {
        "date": "2024-12-23",
        "label": "2024年12月 年次入れ替え",
        "type": "annual",
        "added":   [("PLTR", "Palantir Technologies"), ("AXON", "Axon Enterprise"), ("MSTR", "Strategy (MicroStrategy)")],
        "removed": [("ILMN", "Illumina"), ("SMCI", "Super Micro Computer"), ("MRNA", "Moderna")],
    },
    {
        "date": "2024-09-23",
        "label": "2024年9月 四半期入れ替え",
        "type": "quarterly",
        "added":   [("CDW", "CDW Corp"), ("CSCO", "Cisco Systems")],
        "removed": [("ODFL", "Old Dominion Freight"), ("FAST", "Fastenal")],
    },
    {
        "date": "2023-12-18",
        "label": "2023年12月 年次入れ替え",
        "type": "annual",
        "added":   [("SMCI", "Super Micro Computer"), ("MRVL", "Marvell Technology"), ("AAON", "AAON Inc")],
        "removed": [("SIRI", "SiriusXM"), ("LCID", "Lucid Group"), ("ENPH", "Enphase Energy")],
    },
]

_SP500_STOCKS = {
    "JPM":  "JPMorgan Chase",  "V":    "Visa",
    "MA":   "Mastercard",      "UNH":  "UnitedHealth",
    "JNJ":  "J&J",             "XOM":  "Exxon Mobil",
    "WMT":  "Walmart",         "BAC":  "Bank of America",
    "PG":   "P&G",             "HD":   "Home Depot",
    "CVX":  "Chevron",         "LLY":  "Eli Lilly",
    "ABBV": "AbbVie",          "GS":   "Goldman Sachs",
    "MCD":  "McDonald's",      "CAT":  "Caterpillar",
    "WFC":  "Wells Fargo",     "TMO":  "Thermo Fisher",
    "RTX":  "RTX Corp",        "SPGI": "S&P Global",
    "BLK":  "BlackRock",       "GE":   "GE Aerospace",
    "KO":   "Coca-Cola",       "PEP":  "PepsiCo",
    "MS":   "Morgan Stanley",  "DE":   "John Deere",
    "HON":  "Honeywell",       "UPS":  "UPS",
    "LOW":  "Lowe's",          "PLD":  "Prologis",
}


@st.cache_data(ttl=900, show_spinner=False)
def _fetch_momentum_ranking(market: str) -> pd.DataFrame:
    """日経225 / ナスダック100 / S&P500 構成銘柄のモメンタムスコアを計算"""
    if market == "nk225":
        stocks = _NK225_STOCKS
    elif market == "sp500":
        stocks = _SP500_STOCKS
    else:
        stocks = _NDX_STOCKS
    tickers = list(stocks.keys())
    try:
        end   = datetime.now()
        start = end - timedelta(days=35)
        raw   = yf.download(tickers, start=start, end=end,
                             progress=False, auto_adjust=True, group_by="ticker")
        if raw.empty:
            return pd.DataFrame()

        rows = []
        for ticker, name in stocks.items():
            try:
                # group_by="ticker" → raw[ticker]["Close"]
                if isinstance(raw.columns, pd.MultiIndex):
                    s = raw[ticker]["Close"].dropna()
                else:
                    s = raw["Close"].dropna()
                if len(s) < 2:
                    continue

                ret_1d  = float(s.iloc[-1] / s.iloc[-2]  - 1) * 100 if len(s) >= 2  else None
                ret_5d  = float(s.iloc[-1] / s.iloc[-6]  - 1) * 100 if len(s) >= 6  else None
                ret_20d = float(s.iloc[-1] / s.iloc[-21] - 1) * 100 if len(s) >= 21 else None

                if ret_1d is None:
                    continue

                score = ret_1d * 0.5
                if ret_5d  is not None: score += ret_5d  * 0.3
                if ret_20d is not None: score += ret_20d * 0.2

                rows.append({
                    "ticker": ticker, "name": name,
                    "price":  round(float(s.iloc[-1]), 2),
                    "1日":    round(ret_1d,  2),
                    "5日":    round(ret_5d,  2) if ret_5d  is not None else None,
                    "20日":   round(ret_20d, 2) if ret_20d is not None else None,
                    "score":  round(score, 3),
                })
            except Exception:
                continue

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows).sort_values("score", ascending=False).reset_index(drop=True)
        return df
    except Exception as e:
        logger.error(f"_fetch_momentum_ranking({market}): {e}")
        return pd.DataFrame()


# =====================================================
# 🐻 弱気相場リスク判定
# =====================================================

@st.cache_data(ttl=TTL_DAILY, show_spinner=False)
def compute_bear_market_risk() -> Dict[str, Any]:
    """
    複数の市場指標から弱気相場リスクスコア（0=最悪, 100=最良）を算出。
    各指標に重みを設定し加重平均でcompositeスコアを計算。
    """
    try:
        end   = datetime.now()
        start = end - timedelta(days=420)

        syms = ["^GSPC", "^VIX", "HYG", "LQD", "^TNX", "^IRX", "^VIX3M"]
        raw  = yf.download(syms, start=start, end=end,
                           progress=False, auto_adjust=True, group_by="ticker")

        def _g(sym):
            try:
                if isinstance(raw.columns, pd.MultiIndex):
                    s = raw[sym]["Close"]
                else:
                    s = raw["Close"]
                return s.dropna().astype(float)
            except Exception:
                return pd.Series(dtype=float)

        sp    = _g("^GSPC")
        vix   = _g("^VIX")
        vix3m = _g("^VIX3M")
        hyg   = _g("HYG")
        lqd   = _g("LQD")
        tnx   = _g("^TNX")
        irx   = _g("^IRX")

        signals: List[Dict] = []

        def _add(name, value_str, sig, color, score, weight, desc):
            signals.append({
                "name": name, "value": value_str,
                "signal": sig, "color": color,
                "score": score, "weight": weight, "desc": desc,
            })

        # ① S&P500 52週高値からの下落率
        if len(sp) >= 50:
            high_52w = float(sp.rolling(252, min_periods=50).max().iloc[-1])
            cur      = float(sp.iloc[-1])
            dd       = (cur / high_52w - 1) * 100
            if dd <= -20:
                sig, col, pts = "🔴 弱気相場入り",  "#ef4444", 0
            elif dd <= -10:
                sig, col, pts = "🟠 調整局面",      "#f97316", 25
            elif dd <= -5:
                sig, col, pts = "🟡 軽微な調整",    "#f59e0b", 55
            else:
                sig, col, pts = "🟢 正常レンジ",    "#22c55e", 90
            _add("S&P500 高値比", f"{dd:+.1f}%", sig, col, pts, 2.5,
                 "-20%到達で弱気相場確定。-10%は調整局面。")

        # ② VIX 水準
        if len(vix) >= 5:
            vv = float(vix.iloc[-1])
            if vv >= 40:
                sig, col, pts = "🔴 パニック",      "#ef4444", 0
            elif vv >= 30:
                sig, col, pts = "🔴 高恐怖",        "#ef4444", 15
            elif vv >= 22:
                sig, col, pts = "🟠 警戒",          "#f97316", 40
            elif vv >= 16:
                sig, col, pts = "🟡 やや高め",      "#f59e0b", 65
            else:
                sig, col, pts = "🟢 安定",          "#22c55e", 90
            _add("VIX 恐怖指数", f"{vv:.1f}", sig, col, pts, 1.5,
                 "30超で高恐怖。暴落時は40-80台まで上昇する。")

        # ③ VIX期間構造（VIX3M/VIX）— 逆イールドは極端な恐怖
        if len(vix) >= 5 and len(vix3m) >= 5:
            common = vix.index.intersection(vix3m.index)
            if len(common) >= 5:
                ratio = float(vix3m.loc[common].iloc[-1]) / max(float(vix.loc[common].iloc[-1]), 0.01)
                if ratio < 0.90:
                    sig, col, pts = "🔴 VIX逆イールド（パニック）", "#ef4444", 5
                elif ratio < 1.00:
                    sig, col, pts = "🟠 VIXフラット（警戒）",       "#f97316", 35
                else:
                    sig, col, pts = "🟢 VIX順イールド（安定）",     "#22c55e", 85
                _add("VIX期間構造 VIX3M/VIX", f"{ratio:.2f}x", sig, col, pts, 1.0,
                     "1.0未満（逆イールド）は極度の恐怖・短期パニックのサイン。")

        # ④ 信用スプレッド（HYG vs LQD 20日パフォーマンス差）
        if len(hyg) >= 22 and len(lqd) >= 22:
            common = hyg.index.intersection(lqd.index)
            if len(common) >= 22:
                hyg_r  = (float(hyg.loc[common].iloc[-1]) / float(hyg.loc[common].iloc[-22]) - 1) * 100
                lqd_r  = (float(lqd.loc[common].iloc[-1]) / float(lqd.loc[common].iloc[-22]) - 1) * 100
                spread = hyg_r - lqd_r
                if spread <= -4:
                    sig, col, pts = "🔴 信用収縮",     "#ef4444", 5
                elif spread <= -2:
                    sig, col, pts = "🟠 信用やや悪化", "#f97316", 30
                elif spread <= 0:
                    sig, col, pts = "🟡 中立",         "#f59e0b", 60
                else:
                    sig, col, pts = "🟢 リスク選好",   "#22c55e", 88
                _add("信用スプレッド HY-IG", f"{spread:+.1f}%pt", sig, col, pts, 1.5,
                     "HY債がIG債より大幅下落 = 企業信用収縮。景気後退の先行指標。")

        # ⑤ イールドカーブ（10年 - 3ヶ月）
        if len(tnx) >= 5 and len(irx) >= 5:
            common = tnx.index.intersection(irx.index)
            if len(common) >= 5:
                yc = float(tnx.loc[common].iloc[-1]) - float(irx.loc[common].iloc[-1])
                if yc <= -1.5:
                    sig, col, pts = "🔴 深い逆イールド",  "#ef4444", 10
                elif yc <= 0:
                    sig, col, pts = "🟠 逆イールド",      "#f97316", 30
                elif yc <= 0.5:
                    sig, col, pts = "🟡 フラット",        "#f59e0b", 58
                else:
                    sig, col, pts = "🟢 順イールド",      "#22c55e", 85
                _add("イールドカーブ 10y-3m", f"{yc:+.2f}%", sig, col, pts, 1.5,
                     "逆イールドは過去8回中8回で景気後退を12-18ヶ月先行して発生。")

        # ⑥ S&P500モメンタム（3M・6M）
        if len(sp) >= 130:
            r3m = (float(sp.iloc[-1]) / float(sp.iloc[-66])  - 1) * 100
            r6m = (float(sp.iloc[-1]) / float(sp.iloc[-130]) - 1) * 100
            if r3m < -15 or r6m < -20:
                sig, col, pts = "🔴 モメンタム崩壊",  "#ef4444", 5
            elif r3m < -8 or r6m < -12:
                sig, col, pts = "🟠 下降トレンド",    "#f97316", 28
            elif r3m < -2:
                sig, col, pts = "🟡 軟調",            "#f59e0b", 55
            elif r3m < 5:
                sig, col, pts = "🟡 横ばい",          "#f59e0b", 65
            else:
                sig, col, pts = "🟢 上昇トレンド",    "#22c55e", 88
            _add("価格モメンタム", f"3M {r3m:+.1f}% / 6M {r6m:+.1f}%",
                 sig, col, pts, 1.0,
                 "継続的な下落はトレンド転換を示す。3M・6M両方マイナスは要警戒。")

        # ⑦ サームルール（Alpha Vantage 失業率データがあれば）
        if ALPHA_VANTAGE_KEY:
            try:
                r_av = requests.get(
                    "https://www.alphavantage.co/query",
                    params={"function": "UNEMPLOYMENT", "apikey": ALPHA_VANTAGE_KEY},
                    timeout=8,
                )
                if r_av.status_code == 200:
                    u_data = r_av.json().get("data", [])
                    if len(u_data) >= 13:
                        u_vals = [float(x["value"]) for x in u_data[:13]]
                        u_3m_avg    = sum(u_vals[:3]) / 3
                        u_12m_low   = min(u_vals[1:13])
                        sahm_val    = u_3m_avg - u_12m_low
                        u_current   = u_vals[0]
                        if sahm_val >= 0.5:
                            sig, col, pts = "🔴 景気後退シグナル発動",  "#ef4444", 0
                        elif sahm_val >= 0.3:
                            sig, col, pts = "🟠 警戒域に接近",         "#f97316", 25
                        elif sahm_val >= 0.1:
                            sig, col, pts = "🟡 軽微な上昇",           "#f59e0b", 60
                        else:
                            sig, col, pts = "🟢 正常",                  "#22c55e", 90
                        _add(
                            "サームルール（失業率）",
                            f"現在 {u_current:.1f}% / トリガー差 {sahm_val:+.2f}%pt",
                            sig, col, pts, 2.0,
                            "失業率3ヶ月平均 - 過去12ヶ月最低値 ≥ 0.5% で景気後退シグナル。"
                            "クロードサームが考案。過去8回すべての後退を捉えた。"
                        )
            except Exception:
                pass

        if not signals:
            return {"ok": False, "reason": "データ取得失敗"}

        total_score  = sum(s["score"]  * s["weight"] for s in signals)
        total_weight = sum(s["weight"] for s in signals)
        composite    = total_score / total_weight

        if composite >= 72:
            verdict, vc = "🟢 弱気相場リスク: 低",  "#22c55e"
            detail = "主要指標は安定しています。急な下落を示すシグナルは限定的です。"
        elif composite >= 48:
            verdict, vc = "🟡 弱気相場リスク: 中",  "#f59e0b"
            detail = "一部の指標に警戒シグナルが出ています。過度なリスクを避けた姿勢が適切です。"
        elif composite >= 25:
            verdict, vc = "🔴 弱気相場リスク: 高",  "#ef4444"
            detail = "複数の先行指標が弱気を示しています。防御的なポジションを検討してください。"
        else:
            verdict, vc = "🔴 弱気相場リスク: 非常に高",  "#dc2626"
            detail = "ほぼすべての指標が危険域にあります。強い防御姿勢が必要です。"

        return {
            "ok":          True,
            "signals":     signals,
            "composite":   round(composite, 1),
            "verdict":     verdict,
            "verdict_color": vc,
            "detail":      detail,
        }

    except Exception as e:
        logger.error(f"compute_bear_market_risk: {e}")
        return {"ok": False, "reason": str(e)[:200]}


@st.cache_data(ttl=3600 * 6, show_spinner=False)
def fetch_macro_indicators() -> Dict[str, Any]:
    """CAPE / OECD CLI / インフレ指標を取得
    - CAPE    : multpl.com スクレイピング
    - OECD CLI: OECD SDMX API（FRED不使用）
    - PCE代替 : BLS API — CPI / Core CPI（FRED接続不可のため代替）
    """
    result: Dict[str, Any] = {"_errors": {}, "_ok": []}

    # ① CAPE — multpl.com (BS4 → pd.read_html フォールバック)
    try:
        from bs4 import BeautifulSoup
        from io import StringIO as _SIO
        r_cape = requests.get(
            "https://www.multpl.com/shiller-pe/table/by-month",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            timeout=15,
        )
        if r_cape.status_code == 200:
            soup = BeautifulSoup(r_cape.text, "lxml")
            table = soup.find("table", {"id": "datatable"}) or soup.find("table")
            vals = []
            if table:
                for row in table.find_all("tr"):
                    cells = row.find_all("td")
                    if len(cells) >= 2:
                        date_txt = cells[0].get_text(strip=True)
                        val_txt  = cells[1].get_text(strip=True).replace(",", "").strip()
                        try:
                            vals.append((date_txt, float(val_txt)))
                        except ValueError:
                            pass
            if not vals:
                tables = pd.read_html(_SIO(r_cape.text))
                if tables:
                    df = tables[0].iloc[:, :2]
                    df.columns = ["date", "value"]
                    df["value"] = pd.to_numeric(
                        df["value"].astype(str).str.replace(",", "", regex=False).str.strip(),
                        errors="coerce",
                    )
                    df = df.dropna(subset=["value"])
                    vals = list(zip(df["date"].astype(str), df["value"]))
            if vals:
                result["cape"] = {"value": vals[0][1], "date": vals[0][0], "avg_lt": 17.0}
                result["_ok"].append("cape")
            else:
                result["_errors"]["cape"] = "テーブル行が取得できませんでした"
        else:
            result["_errors"]["cape"] = f"multpl.com HTTP {r_cape.status_code}"
    except Exception as e:
        result["_errors"]["cape"] = str(e)[:120]

    # ② OECD CLI — 旧 stats.oecd.org REST API → 失敗時は yfinance イールドカーブで代替
    def _parse_lei_rows(lei_rows: list):
        latest_v = lei_rows[0][1]
        prev_v   = lei_rows[1][1] if len(lei_rows) > 1 else None
        mom      = ((latest_v - prev_v) / abs(prev_v) * 100) if prev_v else None
        streak   = 0
        for i in range(len(lei_rows) - 1):
            if lei_rows[i][1] < lei_rows[i + 1][1]:
                streak += 1
            else:
                break
        return {"value": latest_v, "date": lei_rows[0][0], "mom": mom, "streak_down": streak}

    _lei_fetched = False

    # 試行 A: OECD SDMX REST API（2023年移行済み新エンドポイント）
    try:
        url_a = (
            "https://sdmx.oecd.org/public/rest/data/"
            "OECD.SDD.STES,DSD_STES@DF_CLI,1.0/"
            "USA.M.LI...AA.....?startPeriod=2022-01&format=csvfilewithlabels"
        )
        r_a = requests.get(url_a, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        if r_a.status_code == 200:
            from io import StringIO as _SIO2
            df_a = pd.read_csv(_SIO2(r_a.text))
            tp  = next((c for c in df_a.columns if c.upper() in ("TIME_PERIOD", "TIME", "PERIOD")), None)
            val = next((c for c in df_a.columns if c.upper() in ("OBS_VALUE", "VALUE")), None)
            if tp and val:
                df_a = df_a[[tp, val]].copy()
                df_a.columns = ["date", "value"]
                df_a["value"] = pd.to_numeric(df_a["value"], errors="coerce")
                df_a = df_a.dropna(subset=["value"]).sort_values("date", ascending=False)
                if not df_a.empty:
                    result["lei"] = _parse_lei_rows(list(zip(df_a["date"], df_a["value"])))
                    result["_ok"].append("lei")
                    _lei_fetched = True
        if not _lei_fetched:
            result["_errors"]["OECD_A"] = f"HTTP {r_a.status_code} / 列={list(df_a.columns)[:4] if 'df_a' in dir() else '?'}"
    except Exception as e:
        result["_errors"]["OECD_A"] = f"{type(e).__name__}: {str(e)[:80]}"

    # 試行 B: yfinance イールドカーブ（10Y - 3M スプレッド）を LEI 代替として使用
    if not _lei_fetched:
        try:
            _yc = yf.download(["^TNX", "^IRX"], period="2y",
                               auto_adjust=True, progress=False)["Close"]
            _tnx = _yc["^TNX"].dropna()
            _irx = _yc["^IRX"].dropna()
            _spread = (_tnx - _irx).dropna()
            if len(_spread) >= 2:
                latest_v = float(_spread.iloc[-1])
                prev_v   = float(_spread.iloc[-2])
                mom      = latest_v - prev_v
                date_str = _spread.index[-1].strftime("%Y-%m-%d")
                result["lei"] = {
                    "value": round(latest_v, 2),
                    "date":  date_str,
                    "mom":   round(mom, 3),
                    "streak_down": 0,
                    "_is_yield_curve": True,   # カード表示で判定用
                }
                result["_ok"].append("lei")
                _lei_fetched = True
        except Exception as e:
            result["_errors"]["OECD_CLI"] = f"OECD+YC両方失敗: {str(e)[:80]}"

    # ③ インフレ指標 — FRED CSV（APIキー不要）
    #    PCEPI    = PCE Price Index（ヘッドライン）
    #    PCEPILFE = PCE Excluding Food and Energy（Core PCE）
    def _fetch_fred_pce(series_id: str, result_key: str):
        try:
            url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
            r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code != 200:
                result["_errors"][result_key] = f"FRED HTTP {r.status_code}"
                return
            from io import StringIO as _SIO3
            df = pd.read_csv(_SIO3(r.text))
            df.columns = ["date", "value"]
            df["value"] = pd.to_numeric(df["value"], errors="coerce")
            df = df.dropna(subset=["value"]).sort_values("date", ascending=False).reset_index(drop=True)
            if len(df) >= 13:
                latest  = float(df.loc[0, "value"])
                prev    = float(df.loc[1, "value"])
                yr12    = float(df.loc[12, "value"])
                mom_pct = (latest - prev) / abs(prev) * 100 if prev else None
                yoy_pct = (latest - yr12) / abs(yr12) * 100 if yr12 else None
                result[result_key] = {
                    "value": latest,
                    "date":  str(df.loc[0, "date"])[:7],
                    "yoy":   yoy_pct,
                    "mom":   mom_pct,
                }
                result["_ok"].append(result_key)
            elif len(df) >= 2:
                latest  = float(df.loc[0, "value"])
                prev    = float(df.loc[1, "value"])
                mom_pct = (latest - prev) / abs(prev) * 100 if prev else None
                result[result_key] = {
                    "value": latest,
                    "date":  str(df.loc[0, "date"])[:7],
                    "yoy":   None,
                    "mom":   mom_pct,
                }
                result["_ok"].append(result_key)
            else:
                result["_errors"][result_key] = "データ行数不足"
        except Exception as e:
            result["_errors"][result_key] = f"{type(e).__name__}: {str(e)[:120]}"

    _fetch_fred_pce("PCEPI",    "pce")
    _fetch_fred_pce("PCEPILFE", "core_pce")

    return result


def render_macro_indicators():
    """🌐 マクロ経済指標（CAPE / LEI / PCE）セクション"""
    st.markdown('<a id="macro"></a>', unsafe_allow_html=True)
    st.markdown(
        '<div style="background:linear-gradient(135deg,#0a1628,#0d2137,#0a1628);'
        'border-radius:12px;padding:14px 20px;margin-bottom:12px;">'
        '<div style="font-size:20px;font-weight:800;color:#7dd3fc">'
        + t('🌐 マクロ経済指標 — シンクタンク視点', '🌐 Macro Indicators — Think Tank View') +
        '</div>'
        '<div style="font-size:12px;color:#94a3b8;margin-top:2px">'
        'Shiller CAPE · Conference Board LEI · PCE Inflation</div>'
        '</div>',
        unsafe_allow_html=True,
    )

    with st.spinner("マクロ指標を取得中..."):
        macro = fetch_macro_indicators()

    cape  = macro.get("cape")
    lei   = macro.get("lei")
    pce   = macro.get("pce")
    cpce  = macro.get("core_pce")
    errs  = macro.get("_errors", {})
    ok    = macro.get("_ok", [])

    # ── 診断パネル ────────────────────────────────────────────
    missing = [x for x in ("cape", "lei", "core_pce") if x not in ok]
    with st.expander("🔧 マクロ指標 取得状況", expanded=bool(missing)):
        st.caption("FRED CSV方式（APIキー不要）・multpl.com スクレイピング")
        all_keys = {"cape": "CAPE", "lei": "OECD CLI", "pce": "PCE", "core_pce": "Core PCE"}
        for k, label in all_keys.items():
            if k in ok:
                st.success(f"✅ {label}: 取得成功")
            else:
                # 関連するエラーキーを探す
                series_map = {"lei": "OECD_CLI", "pce": "pce", "core_pce": "core_pce", "cape": "cape"}
                err_key = series_map.get(k, k)
                err_msg = errs.get(err_key, "エラー詳細なし（サイレント失敗）")
                st.error(f"❌ {label}: {err_msg}")
        if not errs and not ok:
            st.warning("全指標で結果なし。キャッシュクリアボタン（サイドバー）を押してください。")

    # ── 3カラム：CAPE / LEI / PCE ────────────────────────────
    col_cape, col_lei, col_pce = st.columns(3)

    # ① CAPE カード
    with col_cape:
        if cape:
            v = cape["value"]
            avg = cape["avg_lt"]
            pct_above = (v - avg) / avg * 100
            if v >= 40:
                c_color, c_label, c_icon = "#ef4444", "割高警戒", "🔴"
                c_comment = f"{v:.1f}はドットコムバブル（44）に次ぐ歴史的な割高水準。長期リターン低下リスク大。"
            elif v >= 35:
                c_color, c_label, c_icon = "#ef4444", "割高警戒", "🔴"
                c_comment = f"{v:.1f}は2007年リーマン前（27）や2018年（33）を大きく上回る割高水準。"
            elif v >= 25:
                c_color, c_label, c_icon = "#f59e0b", "やや割高", "🟡"
                c_comment = f"{v:.1f}は長期平均17を大きく上回る。強気相場継続中だが長期リターンは低下傾向。"
            elif v >= 15:
                c_color, c_label, c_icon = "#22c55e", "適正水準", "🟢"
                c_comment = f"{v:.1f}は長期平均（17）近辺の適正ゾーン。過去データでは良好なリターンが期待できる水準。"
            else:
                c_color, c_label, c_icon = "#14b8a6", "割安", "🔵"
                c_comment = f"{v:.1f}は歴史的な割安水準。2009年底（13）、1982年底（7）に近い水準。"
            st.markdown(
                f'<div style="background:#1e293b;border:1px solid {c_color};'
                f'border-radius:10px;padding:14px;text-align:center;">'
                f'<div style="font-size:11px;color:#94a3b8;font-weight:700">Shiller CAPE</div>'
                f'<div style="font-size:32px;font-weight:900;color:{c_color};margin:4px 0">{v:.1f}</div>'
                f'<div style="font-size:11px;color:#cbd5e1">{c_icon} {c_label}</div>'
                f'<div style="font-size:10px;color:#64748b;margin-top:6px">'
                f'長期平均{avg:.0f}比 +{pct_above:.0f}%超 | {cape["date"]}</div>'
                f'<div style="font-size:10px;color:#94a3b8;margin-top:6px;text-align:left;'
                f'border-top:1px solid #334155;padding-top:6px">{c_comment}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                '<div style="background:#1e293b;border:1px solid #334155;'
                'border-radius:10px;padding:14px;text-align:center;">'
                '<div style="font-size:11px;color:#94a3b8">Shiller CAPE</div>'
                '<div style="font-size:18px;color:#64748b;margin-top:8px">取得失敗</div>'
                '</div>',
                unsafe_allow_html=True,
            )

    # ② LEI カード
    with col_lei:
        if lei:
            is_yc   = lei.get("_is_yield_curve", False)
            mom     = lei.get("mom")
            streak  = lei.get("streak_down", 0)
            val     = lei["value"]

            if is_yc:
                # イールドカーブ（10Y-3M スプレッド）モード
                card_title = "イールドカーブ（10Y-3M）"
                val_str    = f"{val:+.2f}%"
                mom_str    = f"{mom:+.3f}pt" if mom is not None else "—"
                if val >= 1.0:
                    l_color, l_label, l_icon = "#22c55e", "順イールド・拡張", "🟢"
                elif val >= 0:
                    l_color, l_label, l_icon = "#f59e0b", "フラット・注意", "🟡"
                else:
                    l_color, l_label, l_icon = "#ef4444", "逆イールド・景気後退警戒", "🔴"
                sub_str = f"前日差 {mom_str} | {lei['date']}"
            else:
                # OECD CLI モード
                card_title = "OECD 景気先行指数"
                val_str    = f"{val:.1f}"
                mom_str    = f"{mom:+.2f}%" if mom is not None else "—"
                if streak >= 6:
                    l_color, l_label, l_icon = "#ef4444", f"⚠️ {streak}ヶ月連続低下", "🔴"
                elif streak >= 3:
                    l_color, l_label, l_icon = "#f59e0b", f"注意 {streak}ヶ月低下", "🟡"
                elif mom and mom > 0:
                    l_color, l_label, l_icon = "#22c55e", "拡張", "🟢"
                else:
                    l_color, l_label, l_icon = "#94a3b8", "横ばい", "⬜"
                sub_str = f"前月比 {mom_str} | {lei['date']}"

            st.markdown(
                f'<div style="background:#1e293b;border:1px solid {l_color};'
                f'border-radius:10px;padding:14px;text-align:center;">'
                f'<div style="font-size:11px;color:#94a3b8;font-weight:700">{card_title}</div>'
                f'<div style="font-size:32px;font-weight:900;color:{l_color};margin:4px 0">{val_str}</div>'
                f'<div style="font-size:11px;color:#cbd5e1">{l_icon} {l_label}</div>'
                f'<div style="font-size:10px;color:#64748b;margin-top:4px">{sub_str}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
        else:
            _lei_err = errs.get("OECD_CLI", errs.get("OECD_A", "取得失敗（詳細不明）"))
            st.markdown(
                '<div style="background:#1e293b;border:1px solid #475569;'
                'border-radius:10px;padding:14px;text-align:center;">'
                '<div style="font-size:11px;color:#94a3b8">景気先行指数</div>'
                '<div style="font-size:13px;color:#ef4444;margin-top:8px">取得失敗</div>'
                f'<div style="font-size:10px;color:#64748b;margin-top:4px;word-break:break-all">{_lei_err[:80]}</div>'
                '</div>',
                unsafe_allow_html=True,
            )

    # ③ PCE カード
    with col_pce:
        if cpce:
            yoy  = cpce.get("yoy")
            mom  = cpce.get("mom")
            display_val = yoy if yoy is not None else mom
            if display_val is None:
                p_color, p_label, p_icon = "#94a3b8", "データ不足", "⬜"
            elif display_val >= 3.5:
                p_color, p_label, p_icon = "#ef4444", "Fed目標大幅超過", "🔴"
            elif display_val >= 2.5:
                p_color, p_label, p_icon = "#f59e0b", "目標超過・引締め継続", "🟡"
            elif display_val >= 1.5:
                p_color, p_label, p_icon = "#22c55e", "目標近辺・緩和余地", "🟢"
            else:
                p_color, p_label, p_icon = "#14b8a6", "目標以下・緩和的", "🔵"
            val_label = "前年比" if yoy is not None else "前月比"
            val_str = f"{display_val:.1f}%" if display_val is not None else "—"
            pce_hl = pce.get("yoy") if pce else None
            sub_str = (f"Fed目標 2.0% | ヘッドライン {pce_hl:.1f}% | " if pce_hl else "Fed目標 2.0% | ")
            st.markdown(
                f'<div style="background:#1e293b;border:1px solid {p_color};'
                f'border-radius:10px;padding:14px;text-align:center;">'
                f'<div style="font-size:11px;color:#94a3b8;font-weight:700">Core CPI（{val_label}・PCE代替）</div>'
                f'<div style="font-size:32px;font-weight:900;color:{p_color};margin:4px 0">{val_str}</div>'
                f'<div style="font-size:11px;color:#cbd5e1">{p_icon} {p_label}</div>'
                f'<div style="font-size:10px;color:#64748b;margin-top:4px">'
                f'{sub_str}{cpce["date"]}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
        else:
            _pce_err = errs.get("PCEPILFE", errs.get("PCEPI", "取得失敗（詳細不明）"))
            st.markdown(
                '<div style="background:#1e293b;border:1px solid #475569;'
                'border-radius:10px;padding:14px;text-align:center;">'
                '<div style="font-size:11px;color:#94a3b8">Core CPI インフレ（PCE代替）</div>'
                '<div style="font-size:13px;color:#ef4444;margin-top:8px">取得失敗</div>'
                f'<div style="font-size:10px;color:#64748b;margin-top:4px;word-break:break-all">{_pce_err[:80]}</div>'
                '</div>',
                unsafe_allow_html=True,
            )

    st.markdown("<br>", unsafe_allow_html=True)

    # ── 各指標の詳細解説 ───────────────────────────────────────
    with st.expander("📖 各指標の読み方 — シンクタンクはここを見る", expanded=False):

        st.markdown("### 📊 Shiller CAPE（景気調整済PER）")
        st.markdown(
            "ノーベル賞経済学者ロバート・シラーが考案。直近10年間の実質利益平均で株価を割った指標。\n\n"
            "| 水準 | 意味 | 過去の事例 |\n"
            "|---|---|---|\n"
            "| **44以上** | 極度の過熱 | 2000年ドットコムバブル(最高値44) |\n"
            "| **35〜44** | 非常に割高 | 2021年ピーク(38)、現在もこのゾーン |\n"
            "| **25〜35** | 割高 | 2007年リーマン前(27)、2018年(33) |\n"
            "| **15〜25** | 適正 | 長期平均17、歴史的に最も多い水準 |\n"
            "| **15以下** | 割安 | 2009年金融危機底(13)、1982年底(7) |\n\n"
            "> ⚠️ CAPEは**長期バリュエーション**指標。高水準でも数年上昇が続くことがある。"
            "「いつ下がるか」ではなく「長期リターンが低くなるリスク」を示す指標として使う。"
        )

        st.markdown("---")
        st.markdown("### 📉 Conference Board LEI（景気先行指数）")
        st.markdown(
            "全米経済研究所(NBER)も参照する10指標の合成先行指数。景気転換を**6〜9ヶ月先行**する。\n\n"
            "**構成指標（主なもの）:**\n"
            "- 週平均製造業労働時間、新規失業保険申請件数\n"
            "- ISM新規受注、住宅着工許可件数\n"
            "- S&P500株価、信用スプレッド、イールドカーブ\n\n"
            "| シグナル | 判断 | 実績 |\n"
            "|---|---|---|\n"
            "| **6ヶ月以上連続低下** | 景気後退の強いシグナル | 過去8回の景気後退すべてで発生 |\n"
            "| **3〜5ヶ月連続低下** | 景気減速の注意 | 一時的な調整の場合も |\n"
            "| **横ばい〜上昇** | 景気拡張継続 | — |\n\n"
            "> 2022年〜2023年: LEIが18ヶ月連続低下 → 「景気後退は来る」と言われたが実際はソフトランディング。"
            "LEIは**精度が高いが偽陽性もある**。他指標と組み合わせて判断することが重要。"
        )

        st.markdown("---")
        st.markdown("### 🏦 Core PCE（FRBが最重視するインフレ指標）")
        st.markdown(
            "PCE = 個人消費支出デフレーター。CPIと異なり**代替効果（消費者が安い商品に切り替える行動）**を反映。\n\n"
            "**CPIとの違い:**\n"
            "| | CPI | Core PCE |\n"
            "|---|---|---|\n"
            "| 作成機関 | 労働統計局(BLS) | 商務省(BEA) |\n"
            "| 住居費ウェイト | 約33% | 約15% |\n"
            "| 特徴 | 固定ウェイト | 変動ウェイト（代替効果反映） |\n"
            "| FRBの使用 | 参考 | **政策判断の基準** |\n\n"
            "| Core PCE水準 | FRBの行動示唆 |\n"
            "|---|---|\n"
            "| **3.5%以上** | 利上げ継続または高止まり維持 |\n"
            "| **2.5〜3.5%** | 据え置き、利下げは後退 |\n"
            "| **2.0〜2.5%** | 利下げ検討ゾーン |\n"
            "| **2.0%以下** | 積極的利下げ余地あり |"
        )

    # ── FRED APIキー未設定の案内 ────────────────────────────────
    if not FRED_API_KEY:
        st.info(
            "💡 **LEI・PCE・CAPEをFREDから取得するには FRED API キーが必要です（無料）**\n\n"
            "1. [fred.stlouisfed.org](https://fred.stlouisfed.org) で無料アカウント作成\n"
            "2. My Account → API Keys でキーを発行\n"
            "3. Streamlit Cloud Secrets に追加: `FRED_API_KEY = \"your_key_here\"`"
        )


def render_bear_market_checker():
    """🐻 弱気相場リスク判定セクション"""
    st.markdown('<a id="bear-risk"></a>', unsafe_allow_html=True)
    st.markdown(
        '<div style="background:linear-gradient(135deg,#1a0a0a,#2d0a0a,#1a0a1a);'
        'border-radius:12px;padding:14px 20px;margin-bottom:8px;">'
        '<div style="font-size:20px;font-weight:800;color:#fca5a5">'
        + t('🐻 弱気相場リスク判定', '🐻 Bear Market Risk Assessment') +
        '</div>'
        '<div style="font-size:12px;color:#94a3b8;margin-top:2px">'
        + t('S&P500高値比・VIX・信用スプレッド・イールドカーブ等の複合指標でリスクを評価します。',
            'Composite risk score: S&P500 drawdown, VIX, credit spreads, yield curve & more.') +
        '</div></div>',
        unsafe_allow_html=True,
    )

    with st.spinner("市場リスク指標を取得中..."):
        data = compute_bear_market_risk()

    if not data.get("ok"):
        st.warning(f"データ取得失敗: {data.get('reason', '不明')}")
        return

    composite = data["composite"]
    verdict   = data["verdict"]
    vc        = data["verdict_color"]
    detail    = data["detail"]
    signals   = data["signals"]

    # ── 総合判定バー ──────────────────────────────────────────
    bar_pct  = int(composite)
    bar_col  = "#22c55e" if composite >= 72 else ("#f59e0b" if composite >= 48 else "#ef4444")
    st.markdown(
        f'<div style="margin-bottom:14px">'
        f'<div style="display:flex;align-items:center;gap:12px;margin-bottom:6px">'
        f'<div style="font-size:22px;font-weight:900;color:{vc}">{verdict}</div>'
        f'<div style="font-size:28px;font-weight:900;color:{vc}">{composite:.0f}<span style="font-size:14px;color:#64748b">/100</span></div>'
        f'</div>'
        f'<div style="background:#1e293b;border-radius:6px;height:10px;overflow:hidden">'
        f'<div style="width:{bar_pct}%;height:100%;background:{bar_col};border-radius:6px;transition:width 0.5s"></div>'
        f'</div>'
        f'<div style="font-size:12px;color:#94a3b8;margin-top:5px">{detail}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ── 個別シグナルカード ────────────────────────────────────
    cols = st.columns(min(len(signals), 3))
    for i, sig in enumerate(signals):
        with cols[i % 3]:
            st.markdown(
                f'<div style="border:1px solid {sig["color"]}44;background:{sig["color"]}10;'
                f'border-radius:8px;padding:10px 12px;margin-bottom:8px;">'
                f'<div style="font-size:11px;color:#94a3b8;font-weight:600">{sig["name"]}</div>'
                f'<div style="font-size:16px;font-weight:800;color:{sig["color"]};margin:3px 0">'
                f'{sig["signal"]}</div>'
                f'<div style="font-size:13px;color:#cbd5e1;font-weight:700">{sig["value"]}</div>'
                f'<div style="font-size:10px;color:#64748b;margin-top:4px">{sig["desc"]}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

    # ── 判断ガイド ─────────────────────────────────────────────
    with st.expander("📖 スコアの見方・指標の説明"):
        st.markdown("""
| スコア | 判定 | 意味 |
|--------|------|------|
| 72〜100 | 🟢 低リスク | 主要指標が安定。通常の市場環境。 |
| 48〜71 | 🟡 中リスク | 一部に警戒シグナル。慎重な運用を。 |
| 25〜47 | 🔴 高リスク | 複数の先行指標が悪化。防御的に。 |
| 0〜24 | 🔴 非常に高 | ほぼ全指標が危険域。強い警戒が必要。 |

**スコア計算の重み**
- S&P500高値比 (2.5x) — 最も直接的な弱気相場指標
- サームルール (2.0x) — 失業率による景気後退判定（AVキー必要）
- VIX (1.5x) / 信用スプレッド (1.5x) / イールドカーブ (1.5x)
- VIX期間構造 (1.0x) / 価格モメンタム (1.0x)

> ⚠️ このスコアは参考指標です。投資判断はご自身の責任で行ってください。
        """)

    # ── 雇用統計連携メモ ──────────────────────────────────────
    st.info(
        "💡 **雇用統計（NFP）との関係**: "
        "単発の雇用統計で弱気相場が始まることはまれです。"
        "ただし「NFP大幅miss + サームルール接近 + VIX急騰 + イールドカーブ逆転」が重なると"
        "転換点になりやすい傾向があります。上記の複数指標を組み合わせて判断してください。"
    )


# ── 光通信 vs 半導体 バスケット定義 ──────────────────────────────────
_OPTICAL_BASKET: list[tuple[str, str]] = [
    ("CIEN",   "Ciena（光NW）"),
    ("COHR",   "Coherent（光部品）"),
    ("LITE",   "Lumentum（光部品）"),
    ("VIAV",   "Viavi（光試験）"),
    ("AAOI",   "AAOI（トランシーバ）"),
    ("GLW",    "Corning（光ファイバー）"),
    ("APH",    "Amphenol（コネクタ）"),
    ("VRT",    "Vertiv（AIインフラ電源）"),
    ("5803.T", "フジクラ（光ファイバー・JPY）"),
]

_SEMI_BASKET: list[tuple[str, str]] = [
    ("SMH",   "半導体ETF (SMH)"),
    ("SOXX",  "半導体ETF (SOXX)"),
    ("NVDA",  "NVIDIA"),
    ("AMD",   "AMD"),
    ("AVGO",  "Broadcom"),
    ("INTC",  "Intel"),
    ("QCOM",  "Qualcomm"),
]


@st.cache_data(ttl=3600 * 4, show_spinner=False)
def _fetch_optical_vs_semi(period: str = "1y") -> dict:
    """光通信バスケットと半導体バスケットの正規化パフォーマンスを返す"""
    import yfinance as yf

    optical_tickers = [t for t, _ in _OPTICAL_BASKET]
    semi_tickers    = [t for t, _ in _SEMI_BASKET]
    all_tickers     = optical_tickers + semi_tickers

    try:
        raw = yf.download(all_tickers, period=period, auto_adjust=True, progress=False)
    except Exception:
        return {}

    # MultiIndex 対応
    if isinstance(raw.columns, pd.MultiIndex):
        close = raw["Close"] if "Close" in raw.columns.get_level_values(0) else raw.iloc[:, :len(all_tickers)]
    else:
        close = raw[["Close"]] if "Close" in raw.columns else raw

    close = close.dropna(axis=1, how="all")
    # 休場日・欠損日を前日値で補完（等加重平均の急変を防ぐ）
    close = close.ffill()

    # 正規化（period 最初の有効行を 100 とする）
    first_valid = close.bfill().iloc[0]
    norm = (close / first_valid * 100).copy()

    # 有効ティッカーを抽出
    opt_valid  = [t for t in optical_tickers if t in norm.columns and norm[t].notna().sum() > 5]
    semi_valid = [t for t in semi_tickers    if t in norm.columns and norm[t].notna().sum() > 5]

    # 等加重バスケット
    if opt_valid:
        norm["【光通信バスケット】"] = norm[opt_valid].mean(axis=1)
    if [t for t in semi_valid if t not in ("SMH", "SOXX")]:
        norm["【個別半導体平均】"] = norm[[t for t in semi_valid if t not in ("SMH", "SOXX")]].mean(axis=1)

    # 期間リターン計算
    label_map  = {t: lbl for t, lbl in _OPTICAL_BASKET + _SEMI_BASKET}
    label_map["【光通信バスケット】"] = "光通信バスケット（等加重）"
    label_map["【個別半導体平均】"]   = "個別半導体平均（等加重）"

    rets: dict[str, float] = {}
    for col in norm.columns:
        s = norm[col].dropna()
        if len(s) >= 2:
            rets[col] = round(float(s.iloc[-1]) - 100, 2)

    return {
        "norm":        norm,
        "opt_valid":   opt_valid,
        "semi_valid":  semi_valid,
        "rets":        rets,
        "label_map":   label_map,
        "close":       close,
    }


def render_optical_vs_semi():
    """📡 光通信・AI インフラ vs 半導体 パフォーマンス比較"""
    st.markdown('<a id="optical-semi"></a>', unsafe_allow_html=True)
    st.markdown(
        '<div style="background:linear-gradient(135deg,#0a192f,#112240,#1d3a6e);'
        'border-radius:12px;padding:14px 20px;margin-bottom:8px;">'
        '<div style="font-size:20px;font-weight:800;color:#f0f4f8;">'
        + t('📡 光通信・AIインフラ vs 半導体 パフォーマンス比較',
            '📡 Optical / AI Infra vs Semiconductors') +
        '</div>'
        '<div style="font-size:12px;color:#94a3b8;margin-top:2px;">'
        + t('AI データセンター投資の恩恵：光通信（CIEN/COHR/LITE等）と半導体（NVDA/AMD/SMH）の強さを比較します。',
            'AI datacenter beneficiaries: optical networking (CIEN/COHR/LITE) vs semiconductors (NVDA/AMD/SMH).') +
        '</div></div>',
        unsafe_allow_html=True,
    )

    col_period, col_view = st.columns([3, 1])
    with col_period:
        period_map = {"1ヶ月": "1mo", "3ヶ月": "3mo", "6ヶ月": "6mo", "1年": "1y", "2年": "2y", "5年": "5y"}
        period_lbl = st.radio("期間", list(period_map.keys()), index=3, horizontal=True, key="optsemi_period")
        period = period_map[period_lbl]
    with col_view:
        show_individual = st.checkbox("個別銘柄を表示", value=False, key="optsemi_individual")

    with st.spinner("パフォーマンスデータを取得中..."):
        data = _fetch_optical_vs_semi(period)

    if not data:
        st.warning("データを取得できませんでした。しばらくしてから再試行してください。")
        return

    norm       = data["norm"]
    opt_valid  = data["opt_valid"]
    semi_valid = data["semi_valid"]
    rets       = data["rets"]
    label_map  = data["label_map"]

    # ── パフォーマンスチャート ─────────────────────────────────────
    import plotly.graph_objects as go

    fig = go.Figure()

    OPTICAL_COLOR = "#38bdf8"   # 水色：光通信
    SEMI_COLOR    = "#f59e0b"   # 橙色：半導体

    # 個別光通信銘柄（細い補助線）
    if show_individual:
        for tk in opt_valid:
            if tk in norm.columns:
                fig.add_trace(go.Scatter(
                    x=norm.index, y=norm[tk],
                    name=label_map.get(tk, tk),
                    line=dict(width=1, dash="dot", color=OPTICAL_COLOR),
                    opacity=0.45,
                    legendgroup="optical",
                    showlegend=True,
                ))
        for tk in semi_valid:
            if tk in norm.columns and tk not in ("SMH", "SOXX"):
                fig.add_trace(go.Scatter(
                    x=norm.index, y=norm[tk],
                    name=label_map.get(tk, tk),
                    line=dict(width=1, dash="dot", color=SEMI_COLOR),
                    opacity=0.45,
                    legendgroup="semi",
                    showlegend=True,
                ))

    # SMH / SOXX（半導体ETF — 太い線）
    for etf in ("SMH", "SOXX"):
        if etf in norm.columns:
            fig.add_trace(go.Scatter(
                x=norm.index, y=norm[etf],
                name=label_map.get(etf, etf),
                line=dict(width=2.5, color=SEMI_COLOR, dash="dash"),
                legendgroup="semi",
            ))

    # 等加重バスケット（最も太い線）
    if "【光通信バスケット】" in norm.columns:
        fig.add_trace(go.Scatter(
            x=norm.index, y=norm["【光通信バスケット】"],
            name="光通信バスケット（等加重）",
            line=dict(width=3.5, color=OPTICAL_COLOR),
            legendgroup="optical",
        ))
    if "【個別半導体平均】" in norm.columns:
        fig.add_trace(go.Scatter(
            x=norm.index, y=norm["【個別半導体平均】"],
            name="個別半導体平均（等加重）",
            line=dict(width=3.5, color=SEMI_COLOR),
            legendgroup="semi",
        ))

    # 基準線（100）
    fig.add_hline(y=100, line_dash="dot", line_color="#475569", line_width=1)

    fig.update_layout(
        paper_bgcolor="#0f172a",
        plot_bgcolor="#0f172a",
        font=dict(color="#e2e8f0", family="sans-serif"),
        height=420,
        margin=dict(l=10, r=10, t=30, b=10),
        yaxis=dict(
            title=dict(text="パフォーマンス（期間開始=100）", font=dict(color="#e2e8f0")),
            gridcolor="#1e293b",
            tickcolor="#e2e8f0",
            tickfont=dict(color="#e2e8f0"),
            linecolor="#334155",
            ticksuffix="",
        ),
        xaxis=dict(
            gridcolor="#1e293b",
            tickcolor="#e2e8f0",
            tickfont=dict(color="#e2e8f0"),
            linecolor="#334155",
        ),
        legend=dict(
            orientation="h",
            y=-0.30,
            font=dict(size=11, color="#e2e8f0"),
            bgcolor="rgba(15,23,42,0.8)",
            bordercolor="#334155",
            borderwidth=1,
        ),
        showlegend=True,
        hovermode="x unified",
        hoverlabel=dict(
            bgcolor="#1e293b",
            font=dict(color="#e2e8f0"),
            bordercolor="#334155",
        ),
    )
    st.plotly_chart(fig, width="stretch")

    # ── リターン比較テーブル ──────────────────────────────────────
    st.markdown("#### 📊 期間リターン比較")

    display_order = (
        ["【光通信バスケット】"] + opt_valid +
        ["【個別半導体平均】", "SMH", "SOXX"] +
        [tk for tk in semi_valid if tk not in ("SMH", "SOXX")]
    )

    rows_html = ""
    for col in display_order:
        if col not in rets:
            continue
        r = rets[col]
        color = "#22c55e" if r >= 0 else "#ef4444"
        arrow = "▲" if r >= 0 else "▼"
        is_basket = col.startswith("【")
        bg = "#1e3a5f" if "光通信" in col else ("#3d2a00" if "半導体" in col or col in ("SMH","SOXX") else "#1e293b")
        weight = "font-weight:700;" if is_basket else ""
        rows_html += (
            f"<tr style='border-bottom:1px solid #334155;background:{bg};{weight}'>"
            f"<td style='padding:8px 12px;color:#e2e8f0'>{label_map.get(col, col)}</td>"
            f"<td style='padding:8px 12px;text-align:right;color:{color};font-weight:700'>"
            f"{arrow} {abs(r):.1f}%</td>"
            f"</tr>"
        )

    st.markdown(
        f"<table style='width:100%;border-collapse:collapse;font-size:13px'>"
        f"<thead><tr style='color:#64748b;border-bottom:2px solid #334155'>"
        f"<th style='padding:8px 12px;text-align:left'>銘柄 / バスケット</th>"
        f"<th style='padding:8px 12px;text-align:right'>{period_lbl} リターン</th>"
        f"</tr></thead><tbody>{rows_html}</tbody></table>",
        unsafe_allow_html=True,
    )

    # ── 解説 ────────────────────────────────────────────────────
    st.markdown("---")

    opt_ret  = rets.get("【光通信バスケット】")
    semi_ret = rets.get("SMH") or rets.get("【個別半導体平均】")

    if opt_ret is not None and semi_ret is not None:
        diff = opt_ret - semi_ret
        if diff > 5:
            verdict = f"📡 **光通信バスケットが半導体を {diff:+.1f}pt 上回っています。** AIデータセンター向け光接続需要（800G/1.6T トランシーバ）の拡大が主因と考えられます。"
            verdict_color = "#38bdf8"
        elif diff < -5:
            verdict = f"🔬 **半導体（SMH）が光通信を {abs(diff):.1f}pt 上回っています。** GPUサイクルの直接的な恩恵が大きく、光通信はまだ出遅れ状態です。"
            verdict_color = "#f59e0b"
        else:
            verdict = f"⚖️ **光通信と半導体は拮抗しています（差 {diff:+.1f}pt）。** AIインフラ投資の恩恵を両セクターが受けている局面です。"
            verdict_color = "#a78bfa"
        st.markdown(
            f'<div style="background:#1e293b;border-left:4px solid {verdict_color};'
            f'padding:12px 16px;border-radius:0 8px 8px 0;font-size:13px;color:#e2e8f0">'
            f'{verdict}</div>',
            unsafe_allow_html=True,
        )

    with st.expander("📖 各銘柄の役割メモ", expanded=False):
        st.markdown("""
| 銘柄 | セクター | 役割 |
|---|---|---|
| **CIEN** | 光通信NW | データセンター間長距離光ネットワーク装置 |
| **COHR** | 光部品 | 800G/1.6T 光トランシーバ、VCSEL（旧II-VI） |
| **LITE** | 光部品 | 光コンポーネント・3Dセンシング |
| **VIAV** | 光試験 | 光ファイバー試験・測定機器 |
| **AAOI** | 光トランシーバ | データセンター向け高速トランシーバ（AI DC集中投資で急騰） |
| **ATXI** | 光インフラ | 光通信インフラ関連（データ取得可能な場合のみ表示） |
| **GLW** | 光ファイバー | Corning：光ファイバー素材・ケーブル世界最大手 |
| **APH** | コネクタ・ケーブル | Amphenol：AI DC向け高速コネクタ・ハーネス |
| **VRT** | AIインフラ電源 | Vertiv：AIデータセンター向け電源・冷却システム |
| **SNDK** | NAND Flash | SanDisk（WDスピンオフ・NAND型フラッシュ） |
| **フジクラ（5803.T）** | 光ファイバー | 世界首位級の光ファイバーケーブルメーカー（JPY建て・為替影響含む） |
| **SMH** | 半導体ETF | NVDA/TSMC/AVGO等を含む半導体業界代表ETF |
| **NVDA** | GPU/AI半導体 | AI学習・推論GPU、データセンター主力 |
| **AMD** | CPU/GPU | AI GPU（MI300X）・データセンターCPU |
| **AVGO** | NW/AI半導体 | カスタムAIチップ（XPU）・データセンターNW IC |
        """)
        st.caption("💡 光通信は半導体より出遅れて動く傾向（GPU需要→データセンター拡張→光接続需要）があります。")
        st.caption("⚠️ フジクラ（5803.T）はJPY建て。パフォーマンス比較には円安・円高の影響が含まれます。")


# =====================================================================
# 決算ベース指数予測（SOX・光通信バスケット）
# =====================================================================
_SEMI_FORECAST_TICKERS: list[tuple[str, str]] = [
    ("NVDA", "NVIDIA"),
    ("AMD",  "AMD"),
    ("AVGO", "Broadcom"),
    ("QCOM", "Qualcomm"),
    ("INTC", "Intel"),
    ("AMAT", "Applied Materials"),
    ("MU",   "Micron"),
    ("SNDK", "SanDisk"),
]
_OPTICAL_FORECAST_TICKERS: list[tuple[str, str]] = [
    ("SNDK",   "SanDisk"),
    ("AAOI",   "AAOI"),
    ("LITE",   "Lumentum"),
    ("GLW",    "Corning"),
    ("COHR",   "Coherent"),
    ("APH",    "Amphenol"),
    ("VRT",    "Vertiv"),
    ("5803.T", "フジクラ"),
    ("CIEN",   "Ciena"),
    ("VIAV",   "Viavi Solutions"),
]


@st.cache_data(ttl=3600 * 3, show_spinner=False)
def _fetch_earnings_forecast(tickers_json: str) -> dict:
    """
    代表銘柄のアナリスト目標株価・決算データを取得し、
    バスケット指数の推定上限・中央・下限を算出する。
    tickers_json: JSON文字列 [["SYM","Name"], ...]
    """
    import json as _json
    tickers = _json.loads(tickers_json)
    stocks: dict[str, dict] = {}

    for sym, name in tickers:
        try:
            info = yf.Ticker(sym).info
            price = float(info.get("currentPrice") or info.get("regularMarketPrice") or 0)
            if price <= 0:
                continue
            t_high = info.get("targetHighPrice")
            t_low  = info.get("targetLowPrice")
            t_mean = info.get("targetMeanPrice")
            rec    = info.get("recommendationMean")  # 1=Strong Buy … 5=Strong Sell
            rec_key = info.get("recommendationKey", "")

            def _up(target):
                return round((target / price - 1) * 100, 1) if target else None

            currency = "JPY" if sym.endswith(".T") else "USD"
            stocks[sym] = {
                "name":           name,
                "price":          price,
                "currency":       currency,
                "target_high":    t_high,
                "target_low":     t_low,
                "target_mean":    t_mean,
                "upside_high":    _up(t_high),
                "upside_low":     _up(t_low),
                "upside_mean":    _up(t_mean),
                "trailing_eps":   info.get("trailingEps"),
                "forward_eps":    info.get("forwardEps"),
                "trailing_pe":    info.get("trailingPE"),
                "forward_pe":     info.get("forwardPE"),
                "revenue_growth": info.get("revenueGrowth"),
                "eps_growth":     info.get("earningsGrowth"),
                "n_analysts":     info.get("numberOfAnalystOpinions") or 0,
                "rec_mean":       rec,
                "rec_key":        rec_key,
            }
        except Exception as e:
            logger.debug(f"[earnings_forecast] {sym}: {e}")

    def _wavg(key: str) -> float | None:
        vals = [s[key] for s in stocks.values() if s.get(key) is not None]
        return round(sum(vals) / len(vals), 1) if vals else None

    return {
        "stocks":             stocks,
        "basket_upside_high": _wavg("upside_high"),
        "basket_upside_low":  _wavg("upside_low"),
        "basket_upside_mean": _wavg("upside_mean"),
        "n_valid":            len(stocks),
    }


@st.cache_data(ttl=300, show_spinner=False)
def _fetch_sox_current() -> float | None:
    """SOX 指数の現在値を取得"""
    try:
        df = yf.download("^SOX", period="5d", progress=False, auto_adjust=True)
        if df is not None and not df.empty:
            close = df["Close"]
            if isinstance(close, pd.DataFrame):
                close = close.iloc[:, 0]
            return float(close.dropna().iloc[-1])
    except Exception:
        pass
    return None


def _render_sector_upside_comparison(
    semi_data: dict,
    optical_data: dict,
    lang: str = "ja",
) -> None:
    """半導体 vs 光通信バスケットの推定アップサイド比較チャートを描画"""
    import plotly.graph_objects as go

    s_lo  = semi_data.get("basket_upside_low")
    s_me  = semi_data.get("basket_upside_mean")
    s_hi  = semi_data.get("basket_upside_high")
    o_lo  = optical_data.get("basket_upside_low")
    o_me  = optical_data.get("basket_upside_mean")
    o_hi  = optical_data.get("basket_upside_high")

    if any(v is None for v in [s_lo, s_me, s_hi, o_lo, o_me, o_hi]):
        return

    SEMI_COLOR   = "#818cf8"
    OPTICAL_COLOR = "#38bdf8"

    if lang == "en":
        labels      = ["Downside (Low)", "Consensus (Mean)", "Upside (High)"]
        semi_label  = "🔬 Semiconductors (SOX)"
        opt_label   = "📡 Optical Basket"
        disclaimer  = "⚠️ Estimated from analyst consensus price targets. Not a guarantee of future performance."
        verdict_more = "More upside potential"
        title_txt   = "Estimated Upside/Downside Comparison  ※ Analyst Consensus Based Estimate"
    else:
        labels      = ["悲観シナリオ（下限）", "コンセンサス（中央）", "楽観シナリオ（上限）"]
        semi_label  = "🔬 半導体（SOX）"
        opt_label   = "📡 光通信バスケット"
        disclaimer  = "⚠️ アナリスト・コンセンサス目標株価を等加重平均した推定値です。実際の指数リターンを保証するものではありません。"
        verdict_more = "の方がアップサイド大きい見込み（推定）"
        title_txt   = "上振れ・下振れ シナリオ比較（アナリスト・コンセンサス ベース推定）"

    semi_vals    = [s_lo,  s_me,  s_hi]
    optical_vals = [o_lo,  o_me,  o_hi]

    fig = go.Figure()

    # 半導体バー
    fig.add_trace(go.Bar(
        name=semi_label,
        x=labels,
        y=semi_vals,
        marker_color=[SEMI_COLOR if v >= 0 else "#ef4444" for v in semi_vals],
        marker_opacity=0.85,
        text=[f"{v:+.1f}%" for v in semi_vals],
        textposition="outside",
        textfont=dict(color="#e2e8f0", size=13, family="sans-serif"),
        width=0.35,
        offsetgroup=0,
        hovertemplate="%{x}: <b>%{y:+.1f}%</b><extra>" + semi_label + "</extra>",
    ))

    # 光通信バー
    fig.add_trace(go.Bar(
        name=opt_label,
        x=labels,
        y=optical_vals,
        marker_color=[OPTICAL_COLOR if v >= 0 else "#f87171" for v in optical_vals],
        marker_opacity=0.85,
        text=[f"{v:+.1f}%" for v in optical_vals],
        textposition="outside",
        textfont=dict(color="#e2e8f0", size=13, family="sans-serif"),
        width=0.35,
        offsetgroup=1,
        hovertemplate="%{x}: <b>%{y:+.1f}%</b><extra>" + opt_label + "</extra>",
    ))

    # レンジ範囲の参考線（薄い帯）
    for vals, color in [(semi_vals, SEMI_COLOR), (optical_vals, OPTICAL_COLOR)]:
        fig.add_trace(go.Scatter(
            x=labels + labels[::-1],
            y=[vals[0], vals[1], vals[2], vals[2], vals[1], vals[0]],
            fill="toself",
            fillcolor=color,
            opacity=0.07,
            line=dict(width=0),
            showlegend=False,
            hoverinfo="skip",
        ))

    fig.add_hline(y=0, line_width=1.5, line_color="#475569", line_dash="dot")

    ymin = min(s_lo, o_lo, 0) * 1.4
    ymax = max(s_hi, o_hi, 0) * 1.4

    fig.update_layout(
        title=dict(text=title_txt, font=dict(color="#94a3b8", size=13), x=0),
        paper_bgcolor="#0f172a",
        plot_bgcolor="#0f172a",
        barmode="group",
        bargap=0.25,
        bargroupgap=0.06,
        height=380,
        margin=dict(l=10, r=20, t=60, b=80),
        yaxis=dict(
            title=dict(text="推定アップサイド %" if lang == "ja" else "Estimated Upside %",
                       font=dict(color="#94a3b8", size=11)),
            ticksuffix="%",
            tickfont=dict(color="#94a3b8"),
            gridcolor="#1e293b",
            zeroline=False,
            range=[ymin, ymax],
        ),
        xaxis=dict(
            tickfont=dict(color="#e2e8f0", size=12),
            linecolor="#334155",
        ),
        legend=dict(
            orientation="h",
            y=-0.22,
            x=0.5,
            xanchor="center",
            font=dict(color="#e2e8f0", size=12),
            bgcolor="rgba(0,0,0,0)",
        ),
        hoverlabel=dict(bgcolor="#1e293b", font=dict(color="#e2e8f0")),
        font=dict(color="#e2e8f0"),
    )

    st.plotly_chart(fig, width="stretch")

    # 勝者バナー
    if s_me is not None and o_me is not None:
        if abs(s_me - o_me) < 1.0:
            verdict_html = (
                f'<div style="background:#1e293b;border:1px solid #334155;'
                f'border-radius:8px;padding:10px 16px;font-size:13px;color:#e2e8f0;text-align:center;">'
                + (f"⚖️ コンセンサスベースでは両セクターはほぼ拮抗（差 {s_me-o_me:+.1f}pt）" if lang == "ja"
                   else f"⚖️ Broadly equal on consensus basis (diff {s_me-o_me:+.1f}pt)")
                + f'<span style="font-size:11px;color:#64748b;display:block;margin-top:2px">※ 推定値</span>'
                f'</div>'
            )
        elif s_me > o_me:
            verdict_html = (
                f'<div style="background:#1e1040;border:2px solid {SEMI_COLOR};'
                f'border-radius:8px;padding:10px 16px;font-size:14px;font-weight:700;color:{SEMI_COLOR};text-align:center;">'
                + (f"🔬 半導体（SOX）{verdict_more}　コンセンサス中央値: {s_me:+.1f}% vs {o_me:+.1f}%"
                   if lang == "ja" else
                   f"🔬 Semiconductors (SOX) {verdict_more}  Consensus: {s_me:+.1f}% vs {o_me:+.1f}%")
                + f'<span style="font-size:11px;color:#64748b;display:block;margin-top:2px;font-weight:400">※ 推定値</span>'
                f'</div>'
            )
        else:
            verdict_html = (
                f'<div style="background:#001f3f;border:2px solid {OPTICAL_COLOR};'
                f'border-radius:8px;padding:10px 16px;font-size:14px;font-weight:700;color:{OPTICAL_COLOR};text-align:center;">'
                + (f"📡 光通信バスケット{verdict_more}　コンセンサス中央値: {o_me:+.1f}% vs {s_me:+.1f}%"
                   if lang == "ja" else
                   f"📡 Optical Basket {verdict_more}  Consensus: {o_me:+.1f}% vs {s_me:+.1f}%")
                + f'<span style="font-size:11px;color:#64748b;display:block;margin-top:2px;font-weight:400">※ 推定値</span>'
                f'</div>'
            )
        st.markdown(verdict_html, unsafe_allow_html=True)

    st.caption(disclaimer)


def _render_forecast_gauge(
    basket_label: str,
    current_level: float,
    upside_low: float | None,
    upside_mean: float | None,
    upside_high: float | None,
    lang: str = "ja",
) -> None:
    """バスケット推定レンジをゲージカードで描画"""
    level_low  = current_level * (1 + (upside_low  or 0) / 100)
    level_mean = current_level * (1 + (upside_mean or 0) / 100)
    level_high = current_level * (1 + (upside_high or 0) / 100)

    def _pct(v):
        return f"{v:+.1f}%" if v is not None else "N/A"

    low_color  = "#ef4444" if (upside_low  or 0) < 0 else "#22c55e"
    mean_color = "#f59e0b" if (upside_mean or 0) < 0 else "#3b82f6"
    high_color = "#22c55e"

    if lang == "en":
        label_high, label_mean, label_low = "Upside (High)", "Consensus (Mean)", "Downside (Low)"
        label_current = "Current"
    else:
        label_high, label_mean, label_low = "楽観シナリオ（上限）", "コンセンサス（中央）", "悲観シナリオ（下限）"
        label_current = "現在値"

    st.markdown(
        f"""<div style="background:#0f172a;border:1px solid #334155;border-radius:12px;
        padding:16px 20px;margin-bottom:12px;">
        <div style="font-size:15px;font-weight:800;color:#f8fafc;margin-bottom:12px;">{basket_label}</div>
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:10px;text-align:center;">
          <div style="background:#1e293b;border-radius:8px;padding:10px 6px;">
            <div style="font-size:11px;color:#94a3b8;margin-bottom:4px;">{label_current}</div>
            <div style="font-size:16px;font-weight:700;color:#e2e8f0;">{current_level:,.1f}</div>
          </div>
          <div style="background:#1e293b;border-radius:8px;padding:10px 6px;">
            <div style="font-size:11px;color:#94a3b8;margin-bottom:4px;">{label_low}</div>
            <div style="font-size:16px;font-weight:700;color:{low_color};">{_pct(upside_low)}</div>
            <div style="font-size:12px;color:#64748b;">{level_low:,.1f}</div>
          </div>
          <div style="background:#1e293b;border-radius:8px;padding:10px 6px;">
            <div style="font-size:11px;color:#94a3b8;margin-bottom:4px;">{label_mean}</div>
            <div style="font-size:16px;font-weight:700;color:{mean_color};">{_pct(upside_mean)}</div>
            <div style="font-size:12px;color:#64748b;">{level_mean:,.1f}</div>
          </div>
          <div style="background:#1e293b;border-radius:8px;padding:10px 6px;">
            <div style="font-size:11px;color:#94a3b8;margin-bottom:4px;">{label_high}</div>
            <div style="font-size:16px;font-weight:700;color:{high_color};">{_pct(upside_high)}</div>
            <div style="font-size:12px;color:#64748b;">{level_high:,.1f}</div>
          </div>
        </div></div>""",
        unsafe_allow_html=True,
    )


def _render_stock_table(stocks: dict, lang: str = "ja") -> None:
    """銘柄ごとの決算・アナリスト目標テーブルを描画"""
    if not stocks:
        st.warning(t("データ取得失敗", "Failed to load data") if lang == "ja" else "Failed to load data")
        return

    rec_label = {
        "strong_buy": "💚 Strong Buy", "buy": "🟢 Buy",
        "hold": "🟡 Hold", "underperform": "🔴 Underperform",
        "sell": "🔴 Sell",
    }

    if lang == "en":
        headers = ["Ticker", "Name", "Price", "Low Target", "Mean Target", "High Target",
                   "Down%", "Mean%", "Up%", "Analysts", "Fwd P/E", "Rev.Growth", "Recommendation"]
    else:
        headers = ["銘柄", "社名", "現在値", "目標安値", "目標中央", "目標高値",
                   "下限%", "中央%", "上限%", "アナリスト数", "予想PER", "売上成長", "レーティング"]

    hdr = "".join(f"<th style='padding:7px 10px;text-align:right;color:#94a3b8;"
                  f"font-weight:600;font-size:11px;white-space:nowrap'>{h}</th>" for h in headers)
    rows_html = f"<tr style='border-bottom:1px solid #334155'>{hdr}</tr>"

    for sym, s in stocks.items():
        currency = s.get("currency", "USD")
        is_jpy = currency == "JPY"
        price_sym = "¥" if is_jpy else "$"
        price_fmt = ",.0f" if is_jpy else ",.2f"

        def _fmt_pct(v):
            if v is None:
                return "<td style='padding:6px 10px;text-align:right;color:#64748b'>—</td>"
            color = "#22c55e" if v >= 0 else "#ef4444"
            return f"<td style='padding:6px 10px;text-align:right;color:{color};font-weight:700'>{v:+.1f}%</td>"

        def _fmt_num(v, fmt=".1f", suffix=""):
            if v is None:
                return "<td style='padding:6px 10px;text-align:right;color:#64748b'>—</td>"
            return f"<td style='padding:6px 10px;text-align:right;color:#e2e8f0'>{v:{fmt}}{suffix}</td>"

        def _fmt_price(v, ps=price_sym, pf=price_fmt):
            if v is None:
                return "<td style='padding:6px 10px;text-align:right;color:#64748b'>—</td>"
            return f"<td style='padding:6px 10px;text-align:right;color:#e2e8f0'>{ps}{v:{pf}}</td>"

        rec_text = rec_label.get(s.get("rec_key", ""), "—")
        rev_g = (f"{s['revenue_growth']*100:+.1f}%"
                 if s.get("revenue_growth") is not None else "—")
        n_ana = s.get("n_analysts") or 0
        n_ana_str = str(n_ana) if n_ana else "—"

        rows_html += (
            f"<tr style='border-bottom:1px solid #1e293b'>"
            f"<td style='padding:6px 10px;font-weight:700;color:#7dd3fc'>{sym}</td>"
            f"<td style='padding:6px 10px;color:#94a3b8;font-size:12px;white-space:nowrap'>{s['name']}</td>"
            f"<td style='padding:6px 10px;text-align:right;color:#e2e8f0'>{price_sym}{s['price']:{price_fmt}}</td>"
            + _fmt_price(s.get("target_low"))
            + _fmt_price(s.get("target_mean"))
            + _fmt_price(s.get("target_high"))
            + _fmt_pct(s.get("upside_low"))
            + _fmt_pct(s.get("upside_mean"))
            + _fmt_pct(s.get("upside_high"))
            + f"<td style='padding:6px 10px;text-align:right;color:#64748b;font-size:11px'>{n_ana_str}</td>"
            + _fmt_num(s.get("forward_pe"), ".1f", "x")
            + f"<td style='padding:6px 10px;text-align:right;color:#e2e8f0'>{rev_g}</td>"
            + f"<td style='padding:6px 10px;text-align:right;font-size:11px;color:#e2e8f0'>{rec_text}</td>"
            + "</tr>"
        )

    src_note = ("Data source: Yahoo Finance (Wall Street analyst consensus)"
                if lang == "en" else
                "データソース: Yahoo Finance（ウォール街アナリスト・コンセンサス予測）| ¥ = JPY建て")
    st.markdown(
        f"<div style='overflow-x:auto'>"
        f"<table style='width:100%;border-collapse:collapse;font-size:13px;"
        f"background:#0f172a;border-radius:8px;overflow:hidden'>"
        f"<tbody>{rows_html}</tbody></table></div>"
        f"<div style='font-size:11px;color:#64748b;margin-top:6px;padding-left:4px'>"
        f"📊 {src_note}</div>",
        unsafe_allow_html=True,
    )


def _render_upside_chart(stocks: dict, title: str) -> None:
    """各銘柄の目標株価レンジを横棒グラフで描画"""
    if not stocks:
        return
    syms, lows, means, highs = [], [], [], []
    for sym, s in stocks.items():
        if s.get("upside_mean") is None:
            continue
        syms.append(sym)
        lows.append(s.get("upside_low") or s.get("upside_mean", 0))
        means.append(s.get("upside_mean", 0))
        highs.append(s.get("upside_high") or s.get("upside_mean", 0))

    if not syms:
        return

    import plotly.graph_objects as go
    fig = go.Figure()

    # 低〜高レンジバー（ガントチャート風）
    for i, sym in enumerate(syms):
        lo, hi = lows[i], highs[i]
        color = "#22c55e" if means[i] >= 0 else "#ef4444"
        fig.add_trace(go.Bar(
            x=[hi - lo],
            y=[sym],
            base=[lo],
            orientation="h",
            marker_color=color,
            marker_opacity=0.35,
            showlegend=False,
            hovertemplate=f"<b>{sym}</b><br>Low: {lo:+.1f}%<br>High: {hi:+.1f}%<extra></extra>",
        ))
        # コンセンサス点
        fig.add_trace(go.Scatter(
            x=[means[i]], y=[sym],
            mode="markers",
            marker=dict(symbol="diamond", size=10, color="#f59e0b",
                        line=dict(color="#fff", width=1)),
            showlegend=(i == 0),
            name="Consensus",
            hovertemplate=f"<b>{sym}</b> Consensus: {means[i]:+.1f}%<extra></extra>",
        ))

    fig.add_vline(x=0, line_width=1.5, line_color="#94a3b8", line_dash="dot")
    fig.update_layout(
        title=dict(text=title, font=dict(color="#e2e8f0", size=14)),
        paper_bgcolor="#0f172a", plot_bgcolor="#0f172a",
        height=max(220, len(syms) * 45),
        margin=dict(l=10, r=30, t=40, b=30),
        xaxis=dict(
            title="Upside / Downside %",
            tickfont=dict(color="#94a3b8"),
            title_font=dict(color="#94a3b8"),
            gridcolor="#1e293b",
            zeroline=False,
        ),
        yaxis=dict(
            tickfont=dict(color="#e2e8f0"),
            gridcolor="#1e293b",
        ),
        legend=dict(font=dict(color="#e2e8f0"), bgcolor="rgba(0,0,0,0)"),
        hoverlabel=dict(bgcolor="#1e293b", font=dict(color="#e2e8f0")),
        barmode="overlay",
    )
    st.plotly_chart(fig, width="stretch")


@st.cache_data(ttl=3600 * 6, show_spinner=False)
def _ai_earnings_outlook(
    semi_summary: str, optical_summary: str, lang: str = "ja"
) -> tuple[str, str]:
    """決算データを基に AI が SOX・光通信バスケットの先行き見通しコメントを生成"""
    _lang_suffix = "\n\nIMPORTANT: Respond entirely in English. Do not use Japanese." if lang == "en" else ""
    if lang == "en":
        prompt = f"""You are a senior equity analyst specializing in semiconductors and AI infrastructure.
Based on the analyst consensus price targets and earnings data below, write a concise 6–12 month outlook for:
1. SOX (Philadelphia Semiconductor Index) — represented by key component stocks
2. Optical/AI Infrastructure basket — CIEN, COHR, LITE, VIAV, AAOI

[Semiconductor Basket Data]
{semi_summary}

[Optical Basket Data]
{optical_summary}

Please cover (in ~300 words total):
- Key upside catalysts and downside risks for each basket
- Relative outlook: which basket looks stronger over the next 6–12 months?
- Key earnings events or thresholds to watch

*For informational purposes only. Not investment advice.*{_lang_suffix}"""
    else:
        prompt = f"""あなたは半導体・AIインフラ専門のシニアアナリストです。
以下のアナリストコンセンサス目標株価・決算データをもとに、
SOX（半導体指数）と光通信バスケットの今後6〜12ヶ月の見通しを述べてください。

【半導体バスケット（SOXプロキシ）データ】
{semi_summary}

【光通信バスケットデータ】
{optical_summary}

以下の点を各200字程度でまとめてください：
1. 半導体（SOX）の上昇シナリオと下落リスク
2. 光通信バスケットの上昇シナリオと下落リスク
3. 相対比較：どちらが今後6〜12ヶ月で有望か？
4. 注目すべき決算イベント・閾値

※情報提供目的のみ。投資助言ではありません。"""

    try:
        return call_ai_with_fallback(prompt, max_output_tokens=900, temperature=0.4)
    except Exception as e:
        return f"AI生成エラー: {e}", ""


def render_earnings_index_forecast():
    """📊 決算ベース指数予測 — SOX・光通信バスケット"""
    import json as _json

    lang = st.session_state.get("lang", "ja")
    st.markdown('<a id="earnings-forecast"></a>', unsafe_allow_html=True)
    st.markdown(
        '<div style="background:linear-gradient(135deg,#0a1628,#0d2440,#0a1628);'
        'border-radius:12px;padding:14px 20px;margin-bottom:12px;">'
        '<div style="font-size:20px;font-weight:800;color:#a78bfa">'
        + t("📊 決算ベース指数予測 — SOX & 光通信バスケット",
            "📊 Earnings-Based Index Forecast — SOX & Optical Basket") +
        '</div>'
        '<div style="font-size:12px;color:#94a3b8;margin-top:2px">'
        + t("代表銘柄のアナリスト目標株価・決算データから指数の推定上限・下限を算出します",
            "Estimates index upper/lower bounds using analyst price targets & earnings data from representative stocks") +
        '</div></div>',
        unsafe_allow_html=True,
    )

    # ── データ取得 ──────────────────────────────────────────
    semi_json    = _json.dumps(_SEMI_FORECAST_TICKERS)
    optical_json = _json.dumps(_OPTICAL_FORECAST_TICKERS)

    col_l, col_r = st.columns(2)
    with col_l:
        with st.spinner(t("半導体データ取得中...", "Loading semiconductor data...")):
            semi_data = _fetch_earnings_forecast(semi_json)
    with col_r:
        with st.spinner(t("光通信データ取得中...", "Loading optical data...")):
            optical_data = _fetch_earnings_forecast(optical_json)

    sox_price = _fetch_sox_current()

    # ── タブ表示 ──────────────────────────────────────────
    tab_semi, tab_opt, tab_compare = st.tabs([
        t("🔬 半導体 (SOX)", "🔬 Semiconductors (SOX)"),
        t("📡 光通信バスケット", "📡 Optical Basket"),
        t("📊 比較・AI見通し", "📊 Comparison & AI Outlook"),
    ])

    # ── Tab1: 半導体 ─────────────────────────────────────
    with tab_semi:
        st.markdown(f"**{t('代表銘柄', 'Representative stocks')}**: " +
                    ", ".join(_SEMI_FORECAST_TICKERS[i][0] for i in range(len(_SEMI_FORECAST_TICKERS))))

        if semi_data["n_valid"] > 0:
            # SOX 現在値ベースのゲージ
            sox_base = sox_price if sox_price else 5000.0
            if sox_price:
                st.caption(f"SOX {t('現在値', 'current')}: {sox_price:,.1f} pts")
            _render_forecast_gauge(
                t("SOX 推定レンジ（アナリストコンセンサス合算）",
                  "SOX Estimated Range (analyst consensus composite)"),
                sox_base,
                semi_data["basket_upside_low"],
                semi_data["basket_upside_mean"],
                semi_data["basket_upside_high"],
                lang=lang,
            )
            st.markdown("---")
            st.markdown(f"**{t('銘柄別 決算・目標株価', 'Per-stock Earnings & Price Targets')}**")
            _render_stock_table(semi_data["stocks"], lang=lang)
            _render_upside_chart(
                semi_data["stocks"],
                t("半導体 各銘柄のアップサイド/ダウンサイド",
                  "Semiconductor Upside / Downside by Stock"),
            )
            n = semi_data["n_valid"]
            st.caption(
                t(f"※ {n}銘柄のアナリスト目標株価の等加重平均。SOX実際の指数構成比とは異なります。",
                  f"* Equal-weighted average of {n} stocks' analyst targets. Differs from actual SOX index weighting.")
            )
        else:
            st.warning(t("半導体データを取得できませんでした", "Could not load semiconductor data"))

    # ── Tab2: 光通信 ─────────────────────────────────────
    with tab_opt:
        st.markdown(f"**{t('代表銘柄', 'Representative stocks')}**: " +
                    ", ".join(_OPTICAL_FORECAST_TICKERS[i][0] for i in range(len(_OPTICAL_FORECAST_TICKERS))))

        if optical_data["n_valid"] > 0:
            # 光通信バスケット現在値（等加重平均価格を指数化）
            opt_prices = [s["price"] for s in optical_data["stocks"].values()]
            opt_base = sum(opt_prices) / len(opt_prices) if opt_prices else 100.0
            _render_forecast_gauge(
                t("光通信バスケット 推定レンジ（アナリストコンセンサス合算）",
                  "Optical Basket Estimated Range (analyst consensus composite)"),
                opt_base,
                optical_data["basket_upside_low"],
                optical_data["basket_upside_mean"],
                optical_data["basket_upside_high"],
                lang=lang,
            )
            st.markdown("---")
            st.markdown(f"**{t('銘柄別 決算・目標株価', 'Per-stock Earnings & Price Targets')}**")
            _render_stock_table(optical_data["stocks"], lang=lang)
            _render_upside_chart(
                optical_data["stocks"],
                t("光通信 各銘柄のアップサイド/ダウンサイド",
                  "Optical Basket Upside / Downside by Stock"),
            )
            n = optical_data["n_valid"]
            st.caption(
                t(f"※ {n}銘柄のアナリスト目標株価の等加重平均。5803.T（フジクラ）は JPY 建てのため除外。",
                  f"* Equal-weighted average of {n} stocks' analyst targets. 5803.T (Fujikura) excluded (JPY-denominated).")
            )
        else:
            st.warning(t("光通信データを取得できませんでした", "Could not load optical data"))

    # ── Tab3: 比較・AI見通し ──────────────────────────────
    with tab_compare:
        # サマリー比較カード
        col_s, col_o = st.columns(2)

        def _summary_card(label: str, data: dict, color: str) -> None:
            hi = data.get("basket_upside_high")
            lo = data.get("basket_upside_low")
            me = data.get("basket_upside_mean")

            def _pct(v):
                return f"{v:+.1f}%" if v is not None else "N/A"

            st.markdown(
                f'<div style="background:#0f172a;border:2px solid {color};'
                f'border-radius:10px;padding:14px 16px;text-align:center;">'
                f'<div style="font-size:14px;font-weight:700;color:{color};margin-bottom:8px">{label}</div>'
                f'<div style="display:flex;justify-content:space-around;margin-top:6px">'
                f'<div><div style="font-size:10px;color:#64748b">{t("下限","Low")}</div>'
                f'<div style="font-size:18px;font-weight:700;color:#ef4444">{_pct(lo)}</div></div>'
                f'<div><div style="font-size:10px;color:#64748b">{t("中央","Mean")}</div>'
                f'<div style="font-size:18px;font-weight:700;color:#f59e0b">{_pct(me)}</div></div>'
                f'<div><div style="font-size:10px;color:#22c55e">{t("上限","High")}</div>'
                f'<div style="font-size:22px;font-weight:800;color:#22c55e">{_pct(hi)}</div></div>'
                f'</div></div>',
                unsafe_allow_html=True,
            )

        with col_s:
            _summary_card(t("🔬 半導体（SOX）", "🔬 Semiconductors (SOX)"),
                          semi_data, "#818cf8")
        with col_o:
            _summary_card(t("📡 光通信バスケット", "📡 Optical Basket"),
                          optical_data, "#38bdf8")

        st.markdown("<br>", unsafe_allow_html=True)
        _render_sector_upside_comparison(semi_data, optical_data, lang=lang)
        st.markdown("---")

        # AI 見通しコメント
        st.markdown(f"**🤖 {t('AI 決算見通しコメント', 'AI Earnings Outlook')}**")
        _ai_key = _lang_key("earnings_forecast_ai")

        btn_col, _ = st.columns([1, 2])
        with btn_col:
            if st.button(
                t("🤖 AI見通しを生成", "🤖 Generate AI Outlook"),
                key="earnings_forecast_ai_btn",
                type="primary",
                width="stretch",
            ):
                # プロンプト用サマリーテキスト生成
                def _make_summary(data: dict) -> str:
                    lines = []
                    for sym, s in data["stocks"].items():
                        rg = s.get("revenue_growth")
                        eg = s.get("eps_growth")
                        rev_str = f"{rg*100:.1f}%" if rg is not None else "N/A"
                        eps_str = f"{eg*100:.1f}%" if eg is not None else "N/A"
                        lines.append(
                            f"  {sym} ({s['name']}): "
                            f"Price=${s['price']:.2f}, "
                            f"TargetHigh={s.get('target_high') or 'N/A'}, "
                            f"TargetMean={s.get('target_mean') or 'N/A'}, "
                            f"TargetLow={s.get('target_low') or 'N/A'}, "
                            f"FwdPE={s.get('forward_pe') or 'N/A'}, "
                            f"RevGrowth={rev_str}, "
                            f"EPSGrowth={eps_str}, "
                            f"Analysts={s.get('n_analysts', 0)}, Rec={s.get('rec_key', 'N/A')}"
                        )
                    basket_hi = data.get("basket_upside_high")
                    basket_me = data.get("basket_upside_mean")
                    basket_lo = data.get("basket_upside_low")
                    hi_str = f"+{basket_hi:.1f}%" if basket_hi is not None else "N/A"
                    me_str = f"+{basket_me:.1f}%" if basket_me is not None else "N/A"
                    lo_str = f"{basket_lo:.1f}%" if basket_lo is not None else "N/A"
                    lines.append(
                        f"  Basket consensus upside: High={hi_str}, Mean={me_str}, Low={lo_str}"
                    )
                    return "\n".join(lines)

                semi_summary    = _make_summary(semi_data)   # noqa: E501
                optical_summary = _make_summary(optical_data)  # noqa: E501

                with st.spinner(t("AI分析中（決算データ処理）...",
                                  "Running AI analysis (processing earnings data)...")):
                    comment, model = _ai_earnings_outlook(
                        semi_summary, optical_summary, lang=lang
                    )
                st.session_state[_ai_key] = (comment, model)

        if _ai_key in st.session_state:
            ai_txt, ai_model = st.session_state[_ai_key]
            st.markdown(
                f'<div style="background:#0f172a;border-left:4px solid #a78bfa;'
                f'padding:14px 18px;border-radius:6px;margin-top:8px;">'
                f'<div style="font-size:12px;color:#6b7280;margin-bottom:6px;">🤖 AI ({ai_model})</div>'
                f'<div style="font-size:14px;color:#e2e8f0;line-height:1.7;">'
                f'{ai_txt.replace(chr(10), "<br>")}</div></div>',
                unsafe_allow_html=True,
            )

        st.caption(
            t("⚠️ アナリスト目標株価は将来を保証するものではありません。投資判断は自己責任でお願いします。",
              "⚠️ Analyst price targets do not guarantee future performance. Invest at your own risk.")
        )


def render_momentum_ranking():
    """🚀 モメンタム上位 / 下位 銘柄ランキング"""
    st.markdown('<a id="momentum"></a>', unsafe_allow_html=True)
    st.markdown(
        '<div style="background:linear-gradient(135deg,#0d1117,#161b22,#1f2937);'
        'border-radius:12px;padding:14px 20px;margin-bottom:8px;">'
        '<div style="font-size:20px;font-weight:800;color:#f0f4f8">'
        + t('🚀 モメンタムランキング', '🚀 Momentum Ranking') +
        '</div>'
        '<div style="font-size:12px;color:#94a3b8;margin-top:2px">'
        + t('当日・5日・20日リターンの加重平均スコアで上昇力の強い銘柄を表示します。',
            'Weighted score of 1-day, 5-day, and 20-day returns to rank strongest movers.') +
        '</div>'
        '</div>',
        unsafe_allow_html=True,
    )

    def _render_cards(df: pd.DataFrame):
        if df.empty:
            st.warning("データを取得できませんでした。")
            return

        top5 = df.head(5)
        bot5 = df.tail(5).iloc[::-1]

        col_up, col_dn = st.columns(2)

        def _card(row, col):
            r1  = row["1日"]
            r5  = row["5日"]
            r20 = row["20日"]
            cc  = "#16a34a" if r1 >= 0 else "#dc2626"
            r5s  = f" | 5日 {r5:+.1f}%"  if r5  is not None else ""
            r20s = f" | 20日 {r20:+.1f}%" if r20 is not None else ""
            col.markdown(
                f'<div style="border-left:3px solid {cc};padding:7px 11px;'
                f'margin-bottom:5px;background:#1e293b;'
                f'border-radius:0 6px 6px 0;">'
                f'<div style="font-size:13px;font-weight:700;color:#f1f5f9">'
                f'{row["name"]}'
                f'<span style="font-size:10px;color:#94a3b8;margin-left:4px">'
                f'{row["ticker"]}</span></div>'
                f'<div style="font-size:12px;margin-top:2px">'
                f'<span style="color:{cc};font-weight:700">今日 {r1:+.2f}%</span>'
                f'<span style="color:#94a3b8">{r5s}{r20s}</span></div>'
                f'</div>',
                unsafe_allow_html=True,
            )

        with col_up:
            st.markdown("**▲ 上昇モメンタム TOP5**")
            for _, row in top5.iterrows():
                _card(row, col_up)

        with col_dn:
            st.markdown("**▼ 下落モメンタム TOP5**")
            for _, row in bot5.iterrows():
                _card(row, col_dn)

        with st.expander("📋 全銘柄スコア一覧"):
            disp = df[["name", "ticker", "price", "1日", "5日", "20日", "score"]].copy()
            disp.columns = ["銘柄名", "コード", "株価", "今日%", "5日%", "20日%", "スコア"]
            st.dataframe(disp, width="stretch", hide_index=True)

    tab_nk, tab_ndx, tab_sp = st.tabs(["🇯🇵 日経225", "🇺🇸 NASDAQ100", "🇺🇸 S&P500"])

    with tab_nk:
        with st.spinner("日経225データを取得中..."):
            df_nk = _fetch_momentum_ranking("nk225")
        _render_cards(df_nk)

    with tab_ndx:
        with st.spinner("NASDAQ100データを取得中..."):
            df_ndx = _fetch_momentum_ranking("nasdaq")
        _render_cards(df_ndx)

        with st.expander("📅 NASDAQ100 構成銘柄 入れ替え履歴（直近）"):
            for chg in _NDX100_CHANGES:
                st.markdown(
                    f'<div style="background:#1e293b;border:1px solid #334155;border-radius:8px;'
                    f'padding:10px 14px;margin-bottom:8px;">'
                    f'<div style="font-size:12px;font-weight:700;color:#94a3b8;margin-bottom:6px">'
                    f'{"🔄" if chg["type"]=="annual" else "↔️"} {chg["label"]} '
                    f'<span style="color:#64748b">({chg["date"]})</span></div>'
                    f'<div style="display:flex;gap:16px;">'
                    f'<div style="flex:1">'
                    f'<div style="font-size:11px;color:#22c55e;font-weight:600;margin-bottom:3px">▲ 新規採用</div>'
                    + "".join(
                        f'<div style="font-size:12px;color:#e2e8f0;margin-bottom:2px">'
                        f'<span style="color:#22c55e;font-weight:700">{t}</span>'
                        f' <span style="color:#94a3b8">{n}</span></div>'
                        for t, n in chg["added"]
                    )
                    + f'</div><div style="flex:1">'
                    f'<div style="font-size:11px;color:#ef4444;font-weight:600;margin-bottom:3px">▼ 除外</div>'
                    + "".join(
                        f'<div style="font-size:12px;color:#e2e8f0;margin-bottom:2px">'
                        f'<span style="color:#ef4444;font-weight:700">{t}</span>'
                        f' <span style="color:#94a3b8">{n}</span></div>'
                        for t, n in chg["removed"]
                    )
                    + f'</div></div></div>',
                    unsafe_allow_html=True,
                )
            st.caption("※ 出典: Nasdaq公式発表・各種報道。最新情報は nasdaq.com でご確認ください。")

    with tab_sp:
        with st.spinner("S&P500データを取得中..."):
            df_sp = _fetch_momentum_ranking("sp500")
        _render_cards(df_sp)


# =====================================================
# 米国経済イベント × ボラティリティ分析
# =====================================================

# 2025-2026 主要経済指標の発表日（ET日付）と発表時刻（ET 24h）
_US_ECO_CALENDAR = [
    # ── 雇用統計 / NFP / 失業率（毎月第1金曜 8:30 AM ET）──
    ("2025-01-10", "08:30", "非農業部門雇用者数(NFP) / 失業率",     "👷", "high",  "12月分"),
    ("2025-02-07", "08:30", "非農業部門雇用者数(NFP) / 失業率",     "👷", "high",  "1月分"),
    ("2025-03-07", "08:30", "非農業部門雇用者数(NFP) / 失業率",     "👷", "high",  "2月分"),
    ("2025-04-04", "08:30", "非農業部門雇用者数(NFP) / 失業率",     "👷", "high",  "3月分"),
    ("2025-05-02", "08:30", "非農業部門雇用者数(NFP) / 失業率",     "👷", "high",  "4月分"),
    ("2025-06-06", "08:30", "非農業部門雇用者数(NFP) / 失業率",     "👷", "high",  "5月分"),
    ("2025-07-03", "08:30", "非農業部門雇用者数(NFP) / 失業率",     "👷", "high",  "6月分"),
    ("2025-08-01", "08:30", "非農業部門雇用者数(NFP) / 失業率",     "👷", "high",  "7月分"),
    ("2025-09-05", "08:30", "非農業部門雇用者数(NFP) / 失業率",     "👷", "high",  "8月分"),
    ("2025-10-03", "08:30", "非農業部門雇用者数(NFP) / 失業率",     "👷", "high",  "9月分"),
    ("2025-11-07", "08:30", "非農業部門雇用者数(NFP) / 失業率",     "👷", "high",  "10月分"),
    ("2025-12-05", "08:30", "非農業部門雇用者数(NFP) / 失業率",     "👷", "high",  "11月分"),
    ("2026-01-09", "08:30", "非農業部門雇用者数(NFP) / 失業率",     "👷", "high",  "12月分"),
    ("2026-02-06", "08:30", "非農業部門雇用者数(NFP) / 失業率",     "👷", "high",  "1月分"),
    ("2026-03-06", "08:30", "非農業部門雇用者数(NFP) / 失業率",     "👷", "high",  "2月分"),
    ("2026-04-03", "08:30", "非農業部門雇用者数(NFP) / 失業率",     "👷", "high",  "3月分"),
    ("2026-05-01", "08:30", "非農業部門雇用者数(NFP) / 失業率",     "👷", "high",  "4月分"),
    ("2026-06-05", "08:30", "非農業部門雇用者数(NFP) / 失業率",     "👷", "high",  "5月分"),
    ("2026-07-02", "08:30", "非農業部門雇用者数(NFP) / 失業率",     "👷", "high",  "6月分"),
    ("2026-08-07", "08:30", "非農業部門雇用者数(NFP) / 失業率",     "👷", "high",  "7月分"),
    ("2026-09-04", "08:30", "非農業部門雇用者数(NFP) / 失業率",     "👷", "high",  "8月分"),
    ("2026-10-02", "08:30", "非農業部門雇用者数(NFP) / 失業率",     "👷", "high",  "9月分"),
    ("2026-11-06", "08:30", "非農業部門雇用者数(NFP) / 失業率",     "👷", "high",  "10月分"),
    ("2026-12-04", "08:30", "非農業部門雇用者数(NFP) / 失業率",     "👷", "high",  "11月分"),
    # ── ISM製造業景気指数（毎月第1営業日 10:00 AM ET）──
    ("2025-02-03", "10:00", "ISM製造業景気指数",                    "🏗️", "medium", "1月分"),
    ("2025-03-03", "10:00", "ISM製造業景気指数",                    "🏗️", "medium", "2月分"),
    ("2025-04-01", "10:00", "ISM製造業景気指数",                    "🏗️", "medium", "3月分"),
    ("2025-05-01", "10:00", "ISM製造業景気指数",                    "🏗️", "medium", "4月分"),
    ("2025-06-02", "10:00", "ISM製造業景気指数",                    "🏗️", "medium", "5月分"),
    ("2025-07-01", "10:00", "ISM製造業景気指数",                    "🏗️", "medium", "6月分"),
    ("2025-08-01", "10:00", "ISM製造業景気指数",                    "🏗️", "medium", "7月分"),
    ("2025-09-02", "10:00", "ISM製造業景気指数",                    "🏗️", "medium", "8月分"),
    ("2025-10-01", "10:00", "ISM製造業景気指数",                    "🏗️", "medium", "9月分"),
    ("2025-11-03", "10:00", "ISM製造業景気指数",                    "🏗️", "medium", "10月分"),
    ("2025-12-01", "10:00", "ISM製造業景気指数",                    "🏗️", "medium", "11月分"),
    ("2026-01-05", "10:00", "ISM製造業景気指数",                    "🏗️", "medium", "12月分"),
    ("2026-02-03", "10:00", "ISM製造業景気指数",                    "🏗️", "medium", "1月分"),
    ("2026-03-02", "10:00", "ISM製造業景気指数",                    "🏗️", "medium", "2月分"),
    ("2026-04-01", "10:00", "ISM製造業景気指数",                    "🏗️", "medium", "3月分"),
    ("2026-05-01", "10:00", "ISM製造業景気指数",                    "🏗️", "medium", "4月分"),
    ("2026-06-01", "10:00", "ISM製造業景気指数",                    "🏗️", "medium", "5月分"),
    ("2026-07-01", "10:00", "ISM製造業景気指数",                    "🏗️", "medium", "6月分"),
    ("2026-08-03", "10:00", "ISM製造業景気指数",                    "🏗️", "medium", "7月分"),
    # ── ISM非製造業景気指数（毎月第3〜4営業日 10:00 AM ET）──
    ("2025-02-05", "10:00", "ISM非製造業景気指数(サービス業PMI)",    "🏢", "medium", "1月分"),
    ("2025-03-05", "10:00", "ISM非製造業景気指数(サービス業PMI)",    "🏢", "medium", "2月分"),
    ("2025-04-03", "10:00", "ISM非製造業景気指数(サービス業PMI)",    "🏢", "medium", "3月分"),
    ("2025-05-05", "10:00", "ISM非製造業景気指数(サービス業PMI)",    "🏢", "medium", "4月分"),
    ("2025-06-04", "10:00", "ISM非製造業景気指数(サービス業PMI)",    "🏢", "medium", "5月分"),
    ("2025-07-03", "10:00", "ISM非製造業景気指数(サービス業PMI)",    "🏢", "medium", "6月分"),
    ("2025-08-05", "10:00", "ISM非製造業景気指数(サービス業PMI)",    "🏢", "medium", "7月分"),
    ("2025-09-03", "10:00", "ISM非製造業景気指数(サービス業PMI)",    "🏢", "medium", "8月分"),
    ("2025-10-03", "10:00", "ISM非製造業景気指数(サービス業PMI)",    "🏢", "medium", "9月分"),
    ("2025-11-05", "10:00", "ISM非製造業景気指数(サービス業PMI)",    "🏢", "medium", "10月分"),
    ("2025-12-03", "10:00", "ISM非製造業景気指数(サービス業PMI)",    "🏢", "medium", "11月分"),
    ("2026-01-07", "10:00", "ISM非製造業景気指数(サービス業PMI)",    "🏢", "medium", "12月分"),
    ("2026-02-04", "10:00", "ISM非製造業景気指数(サービス業PMI)",    "🏢", "medium", "1月分"),
    ("2026-03-04", "10:00", "ISM非製造業景気指数(サービス業PMI)",    "🏢", "medium", "2月分"),
    ("2026-04-03", "10:00", "ISM非製造業景気指数(サービス業PMI)",    "🏢", "medium", "3月分"),
    ("2026-05-06", "10:00", "ISM非製造業景気指数(サービス業PMI)",    "🏢", "medium", "4月分"),
    ("2026-06-03", "10:00", "ISM非製造業景気指数(サービス業PMI)",    "🏢", "medium", "5月分"),
    ("2026-07-08", "10:00", "ISM非製造業景気指数(サービス業PMI)",    "🏢", "medium", "6月分"),
    ("2026-08-05", "10:00", "ISM非製造業景気指数(サービス業PMI)",    "🏢", "medium", "7月分"),
    # ── CPI（毎月10〜15日頃 8:30 AM ET）──
    ("2025-01-15", "08:30", "CPI（消費者物価指数）",                 "💹", "high",   "12月分"),
    ("2025-02-12", "08:30", "CPI（消費者物価指数）",                 "💹", "high",   "1月分"),
    ("2025-03-12", "08:30", "CPI（消費者物価指数）",                 "💹", "high",   "2月分"),
    ("2025-04-10", "08:30", "CPI（消費者物価指数）",                 "💹", "high",   "3月分"),
    ("2025-05-13", "08:30", "CPI（消費者物価指数）",                 "💹", "high",   "4月分"),
    ("2025-06-11", "08:30", "CPI（消費者物価指数）",                 "💹", "high",   "5月分"),
    ("2025-07-15", "08:30", "CPI（消費者物価指数）",                 "💹", "high",   "6月分"),
    ("2025-08-12", "08:30", "CPI（消費者物価指数）",                 "💹", "high",   "7月分"),
    ("2025-09-10", "08:30", "CPI（消費者物価指数）",                 "💹", "high",   "8月分"),
    ("2025-10-15", "08:30", "CPI（消費者物価指数）",                 "💹", "high",   "9月分"),
    ("2025-11-12", "08:30", "CPI（消費者物価指数）",                 "💹", "high",   "10月分"),
    ("2025-12-10", "08:30", "CPI（消費者物価指数）",                 "💹", "high",   "11月分"),
    ("2026-01-14", "08:30", "CPI（消費者物価指数）",                 "💹", "high",   "12月分"),
    ("2026-02-11", "08:30", "CPI（消費者物価指数）",                 "💹", "high",   "1月分"),
    ("2026-03-11", "08:30", "CPI（消費者物価指数）",                 "💹", "high",   "2月分"),
    ("2026-04-09", "08:30", "CPI（消費者物価指数）",                 "💹", "high",   "3月分"),
    ("2026-05-13", "08:30", "CPI（消費者物価指数）",                 "💹", "high",   "4月分"),
    ("2026-06-10", "08:30", "CPI（消費者物価指数）",                 "💹", "high",   "5月分"),
    ("2026-07-14", "08:30", "CPI（消費者物価指数）",                 "💹", "high",   "6月分"),
    ("2026-08-12", "08:30", "CPI（消費者物価指数）",                 "💹", "high",   "7月分"),
    # ── FOMC政策金利発表（年8回 2:00 PM ET）──
    ("2025-01-29", "14:00", "FOMC政策金利発表",                      "🏦", "high",   ""),
    ("2025-03-19", "14:00", "FOMC政策金利発表",                      "🏦", "high",   ""),
    ("2025-05-07", "14:00", "FOMC政策金利発表",                      "🏦", "high",   ""),
    ("2025-06-18", "14:00", "FOMC政策金利発表",                      "🏦", "high",   ""),
    ("2025-07-30", "14:00", "FOMC政策金利発表",                      "🏦", "high",   ""),
    ("2025-09-17", "14:00", "FOMC政策金利発表",                      "🏦", "high",   ""),
    ("2025-11-05", "14:00", "FOMC政策金利発表",                      "🏦", "high",   ""),
    ("2025-12-17", "14:00", "FOMC政策金利発表",                      "🏦", "high",   ""),
    ("2026-01-28", "14:00", "FOMC政策金利発表",                      "🏦", "high",   ""),
    ("2026-03-18", "14:00", "FOMC政策金利発表",                      "🏦", "high",   ""),
    ("2026-04-29", "14:00", "FOMC政策金利発表",                      "🏦", "high",   ""),
    ("2026-06-18", "14:00", "FOMC政策金利発表",                      "🏦", "high",   ""),
    ("2026-07-28", "14:00", "FOMC政策金利発表",                      "🏦", "high",   ""),
    ("2026-09-15", "14:00", "FOMC政策金利発表",                      "🏦", "high",   ""),
    ("2026-11-03", "14:00", "FOMC政策金利発表",                      "🏦", "high",   ""),
    ("2026-12-15", "14:00", "FOMC政策金利発表",                      "🏦", "high",   ""),
]

_ET_TZ = pytz.timezone("America/New_York")

def _eco_event_to_jst(date_str: str, time_et_str: str) -> datetime:
    """ET日付+時刻文字列 → JST datetime に変換（サマータイム自動対応）"""
    h, m = map(int, time_et_str.split(":"))
    y, mo, d = map(int, date_str.split("-"))
    dt_et = _ET_TZ.localize(datetime(y, mo, d, h, m))
    return dt_et.astimezone(JST)




# ===========================
# 日本経済カレンダー
# ===========================
_JP_ECO_CALENDAR: List[Tuple[str, str, str, str, str, str]] = [
    # BOJ 金融政策決定会合（結果発表日、JST午後）
    ("2025-01-24", "12:00", "BOJ 金融政策決定会合",            "🏦", "high",   "1月"),
    ("2025-03-19", "12:00", "BOJ 金融政策決定会合",            "🏦", "high",   "3月"),
    ("2025-05-01", "12:00", "BOJ 金融政策決定会合",            "🏦", "high",   "4-5月"),
    ("2025-06-17", "12:00", "BOJ 金融政策決定会合",            "🏦", "high",   "6月"),
    ("2025-07-31", "12:00", "BOJ 金融政策決定会合",            "🏦", "high",   "7月"),
    ("2025-09-19", "12:00", "BOJ 金融政策決定会合",            "🏦", "high",   "9月"),
    ("2025-10-29", "12:00", "BOJ 金融政策決定会合",            "🏦", "high",   "10月"),
    ("2025-12-19", "12:00", "BOJ 金融政策決定会合",            "🏦", "high",   "12月"),
    ("2026-01-24", "12:00", "BOJ 金融政策決定会合",            "🏦", "high",   "1月"),
    ("2026-03-19", "12:00", "BOJ 金融政策決定会合",            "🏦", "high",   "3月"),
    ("2026-04-28", "12:00", "BOJ 金融政策決定会合",            "🏦", "high",   "4月"),
    ("2026-06-17", "12:00", "BOJ 金融政策決定会合",            "🏦", "high",   "6月"),
    ("2026-07-31", "12:00", "BOJ 金融政策決定会合",            "🏦", "high",   "7月"),
    ("2026-09-18", "12:00", "BOJ 金融政策決定会合",            "🏦", "high",   "9月"),
    ("2026-10-29", "12:00", "BOJ 金融政策決定会合",            "🏦", "high",   "10月"),
    ("2026-12-18", "12:00", "BOJ 金融政策決定会合",            "🏦", "high",   "12月"),
    # 日本CPI（全国消費者物価指数）毎月第3金曜前後 8:30 JST
    ("2025-01-24", "08:30", "日本CPI（消費者物価指数）",       "💴", "high",   "12月分"),
    ("2025-02-21", "08:30", "日本CPI（消費者物価指数）",       "💴", "high",   "1月分"),
    ("2025-03-21", "08:30", "日本CPI（消費者物価指数）",       "💴", "high",   "2月分"),
    ("2025-04-18", "08:30", "日本CPI（消費者物価指数）",       "💴", "high",   "3月分"),
    ("2025-05-23", "08:30", "日本CPI（消費者物価指数）",       "💴", "high",   "4月分"),
    ("2025-06-20", "08:30", "日本CPI（消費者物価指数）",       "💴", "high",   "5月分"),
    ("2025-07-18", "08:30", "日本CPI（消費者物価指数）",       "💴", "high",   "6月分"),
    ("2025-08-22", "08:30", "日本CPI（消費者物価指数）",       "💴", "high",   "7月分"),
    ("2025-09-19", "08:30", "日本CPI（消費者物価指数）",       "💴", "high",   "8月分"),
    ("2025-10-24", "08:30", "日本CPI（消費者物価指数）",       "💴", "high",   "9月分"),
    ("2025-11-21", "08:30", "日本CPI（消費者物価指数）",       "💴", "high",   "10月分"),
    ("2025-12-19", "08:30", "日本CPI（消費者物価指数）",       "💴", "high",   "11月分"),
    ("2026-01-23", "08:30", "日本CPI（消費者物価指数）",       "💴", "high",   "12月分"),
    ("2026-02-20", "08:30", "日本CPI（消費者物価指数）",       "💴", "high",   "1月分"),
    ("2026-03-20", "08:30", "日本CPI（消費者物価指数）",       "💴", "high",   "2月分"),
    ("2026-04-17", "08:30", "日本CPI（消費者物価指数）",       "💴", "high",   "3月分"),
    ("2026-05-22", "08:30", "日本CPI（消費者物価指数）",       "💴", "high",   "4月分"),
    ("2026-06-19", "08:30", "日本CPI（消費者物価指数）",       "💴", "high",   "5月分"),
    ("2026-07-17", "08:30", "日本CPI（消費者物価指数）",       "💴", "high",   "6月分"),
    ("2026-08-21", "08:30", "日本CPI（消費者物価指数）",       "💴", "high",   "7月分"),
    ("2026-09-18", "08:30", "日本CPI（消費者物価指数）",       "💴", "high",   "8月分"),
    ("2026-10-23", "08:30", "日本CPI（消費者物価指数）",       "💴", "high",   "9月分"),
    ("2026-11-20", "08:30", "日本CPI（消費者物価指数）",       "💴", "high",   "10月分"),
    ("2026-12-18", "08:30", "日本CPI（消費者物価指数）",       "💴", "high",   "11月分"),
    # 日本失業率・有効求人倍率（毎月末）
    ("2025-01-31", "08:30", "日本失業率 / 有効求人倍率",       "👷", "medium", "12月分"),
    ("2025-02-28", "08:30", "日本失業率 / 有効求人倍率",       "👷", "medium", "1月分"),
    ("2025-03-28", "08:30", "日本失業率 / 有効求人倍率",       "👷", "medium", "2月分"),
    ("2025-04-25", "08:30", "日本失業率 / 有効求人倍率",       "👷", "medium", "3月分"),
    ("2025-05-30", "08:30", "日本失業率 / 有効求人倍率",       "👷", "medium", "4月分"),
    ("2025-06-27", "08:30", "日本失業率 / 有効求人倍率",       "👷", "medium", "5月分"),
    ("2025-07-29", "08:30", "日本失業率 / 有効求人倍率",       "👷", "medium", "6月分"),
    ("2025-08-29", "08:30", "日本失業率 / 有効求人倍率",       "👷", "medium", "7月分"),
    ("2025-09-30", "08:30", "日本失業率 / 有効求人倍率",       "👷", "medium", "8月分"),
    ("2025-10-31", "08:30", "日本失業率 / 有効求人倍率",       "👷", "medium", "9月分"),
    ("2025-11-28", "08:30", "日本失業率 / 有効求人倍率",       "👷", "medium", "10月分"),
    ("2025-12-26", "08:30", "日本失業率 / 有効求人倍率",       "👷", "medium", "11月分"),
    ("2026-01-30", "08:30", "日本失業率 / 有効求人倍率",       "👷", "medium", "12月分"),
    ("2026-02-27", "08:30", "日本失業率 / 有効求人倍率",       "👷", "medium", "1月分"),
    ("2026-03-31", "08:30", "日本失業率 / 有効求人倍率",       "👷", "medium", "2月分"),
    ("2026-04-28", "08:30", "日本失業率 / 有効求人倍率",       "👷", "medium", "3月分"),
    ("2026-05-29", "08:30", "日本失業率 / 有効求人倍率",       "👷", "medium", "4月分"),
    ("2026-06-30", "08:30", "日本失業率 / 有効求人倍率",       "👷", "medium", "5月分"),
    ("2026-07-31", "08:30", "日本失業率 / 有効求人倍率",       "👷", "medium", "6月分"),
    # 日銀短観（四半期）
    ("2025-04-01", "08:50", "日銀短観（企業短期経済観測）",    "📊", "high",   "3月調査"),
    ("2025-07-01", "08:50", "日銀短観（企業短期経済観測）",    "📊", "high",   "6月調査"),
    ("2025-10-01", "08:50", "日銀短観（企業短期経済観測）",    "📊", "high",   "9月調査"),
    ("2026-01-05", "08:50", "日銀短観（企業短期経済観測）",    "📊", "high",   "12月調査"),
    ("2026-04-01", "08:50", "日銀短観（企業短期経済観測）",    "📊", "high",   "3月調査"),
    ("2026-07-01", "08:50", "日銀短観（企業短期経済観測）",    "📊", "high",   "6月調査"),
    ("2026-10-01", "08:50", "日銀短観（企業短期経済観測）",    "📊", "high",   "9月調査"),
    # 日本GDP速報（四半期）
    ("2025-02-17", "08:50", "日本GDP速報値",                  "📈", "high",   "2024Q4"),
    ("2025-05-15", "08:50", "日本GDP速報値",                  "📈", "high",   "2025Q1"),
    ("2025-08-15", "08:50", "日本GDP速報値",                  "📈", "high",   "2025Q2"),
    ("2025-11-17", "08:50", "日本GDP速報値",                  "📈", "high",   "2025Q3"),
    ("2026-02-16", "08:50", "日本GDP速報値",                  "📈", "high",   "2025Q4"),
    ("2026-05-15", "08:50", "日本GDP速報値",                  "📈", "high",   "2026Q1"),
]


# FMP イベント名 → カレンダー表示名 + beat判定の向き（True = actual > estimate が良い）
_FMP_EVENT_MAP: List[Tuple[str, str, bool]] = [
    ("Nonfarm Payrolls",          "非農業部門雇用者数(NFP) / 失業率",  True),   # 多いほど良い
    ("Unemployment Rate",         "非農業部門雇用者数(NFP) / 失業率",  False),  # 低いほど良い
    ("CPI",                       "CPI（消費者物価指数）",             False),  # 低いほど良い
    ("Consumer Price Index",      "CPI（消費者物価指数）",             False),
    ("ISM Manufacturing",         "ISM製造業景気指数",                 True),   # 高いほど良い
    ("ISM Non-Manufacturing",     "ISM非製造業景気指数",               True),
    ("ISM Services",              "ISM非製造業景気指数",               True),
    ("Fed Interest Rate",         "FOMC（連邦公開市場委員会）",        False),  # 利下げ=緩和
    ("Federal Funds",             "FOMC（連邦公開市場委員会）",        False),
]

# 実績値の単位フォーマット: FMPイベント名一部 → (単位文字列, 小数桁)
_FMP_UNIT_MAP: List[Tuple[str, str, int]] = [
    ("Nonfarm Payrolls",    "K",   0),   # 256K jobs
    ("Unemployment",        "%",   1),
    ("CPI",                 "%",   1),
    ("Consumer Price",      "%",   1),
    ("ISM",                 "",    1),   # index value
    ("Fed Interest",        "%",   2),
    ("Federal Funds",       "%",   2),
]



@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_eco_actuals_bls() -> Dict[str, Dict]:
    """
    BLS APIから主要米国経済指標の実績値を取得（APIキー不要）。
    CPI(YoY)・失業率・NFP(MoM変化)を対応するカレンダー日付にマッピング。
    """
    try:
        now = datetime.now()
        resp = requests.post(
            "https://api.bls.gov/publicAPI/v2/timeseries/data/",
            json={
                "seriesid": ["CUSR0000SA0", "LNS14000000", "CES0000000001"],
                "startyear": str(now.year - 2),
                "endyear":   str(now.year),
            },
            headers={"Content-Type": "application/json"},
            timeout=15,
        )
        if resp.status_code != 200:
            return {}
        payload = resp.json()
        if payload.get("status") != "REQUEST_SUCCEEDED":
            return {}

        # (year, month) → value
        series_ym: Dict[str, Dict] = {}
        for s in payload.get("Results", {}).get("series", []):
            sid  = s["seriesID"]
            vals: Dict[tuple, float] = {}
            for item in s.get("data", []):
                p = item.get("period", "")
                if not p.startswith("M"):
                    continue
                m = int(p[1:])
                y = int(item["year"])
                try:
                    vals[(y, m)] = float(item["value"])
                except (ValueError, TypeError):
                    pass
            series_ym[sid] = vals

        cpi = series_ym.get("CUSR0000SA0", {})
        une = series_ym.get("LNS14000000", {})
        nfp = series_ym.get("CES0000000001", {})

        results: Dict[str, Dict] = {}
        for date_str, _time_et, name, _icon, _impact, _note in _US_ECO_CALENDAR:
            try:
                ev = datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                continue

            # データ対象月 = リリース月の1ヶ月前
            dm = ev.month - 1 if ev.month > 1 else 12
            dy = ev.year  if ev.month > 1 else ev.year - 1
            pm = dm - 1   if dm > 1 else 12
            py = dy       if dm > 1 else dy - 1

            if date_str in results:
                continue

            if "CPI" in name or "消費者物価" in name:
                cur = cpi.get((dy, dm))
                prv = cpi.get((dy - 1, dm))   # 1年前同月 → YoY
                p_cur = cpi.get((py, pm))
                p_prv = cpi.get((py - 1, pm))
                if cur and prv:
                    actual   = round((cur / prv - 1) * 100, 2)
                    previous = round((p_cur / p_prv - 1) * 100, 2) if p_cur and p_prv else None
                    results[date_str] = {
                        "name": name, "actual": actual, "estimate": None,
                        "previous": previous, "beat": None, "unit": "%",
                    }

            elif "非農業部門" in name or "NFP" in name:
                cur_nfp = nfp.get((dy, dm))
                prv_nfp = nfp.get((py, pm))
                cur_une = une.get((dy, dm))
                prv_une = une.get((py, pm))
                if cur_nfp and prv_nfp:
                    nfp_chg  = round(cur_nfp - prv_nfp, 0)
                    prev_chg = None
                    pp_nfp = nfp.get((py - 1 if pm == 12 else py, pm - 1 if pm > 1 else 12))
                    if pp_nfp:
                        prev_chg = round(prv_nfp - pp_nfp, 0)
                    results[date_str] = {
                        "name": name, "actual": nfp_chg, "estimate": None,
                        "previous": prev_chg, "beat": None, "unit": "K",
                        "unemployment": cur_une, "prev_unemployment": prv_une,
                    }

        return results
    except Exception as e:
        logger.warning(f"[bls] {e}")
        return {}



@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_eco_actuals_fred() -> Dict[str, Dict]:
    """
    FRED CSV エンドポイントから ISM PMI・日本マクロ指標を取得（APIキー不要）。
    ISM Manufacturing: NAPMSA / Japan CPI: JPNCPIALLMINMEI 等
    """
    FRED_MAP = [
        # (series_id, calendar_name_substr, unit, higher_is_good, is_japan)
        ("NAPMSA",           "ISM製造業",      "",   True,  False),
        ("NMFSA",            "ISM非製造業",    "",   True,  False),
        ("JPNCPIALLMINMEI",  "日本CPI",        "%",  False, True),
        ("LRUN74TTJPM156S",  "日本失業率",     "%",  False, True),
        ("IRSTCB01JPM156N",  "BOJ",           "%",  False, True),
    ]
    BASE = "https://fred.stlouisfed.org/graph/fredgraph.csv?id="

    def _fetch_series(sid: str) -> Dict[str, float]:
        """date_str -> value"""
        try:
            r = requests.get(BASE + sid, timeout=12,
                             headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code != 200:
                return {}
            vals = {}
            for line in r.text.strip().split("\n")[1:]:
                parts = line.split(",")
                if len(parts) < 2 or parts[1].strip() == ".":
                    continue
                try:
                    vals[parts[0].strip()] = float(parts[1].strip())
                except ValueError:
                    pass
            return vals
        except Exception as e:
            logger.debug(f"[fred] {sid}: {e}")
            return {}

    results: Dict[str, Dict] = {}

    for sid, name_sub, unit, higher_good, is_jp in FRED_MAP:
        series = _fetch_series(sid)
        if not series:
            continue

        sorted_dates = sorted(series.keys())
        cal = _JP_ECO_CALENDAR if is_jp else _US_ECO_CALENDAR

        for date_str, _time, ev_name, _icon, _impact, _note in cal:
            if name_sub not in ev_name:
                continue
            if date_str in results:
                continue
            try:
                ev = datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                continue

            # データ対象月 = リリース月の1ヶ月前
            dm = ev.month - 1 if ev.month > 1 else 12
            dy = ev.year  if ev.month > 1 else ev.year - 1
            key = f"{dy}-{dm:02d}-01"

            # 直近の該当データ日付を探す
            match_key = None
            for dk in sorted_dates:
                if dk[:7] == key[:7]:
                    match_key = dk
                    break
            if not match_key:
                # 同月が見つからなければ最も近い過去
                past = [d for d in sorted_dates if d <= date_str]
                if past:
                    match_key = past[-1]

            if not match_key:
                continue

            actual = series[match_key]
            prev_key = sorted_dates[sorted_dates.index(match_key) - 1] if sorted_dates.index(match_key) > 0 else None
            previous = series.get(prev_key) if prev_key else None

            if unit == "%" and name_sub == "日本CPI":
                # YoY計算
                try:
                    idx = sorted_dates.index(match_key)
                    yoy_key = sorted_dates[idx - 12] if idx >= 12 else None
                    if yoy_key:
                        actual_yoy = round((actual / series[yoy_key] - 1) * 100, 2)
                        prev_idx = idx - 1
                        prev_yoy_key = sorted_dates[prev_idx - 12] if prev_idx >= 12 else None
                        previous_yoy = round((series[sorted_dates[prev_idx]] / series[prev_yoy_key] - 1) * 100, 2) if prev_yoy_key else None
                        actual = actual_yoy
                        previous = previous_yoy
                    else:
                        continue
                except Exception:
                    continue

            beat = None
            results[date_str] = {
                "name": ev_name, "actual": round(actual, 2),
                "estimate": None, "previous": round(previous, 2) if previous else None,
                "beat": beat, "unit": unit,
            }

    return results

@st.cache_data(ttl=60 * 60 * 6, show_spinner=False)
def _fetch_eco_actuals_fmp() -> Dict[str, Dict]:
    """
    Financial Modeling Prep から米国経済イベントの実績値・予想値・前回値を取得。
    {date_str: {"name": str, "actual": float, "estimate": float|None,
                "previous": float|None, "beat": bool|None, "unit": str,
                "_debug_count": int, "_debug_sample": list}}
    FMP_API_KEY が未設定の場合は空辞書を返す。
    """
    if not FMP_API_KEY:
        return {}
    try:
        from_d = (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d")
        to_d   = datetime.now().strftime("%Y-%m-%d")

        items = None
        _last_status: int = 0
        _last_body: str = ""
        # v4 → v3 の順で試す
        for endpoint in [
            "https://financialmodelingprep.com/api/v4/economic_calendar",
            "https://financialmodelingprep.com/api/v3/economic_calendar",
        ]:
            try:
                r = requests.get(
                    endpoint,
                    params={"from": from_d, "to": to_d, "apikey": FMP_API_KEY},
                    timeout=12,
                )
                _last_status = r.status_code
                _last_body   = r.text[:400]
                if r.status_code == 200:
                    data = r.json()
                    # リスト形式（通常）
                    if isinstance(data, list) and len(data) > 0:
                        items = data
                        logger.info(f"[fmp] {endpoint} → {len(items)}件取得")
                        break
                    # {"data": [...]} ネスト形式（FMP v4 の一部プランで発生）
                    elif isinstance(data, dict) and isinstance(data.get("data"), list) and len(data["data"]) > 0:
                        items = data["data"]
                        logger.info(f"[fmp] {endpoint} (nested) → {len(items)}件取得")
                        break
                    elif isinstance(data, dict):
                        logger.warning(f"[fmp] {endpoint} error response: {str(data)[:200]}")
                    else:
                        logger.warning(f"[fmp] {endpoint} empty list returned")
                else:
                    logger.warning(f"[fmp] {endpoint} HTTP {r.status_code}: {_last_body[:100]}")
            except Exception as e:
                logger.debug(f"[fmp] {endpoint} 失敗: {e}")
                _last_body = str(e)[:200]

        if not items:
            err_msg = f"APIからデータが取得できませんでした (HTTP {_last_status}) | レスポンス: {_last_body[:200]}"
            return {"_debug_count": 0, "_debug_sample": [], "_debug_error": err_msg}

        # デバッグ用: 最初の5件のevent/country/dateを記録
        _sample = [
            {"event": x.get("event"), "country": x.get("country"), "date": x.get("date")}
            for x in items[:5]
        ]

        results: Dict[str, Dict] = {}
        for item in items:
            country = (item.get("country") or "").upper()
            if country != "US":
                continue
            raw_event = item.get("event") or ""
            date_raw  = (item.get("date") or "")[:10]   # "2025-12-10"
            if not date_raw:
                continue

            actual_raw   = item.get("actual")
            estimate_raw = item.get("estimate")
            previous_raw = item.get("previous")
            if actual_raw is None:
                continue

            # イベント名マッピング
            matched_name: Optional[str] = None
            beat_higher: Optional[bool] = None
            for fmp_kw, jp_name, higher_is_good in _FMP_EVENT_MAP:
                if fmp_kw.lower() in raw_event.lower():
                    matched_name  = jp_name
                    beat_higher   = higher_is_good
                    break
            if matched_name is None:
                continue

            try:
                actual   = float(actual_raw)
                estimate = float(estimate_raw) if estimate_raw is not None else None
                previous = float(previous_raw) if previous_raw is not None else None
            except (TypeError, ValueError):
                continue

            # beat判定
            beat: Optional[bool] = None
            if estimate is not None and beat_higher is not None:
                if beat_higher:
                    beat = actual > estimate
                else:
                    beat = actual < estimate

            # 単位
            unit = ""
            for kw, u, _dp in _FMP_UNIT_MAP:
                if kw.lower() in raw_event.lower():
                    unit = u
                    break

            # 日付 + イベント名をキーにして同日複数イベントを区別
            key = f"{date_raw}|{matched_name}"
            if key not in results:
                results[key] = {
                    "name":     matched_name,
                    "actual":   actual,
                    "estimate": estimate,
                    "previous": previous,
                    "beat":     beat,
                    "unit":     unit,
                    "raw_event": raw_event,
                }
            # 日付のみキーにも記録（後方互換・フォールバック用）
            if date_raw not in results:
                results[date_raw] = results[key]
        results["_debug_count"]  = len(items)
        results["_debug_sample"] = _sample
        return results
    except Exception as e:
        logger.warning(f"[fmp_eco] {e}")
        return {"_debug_error": str(e)[:200]}


@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_gspc_760d() -> Tuple[pd.Series, pd.Series, list]:
    """^GSPC 760日分の終値・日次リターン・日付リストを共有キャッシュで返す。
    _fetch_event_market_reactions / _fetch_sp500_event_volatility の共通基盤。"""
    try:
        raw = yf.download(
            "^GSPC",
            start=datetime.now() - timedelta(days=760),
            end=datetime.now(),
            progress=False, auto_adjust=True,
        )
        if raw.empty:
            return pd.Series(dtype=float), pd.Series(dtype=float), []
        if isinstance(raw.columns, pd.MultiIndex):
            try:
                sp = raw[("Close", "^GSPC")]
            except KeyError:
                sp = raw["Close"].iloc[:, 0]
        else:
            sp = raw["Close"]
        if isinstance(sp, pd.DataFrame):
            sp = sp.iloc[:, 0]
        sp   = sp.dropna().astype(float)
        rets = sp.pct_change().dropna()
        close_dates = [d.date() if hasattr(d, "date") else d for d in sp.index]
        return sp, rets, close_dates
    except Exception:
        return pd.Series(dtype=float), pd.Series(dtype=float), []


@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_event_market_reactions() -> Dict[str, float]:
    """過去の経済イベント日ごとのS&P500当日リターン（%）を返す。"""
    _sp, rets, close_dates = _fetch_gspc_760d()
    if rets.empty:
        return {}
    reactions: Dict[str, float] = {}
    now_date = datetime.now().date()
    for date_str, _time_et, _name, _icon, _impact, _note in _US_ECO_CALENDAR:
        ev_date = dt.date(*map(int, date_str.split("-")))
        if ev_date >= now_date:
            continue
        for offset in range(3):
            check = ev_date + timedelta(days=offset)
            if check not in close_dates:
                continue
            idx = close_dates.index(check)
            if idx >= len(rets):
                break
            val = rets.iloc[idx]
            if isinstance(val, pd.Series):
                val = val.iloc[0]
            r = float(val)
            if not pd.isna(r):
                reactions[date_str] = round(r * 100, 2)
            break
    return reactions


def _get_upcoming_us_eco_events(days_back: int = 3, days_ahead: int = 30) -> List[Dict]:
    """直近〜今後のイベントを返す（過去3日分も含む）"""
    now_jst = datetime.now(JST)
    cutoff_past  = now_jst - timedelta(days=days_back)
    cutoff_future = now_jst + timedelta(days=days_ahead)

    result = []
    for date_str, time_et, name, icon, impact, note in _US_ECO_CALENDAR:
        try:
            jst_dt = _eco_event_to_jst(date_str, time_et)
        except Exception:
            continue
        if cutoff_past <= jst_dt <= cutoff_future:
            delta_h = (jst_dt - now_jst).total_seconds() / 3600
            result.append({
                "date_str": date_str,
                "jst_dt":   jst_dt,
                "name":     name,
                "icon":     icon,
                "impact":   impact,
                "note":     note,
                "delta_h":  delta_h,
                "is_past":  jst_dt < now_jst,
            })
    result.sort(key=lambda x: x["jst_dt"])
    return result


@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_sp500_event_volatility() -> Dict[str, Any]:
    """
    過去2年分のS&P500データから、主要経済指標発表日の
    当日・翌日・前後3日ボラティリティを計算して返す
    """
    _sp, rets, close_dates = _fetch_gspc_760d()
    if rets.empty:
        return {}

    # イベント種別ごとに集計
    groups: Dict[str, List[float]] = {}
    for date_str, time_et, name, icon, impact, note in _US_ECO_CALENDAR:
        ev_date = dt.date(*map(int, date_str.split("-")))
        # 当日もしくは翌営業日を探す（最大2営業日の余裕）
        for offset in range(3):
            check = ev_date + timedelta(days=offset)
            if check not in close_dates:
                continue
            idx = close_dates.index(check)
            if idx >= len(rets):
                break
            val = rets.iloc[idx]
            # Series が返ってきた場合（MultiIndex残骸）にも対応
            if isinstance(val, pd.Series):
                val = val.iloc[0]
            r = float(val)
            if pd.isna(r):
                break
            groups.setdefault(name, []).append(r * 100)
            break

    # 各種別の統計を計算
    result: Dict[str, Dict] = {}
    icon_map   = {row[2]: row[3] for row in _US_ECO_CALENDAR}
    impact_map = {row[2]: row[4] for row in _US_ECO_CALENDAR}
    for name, vals in groups.items():
        if len(vals) < 2:
            continue
        arr = np.array(vals)
        result[name] = {
            "icon":         icon_map.get(name, "📊"),
            "impact":       impact_map.get(name, "medium"),
            "n":            len(arr),
            "avg_abs":      float(np.mean(np.abs(arr))),
            "avg_ret":      float(np.mean(arr)),
            "up_rate":      float(np.mean(arr > 0) * 100),
            "max_up":       float(np.max(arr)),
            "max_dn":       float(np.min(arr)),
            "std":          float(np.std(arr)),
            "returns":      arr.tolist(),
        }
    return result


def render_economic_events_section(preloaded: dict | None = None):
    """📅 米国経済イベントカレンダー × ボラティリティ分析セクション

    preloaded: main() で事前並列取得したデータ辞書。
        {"reactions": dict, "fmp": dict, "bls": dict, "fred": dict}
    None の場合は従来どおり逐次取得する。
    """
    st.markdown('<a id="eco-calendar"></a>', unsafe_allow_html=True)
    st.markdown(
        '<div style="background:linear-gradient(135deg,#0f2027,#203a43,#2c5364);'
        'border-radius:12px;padding:14px 20px;margin-bottom:8px;">'
        '<div style="font-size:20px;font-weight:800;color:#f0f4f8;">'
        '📅 米国経済イベントカレンダー</div>'
        '<div style="font-size:12px;color:#94a3b8;margin-top:2px;">'
        '主要指標の発表日時（日本時間）と、過去の株価ボラティリティ（S&amp;P500）を表示します。'
        '</div></div>',
        unsafe_allow_html=True,
    )

    tab_cal, tab_jp, tab_vol = st.tabs(["📆 米国 発表スケジュール", "🇯🇵 日本 マクロ指標", "📊 過去の株価変動実績"])

    # ── タブ①: カレンダー ──────────────────────────────────
    with tab_cal:
        days_ahead = st.slider("今後何日分を表示", 7, 60, 30, key="eco_days_ahead")
        events = _get_upcoming_us_eco_events(days_back=180, days_ahead=days_ahead)

        if preloaded is not None:
            # 事前並列取得済みデータを使用（待ち時間なし）
            reactions   = preloaded["reactions"]
            eco_actuals = dict(preloaded["fmp"])
            _fmp_has_data = any(
                isinstance(v, dict) for k, v in eco_actuals.items()
                if not k.startswith("_")
            )
            if not _fmp_has_data:
                for _k, _v in preloaded["bls"].items():
                    if _k not in eco_actuals:
                        eco_actuals[_k] = _v
            for _k, _v in preloaded["fred"].items():
                if _k not in eco_actuals:
                    eco_actuals[_k] = _v
        else:
            # fallback: 逐次取得
            reactions = _fetch_event_market_reactions()
            eco_actuals = _fetch_eco_actuals_fmp()
            _fmp_has_data = any(
                isinstance(v, dict) for k, v in eco_actuals.items()
                if not k.startswith("_")
            )
            if not _fmp_has_data:
                _bls = _fetch_eco_actuals_bls()
                for _k, _v in _bls.items():
                    if _k not in eco_actuals:
                        eco_actuals[_k] = _v
            _fred = _fetch_eco_actuals_fred()
            for _k, _v in _fred.items():
                if _k not in eco_actuals:
                    eco_actuals[_k] = _v
        has_fmp = bool(eco_actuals)

        now_jst = datetime.now(JST)
        impact_color = {"high": "#ef4444", "medium": "#f59e0b", "low": "#6b7280"}
        impact_label = {"high": "🔴 高", "medium": "🟡 中", "low": "🟢 低"}

        if not events:
            st.info(f"今後{days_ahead}日間に主要イベントはありません。")
        else:
            rows_html = ""
            for ev in events:
                jst_str     = ev["jst_dt"].strftime("%m/%d(%a) %H:%M JST")
                delta_h     = ev["delta_h"]
                is_past     = ev["is_past"]
                ev_date_str = ev.get("date_str")

                # FMP 実績データ（日付+名前で先に探し、なければ日付のみで検索）
                fmp = None
                if ev_date_str:
                    fmp = eco_actuals.get(f"{ev_date_str}|{ev['name']}")
                    if fmp is None:
                        fmp = eco_actuals.get(ev_date_str)

                # ── 実績/予想セル ──────────────────────────────────
                if is_past and fmp and fmp.get("actual") is not None:
                    actual   = fmp["actual"]
                    estimate = fmp.get("estimate")
                    previous = fmp.get("previous")
                    unit     = fmp.get("unit", "")
                    beat     = fmp.get("beat")

                    # 単位変換・フォーマット
                    def _fmt(v, u):
                        if v is None:
                            return "—"
                        if u == "K":
                            return f"{v:,.0f}K"
                        elif u == "%":
                            return f"{v:.2f}%"
                        else:
                            return f"{v:.1f}"

                    act_str  = _fmt(actual, unit)
                    est_str  = _fmt(estimate, unit) if estimate is not None else None
                    prev_str = _fmt(previous, unit) if previous is not None else None

                    if beat is True:
                        beat_badge = "<span style='background:#166534;color:#86efac;padding:1px 5px;border-radius:4px;font-size:10px;font-weight:700'>👍 予想上回り</span>"
                    elif beat is False:
                        beat_badge = "<span style='background:#7f1d1d;color:#fca5a5;padding:1px 5px;border-radius:4px;font-size:10px;font-weight:700'>👎 予想下回り</span>"
                    else:
                        beat_badge = ""

                    actual_html = f"<span style='font-weight:700;color:#f1f5f9'>{act_str}</span>"
                    if est_str:
                        actual_html += f"<span style='color:#94a3b8;font-size:10px'> 予想:{est_str}</span>"
                    if prev_str:
                        actual_html += f"<span style='color:#64748b;font-size:10px'> 前回:{prev_str}</span>"
                    if beat_badge:
                        actual_html += f"<br>{beat_badge}"
                    actual_cell = actual_html
                elif not is_past:
                    actual_cell = "<span style='color:#64748b;font-size:11px'>未発表</span>"
                else:
                    actual_cell = "<span style='color:#475569;font-size:11px'>—</span>"

                # ── 株価反応セル ───────────────────────────────────
                if is_past:
                    timing = f"<span style='color:#6b7280'>✅ {abs(delta_h/24):.0f}日前</span>"
                    sp_ret = reactions.get(ev_date_str) if ev_date_str else None
                    if sp_ret is not None:
                        react_color = "#22c55e" if sp_ret >= 0 else "#ef4444"
                        react_arrow = "▲" if sp_ret >= 0 else "▼"
                        react_sign  = "+" if sp_ret >= 0 else ""
                        react_html  = (
                            f"<span style='color:{react_color};font-weight:700;font-size:13px'>"
                            f"{react_arrow} {react_sign}{sp_ret:.2f}%</span>"
                            f"<span style='color:#6b7280;font-size:10px'> S&P500</span>"
                        )
                    else:
                        react_html = "<span style='color:#475569;font-size:11px'>—</span>"
                elif delta_h < 2:
                    timing = f"<span style='color:#ef4444;font-weight:bold'>🔴 まもなく ({delta_h*60:.0f}分後)</span>"
                    react_html = "<span style='color:#f59e0b;font-size:11px'>発表待ち</span>"
                elif delta_h < 24:
                    timing = f"<span style='color:#f59e0b;font-weight:bold'>🟡 本日 ({delta_h:.1f}時間後)</span>"
                    react_html = "<span style='color:#94a3b8;font-size:11px'>—</span>"
                elif delta_h < 48:
                    timing = f"<span style='color:#fbbf24'>🟠 明日 ({delta_h:.0f}時間後)</span>"
                    react_html = "<span style='color:#94a3b8;font-size:11px'>—</span>"
                else:
                    timing = f"<span style='color:#94a3b8'>⚪ {delta_h/24:.0f}日後</span>"
                    react_html = "<span style='color:#94a3b8;font-size:11px'>—</span>"

                note_badge = (f"<span style='background:#1e3a5f;color:#93c5fd;"
                              f"padding:1px 6px;border-radius:8px;font-size:11px'>"
                              f"{ev['note']}</span> " if ev["note"] else "")
                imp_col = impact_color.get(ev["impact"], "#6b7280")
                imp_lbl = impact_label.get(ev["impact"], "")
                row_style = "opacity:0.75;" if is_past else ""

                rows_html += (
                    f"<tr style='border-bottom:1px solid #1e293b;{row_style}'>"
                    f"<td style='padding:7px 8px;white-space:nowrap'>{jst_str}</td>"
                    f"<td style='padding:7px 8px'>{ev['icon']} <b>{ev['name']}</b> {note_badge}</td>"
                    f"<td style='padding:7px 8px;color:{imp_col};white-space:nowrap'>{imp_lbl}</td>"
                    f"<td style='padding:7px 8px'>{timing}</td>"
                    f"<td style='padding:7px 8px'>{actual_cell}</td>"
                    f"<td style='padding:7px 8px;white-space:nowrap'>{react_html}</td>"
                    f"</tr>"
                )

            # ヘッダー行（FMPキーあり/なしで列変更）
            actual_th = (
                "<th style='padding:6px 8px;text-align:left'>実績 / 予想</th>"
                if has_fmp else
                "<th style='padding:6px 8px;text-align:left'>実績</th>"
            )
            st.markdown(
                f"<table style='width:100%;border-collapse:collapse;font-size:13px'>"
                f"<thead><tr style='color:#64748b;border-bottom:2px solid #334155'>"
                f"<th style='padding:6px 8px;text-align:left'>発表日時（JST）</th>"
                f"<th style='padding:6px 8px;text-align:left'>指標名</th>"
                f"<th style='padding:6px 8px;text-align:left'>影響度</th>"
                f"<th style='padding:6px 8px;text-align:left'>タイミング</th>"
                f"{actual_th}"
                f"<th style='padding:6px 8px;text-align:left'>株価反応（当日）</th>"
                f"</tr></thead><tbody>{rows_html}</tbody></table>",
                unsafe_allow_html=True,
            )
            if not has_fmp:
                st.caption("💡 `FMP_API_KEY` を secrets.toml に設定すると「実績 vs 予想（予想上回り/下回り）」が表示されます。無料登録: financialmodelingprep.com")
            st.caption("📌 株価反応はS&P500の発表日当日の終値騰落率。")

        # FMP 診断パネル（FMPキー設定済みの場合のみ表示）
        if FMP_API_KEY:
            _dbg_count  = eco_actuals.get("_debug_count", "キャッシュ済み")
            _dbg_sample = eco_actuals.get("_debug_sample", [])
            _dbg_err    = eco_actuals.get("_debug_error")
            _matched    = sum(1 for k, v in eco_actuals.items()
                              if not k.startswith("_") and isinstance(v, dict))
            with st.expander("🔧 FMP診断（デバッグ）", expanded=False):
                if _dbg_err:
                    st.error(f"FMP APIエラー: {_dbg_err}")
                    st.caption(
                        "**よくある原因:** ①FMPの無料プランは経済カレンダーが制限される場合あり "
                        "②APIキーが無効・期限切れ ③FMP側のサーバーエラー\n\n"
                        "**対処:** サイドバーの「🔄 キャッシュクリア」→再読み込みで再試行。"
                        "エラーが続く場合は [financialmodelingprep.com](https://financialmodelingprep.com) でプラン確認。"
                    )
                else:
                    st.write(f"**FMP取得件数:** {_dbg_count}件　**カレンダー一致:** {_matched}件")
                    if _dbg_sample:
                        st.write("**FMPサンプル（最初の5件）:**")
                        st.json(_dbg_sample)
                    else:
                        st.warning("FMPからデータが取得できていません。キャッシュクリアを試してください。")
        st.caption(
            "⏰ 8:30 AM ET = 夏時間21:30 JST / 冬時間22:30 JST　"
            "| 10:00 AM ET = 夏時間23:00 JST / 冬時間00:00 JST(翌日)　"
            "| FOMC 2:00 PM ET = 夏時間3:00 JST(翌日) / 冬時間4:00 JST(翌日)　"
            "※ 日程は近似値。investing.com等でご確認ください。"
        )


    # ── タブ②: 日本マクロ指標 ──────────────────────────────────────
    with tab_jp:
        now_jst_jp = datetime.now(JST)
        days_ahead_jp = st.slider("今後何日分を表示", 7, 90, 60, key="jp_eco_days_ahead")
        cutoff_past_jp  = now_jst_jp - timedelta(days=180)
        cutoff_future_jp = now_jst_jp + timedelta(days=days_ahead_jp)

        jp_events = []
        for date_str, time_jst, name, icon, impact, note in _JP_ECO_CALENDAR:
            try:
                naive = datetime.strptime(f"{date_str} {time_jst}", "%Y-%m-%d %H:%M")
                jst_dt = JST.localize(naive)
            except Exception:
                continue
            if cutoff_past_jp <= jst_dt <= cutoff_future_jp:
                delta_h = (jst_dt - now_jst_jp).total_seconds() / 3600
                jp_events.append({
                    "date_str": date_str, "jst_dt": jst_dt,
                    "name": name, "icon": icon, "impact": impact,
                    "note": note, "delta_h": delta_h,
                    "is_past": jst_dt < now_jst_jp,
                })
        jp_events.sort(key=lambda x: x["jst_dt"])

        jp_actuals = preloaded["fred"] if preloaded is not None else _fetch_eco_actuals_fred()

        impact_color_jp = {"high": "#ef4444", "medium": "#f59e0b", "low": "#6b7280"}
        impact_label_jp = {"high": "🔴 高", "medium": "🟡 中", "low": "🟢 低"}

        if not jp_events:
            st.info(f"今後{days_ahead_jp}日間に主要イベントはありません。")
        else:
            rows_html_jp = ""
            for ev in jp_events:
                jst_str   = ev["jst_dt"].strftime("%m/%d(%a) %H:%M JST")
                delta_h   = ev["delta_h"]
                is_past   = ev["is_past"]
                dstr      = ev["date_str"]
                fmp_jp    = jp_actuals.get(dstr)

                if is_past and fmp_jp and fmp_jp.get("actual") is not None:
                    act  = fmp_jp["actual"]
                    prv  = fmp_jp.get("previous")
                    unit = fmp_jp.get("unit", "")
                    def _fmt_jp(v, u):
                        if v is None: return "—"
                        return f"{v:.2f}{u}"
                    act_html = f"<span style='font-weight:700;color:#f1f5f9'>{_fmt_jp(act, unit)}</span>"
                    if prv is not None:
                        act_html += f"<span style='color:#64748b;font-size:10px'> 前回:{_fmt_jp(prv, unit)}</span>"
                    actual_cell_jp = act_html
                elif not is_past:
                    actual_cell_jp = "<span style='color:#64748b;font-size:11px'>未発表</span>"
                else:
                    actual_cell_jp = "<span style='color:#475569;font-size:11px'>—</span>"

                if is_past:
                    timing_jp = f"<span style='color:#6b7280'>✅ {abs(delta_h/24):.0f}日前</span>"
                elif delta_h < 2:
                    timing_jp = f"<span style='color:#ef4444;font-weight:bold'>🔴 まもなく</span>"
                elif delta_h < 24:
                    timing_jp = f"<span style='color:#f59e0b;font-weight:bold'>🟡 本日</span>"
                elif delta_h < 48:
                    timing_jp = f"<span style='color:#fbbf24'>🟠 明日</span>"
                else:
                    timing_jp = f"<span style='color:#94a3b8'>⚪ {delta_h/24:.0f}日後</span>"

                note_badge_jp = (
                    f"<span style='background:#1e3a5f;color:#93c5fd;padding:1px 6px;border-radius:8px;font-size:11px'>{ev['note']}</span> "
                    if ev["note"] else ""
                )
                imp_col_jp = impact_color_jp.get(ev["impact"], "#6b7280")
                imp_lbl_jp = impact_label_jp.get(ev["impact"], "")
                row_style_jp = "opacity:0.75;" if is_past else ""

                rows_html_jp += (
                    f"<tr style='border-bottom:1px solid #1e293b;{row_style_jp}'>"
                    f"<td style='padding:7px 8px;white-space:nowrap'>{jst_str}</td>"
                    f"<td style='padding:7px 8px'>{ev['icon']} <b>{ev['name']}</b> {note_badge_jp}</td>"
                    f"<td style='padding:7px 8px;color:{imp_col_jp};white-space:nowrap'>{imp_lbl_jp}</td>"
                    f"<td style='padding:7px 8px'>{timing_jp}</td>"
                    f"<td style='padding:7px 8px'>{actual_cell_jp}</td>"
                    f"</tr>"
                )

            st.markdown(
                f"<table style='width:100%;border-collapse:collapse;font-size:13px'>"
                f"<thead><tr style='color:#64748b;border-bottom:2px solid #334155'>"
                f"<th style='padding:6px 8px;text-align:left'>発表日時（JST）</th>"
                f"<th style='padding:6px 8px;text-align:left'>指標名</th>"
                f"<th style='padding:6px 8px;text-align:left'>影響度</th>"
                f"<th style='padding:6px 8px;text-align:left'>タイミング</th>"
                f"<th style='padding:6px 8px;text-align:left'>実績</th>"
                f"</tr></thead><tbody>{rows_html_jp}</tbody></table>",
                unsafe_allow_html=True,
            )
            st.caption("📌 実績値はFRED（セントルイス連銀）から取得。日本CPI=前年同月比%、失業率=%。")
            st.caption("⏰ BOJ会合は結果発表の概算時刻。短観は8:50 JST発表。")


    # ── タブ③: ボラティリティ実績 ──────────────────────────
    with tab_vol:
        if not st.session_state.get("_vol_loaded"):
            st.info("「ボラティリティを分析」ボタンを押すと過去のS&P500変動データを取得します（初回のみ時間がかかります）。")
            if st.button("📊 ボラティリティを分析", key="vol_load_btn", type="primary"):
                st.session_state["_vol_loaded"] = True
                st.rerun()
            return

        with st.spinner("過去のS&P500ボラティリティを分析中..."):
            vol = _fetch_sp500_event_volatility()

        if not vol:
            st.warning("データを取得できませんでした。しばらく待ってから再読み込みしてください。")
            return

        st.markdown("#### 📈 イベント別 S&P500 当日平均変動幅（過去2年）")
        st.caption(
            "各経済指標の発表日当日のS&P500リターン（実績値）を集計しています。"
            "予想との乖離が大きいほど株価が動きやすい傾向があります。"
        )

        # ソート & バーチャート
        sorted_v = sorted(vol.items(), key=lambda x: -x[1]["avg_abs"])

        if PLOTLY_AVAILABLE:
            names  = [f"{v['icon']} {k}" for k, v in sorted_v]
            avgs   = [v["avg_abs"] for _, v in sorted_v]
            colors = ["#ef4444" if v["impact"] == "high" else "#f59e0b" for _, v in sorted_v]

            fig_bar = go.Figure(go.Bar(
                x=avgs, y=names, orientation="h",
                marker_color=colors,
                text=[f"{a:.2f}%" for a in avgs],
                textposition="outside",
                hovertemplate="<b>%{y}</b><br>平均変動幅: %{x:.2f}%<extra></extra>",
            ))
            fig_bar.update_layout(
                xaxis_title="当日平均変動幅（絶対値, %）",
                height=max(250, len(sorted_v) * 42),
                margin=dict(l=10, r=70, t=10, b=30),
                plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                font=dict(color="white"),
                xaxis=dict(gridcolor="rgba(255,255,255,0.1)"),
            )
            st.plotly_chart(fig_bar, width="stretch")

        # 統計テーブル
        st.markdown("#### 📋 詳細統計")
        rows = []
        for name, v in sorted_v:
            rows.append({
                "指標":       f"{v['icon']} {name}",
                "平均変動幅": f"{v['avg_abs']:.2f}%",
                "平均ﾘﾀｰﾝ":  f"{v['avg_ret']:+.2f}%",
                "上昇確率":   f"{v['up_rate']:.0f}%",
                "最大上昇":   f"{v['max_up']:+.2f}%",
                "最大下落":   f"{v['max_dn']:+.2f}%",
                "σ(標準偏差)": f"{v['std']:.2f}%",
                "サンプル":   f"{v['n']}回",
            })
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

        # 個別イベント詳細
        st.markdown("#### 🔍 個別イベントのリターン分布")
        sel = st.selectbox(
            "イベントを選択",
            options=list(vol.keys()),
            format_func=lambda k: f"{vol[k]['icon']} {k}",
            key="eco_vol_sel",
        )
        if sel and sel in vol:
            ev_data = vol[sel]
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("平均変動幅", f"{ev_data['avg_abs']:.2f}%",
                      help="当日の絶対リターン平均（予想外れ時はこれ以上動く可能性あり）")
            c2.metric("上昇確率",   f"{ev_data['up_rate']:.0f}%",
                      help="発表日にS&P500が上昇した割合（過去2年）")
            c3.metric("最大上昇",   f"{ev_data['max_up']:+.2f}%")
            c4.metric("最大下落",   f"{ev_data['max_dn']:+.2f}%")

            if PLOTLY_AVAILABLE and ev_data["returns"]:
                rets_arr = ev_data["returns"]
                fig_hist = go.Figure()
                fig_hist.add_trace(go.Histogram(
                    x=rets_arr, nbinsx=12,
                    marker_color="#3b82f6", opacity=0.8,
                    name="当日リターン",
                    hovertemplate="リターン: %{x:.2f}%<br>回数: %{y}回<extra></extra>",
                ))
                fig_hist.add_vline(x=0, line_dash="dash", line_color="white", opacity=0.5)
                fig_hist.update_layout(
                    title=f"{sel} — 当日リターン分布（{ev_data['n']}回, 過去2年）",
                    xaxis_title="S&P500 当日リターン（%）",
                    yaxis_title="回数",
                    height=300,
                    plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="white"),
                    xaxis=dict(gridcolor="rgba(255,255,255,0.1)", zeroline=True,
                               zerolinecolor="rgba(255,255,255,0.4)"),
                    yaxis=dict(gridcolor="rgba(255,255,255,0.1)"),
                    margin=dict(t=40, b=30),
                )
                st.plotly_chart(fig_hist, width="stretch")

        st.caption(
            "※ データはYahoo Finance (^GSPC) から取得。"
            "発表日の特定はFOMCを除き近似値のため、実際と1〜2日ずれることがあります。"
        )


def render_market_summary():
    """📊 Today's Market Snapshot — Fear & Greed より前に表示する全体概要"""
    st.markdown('<a id="market-snapshot"></a>', unsafe_allow_html=True)
    st.markdown(
        '<div style="background:linear-gradient(135deg,#0f172a,#1e293b);'
        'border-radius:12px;padding:16px 22px;margin-bottom:6px;">'
        '<div style="font-size:22px;font-weight:900;color:#f8fafc;letter-spacing:-0.5px;">'
        '📊 Today\'s Market Snapshot</div>'
        '<div style="font-size:12px;color:#94a3b8;margin-top:2px;">'
        + t('主要指標を自動集計した今日の概要。詳細は各セクションで確認できます。',
            'Auto-aggregated overview of today\'s key indicators. See each section for details.') +
        '</div>'
        '</div>',
        unsafe_allow_html=True,
    )

    with st.spinner("データ取得中..."):
        with ThreadPoolExecutor(max_workers=5) as _pool:
            _f_prices = _pool.submit(_fetch_summary_prices)
            _f_sent   = _pool.submit(compute_composite_sentiment)
            _f_pred   = _pool.submit(compute_us_prediction, "SP500")
            _f_sector = _pool.submit(compute_sector_rotation)
            _f_crisis = _pool.submit(compute_crisis_pattern_similarity)
        prices  = _f_prices.result()
        sent    = _f_sent.result()
        us_pred = _f_pred.result()
        sector  = _f_sector.result()
        crisis  = _f_crisis.result()

    # ── 総合判断 ─────────────────────────────────────────
    composite    = sent.get("composite",    50) if sent.get("ok") else 50
    us_composite = sent.get("us_composite", 50) if sent.get("ok") else 50
    jp_composite = sent.get("jp_composite", 50) if sent.get("ok") else 50

    if composite >= 68:
        oc, ol, oe = "#16a34a", "強気 Bullish",    "🟢"
    elif composite >= 55:
        oc, ol, oe = "#22c55e", "やや強気",         "🟡"
    elif composite >= 45:
        oc, ol, oe = "#f59e0b", "中立 Neutral",    "🟡"
    elif composite >= 32:
        oc, ol, oe = "#ef4444", "やや弱気",         "🟠"
    else:
        oc, ol, oe = "#dc2626", "弱気 Bearish",    "🔴"

    st.markdown(
        f'<div style="background:{oc}12;border:2px solid {oc}50;'
        f'border-radius:10px;padding:14px 18px;margin-bottom:14px;'
        f'display:flex;align-items:center;gap:14px;">'
        f'<div style="font-size:40px;line-height:1">{oe}</div>'
        f'<div>'
        f'<div style="font-size:20px;font-weight:900;color:{oc};">'
        f'総合判断: {ol}</div>'
        f'<div style="font-size:12px;color:#6b7280;margin-top:3px;">'
        f'AI Sentiment Score <b style="color:{oc}">{composite:.0f}/100</b>'
        f' &nbsp;|&nbsp; 🇺🇸 米国 {us_composite:.0f}'
        f' &nbsp;|&nbsp; 🇯🇵 日本 {jp_composite:.0f}'
        f'</div></div></div>',
        unsafe_allow_html=True,
    )

    # ── AIコメント（なぜこの判断か） ──────────────────────
    if sent.get("ok") and sent.get("components"):
        ai_comment_key = _lang_key("sentiment_ai_comment")
        _, col_ai_btn = st.columns([3, 1])
        with col_ai_btn:
            if st.button(t("🤖 AIに理由を聞く", "🤖 Ask AI"), key="btn_sentiment_ai", width="stretch"):
                comps_for_prompt = sent["components"]
                lines_data = [
                    f"  - {n}: {c['score']:.0f}/100 ({c['label']})"
                    for n, c in sorted(comps_for_prompt.items(), key=lambda x: x[1]["score"])
                ]
                comp_text = "\n".join(lines_data)
                prompt = (
                    "あなたは市場アナリストです。以下の市場センチメント指標データをもとに、"
                    f"なぜ総合スコアが{composite:.0f}/100（{ol}）になったのか、"
                    f"米国{us_composite:.0f}・日本{jp_composite:.0f}の地域差も含め説明してください。"
                    "さらに「このまま下落が続く可能性が高いのか、それとも一時的な調整で終わりそうか」"
                    "という今後の見通しも含めて、投資家向けに300字以内で簡潔に日本語で答えてください。\n\n"
                    f"指標スコア一覧（低い順）:\n{comp_text}"
                    + _lang_prompt_suffix()
                )
                with st.spinner(t("AI分析中...", "Analyzing with AI...")):
                    comment, used_model = call_ai_with_fallback(prompt, max_output_tokens=600, temperature=0.4)
                st.session_state[ai_comment_key] = (comment, used_model)

        if ai_comment_key in st.session_state:
            ai_txt, ai_model = st.session_state[ai_comment_key]
            st.markdown(
                f'<div style="background:#f0f9ff;border-left:4px solid #3b82f6;'
                f'padding:12px 16px;border-radius:6px;margin-bottom:12px;">'
                f'<div style="font-size:12px;color:#6b7280;margin-bottom:4px;">🤖 AI解説 ({ai_model})</div>'
                f'<div style="font-size:14px;color:#1e293b;line-height:1.6;">{ai_txt}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

    # ── スコアの内訳と判断理由（透明性セクション） ──────────
    if sent.get("ok") and sent.get("components"):
        _EXPLAIN = {
            "F&G Index":      ("CNNの投資家心理指数。",      "高い=市場が強欲(Greed)＝強気。低い=恐怖(Fear)＝弱気。"),
            "VIX水準":        ("S&P500の予想変動率。",       "低い(15以下)=市場が落ち着いている=強気。高い(30超)=投資家が怯えている=弱気。"),
            "VIX期間構造":    ("近い将来vs遠い将来の恐怖度。","長期>短期(順イールド)=正常=強気。逆転=パニック売り懸念=弱気。"),
            "Put/Call比率":   ("CBOE Put/Call Ratio。プット(下落ヘッジ)÷コール(上昇賭け)の出来高比。",
                              "高い(>1.0)=投資家が守りに入っている=弱気。ただし1.3超は逆張り底打ちサインになることも。"
                              "低い(<0.7)=強気一色=慢心の警戒ゾーン。"),
            "Put/Call(VXX代替)": ("VXX(VIX先物ETF)価格水準でPut/Call的な恐怖度を代替計測。",
                                 "低%ile=市場が『変動は来ない』と楽観=安心感強い=強気シグナル。"
                                 "ただし低すぎ(5%ile以下)は慢心警戒ゾーン。高%ile=恐怖ETFに資金流入=パニック=弱気だが逆張りチャンスにも。"),
            "価格モメンタム": ("S&P500の20日リターン。",     "上昇が続いている=トレンドが強い=強気。下落中=弱気。"),
            "Safe Haven需要": ("株vs国債の相対パフォーマンス。","国債より株が強い=リスクオン=強気。国債に資金が逃げている=弱気。"),
            "セクター配分":   ("テック株(攻め)vs公益株(守り)。","テックが公益を上回っている=リスクオン=強気。公益優位=守り型=弱気。"),
            "信用リスク選好": ("高リスク社債vs投資適格債。", "高リスク債が強い=信用環境良好=強気。逆転=信用収縮懸念=弱気。"),
            "ブレッドス":     ("主要ETFの騰落の広がり。",    "幅広く上昇している=健全な相場=強気。一部だけ上昇=偏り=中立。"),
            "日経225モメンタム":("日本株の20日リターン。",   "上昇が続いている=日本市場も強い=強気。"),
            "ドル円リスク":   ("ドル円の方向性。",            "円安(ドル高)=リスクオン=日本株に追い風=強気。円高=リスクオフ=弱気。"),
            "日本実現VIX":    ("日経の実際の変動率。",        "低い=市場が安定=強気。高い=乱高下=弱気。"),
        }

        with st.expander("🔍 なぜこのスコアか？ — 指標ごとの判断理由を見る", expanded=False):
            st.caption("各指標が今どの方向を指しているか。自分の判断と照らし合わせてください。")

            comps = sent["components"]
            # スコア順にソート
            sorted_comps = sorted(comps.items(), key=lambda x: -x[1]["score"])

            for name, comp in sorted_comps:
                sc   = comp["score"]
                lbl  = comp["label"]
                wt   = comp.get("weight", 0)

                if sc >= 68:   dc, di, dt = "#16a34a", "↑", "強気"
                elif sc >= 55: dc, di, dt = "#22c55e", "↑", "やや強気"
                elif sc >= 45: dc, di, dt = "#f59e0b", "→", "中立"
                elif sc >= 32: dc, di, dt = "#ef4444", "↓", "やや弱気"
                else:           dc, di, dt = "#dc2626", "↓", "弱気"

                intro, reason = _EXPLAIN.get(name, ("", ""))
                bar_w = int(sc)

                st.markdown(
                    f'<div style="border:1px solid #e2e8f0;border-radius:8px;'
                    f'padding:10px 14px;margin-bottom:8px;background:white;">'
                    # ヘッダ行
                    f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">'
                    f'<span style="font-size:13px;font-weight:700;color:#1e293b">{name}</span>'
                    f'<span style="font-size:13px;font-weight:900;color:{dc}">'
                    f'{di} {dt} &nbsp;<b style="font-size:16px">{sc:.0f}</b>/100'
                    f'&nbsp;<span style="font-size:10px;color:#94a3b8">重み {wt*100:.0f}%</span>'
                    f'</span></div>'
                    # スコアバー
                    f'<div style="background:#f1f5f9;border-radius:3px;height:6px;margin-bottom:6px">'
                    f'<div style="width:{bar_w}%;height:100%;background:{dc};border-radius:3px"></div></div>'
                    # 現在値
                    f'<div style="font-size:11px;color:#475569;margin-bottom:3px">'
                    f'📊 現在値: <b>{lbl}</b></div>'
                    # 説明
                    f'<div style="font-size:11px;color:#64748b">'
                    f'<b>この指標は？</b> {intro}<br>'
                    f'<b>今の読み方:</b> {reason}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

            # ── Put/Call 深掘り解説 ──────────────────────────────
            pc_comp = comps.get("Put/Call比率") or comps.get("Put/Call(VXX代替)")
            if pc_comp:
                pc_sc = pc_comp["score"]
                pc_lbl = pc_comp["label"]
                if pc_sc >= 80:
                    pc_state = "low"    # VXX低・P/C低 = 楽観・慢心
                elif pc_sc <= 25:
                    pc_state = "high"   # VXX高・P/C高 = パニック
                else:
                    pc_state = "normal"

                _PC_LOW_MSG = (
                    "**⚠️ 現在: 極度の楽観 (Put/Call が低水準)**\n\n"
                    "投資家がほとんどヘッジを買っていません。市場全体が「下落しない」と信じている状態です。\n\n"
                    "| 過去の類似局面 | Put/Call水準 | その後の値動き |\n"
                    "|---|---|---|\n"
                    "| 2020年2月（コロナ直前） | 歴史的低水準 | 翌月S&P500 **▼34%** 急落 |\n"
                    "| 2021年1月（ミームストック狂乱） | 0.4台まで低下 | 2022年に**▼25%**調整 |\n"
                    "| 2007年10月（リーマン前夜） | 極端に低い | 2008年に**▼57%**暴落 |\n\n"
                    "> **類推**: 楽観が行き過ぎると、わずかな悪材料で連鎖的な売りが発生しやすい。"
                    "現在は「守り」を意識しつつ、ポジションサイズを管理することを推奨します。"
                )
                _PC_HIGH_MSG = (
                    "**📉 現在: パニック的恐怖 (Put/Call が高水準)**\n\n"
                    "投資家が大量に下落ヘッジを買っています。悲観が支配的な状態です。\n\n"
                    "| 過去の類似局面 | Put/Call水準 | その後の値動き |\n"
                    "|---|---|---|\n"
                    "| 2020年3月（コロナショック底） | 1.5超 | 翌月から**▲35%**反発 |\n"
                    "| 2022年10月（FRB利上げ底） | 高水準 | 3ヶ月で**▲20%**反発 |\n"
                    "| 2011年8月（米国債格下げ） | 急騰 | 数週間で**▲15%**反発 |\n\n"
                    "> **類推**: 極端なパニック買いは逆張りの買いシグナルになることが多い。"
                    "ただし底を確認してからの行動が安全。焦りの売りには付き合わないこと。"
                )
                _PC_NORMAL_MSG = (
                    "**✅ 現在: 正常レンジ**\n\n"
                    "Put/Call比率は中立的な水準です。極端な楽観も悲観もない、バランスの取れた市場心理です。"
                )
                pc_msgs = {"low": _PC_LOW_MSG, "high": _PC_HIGH_MSG, "normal": _PC_NORMAL_MSG}
                pc_colors = {"low": "#fef3c7", "high": "#fee2e2", "normal": "#f0fdf4"}
                pc_borders = {"low": "#f59e0b", "high": "#ef4444", "normal": "#22c55e"}

                st.markdown("---")
                st.markdown("#### 📊 Put/Call比率(VXX代替) 深掘り解説")
                st.markdown(
                    f'<div style="background:{pc_colors[pc_state]};border-left:4px solid {pc_borders[pc_state]};'
                    f'border-radius:6px;padding:12px 16px;margin-bottom:8px;">'
                    f'<div style="font-size:11px;color:#475569;margin-bottom:4px">'
                    f'現在スコア: <b>{pc_sc:.0f}/100</b> &nbsp;|&nbsp; {pc_lbl}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                st.markdown(pc_msgs[pc_state])
                with st.expander("📖 Put/Call比率の仕組みをもっと詳しく"):
                    st.markdown(
                        "**Put/Call比率とは？**\n\n"
                        "- **Put オプション**: 「株が下がったら利益が出る」権利。下落ヘッジや空売り代替に使われる\n"
                        "- **Call オプション**: 「株が上がったら利益が出る」権利。上昇に賭けるときに使われる\n"
                        "- **P/C比率 = Put出来高 ÷ Call出来高**\n\n"
                        "| 水準 | 意味 | 市場心理 | 投資行動の示唆 |\n"
                        "|---|---|---|---|\n"
                        "| 1.3以上 | ヘッジ需要急増 | 極端な悲観 | 逆張り検討（底に近い可能性） |\n"
                        "| 0.9〜1.2 | やや守り優位 | 弱気 | 慎重 |\n"
                        "| 0.7〜0.9 | 中立 | ニュートラル | 標準的なポジション |\n"
                        "| 0.5〜0.7 | コール優位 | 強気 | 過熱に注意しつつ保有 |\n"
                        "| 0.5以下 | 楽観が支配的 | 極端な強気 | 慢心警戒、ヘッジ増加を検討 |\n\n"
                        "**VXX代替について**\n\n"
                        "CBOEのP/Cデータが取得できない場合、VXX（VIX先物ETF）の価格水準で代替します。"
                        "VXXが低い = 市場が将来の変動を恐れていない = 実質的にPut需要が低い状態と同義です。\n\n"
                        "> ⚠️ **注意**: Put/Call比率は **逆張り指標** です。極端な値が出たときが最も注目すべき局面ですが、"
                        "タイミングの特定は難しく、単独では使わず他の指標と組み合わせて判断してください。"
                    )

            # 米国・日本の内訳
            us_comps = sent.get("us_components", {})
            jp_comps = sent.get("jp_components", {})
            if us_comps and jp_comps:
                st.markdown("---")
                uc1, uc2 = st.columns(2)
                with uc1:
                    st.markdown(f"**🇺🇸 米国スコア {us_composite:.0f}/100 の内訳**")
                    for n, c in sorted(us_comps.items(), key=lambda x: -x[1]["score"]):
                        cc = "#16a34a" if c["score"] >= 55 else ("#dc2626" if c["score"] < 45 else "#f59e0b")
                        st.markdown(
                            f'<span style="font-size:11px">'
                            f'{n}: <b style="color:{cc}">{c["score"]:.0f}</b></span>',
                            unsafe_allow_html=True,
                        )
                with uc2:
                    st.markdown(f"**🇯🇵 日本スコア {jp_composite:.0f}/100 の内訳**")
                    for n, c in sorted(jp_comps.items(), key=lambda x: -x[1]["score"]):
                        cc = "#16a34a" if c["score"] >= 55 else ("#dc2626" if c["score"] < 45 else "#f59e0b")
                        st.markdown(
                            f'<span style="font-size:11px">'
                            f'{n}: <b style="color:{cc}">{c["score"]:.0f}</b></span>',
                            unsafe_allow_html=True,
                        )

    # ── 3列: 指数 / VIX / 予測確率 ───────────────────────
    col_idx, col_vix, col_prob = st.columns(3)

    with col_idx:
        st.markdown("**📈 主要指数・先物CFD（前日比）**")
        # 現物指数
        for key, flag, name in [("sp","🇺🇸","S&P500"), ("nk","🇯🇵","日経225")]:
            val  = prices.get(key)
            chg1 = prices.get(f"{key}_chg1", 0)
            chg5 = prices.get(f"{key}_chg5")
            if val:
                cc = "#16a34a" if chg1 >= 0 else "#dc2626"
                arrow = "▲" if chg1 >= 0 else "▼"
                w5 = f" / 5日 {chg5:+.1f}%" if chg5 is not None else ""
                st.markdown(
                    f'{flag} **{name}**<br>'
                    f'<span style="font-size:17px;font-weight:700">'
                    f'{val:,.0f}</span> '
                    f'<span style="color:{cc};font-size:13px">'
                    f'{arrow}{abs(chg1):.2f}%{w5}</span>',
                    unsafe_allow_html=True,
                )
        # 先物・CFD（時間外価格）
        st.markdown(
            '<div style="font-size:11px;color:#64748b;margin-top:6px;margin-bottom:2px">'
            '📊 先物・CFD（時間外価格）</div>',
            unsafe_allow_html=True,
        )
        for key, flag, name, fmt in [
            ("dow_f",  "🇺🇸", "ダウ先物",           ",.0f"),
            ("ndx_f",  "🇺🇸", "ナスダック100先物",   ",.0f"),
            ("nk_f",   "🇯🇵", "日経225先物(ドル建)", ",.2f"),
        ]:
            val  = prices.get(key)
            chg1 = prices.get(f"{key}_chg1", 0)
            if val:
                cc    = "#16a34a" if chg1 >= 0 else "#dc2626"
                arrow = "▲" if chg1 >= 0 else "▼"
                val_str = f"{val:{fmt}}"
                st.markdown(
                    f'<span style="font-size:12px;color:#94a3b8">{flag} {name}</span><br>'
                    f'<span style="font-size:15px;font-weight:700">{val_str}</span> '
                    f'<span style="color:{cc};font-size:12px">{arrow}{abs(chg1):.2f}%</span>',
                    unsafe_allow_html=True,
                )

    with col_vix:
        st.markdown("**😰 VIX（市場の恐怖指数）/ 米10年金利**")
        st.caption("VIX＝投資家の不安度。低いほど市場が落ち着いている。")
        vix = prices.get("vix")
        if vix:
            if vix < 15:   vc, vl = "#16a34a", "低水準 → 市場落ち着き・強気"
            elif vix < 20: vc, vl = "#22c55e", "やや低 → 概ね安定"
            elif vix < 25: vc, vl = "#f59e0b", "中程度 → 注意が必要"
            elif vix < 30: vc, vl = "#ef4444", "高水準 → 警戒域"
            else:           vc, vl = "#dc2626", "極めて高い → 恐怖・パニック域"
            vchg = prices.get("vix_chg1", 0)
            st.markdown(
                f'<span style="font-size:26px;font-weight:900;color:{vc}">{vix:.1f}</span>'
                f' <span style="font-size:12px;color:{vc}">{vl}</span><br>'
                f'<span style="font-size:11px;color:#6b7280">'
                f'前日比 {vchg:+.2f}%</span>',
                unsafe_allow_html=True,
            )
        tnx = prices.get("tnx")
        if tnx:
            tc = "#ef4444" if tnx > 4.5 else ("#f59e0b" if tnx > 4.0 else "#22c55e")
            tl = "高め→株に逆風" if tnx > 4.5 else ("やや高め" if tnx > 4.0 else "低め→株に追い風")
            st.markdown(
                f'米10年金利: <b style="color:{tc}">{tnx:.3f}%</b>'
                f' <span style="font-size:11px;color:{tc}">({tl})</span>',
                unsafe_allow_html=True,
            )

    with col_prob:
        st.markdown("**🎯 翌日・今週の上昇確率**")
        st.caption("9カテゴリのシグナルを合成して算出。50%超で上昇優位。")
        if us_pred.get("ok"):
            p_t = us_pred.get("prob_up_tomorrow", 50)
            p_w = us_pred.get("prob_up_week",     50)
            # 主要シグナルをひとこと要因として表示
            cat_scores = us_pred.get("cat_scores", {})
            top_bull = [(k,v) for k,v in sorted(cat_scores.items(), key=lambda x:-x[1]) if v > 0.1]
            top_bear = [(k,v) for k,v in sorted(cat_scores.items(), key=lambda x: x[1]) if v < -0.1]
            for label, prob in [("翌日", p_t), ("今週", p_w)]:
                pc = "#16a34a" if prob >= 57 else ("#dc2626" if prob < 43 else "#f59e0b")
                bar = int(prob)
                st.markdown(
                    f'<div style="margin-bottom:6px">'
                    f'<span style="font-size:12px;color:#6b7280">{label}</span> '
                    f'<b style="font-size:18px;color:{pc}">{prob:.0f}%</b>'
                    f'<div style="background:#e5e7eb;border-radius:4px;height:5px;margin-top:2px">'
                    f'<div style="width:{bar}%;height:100%;background:{pc};border-radius:4px"></div>'
                    f'</div></div>',
                    unsafe_allow_html=True,
                )
            # 主要要因を表示
            if top_bull:
                b_name = top_bull[0][0].replace("① ","").replace("② ","").replace("③ ","").replace("④ ","").replace("⑤ ","").replace("⑥ ","").replace("⑦ ","").replace("⑧ ","").replace("⑨ ","")
                st.caption(f"▲ 主な押し上げ: {b_name}")
            if top_bear:
                r_name = top_bear[0][0].replace("① ","").replace("② ","").replace("③ ","").replace("④ ","").replace("⑤ ","").replace("⑥ ","").replace("⑦ ","").replace("⑧ ","").replace("⑨ ","")
                st.caption(f"▼ 主な押し下げ: {r_name}")

    # ── 主要指数・先物 直近トレンドチャート ─────────────
    if PLOTLY_AVAILABLE:
        trend_items = [
            ("sp",    "🇺🇸 S&P500",          "#60a5fa"),
            ("nk",    "🇯🇵 日経225",          "#f472b6"),
            ("dow_f", "🇺🇸 ダウ先物",         "#34d399"),
            ("ndx_f", "🇺🇸 ナスダック先物",    "#fbbf24"),
        ]
        fig_trend = go.Figure()
        has_any = False
        for key, label, color in trend_items:
            dates  = prices.get(f"{key}_dates",  [])
            series = prices.get(f"{key}_series", [])
            if len(series) >= 2:
                base = series[0]
                norm = [(v / base - 1) * 100 for v in series]  # 起点を0%に正規化
                fig_trend.add_trace(go.Scatter(
                    x=dates, y=norm,
                    mode="lines+markers",
                    name=label,
                    line=dict(color=color, width=2),
                    marker=dict(size=4),
                    hovertemplate=f"<b>{label}</b><br>%{{x}}<br>起点比: %{{y:+.2f}}%<extra></extra>",
                ))
                has_any = True
        if has_any:
            # 直近のイベントマーカーを重ねる
            now_jst = datetime.now(JST)
            eco_events = _get_upcoming_us_eco_events(days_back=15, days_ahead=3)
            seen_dates = set()
            for ev in eco_events:
                ev_date_str = ev["jst_dt"].strftime("%Y-%m-%d")
                if ev_date_str in seen_dates:
                    continue
                seen_dates.add(ev_date_str)
                imp_col = "#ef4444" if ev["impact"] == "high" else "#f59e0b"
                short_name = ev["name"].split("（")[0].split("/")[0][:10]
                try:
                    fig_trend.add_vline(
                        x=ev_date_str,
                        line_dash="dot", line_color=imp_col, line_width=1, opacity=0.6,
                        annotation_text=f"{ev['icon']} {short_name}",
                        annotation_position="top left",
                        annotation=dict(font=dict(size=9, color=imp_col)),
                    )
                except Exception:
                    fig_trend.add_vline(
                        x=ev_date_str,
                        line_dash="dot", line_color=imp_col, line_width=1, opacity=0.5,
                    )
            fig_trend.add_hline(y=0, line_dash="dash", line_color="rgba(255,255,255,0.3)", line_width=1)
            fig_trend.update_layout(
                title=dict(text="📈 直近15営業日のトレンド（起点=0%に正規化）", font=dict(size=13)),
                yaxis_title="起点比（%）",
                height=260,
                margin=dict(l=10, r=10, t=40, b=20),
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
                font=dict(color="white", size=11),
                legend=dict(orientation="h", y=-0.15, x=0, font=dict(size=11)),
                xaxis=dict(gridcolor="rgba(255,255,255,0.08)", tickangle=-30),
                yaxis=dict(gridcolor="rgba(255,255,255,0.08)", zeroline=False,
                           ticksuffix="%"),
                hovermode="x unified",
            )
            st.plotly_chart(fig_trend, width="stretch")

    # ── セクター: 買われている / 売られている ─────────────
    if sector.get("ok") and sector.get("sectors"):
        secs     = sector["sectors"]
        leading  = sorted([(s,d) for s,d in secs.items() if d["quadrant"]=="Leading"],
                          key=lambda x: x[1]["rs_ratio"], reverse=True)[:3]
        lagging  = sorted([(s,d) for s,d in secs.items() if d["quadrant"]=="Lagging"],
                          key=lambda x: x[1]["rs_ratio"])[:3]
        weakening = sorted([(s,d) for s,d in secs.items() if d["quadrant"]=="Weakening"],
                            key=lambda x: x[1]["ret_1d"])[:2]

        st.markdown("---")
        sc1, sc2 = st.columns(2)
        with sc1:
            st.markdown("**📈 買われているセクター**")
            for sym, d in leading:
                cc = "#16a34a" if d["ret_1d"] >= 0 else "#ef4444"
                st.markdown(
                    f'🟢 **{d.get("jp", d["name"])}** `{sym}` '
                    f'<span style="color:{cc}">今日 {d["ret_1d"]:+.2f}%</span> '
                    f'<span style="color:#9ca3af;font-size:11px">1ヶ月 {d["ret_1m"]:+.1f}%</span>',
                    unsafe_allow_html=True,
                )
        with sc2:
            st.markdown("**📉 売られているセクター**")
            for sym, d in lagging:
                cc = "#ef4444" if d["ret_1d"] < 0 else "#16a34a"
                st.markdown(
                    f'🔴 **{d.get("jp", d["name"])}** `{sym}` '
                    f'<span style="color:{cc}">今日 {d["ret_1d"]:+.2f}%</span> '
                    f'<span style="color:#9ca3af;font-size:11px">1ヶ月 {d["ret_1m"]:+.1f}%</span>',
                    unsafe_allow_html=True,
                )
            for sym, d in weakening:
                st.markdown(
                    f'🟡 **{d.get("jp", d["name"])}** `{sym}` '
                    f'<span style="color:#f59e0b">失速中 今日 {d["ret_1d"]:+.2f}%</span>',
                    unsafe_allow_html=True,
                )

    # ── 注目シグナル ──────────────────────────────────────
    if sent.get("ok"):
        bull_sigs, bear_sigs = [], []
        for name, c in sent.get("components", {}).items():
            sc = c["score"]
            if sc >= 70:
                bull_sigs.append((name, c["label"]))
            elif sc <= 30:
                bear_sigs.append((name, c["label"]))

        if bull_sigs or bear_sigs:
            st.markdown("---")
            st.markdown("**⚡ 注目シグナル**")
            sig_col1, sig_col2 = st.columns(2)
            with sig_col1:
                for name, detail in bull_sigs[:4]:
                    st.markdown(
                        f'✅ **{name}** — '
                        f'<span style="color:#16a34a">{detail}</span>',
                        unsafe_allow_html=True,
                    )
            with sig_col2:
                for name, detail in bear_sigs[:4]:
                    st.markdown(
                        f'⚠️ **{name}** — '
                        f'<span style="color:#dc2626">{detail}</span>',
                        unsafe_allow_html=True,
                    )

    # ── 複合指標による歴史的パターンマッチング ─────────────
    st.markdown("---")
    st.markdown("**📚 現在の市場は過去のどの局面に似ているか？**")
    st.caption("VIXの上がり方・セクターローテーション・為替・信用スプレッド・金利など複数指標を総合評価")

    current_vix = prices.get("vix", 0)

    # 歴史的参照値（VIX ピーク・概要）
    HISTORY = [
        {"label": "コロナショック",      "year": "2020/3",  "vix": 82.7,  "sp_dd": -33.9, "days": 33,
         "desc": "パンデミック宣言。過去最速の急落。わずか33日でS&P500が34%下落。"},
        {"label": "リーマンショック",    "year": "2008/10", "vix": 80.9,  "sp_dd": -56.8, "days": 517,
         "desc": "リーマン破綻で金融システム崩壊危機。18ヶ月かけてS&P500が半値以下に。"},
        {"label": "ITバブル崩壊",        "year": "2001/9",  "vix": 43.7,  "sp_dd": -49.1, "days": 929,
         "desc": "9.11同時多発テロが追い打ち。ハイテク株中心に3年かけて半値に。"},
        {"label": "欧州債務危機",        "year": "2011/8",  "year2": "欧州",  "vix": 48.0,  "sp_dd": -19.4, "days": 157,
         "desc": "ギリシャ危機が欧州全体に波及。米国も格下げされVIXが急騰。"},
        {"label": "コロナ前高値 (平常)",  "year": "2020/1",  "vix": 12.1,  "sp_dd": 0,    "days": 0,
         "desc": "市場が最も楽観的だった時期。VIXは歴史的低水準。"},
        {"label": "金利上昇ショック",    "year": "2022/6",  "vix": 36.5,  "sp_dd": -24.5, "days": 282,
         "desc": "FRBの急激な利上げでS&P500が24%下落。現代版スタグフレーション懸念。"},
    ]

    # 現在VIX表示
    if current_vix < 20:   vc, vs = "#16a34a", "安定域"
    elif current_vix < 30: vc, vs = "#f59e0b", "注意域"
    elif current_vix < 40: vc, vs = "#ef4444", "警戒域"
    else:                   vc, vs = "#dc2626", "危機域"

    st.markdown(
        f'<div style="background:#f8fafc;border:1px solid #e2e8f0;'
        f'border-radius:8px;padding:10px 16px;margin-bottom:12px;">'
        f'現在のVIX: <b style="color:{vc};font-size:20px">{current_vix:.1f}</b>'
        f' <span style="color:{vc}">({vs})</span> &nbsp;|&nbsp;'
        f' <span style="font-size:12px;color:#64748b">'
        f'VIX単体ではなく以下の複合指標でパターンを判定しています</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # パターンマッチング結果
    if crisis.get("ok") and crisis.get("ranked"):
        ranked = crisis["ranked"]
        top_name, top_data = ranked[0]

        # 1位の結果をハイライト
        tc = top_data["color"]
        st.markdown(
            f'<div style="background:{tc}12;border:2px solid {tc}50;'
            f'border-radius:10px;padding:14px 18px;margin-bottom:12px;">'
            f'<div style="font-size:12px;color:#64748b;margin-bottom:4px">現在の市場が最も似ている過去の局面</div>'
            f'<div style="display:flex;align-items:center;gap:12px;">'
            f'<div style="font-size:36px">{top_data["emoji"]}</div>'
            f'<div>'
            f'<div style="font-size:18px;font-weight:900;color:{tc};">'
            f'{top_name.replace(chr(10)," ")} '
            f'<span style="font-size:14px;background:{tc};color:white;'
            f'padding:2px 8px;border-radius:12px;">{top_data["score"]:.0f}%一致</span>'
            f'</div>'
            f'<div style="font-size:12px;color:#475569;margin-top:4px">'
            f'{top_data["period"]} | S&P500最大 {top_data["sp_peak_loss"]:+.1f}%</div>'
            f'<div style="font-size:12px;color:#334155;margin-top:4px">'
            f'{top_data["description"]}</div>'
            f'</div></div></div>',
            unsafe_allow_html=True,
        )

        # 判定に使った指標の一致状況
        if top_data.get("matched") or top_data.get("mismatched"):
            st.markdown("**🔍 判定根拠（一致した指標・一致しなかった指標）**")
            m_col, mm_col = st.columns(2)
            with m_col:
                for m in top_data.get("matched", []):
                    st.markdown(f'<span style="font-size:12px">{m}</span>', unsafe_allow_html=True)
            with mm_col:
                for mm in top_data.get("mismatched", []):
                    st.markdown(f'<span style="font-size:12px">{mm}</span>', unsafe_allow_html=True)

        # 当時に気をつけるべきシグナル
        st.markdown("**⚡ このパターンで注意すべきシグナル**")
        for sig in top_data.get("key_signals", []):
            st.markdown(f"- {sig}")

        # 全パターンのスコア一覧
        with st.expander("📊 全パターンとの類似度スコアを見る", expanded=False):
            st.markdown("**複合指標による類似度（VIX上昇速度・セクター・為替・信用・金利を総合判定）**")
            for name, data in ranked:
                sc = data["score"]
                bc = data["color"]
                bar_w = int(sc)
                st.markdown(
                    f'<div style="margin-bottom:8px">'
                    f'<div style="display:flex;justify-content:space-between;'
                    f'font-size:12px;margin-bottom:3px;">'
                    f'<span>{data["emoji"]} {name.replace(chr(10)," ")}</span>'
                    f'<b style="color:{bc}">{sc:.0f}%</b></div>'
                    f'<div style="background:#e5e7eb;border-radius:4px;height:8px">'
                    f'<div style="width:{bar_w}%;height:100%;background:{bc};border-radius:4px"></div>'
                    f'</div>'
                    f'<div style="font-size:10px;color:#94a3b8;margin-top:2px">'
                    f'{data["period"]} | {data["description"][:50]}...</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            st.caption(f"判定時刻: {crisis.get('updated_at','')}")
    else:
        st.info("パターン分析データを取得できませんでした")

    st.caption("⚠️ 投資判断はご自身の責任で。本サマリーは参考情報です。")
    st.markdown("---")


def render_composite_sentiment():
    """AI Sentiment Index (Claude Edition) 描画"""

    # ── ヘッダー ──────────────────────────────────────
    st.markdown(
        """
        <div style="display:flex;align-items:center;gap:14px;margin-bottom:4px;">
          <div style="background:linear-gradient(135deg,#3b82f6,#8b5cf6);
            border-radius:10px;width:44px;height:44px;display:flex;
            align-items:center;justify-content:center;font-size:24px;
            box-shadow:0 0 18px rgba(59,130,246,.4);flex-shrink:0;">🧠</div>
          <div>
            <div style="font-size:22px;font-weight:900;line-height:1.2;
              background:linear-gradient(135deg,#1e293b 0%,#3b82f6 100%);
              -webkit-background-clip:text;-webkit-text-fill-color:transparent;">
              AI Sentiment Index</div>
            <div style="font-size:13px;font-weight:600;color:#6b7280;margin-top:1px;">
              Claude Edition &nbsp;·&nbsp; Claude base LLM Score</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div style="background:linear-gradient(135deg,#eff6ff,#f5f3ff);'
        'border-left:4px solid #3b82f6;border-radius:6px;padding:10px 16px;'
        'font-size:13px;color:#374151;margin-bottom:14px;">'
        '12指標を加重合成した総合センチメントスコア（0〜100）。全指標をパーセンタイルランクで正規化。'
        'F&amp;G Index · VIX · VIX期間構造 · Put/Call · SP500モメンタム · '
        'Safe Haven需要 · セクター配分 · 信用リスク選好 · ブレッドス · '
        '日経225モメンタム · ドル円リスク · 日本実現VIXを統合。'
        '</div>',
        unsafe_allow_html=True,
    )

    # ── キャッシュクリアボタン（デバッグ用） ──────────
    col_dbg1, col_dbg2 = st.columns([3, 1])
    with col_dbg2:
        if st.button("🗑️ キャッシュクリア", key="sentiment_cache_clear",
                     help="センチメントキャッシュを強制クリアして再計算"):
            compute_composite_sentiment.clear()
            st.rerun()

    with st.spinner("センチメント指標取得中..."):
        sent_data = compute_composite_sentiment()

    if not sent_data.get("ok"):
        st.error(f"取得失敗: {sent_data.get('reason')}")
        return

    composite    = sent_data["composite"]
    label        = sent_data["label"]
    color        = sent_data["color"]
    components   = sent_data["components"]
    hist_df      = sent_data["hist_df"]
    us_composite = sent_data.get("us_composite", 50.0)
    jp_composite = sent_data.get("jp_composite", 50.0)
    us_comps     = sent_data.get("us_components", {})
    jp_comps     = sent_data.get("jp_components", {})
    us_hist_df   = sent_data.get("us_hist_df", pd.DataFrame())
    jp_hist_df   = sent_data.get("jp_hist_df", pd.DataFrame())

    def _slabel(s):
        if s > 75: return "Extreme Greed 🤑"
        if s > 55: return "Greed 😊"
        if s > 45: return "Neutral 😐"
        if s > 25: return "Fear 😟"
        return "Extreme Fear 😱"

    def _scolor(s):
        return "#1a7f37" if s > 55 else ("#d1242f" if s < 45 else "#888")

    # ── デバッグ情報（折りたたみ・通常時は非表示） ────────
    with st.expander("🔧 センチメント履歴デバッグ", expanded=False):
        dbg = sent_data.get("hist_debug", {})
        st.write(f"**hist_df 行数:** {len(hist_df)}")
        st.write(f"**sp_c 長さ:** {dbg.get('sp_c_len', 'N/A')}")
        st.write(f"**vix_c 長さ:** {dbg.get('vix_c_len', 'N/A')}")
        st.write(f"**series_map キー:** {dbg.get('series_map_keys', 'N/A')}")
        st.write(f"**フォールバック:** {dbg.get('fallback', False)}")
        if dbg.get('error'):
            st.error(f"エラー: {dbg['error']}")
        if not hist_df.empty:
            st.success(f"✅ hist_df取得成功！{len(hist_df)}行")
            st.dataframe(hist_df.tail(5))
        else:
            st.error("❌ hist_dfが空です")

    # ゾーン定義
    def _zone_style(score: float):
        if score > 75:
            return "#14532d", "#dcfce7", "Extreme Greed"
        if score > 55:
            return "#166534", "#f0fdf4", "Greed"
        if score > 45:
            return "#78350f", "#fffbeb", "Neutral"
        if score > 25:
            return "#991b1b", "#fef2f2", "Fear"
        return "#7f1d1d", "#fecaca", "Extreme Fear"

    txt_c, bg_c, zone_name = _zone_style(composite)

    # ── メインレイアウト ──────────────────────────────
    col_g, col_info = st.columns([1, 2])

    with col_g:
        st.image(make_fear_greed_gauge(composite))

        # ゾーン凡例バー
        st.markdown(
            """
            <div style="margin-top:6px;">
              <div style="display:flex;height:7px;border-radius:4px;overflow:hidden;gap:2px;">
                <div style="flex:1;background:#b91c1c;" title="Extreme Fear"></div>
                <div style="flex:1;background:#ef4444;" title="Fear"></div>
                <div style="flex:1;background:#f59e0b;" title="Neutral"></div>
                <div style="flex:1;background:#22c55e;" title="Greed"></div>
                <div style="flex:1;background:#14b8a6;" title="Extreme Greed"></div>
              </div>
              <div style="display:flex;justify-content:space-between;
                font-size:9px;color:#9ca3af;margin-top:3px;">
                <span>Ext.Fear</span><span>Fear</span>
                <span>Neutral</span><span>Greed</span><span>Ext.Greed</span>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with col_info:
        # スコア大表示
        st.markdown(
            f'<div style="background:{bg_c};border:1px solid {color}33;'
            f'border-radius:10px;padding:14px 18px;margin-bottom:12px;">'
            f'<div style="font-size:48px;font-weight:900;color:{color};line-height:1;">'
            f'{composite:.0f}'
            f'<span style="font-size:22px;color:#9ca3af;font-weight:400;">/100</span></div>'
            f'<div style="font-size:18px;font-weight:700;color:{color};margin-top:4px;">'
            f'{label}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        # コンポーネント一覧（改善版バー）
        for name, comp in components.items():
            bar_w  = max(1, int(comp["normalized"]))
            bar_c  = comp["color"]
            score_val = comp["score"]
            detail = comp["label"]

            # スコアに応じた背景色
            if score_val > 55:
                row_bg = "rgba(34,197,94,0.05)"
            elif score_val < 45:
                row_bg = "rgba(239,68,68,0.05)"
            else:
                row_bg = "transparent"

            st.markdown(
                f'<div style="margin:5px 0;padding:5px 8px;'
                f'border-radius:5px;background:{row_bg};">'
                f'<div style="display:flex;justify-content:space-between;'
                f'align-items:baseline;font-size:12px;margin-bottom:3px;">'
                f'<span style="color:#374151;font-weight:500;">{name}</span>'
                f'<span style="color:{bar_c};font-weight:700;font-size:13px;">'
                f'{score_val:.0f}'
                f'<span style="color:#9ca3af;font-weight:400;font-size:10px;"> ({detail})</span>'
                f'</span></div>'
                f'<div style="background:#e5e7eb;border-radius:4px;height:6px;">'
                f'<div style="width:{bar_w}%;height:100%;background:{bar_c};'
                f'border-radius:4px;transition:width .6s;"></div>'
                f'</div></div>',
                unsafe_allow_html=True,
            )

        # 前週比・サマリ
        st.markdown(
            f'<div style="margin-top:10px;font-size:11px;color:#6b7280;'
            f'border-top:1px solid #e5e7eb;padding-top:8px;">'
            f'📅 更新: {sent_data["updated_at"]} &nbsp;|&nbsp; '
            f'🤖 Claude base LLM Score v2.0'
            f'</div>',
            unsafe_allow_html=True,
        )

    # ── 米国 / 日本 センチメント分離表示 ──────────────────
    st.markdown("---")
    st.markdown("#### 🌐 米国 / 日本 センチメント内訳")

    def _render_region_panel(r_composite, r_comps, r_hist_df, region_label, flag):
        r_color  = _scolor(r_composite)
        r_label  = _slabel(r_composite)
        r_txt, r_bg, _ = (
            ("#14532d", "#dcfce7", "") if r_composite > 75 else
            ("#166534", "#f0fdf4", "") if r_composite > 55 else
            ("#78350f", "#fffbeb", "") if r_composite > 45 else
            ("#991b1b", "#fef2f2", "") if r_composite > 25 else
            ("#7f1d1d", "#fecaca", "")
        )
        st.markdown(
            f'<div style="background:{r_bg};border:1px solid {r_color}33;'
            f'border-radius:8px;padding:10px 16px;margin-bottom:10px;display:flex;'
            f'align-items:center;gap:14px;">'
            f'<div style="font-size:36px;font-weight:900;color:{r_color};line-height:1;">'
            f'{flag} {r_composite:.0f}'
            f'<span style="font-size:16px;color:#9ca3af;font-weight:400;">/100</span></div>'
            f'<div style="font-size:15px;font-weight:700;color:{r_color};">{r_label}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        for name, comp in r_comps.items():
            bw  = max(1, int(comp["normalized"]))
            bc  = comp["color"]
            sv  = comp["score"]
            row_bg = "rgba(34,197,94,0.05)" if sv > 55 else ("rgba(239,68,68,0.05)" if sv < 45 else "transparent")
            st.markdown(
                f'<div style="margin:4px 0;padding:4px 8px;border-radius:5px;background:{row_bg};">'
                f'<div style="display:flex;justify-content:space-between;font-size:11px;margin-bottom:2px;">'
                f'<span style="color:#374151;font-weight:500;">{name}</span>'
                f'<span style="color:{bc};font-weight:700;">{sv:.0f}'
                f'<span style="color:#9ca3af;font-weight:400;font-size:10px;"> ({comp["label"]})</span>'
                f'</span></div>'
                f'<div style="background:#e5e7eb;border-radius:4px;height:5px;">'
                f'<div style="width:{bw}%;height:100%;background:{bc};border-radius:4px;"></div>'
                f'</div></div>',
                unsafe_allow_html=True,
            )
        if not r_hist_df.empty and PLOTLY_AVAILABLE:
            import plotly.graph_objects as go
            rdf = r_hist_df.copy()
            rdf["date"] = pd.to_datetime(rdf["date"])
            latest_r = rdf["date"].max()
            tab_r3y, tab_r1y, tab_r3m = st.tabs(["3年", "1年", "3か月"])
            for tab_r, days_r, ttl_r in [
                (tab_r3y, 1095, f"{region_label} センチメント推移 — 3年"),
                (tab_r1y,  365, f"{region_label} センチメント推移 — 1年"),
                (tab_r3m,   92, f"{region_label} センチメント推移 — 3か月"),
            ]:
                with tab_r:
                    df_r = rdf[rdf["date"] >= (latest_r - pd.Timedelta(days=days_r))]
                    if df_r.empty:
                        st.info("データ不足")
                        continue
                    fig_r = go.Figure()
                    fig_r.add_trace(go.Scatter(
                        x=df_r["date"], y=df_r["score"],
                        name=region_label, line=dict(color=r_color, width=2),
                        fill="tozeroy", fillcolor=f"rgba(59,130,246,0.08)",
                    ))
                    for yv, lc in [(75, "rgba(22,163,74,0.15)"), (25, "rgba(220,38,38,0.15)")]:
                        fig_r.add_hline(y=yv, line_dash="dot", line_color=lc)
                    fig_r.update_layout(
                        title=dict(text=ttl_r, font=dict(size=12)),
                        height=260, margin=dict(l=0, r=0, t=30, b=0),
                        hovermode="x unified", plot_bgcolor="rgba(0,0,0,0)",
                        paper_bgcolor="rgba(0,0,0,0)",
                        yaxis=dict(range=[0, 100], title="Score"),
                    )
                    st.plotly_chart(fig_r, width="stretch")

    tab_us_sent, tab_jp_sent = st.tabs(["🇺🇸 米国センチメント", "🇯🇵 日本センチメント"])
    with tab_us_sent:
        st.caption("SP500モメンタム · VIX · Put/Call · Safe Haven · 信用リスク等 | 参照: FOMC, 利上げ, 関税, 雇用統計")
        _render_region_panel(us_composite, us_comps, us_hist_df, "米国", "🇺🇸")
    with tab_jp_sent:
        st.caption("日経225モメンタム · ドル円リスク · 日本実現VIX | 参照: 日銀, BOJ, 円安, 日経")
        _render_region_panel(jp_composite, jp_comps, jp_hist_df, "日本", "🇯🇵")

    # ── 総合履歴チャート ──────────────────────────────────
    if not hist_df.empty:
        st.markdown("#### 📈 総合センチメントスコア推移")

        tab_3y, tab_1y, tab_3m = st.tabs(["3年", "1年", "3か月"])

        def _draw_sentiment_history(plot_df: pd.DataFrame, chart_title: str):
            if plot_df.empty:
                st.info("表示できる履歴データがありません。")
                return

            if PLOTLY_AVAILABLE:
                import plotly.graph_objects as go
                # ── Plotly インタラクティブチャート ──────────────
                df_p = plot_df.copy()
                df_p["date"] = pd.to_datetime(df_p["date"])

                # ゾーン判定ラベル
                def _zone(s):
                    if s >= 75: return "Extreme Greed"
                    if s >= 55: return "Greed"
                    if s >= 45: return "Neutral"
                    if s >= 25: return "Fear"
                    return "Extreme Fear"

                def _zone_color(s):
                    if s >= 75: return "#14b8a6"
                    if s >= 55: return "#22c55e"
                    if s >= 45: return "#9ca3af"
                    if s >= 25: return "#f97316"
                    return "#ef4444"

                df_p["zone"]       = df_p["score"].apply(_zone)
                df_p["zone_color"] = df_p["score"].apply(_zone_color)
                df_p["tooltip"]    = df_p.apply(
                    lambda r: f"{r['date'].strftime('%Y-%m-%d')}<br>"
                              f"スコア: <b>{r['score']:.1f}</b><br>"
                              f"ゾーン: {r['zone']}",
                    axis=1
                )

                fig = go.Figure()

                # ゾーン背景
                zone_bands = [
                    (75, 100, "rgba(20,184,166,0.07)",  "Extreme Greed"),
                    (55,  75, "rgba(34,197,94,0.05)",   "Greed"),
                    (45,  55, "rgba(156,163,175,0.05)", "Neutral"),
                    (25,  45, "rgba(249,115,22,0.05)",  "Fear"),
                    (0,   25, "rgba(239,68,68,0.08)",   "Extreme Fear"),
                ]
                for y0, y1, fillcolor, name in zone_bands:
                    fig.add_hrect(
                        y0=y0, y1=y1,
                        fillcolor=fillcolor,
                        line_width=0,
                        annotation_text=name,
                        annotation_position="right",
                        annotation_font_size=9,
                        annotation_font_color="#9ca3af",
                    )

                # 中立ライン
                fig.add_hline(
                    y=50, line_dash="dash",
                    line_color="rgba(156,163,175,0.5)", line_width=1,
                )

                # 塗りつぶしエリア（50以上 = 緑、50未満 = 赤）
                df_above = df_p.copy()
                df_above["score_clipped"] = df_above["score"].clip(lower=50)
                fig.add_trace(go.Scatter(
                    x=df_above["date"], y=df_above["score_clipped"],
                    fill="tonexty", fillcolor="rgba(34,197,94,0.12)",
                    line=dict(width=0), showlegend=False,
                    hoverinfo="skip",
                ))
                fig.add_trace(go.Scatter(
                    x=df_p["date"], y=[50] * len(df_p),
                    line=dict(width=0), showlegend=False,
                    hoverinfo="skip",
                ))

                # メインライン（ホバー付き）
                fig.add_trace(go.Scatter(
                    x=df_p["date"],
                    y=df_p["score"],
                    mode="lines+markers",
                    line=dict(color="#e879a0", width=2),
                    marker=dict(size=4, color="#e879a0"),
                    name="AI Sentiment Score",
                    hovertemplate="%{customdata}<extra></extra>",
                    customdata=df_p["tooltip"],
                ))

                # 最新値マーカー
                last_row = df_p.iloc[-1]
                fig.add_trace(go.Scatter(
                    x=[last_row["date"]],
                    y=[last_row["score"]],
                    mode="markers+text",
                    marker=dict(size=10, color="#e879a0",
                                line=dict(width=2, color="white")),
                    text=[f"{last_row['score']:.0f}"],
                    textposition="middle right",
                    textfont=dict(size=13, color="#e879a0", family="Arial Black"),
                    showlegend=False,
                    hoverinfo="skip",
                ))

                # ── 21日先 類推ライン (直近30日 × 2次多項式回帰) ──
                try:
                    n_fit = min(30, len(df_p))
                    df_fit = df_p.tail(n_fit)
                    xi = np.arange(n_fit, dtype=float)
                    yi = df_fit["score"].values.astype(float)
                    coeffs = np.polyfit(xi, yi, deg=2)
                    resid_std = float((yi - np.polyval(coeffs, xi)).std())

                    n_proj = 21
                    xi_proj = np.arange(n_fit - 1, n_fit + n_proj, dtype=float)
                    yi_proj  = np.polyval(coeffs, xi_proj).clip(0, 100)
                    yi_upper = (yi_proj + resid_std).clip(0, 100)
                    yi_lower = (yi_proj - resid_std).clip(0, 100)
                    last_dt  = df_p["date"].iloc[-1]
                    proj_dates = [last_dt + pd.Timedelta(days=i) for i in range(n_proj + 1)]

                    # 信頼帯（±1σ）
                    fig.add_trace(go.Scatter(
                        x=proj_dates + proj_dates[::-1],
                        y=list(yi_upper) + list(yi_lower[::-1]),
                        fill="toself",
                        fillcolor="rgba(232,121,160,0.13)",
                        line=dict(width=0),
                        showlegend=True,
                        name="類推レンジ(±1σ)",
                        hoverinfo="skip",
                    ))
                    # 類推ライン本体
                    proj_end_score = float(yi_proj[-1])
                    direction = "↗" if proj_end_score > float(yi_proj[0]) else "↘"
                    fig.add_trace(go.Scatter(
                        x=proj_dates,
                        y=yi_proj,
                        mode="lines",
                        line=dict(color="#e879a0", width=2, dash="dot"),
                        name=f"📈 類推 {direction}{proj_end_score:.0f}(21日後)",
                        hovertemplate="類推: <b>%{y:.1f}</b><extra></extra>",
                    ))
                    # 類推終端マーカー
                    fig.add_trace(go.Scatter(
                        x=[proj_dates[-1]],
                        y=[proj_end_score],
                        mode="markers+text",
                        marker=dict(size=9, color="#e879a0", symbol="diamond",
                                    line=dict(width=2, color="white")),
                        text=[f"{proj_end_score:.0f}"],
                        textposition="middle right",
                        textfont=dict(size=12, color="#e879a0"),
                        showlegend=False,
                        hoverinfo="skip",
                    ))
                except Exception:
                    pass  # 類推失敗時はスキップ

                fig.update_layout(
                    title=dict(text=chart_title, font=dict(size=13, color="#111827")),
                    yaxis=dict(range=[0, 100], title="AI Sentiment Score",
                               gridcolor="rgba(209,213,219,0.4)",
                               tickfont=dict(size=10)),
                    xaxis=dict(gridcolor="rgba(209,213,219,0.3)",
                               tickfont=dict(size=10)),
                    plot_bgcolor="#f9fafb",
                    paper_bgcolor="#f9fafb",
                    hovermode="x unified",
                    hoverlabel=dict(
                        bgcolor="white",
                        bordercolor="#e879a0",
                        font_size=12,
                    ),
                    margin=dict(l=50, r=80, t=40, b=60),
                    height=420,
                    showlegend=True,
                    legend=dict(
                        orientation="h",
                        yanchor="bottom", y=-0.18,
                        xanchor="left", x=0,
                        font=dict(size=11),
                    ),
                )
                st.plotly_chart(fig, width="stretch")

            else:
                # ── Plotly未インストール時はmatplotlibフォールバック ──
                fig, ax = plt.subplots(figsize=(12, 4))
                ax.plot(plot_df["date"], plot_df["score"], linewidth=2.0, color="#e879a0")
                ax.axhspan(75, 100, alpha=0.08, color="#14b8a6")
                ax.axhspan(0,  25,  alpha=0.08, color="#ef4444")
                ax.axhline(50, color="#9ca3af", linestyle="--", linewidth=1, alpha=0.7)
                ax.set_ylim(0, 100)
                ax.set_title(chart_title, fontsize=12)
                ax.grid(True, alpha=0.2)
                plt.tight_layout()
                st.pyplot(fig, clear_figure=True)

            st.caption(
                f"データ点数: {len(plot_df)}日 | "
                f"平均: {plot_df['score'].mean():.1f} | "
                f"最高: {plot_df['score'].max():.1f} | "
                f"最低: {plot_df['score'].min():.1f}"
            )

        hist_df = hist_df.copy()
        hist_df["date"] = pd.to_datetime(hist_df["date"])
        latest_date = hist_df["date"].max()

        with tab_3y:
            df_3y = hist_df[hist_df["date"] >= (latest_date - pd.Timedelta(days=1095))].copy()
            _draw_sentiment_history(df_3y, "AI Sentiment Index — 3 Years (Claude Edition)")
        with tab_1y:
            df_1y = hist_df[hist_df["date"] >= (latest_date - pd.Timedelta(days=365))].copy()
            _draw_sentiment_history(df_1y, "AI Sentiment Index — 1 Year (Claude Edition)")
        with tab_3m:
            df_3m = hist_df[hist_df["date"] >= (latest_date - pd.Timedelta(days=92))].copy()
            _draw_sentiment_history(df_3m, "AI Sentiment Index — 3 Months (Claude Edition)")



    st.caption(
        "🤖 AI Sentiment Index (Claude Edition) — "
        "Claude base LLM Score v2.0 | "
        "9指標加重合成 | "
        f"更新: {sent_data['updated_at']}"
    )

    # ── ★ 追加①: 日経平均との比較チャート ──────────────
    st.markdown("---")
    st.markdown("#### 📈 AI Sentiment vs 日経平均 比較チャート")
    st.caption("センチメントスコアと日経平均の相関を可視化します")

    if not hist_df.empty and PLOTLY_AVAILABLE:
        try:
            import plotly.graph_objects as go
            from plotly.subplots import make_subplots

            hist_cmp = hist_df.copy()
            hist_cmp["date"] = pd.to_datetime(hist_cmp["date"])
            latest = hist_cmp["date"].max()

            # 日経平均を取得
            n225_raw = yf.Ticker("^N225").history(period="3y", interval="1d", auto_adjust=True)
            if not n225_raw.empty:
                n225 = n225_raw["Close"].copy()
                if hasattr(n225.index, "tz") and n225.index.tz is not None:
                    n225.index = n225.index.tz_localize(None)
                n225.index = pd.to_datetime(n225.index).normalize()
                n225_df = pd.DataFrame({"date": n225.index, "nikkei": n225.values})

                # マージ
                merged = pd.merge(
                    hist_cmp[["date", "score"]],
                    n225_df,
                    on="date", how="inner"
                ).sort_values("date")

                if not merged.empty:
                    tab_cmp_1y, tab_cmp_3m, tab_cmp_3y = st.tabs(["1年", "3か月", "3年"])

                    def _draw_comparison(df_c, title):
                        if df_c.empty:
                            st.info("データがありません")
                            return
                        fig = make_subplots(
                            specs=[[{"secondary_y": True}]],
                        )
                        # センチメントスコア（左軸）
                        fig.add_trace(go.Scatter(
                            x=df_c["date"], y=df_c["score"],
                            name="AI Sentiment", line=dict(color="#3b82f6", width=2),
                            fill="tozeroy", fillcolor="rgba(59,130,246,0.08)",
                        ), secondary_y=False)
                        # 日経平均（右軸）
                        fig.add_trace(go.Scatter(
                            x=df_c["date"], y=df_c["nikkei"],
                            name="日経平均", line=dict(color="#f59e0b", width=2),
                        ), secondary_y=True)
                        # ゾーンライン
                        for y_val, color, label in [
                            (75, "rgba(22,163,74,0.3)", "Extreme Greed"),
                            (55, "rgba(22,163,74,0.15)", "Greed"),
                            (45, "rgba(234,179,8,0.15)", "Neutral"),
                            (25, "rgba(220,38,38,0.15)", "Fear"),
                        ]:
                            fig.add_hline(y=y_val, line_dash="dot",
                                          line_color=color, secondary_y=False)
                        fig.update_layout(
                            height=380,
                            margin=dict(l=0, r=0, t=30, b=0),
                            legend=dict(orientation="h", y=1.08),
                            hovermode="x unified",
                            plot_bgcolor="rgba(0,0,0,0)",
                            paper_bgcolor="rgba(0,0,0,0)",
                        )
                        fig.update_yaxes(title_text="Sentiment (0-100)", secondary_y=False,
                                         range=[0, 100])
                        fig.update_yaxes(title_text="日経平均 (円)", secondary_y=True)
                        st.plotly_chart(fig, width="stretch")

                        # 相関係数
                        if len(df_c) >= 10:
                            corr = df_c["score"].corr(df_c["nikkei"])
                            lag_corr = df_c["score"].shift(5).corr(df_c["nikkei"])
                            col_c1, col_c2 = st.columns(2)
                            col_c1.metric("同期相関係数", f"{corr:.3f}",
                                          help="センチメントと日経平均の同日相関")
                            col_c2.metric("5日先行相関", f"{lag_corr:.3f}",
                                          help="センチメントが5日先行した場合の相関")

                    with tab_cmp_1y:
                        df_1y = merged[merged["date"] >= (latest - pd.Timedelta(days=365))]
                        _draw_comparison(df_1y, "1年")
                    with tab_cmp_3m:
                        df_3m = merged[merged["date"] >= (latest - pd.Timedelta(days=92))]
                        _draw_comparison(df_3m, "3か月")
                    with tab_cmp_3y:
                        _draw_comparison(merged, "3年")
            else:
                st.info("日経平均データを取得できませんでした")
        except Exception as e:
            st.info(f"比較チャート取得エラー: {e}")
    elif hist_df.empty:
        st.info("センチメント履歴データが必要です")

    # ── ★ 追加②: ニュース×センチメント相関ダッシュボード ──
    st.markdown("---")
    st.markdown("#### 📰 経済ニュース × センチメント相関")
    st.caption("関税・経済ニュースとセンチメントスコアの時系列相関を分析します")

    with st.expander("🔍 ニュース×センチメント相関を表示", expanded=False):
        # 米国 / 日本 で異なるキーワード・センチメント系列を使用
        US_THEMES = {
            "関税・通商":     ["tariff", "trade war", "関税", "輸入関税", "制裁", "sanctions", "USMCA", "WTO"],
            "FOMC・金融政策": ["FOMC", "fed", "利上げ", "利下げ", "interest rate", "インフレ", "CPI", "QT"],
            "雇用・景気":     ["jobs", "payroll", "雇用統計", "unemployment", "GDP", "recession", "PMI", "ISM"],
        }
        JP_THEMES = {
            "日銀・金融政策": ["日銀", "BOJ", "植田", "利上げ", "金融政策決定会合", "YCC", "国債"],
            "円安・為替":     ["円安", "円高", "ドル円", "USDJPY", "為替介入", "外為"],
            "日経・景気":     ["日経", "日経平均", "景気", "GDP", "貿易収支", "インフレ", "CPI"],
        }

        nws_col1, nws_col2 = st.columns(2)
        with nws_col1:
            nws_region = st.selectbox("市場", ["🇺🇸 米国", "🇯🇵 日本"], key="news_region_sel")
        with nws_col2:
            kw_period = st.selectbox("期間", ["3か月", "1年", "3年"], key="news_sentiment_period")

        is_us_news = nws_region.startswith("🇺🇸")
        theme_opts = list(US_THEMES.keys()) if is_us_news else list(JP_THEMES.keys())
        kw_theme = st.selectbox("テーマ", theme_opts, key="news_sentiment_theme")

        active_hist = us_hist_df if is_us_news else jp_hist_df
        if active_hist.empty:
            active_hist = hist_df  # フォールバック
        active_themes = US_THEMES if is_us_news else JP_THEMES
        keywords = active_themes.get(kw_theme, [])

        if not active_hist.empty:
            hist_kw = active_hist.copy()
            hist_kw["date"] = pd.to_datetime(hist_kw["date"])
            period_days = {"3か月": 92, "1年": 365, "3年": 1095}[kw_period]
            latest_d = hist_kw["date"].max()
            df_kw = hist_kw[hist_kw["date"] >= (latest_d - pd.Timedelta(days=period_days))].copy()

            st.markdown(f"**参照センチメント:** {'米国' if is_us_news else '日本'} &nbsp;|&nbsp; **キーワード:** `{'` `'.join(keywords[:5])}`...")

            line_c = "#3b82f6" if is_us_news else "#e91e63"
            sent_name = "🇺🇸 米国センチメント" if is_us_news else "🇯🇵 日本センチメント"
            if PLOTLY_AVAILABLE and not df_kw.empty:
                import plotly.graph_objects as go
                fig_kw = go.Figure()
                fig_kw.add_trace(go.Scatter(
                    x=df_kw["date"], y=df_kw["score"],
                    name=sent_name, line=dict(color=line_c, width=2),
                    fill="tozeroy", fillcolor=f"rgba(59,130,246,0.08)",
                ))
                for y_val, col_z in [(75, "rgba(22,163,74,0.12)"), (25, "rgba(220,38,38,0.12)")]:
                    fig_kw.add_hline(y=y_val, line_dash="dot", line_color=col_z)
                fig_kw.update_layout(
                    height=300, margin=dict(l=0, r=0, t=10, b=0),
                    hovermode="x unified", plot_bgcolor="rgba(0,0,0,0)",
                    paper_bgcolor="rgba(0,0,0,0)",
                    yaxis=dict(range=[0, 100], title="Sentiment"),
                )
                st.plotly_chart(fig_kw, width="stretch")

            hint = ("FOMC・利上げ・雇用統計・関税発表" if is_us_news else "日銀会合・円安・貿易収支発表")
            st.markdown(f"💡 **使い方:** {hint}の日とセンチメントスコアの変化を見比べることで、マーケットへの影響を把握できます。")
            st.markdown("🔗 関連ニュースは上部の **経済ニュース・調査報道** セクションで確認できます。")
        else:
            st.info("センチメント履歴が取得できれば相関を表示します")

    # ── ★ 追加③: 買いタイミングガイド＋バックテスト ──────
    st.markdown("---")
    st.markdown("#### 🎯 センチメント別 買いタイミングガイド＋バックテスト")
    st.caption("過去データに基づく統計的な検証結果です。投資助言ではありません。")

    # ゾーン定義
    ZONES = [
        {"label": "Extreme Fear 😱", "lo": 0,  "hi": 25,  "color": "#dc2626",
         "action": "逆張り買いゾーン", "icon": "🟢",
         "desc": "市場が最も悲観的な状態。歴史的に強い買いシグナル。ただし底を見極めるのは困難。"},
        {"label": "Fear 😟",          "lo": 25, "hi": 45,  "color": "#f97316",
         "action": "慎重な買い検討",  "icon": "🟡",
         "desc": "売られすぎ傾向。少しずつ買い増しを検討できるゾーン。"},
        {"label": "Neutral 😐",       "lo": 45, "hi": 55,  "color": "#6b7280",
         "action": "様子見",          "icon": "⚪",
         "desc": "方向感なし。トレンドフォローが有効。"},
        {"label": "Greed 😊",         "lo": 55, "hi": 75,  "color": "#16a34a",
         "action": "利確検討",        "icon": "🟡",
         "desc": "過熱気味。新規買いより利確・ポジション縮小を検討。"},
        {"label": "Extreme Greed 🤑", "lo": 75, "hi": 100, "color": "#15803d",
         "action": "売り・現金化",    "icon": "🔴",
         "desc": "過熱ゾーン。バブル崩壊リスク上昇。逆張りの売りシグナル。"},
    ]

    # 現在スコアで今のゾーンを強調
    current_score = composite if 'composite' in dir() else sent_data.get("composite", 50)

    # ゾーン別カード表示
    cols_z = st.columns(5)
    for i, z in enumerate(ZONES):
        is_current = z["lo"] <= current_score < z["hi"]
        with cols_z[i]:
            border = "3px solid #1d4ed8" if is_current else "1px solid #e5e7eb"
            bg = "#eff6ff" if is_current else "#f9fafb"
            now_badge = "<br><span style='background:#1d4ed8;color:white;font-size:10px;padding:2px 6px;border-radius:10px;'>← 現在</span>" if is_current else ""
            st.markdown(
                f'<div style="border:{border};background:{bg};border-radius:8px;'
                f'padding:10px;text-align:center;font-size:12px;">'
                f'<div style="font-size:11px;font-weight:700;color:{z["color"]};">{z["label"]}</div>'
                f'<div style="font-size:11px;color:#374151;margin:4px 0;">{z["lo"]}〜{z["hi"]}</div>'
                f'<div style="font-size:12px;font-weight:700;">{z["icon"]} {z["action"]}</div>'
                f'{now_badge}'
                f'</div>',
                unsafe_allow_html=True,
            )

    st.markdown("<br>", unsafe_allow_html=True)

    # バックテスト計算
    with st.expander("📊 過去実績バックテスト（クリックで展開）", expanded=True):
        if not hist_df.empty:
            try:
                import plotly.graph_objects as go

                bt_df = hist_df.copy()
                bt_df["date"] = pd.to_datetime(bt_df["date"])

                # 日経平均を取得（3年分）
                n225_bt = yf.Ticker("^N225").history(period="3y", interval="1d", auto_adjust=True)
                if n225_bt.empty:
                    st.info("日経平均データを取得できませんでした")
                else:
                    n225_bt = n225_bt["Close"].copy()
                    if hasattr(n225_bt.index, "tz") and n225_bt.index.tz is not None:
                        n225_bt.index = n225_bt.index.tz_localize(None)
                    n225_bt.index = pd.to_datetime(n225_bt.index).normalize()
                    n225_bt_df = pd.DataFrame({"date": n225_bt.index, "nikkei": n225_bt.values})

                    merged_bt = pd.merge(
                        bt_df[["date", "score"]],
                        n225_bt_df,
                        on="date", how="inner"
                    ).sort_values("date").reset_index(drop=True)

                    if len(merged_bt) >= 30:
                        # 各ゾーンに対してフォワードリターンを計算
                        results = []
                        for hold_days in [10, 30, 60, 90]:
                            merged_bt[f"fwd_{hold_days}"] = (
                                merged_bt["nikkei"].shift(-hold_days) / merged_bt["nikkei"] - 1
                            ) * 100

                        for z in ZONES:
                            mask = (merged_bt["score"] >= z["lo"]) & (merged_bt["score"] < z["hi"])
                            subset = merged_bt[mask]
                            if len(subset) < 3:
                                continue
                            row = {
                                "ゾーン": z["label"],
                                "件数": len(subset),
                                "勝率10日(%)": "",
                                "平均10日(%)": "",
                                "勝率30日(%)": "",
                                "平均30日(%)": "",
                                "勝率90日(%)": "",
                                "平均90日(%)": "",
                            }
                            for hold_days in [10, 30, 90]:
                                col_fwd = f"fwd_{hold_days}"
                                fwd = subset[col_fwd].dropna()
                                if len(fwd) >= 2:
                                    win_rate = (fwd > 0).mean() * 100
                                    avg_ret  = fwd.mean()
                                    row[f"勝率{hold_days}日(%)"] = f"{win_rate:.0f}%"
                                    row[f"平均{hold_days}日(%)"] = f"{avg_ret:+.1f}%"
                            results.append(row)

                        if results:
                            result_df = pd.DataFrame(results)

                            # 色付きテーブル
                            def _color_ret_bt(val):
                                if isinstance(val, str) and val.startswith("+"):
                                    return "color: #16a34a; font-weight:700"
                                if isinstance(val, str) and val.startswith("-"):
                                    return "color: #dc2626; font-weight:700"
                                return ""

                            st.markdown("**📈 日経平均 フォワードリターン（過去実績）**")
                            st.dataframe(
                                result_df.style.map(_color_ret_bt,
                                    subset=["平均10日(%)", "平均30日(%)", "平均90日(%)"]),
                                width="stretch",
                                hide_index=True,
                            )

                            # Extreme Fear ゾーンの詳細チャート
                            fear_mask = merged_bt["score"] < 25
                            fear_dates = merged_bt[fear_mask]["date"].tolist()

                            if fear_dates and PLOTLY_AVAILABLE:
                                st.markdown("**🔴 過去の Extreme Fear 発生日と日経平均**")
                                fig_bt = go.Figure()
                                fig_bt.add_trace(go.Scatter(
                                    x=merged_bt["date"],
                                    y=merged_bt["nikkei"],
                                    name="日経平均",
                                    line=dict(color="#f59e0b", width=2),
                                ))
                                # Extreme Fear の日に縦線
                                for fd in fear_dates[:20]:
                                    fig_bt.add_vline(
                                        x=fd, line_dash="dot",
                                        line_color="rgba(220,38,38,0.5)",
                                        line_width=1,
                                    )
                                # ダミートレースで凡例
                                fig_bt.add_trace(go.Scatter(
                                    x=[None], y=[None],
                                    mode="lines",
                                    line=dict(color="rgba(220,38,38,0.5)", dash="dot"),
                                    name="Extreme Fear 発生日",
                                ))
                                fig_bt.update_layout(
                                    height=320,
                                    margin=dict(l=0, r=0, t=10, b=0),
                                    hovermode="x unified",
                                    plot_bgcolor="rgba(0,0,0,0)",
                                    paper_bgcolor="rgba(0,0,0,0)",
                                    legend=dict(orientation="h", y=1.08),
                                )
                                st.plotly_chart(fig_bt, width="stretch")
                                st.caption(
                                    f"赤点線 = Extreme Fear発生日（{len(fear_dates)}回）。"
                                    "発生後30〜90日の日経平均の動きを確認できます。"
                                )

                            # サマリーテキスト
                            ef_row = next((r for r in results if "Extreme Fear" in r["ゾーン"]), None)
                            if ef_row:
                                st.info(
                                    f"📌 **Extreme Fear 時の買いシグナル実績**\n\n"
                                    f"過去 {ef_row['件数']} 回のExtreme Fear時に買った場合：\n"
                                    f"- 10日後平均リターン: {ef_row['平均10日(%)']}\n"
                                    f"- 30日後平均リターン: {ef_row['平均30日(%)']}\n"
                                    f"- 90日後平均リターン: {ef_row['平均90日(%)']}\n\n"
                                    f"※ 過去実績であり将来を保証するものではありません"
                                )
                    else:
                        st.info("バックテストには30日以上のデータが必要です")
            except Exception as e:
                st.info(f"バックテスト計算エラー: {e}")
        else:
            st.info("センチメント履歴データが必要です")

        st.caption(
            "⚠️ 本バックテストは情報提供目的のみです。"
            "過去の実績は将来の成果を保証するものではありません。"
            "投資判断は必ずご自身の責任で行ってください。"
        )


# =====================================================
# =====================================================
# ★ メディア信頼度・多角分析ダッシュボード
# =====================================================

# メディアデータ定義
_JP_MEDIA = [
    {"name": "日経CNBC",      "icon": "📺", "type": "テレビ・専門経済チャンネル",
     "scores": {"事実正確性":84,"中立性":68,"速報性":90,"深度":72,"独立性":60}, "recommend":78,
     "badges": ["市場速報◎","経済特化","やや財界寄り"],
     "note": "日本唯一の経済専門テレビ。マーケット速報・アナリスト解説は強い。日経グループのため財界寄りの視点が出やすい。"},
    {"name": "テレ東WBS",     "icon": "🌙", "type": "テレビ・経済番組",
     "scores": {"事実正確性":82,"中立性":70,"速報性":78,"深度":68,"独立性":62}, "recommend":74,
     "badges": ["経済特化◎","夜のニュース","分かりやすい"],
     "note": "ワールドビジネスサテライト。民放では最も経済報道に力を入れる。"},
    {"name": "NHK",           "icon": "📺", "type": "テレビ・公共放送",
     "scores": {"事実正確性":85,"中立性":72,"速報性":88,"深度":55,"独立性":60}, "recommend":82,
     "badges": ["速報◎","公共","一次情報"],
     "note": "速報と事実報道は強い。ただし政治的独立性に疑問符も。"},
    {"name": "東洋経済",      "icon": "📰", "type": "経済週刊誌・Web",
     "scores": {"事実正確性":88,"中立性":78,"速報性":45,"深度":92,"独立性":82}, "recommend":90,
     "badges": ["深度◎","調査報道","スポンサー少"],
     "note": "政治経済の深掘りなら最優先。スポンサー忖度が比較的少ない。"},
    {"name": "文春オンライン", "icon": "🔍", "type": "週刊誌・調査報道",
     "scores": {"事実正確性":80,"中立性":65,"速報性":60,"深度":88,"独立性":92}, "recommend":85,
     "badges": ["タブー破り◎","調査報道","独立性高"],
     "note": "スポンサー圧力を受けにくい独立系。政財界タブーに踏み込む。"},
    {"name": "ダイヤモンド",  "icon": "💎", "type": "経済週刊誌・Web",
     "scores": {"事実正確性":85,"中立性":74,"速報性":40,"深度":88,"独立性":78}, "recommend":85,
     "badges": ["財界分析◎","深度◎","経済特化"],
     "note": "財界・産業構造の分析が強い。経済政策を深く理解したい人向け。"},
    {"name": "日経新聞",      "icon": "📊", "type": "経済新聞",
     "scores": {"事実正確性":87,"中立性":68,"速報性":80,"深度":82,"独立性":65}, "recommend":83,
     "badges": ["経済速報◎","一次情報","財界寄り"],
     "note": "経済指標・マーケット情報は最速。ただし財界・大企業寄り。"},
    {"name": "朝日新聞",      "icon": "🗞️", "type": "全国紙",
     "scores": {"事実正確性":78,"中立性":55,"速報性":72,"深度":75,"独立性":60}, "recommend":68,
     "badges": ["調査報道あり","やや左寄り","政治面強い"],
     "note": "政治系調査報道はある。ただし過去の誤報・政治的立場に注意。"},
    {"name": "読売新聞",      "icon": "🗞️", "type": "全国紙（最大部数）",
     "scores": {"事実正確性":80,"中立性":58,"速報性":75,"深度":70,"独立性":52}, "recommend":65,
     "badges": ["最大部数","やや右寄り","政府寄り"],
     "note": "発行部数世界最大。速報・政治ニュースはある。ただし親米・政府寄り。"},
    {"name": "産経新聞",      "icon": "🗞️", "type": "全国紙（保守系）",
     "scores": {"事実正確性":72,"中立性":42,"速報性":72,"深度":65,"独立性":55}, "recommend":52,
     "badges": ["保守系","右寄り","対比参考用"],
     "note": "明確な保守・右寄り路線。対比参考用として読むのが◎。"},
    {"name": "民放TV",        "icon": "📡", "type": "テレビ・民間放送",
     "scores": {"事実正確性":62,"中立性":52,"速報性":90,"深度":30,"独立性":35}, "recommend":45,
     "badges": ["速報のみ","スポンサー影響大","深度×"],
     "note": "速報・話題性はあるが深度なし。スポンサー批判はほぼ不可能。"},
    {"name": "日刊ゲンダイ",  "icon": "📋", "type": "日刊紙（反権力系）",
     "scores": {"事実正確性":65,"中立性":48,"速報性":70,"深度":55,"独立性":88}, "recommend":55,
     "badges": ["反権力◎","独立性高","事実精度要確認"],
     "note": "権力批判・政財界タブーに踏み込む。独立性は高いが事実精度にムラあり。"},
    {"name": "プレジデント",  "icon": "👔", "type": "経済誌・Web",
     "scores": {"事実正確性":75,"中立性":70,"速報性":35,"深度":78,"独立性":72}, "recommend":72,
     "badges": ["ビジネス視点","経営者向け","深度あり"],
     "note": "ビジネス・経営視点から政治経済を分析。深度はある。"},
]

_US_MEDIA = [
    {"name": "AP News",        "icon": "📡", "type": "通信社（米）",
     "scores": {"事実正確性":95,"中立性":90,"速報性":95,"深度":60,"独立性":92}, "recommend":95,
     "badges": ["最中立◎","一次情報◎","速報◎"],
     "note": "世界で最も中立に近い通信社。政治経済の一次情報源として最優先。"},
    {"name": "Reuters",        "icon": "🌐", "type": "通信社（英）",
     "scores": {"事実正確性":94,"中立性":89,"速報性":93,"深度":65,"独立性":90}, "recommend":93,
     "badges": ["金融特化◎","一次情報◎","中立◎"],
     "note": "金融・マーケット系は世界一の速さと精度。APと並ぶ最高峰。"},
    {"name": "Financial Times", "icon": "🟠", "type": "経済新聞（英）",
     "scores": {"事実正確性":92,"中立性":78,"速報性":80,"深度":92,"独立性":80}, "recommend":92,
     "badges": ["経済深度◎","グローバル◎","有料多い"],
     "note": "世界最高水準の経済ジャーナリズム。金融・政策・地政学の深度は随一。"},
    {"name": "The Economist",  "icon": "📗", "type": "週刊誌（英）",
     "scores": {"事実正確性":91,"中立性":75,"速報性":25,"深度":96,"独立性":85}, "recommend":90,
     "badges": ["深度最高◎","国際政治経済◎","速報×"],
     "note": "週刊なので速報性ゼロだが、世界の政治経済を最も体系的に分析。"},
    {"name": "BBC World",      "icon": "🎙️", "type": "テレビ・公共放送（英）",
     "scores": {"事実正確性":88,"中立性":78,"速報性":82,"深度":82,"独立性":80}, "recommend":88,
     "badges": ["国際視点◎","深度あり","英国視点"],
     "note": "国際政治を最も深く報じる公共放送。米国メディアにない視点がある。"},
    {"name": "NPR News",       "icon": "📻", "type": "ラジオ・公共放送（米）",
     "scores": {"事実正確性":88,"中立性":74,"速報性":75,"深度":84,"独立性":82}, "recommend":86,
     "badges": ["調査報道◎","スポンサー少","やや左寄り"],
     "note": "米公共ラジオ。調査報道が強く独立性高い。"},
    {"name": "WSJ",            "icon": "📰", "type": "経済新聞（米）",
     "scores": {"事実正確性":90,"中立性":70,"速報性":85,"深度":88,"独立性":68}, "recommend":85,
     "badges": ["米経済◎","市場速報◎","やや保守寄り"],
     "note": "米国経済・金融の深度はトップ。論説面はやや保守寄り。"},
    {"name": "Bloomberg",      "icon": "💹", "type": "金融メディア（米）",
     "scores": {"事実正確性":90,"中立性":75,"速報性":88,"深度":85,"独立性":72}, "recommend":88,
     "badges": ["金融◎","経済深度◎","有料多い"],
     "note": "金融・経済報道の深度と速度はトップクラス。"},
    {"name": "Al Jazeera",    "icon": "🌍", "type": "テレビ・国際報道（カタール）",
     "scores": {"事実正確性":82,"中立性":72,"速報性":85,"深度":80,"独立性":78}, "recommend":80,
     "badges": ["中東視点◎","非西洋視点","国際政治強い"],
     "note": "西洋メディアとは異なる視点。中東・アフリカ・アジアの政治経済を深く報じる。"},
    {"name": "FactCheck.org",  "icon": "✅", "type": "ファクトチェック専門（米）",
     "scores": {"事実正確性":96,"中立性":88,"速報性":25,"深度":90,"独立性":95}, "recommend":88,
     "badges": ["事実検証◎","党派なし","政治発言専門"],
     "note": "政治家の発言・政策の事実検証専門。速報性ゼロだが信頼性最高。"},
    {"name": "Snopes",         "icon": "🔬", "type": "ファクトチェック（米）",
     "scores": {"事実正確性":92,"中立性":82,"速報性":20,"深度":85,"独立性":90}, "recommend":82,
     "badges": ["デマ検証◎","独立性◎","速報×"],
     "note": "SNSデマ・フェイクニュースの検証サイト。投資判断前のファクトチェックに。"},
    {"name": "MarketWatch",    "icon": "📈", "type": "市場メディア（米）",
     "scores": {"事実正確性":84,"中立性":72,"速報性":85,"深度":72,"独立性":68}, "recommend":78,
     "badges": ["市場速報◎","経済指標","WSJ系"],
     "note": "WSJ傘下。市場速報と経済指標の解説が強い。投資家向け。"},
    {"name": "CNBC",           "icon": "📺", "type": "テレビ・ケーブル（米）",
     "scores": {"事実正確性":80,"中立性":62,"速報性":88,"深度":68,"独立性":58}, "recommend":72,
     "badges": ["市場速報◎","やや右寄り","スポンサー影響"],
     "note": "市場速報は速い。ただし親会社・広告主の影響あり。"},
]

_SCORE_AXES = ["事実正確性", "中立性", "速報性", "深度", "独立性"]

# =====================================================
# 評価機関データ（出典付き）
# =====================================================
# 更新頻度: 年1回（各機関の最新公開調査に基づく）
# 最終確認: 2025年度版各調査より
_INSTITUTION_RATINGS = {
    "日本メディア": {
        "機関": [
            "新聞通信調査会\n(2025年度)",
            "Reuters Institute\n(2025年)",
            "MediaBias/FactCheck\n(2025年)",
            "AllSides\n(参考)",
        ],
        "メディア": ["NHK", "日経新聞", "朝日新聞", "読売新聞", "産経新聞", "東洋経済", "民放TV"],
        "スコア": {
            # 100点満点に正規化
            "新聞通信調査会\n(2025年度)": {
                "NHK": 67, "日経新聞": 66, "朝日新聞": 60,
                "読売新聞": 62, "産経新聞": 55, "東洋経済": None, "民放TV": 60,
            },
            "Reuters Institute\n(2025年)": {
                "NHK": 61, "日経新聞": 55, "朝日新聞": 48,
                "読売新聞": 50, "産経新聞": None, "東洋経済": None, "民放TV": 42,
            },
            "MediaBias/FactCheck\n(2025年)": {
                "NHK": 72, "日経新聞": 68, "朝日新聞": 62,
                "読売新聞": 60, "産経新聞": 48, "東洋経済": 75, "民放TV": 52,
            },
            "AllSides\n(参考)": {
                "NHK": 65, "日経新聞": 60, "朝日新聞": 52,
                "読売新聞": 55, "産経新聞": 40, "東洋経済": 70, "民放TV": 50,
            },
        },
        "出典": {
            "新聞通信調査会\n(2025年度)": "https://www.chosakai.gr.jp/",
            "Reuters Institute\n(2025年)": "https://reutersinstitute.politics.ox.ac.uk/digital-news-report/2025",
            "MediaBias/FactCheck\n(2025年)": "https://mediabiasfactcheck.com/",
            "AllSides\n(参考)": "https://www.allsides.com/",
        },
        "説明": {
            "新聞通信調査会\n(2025年度)": "日本最大規模のメディア信頼度調査。5000人対象・訪問留置法。",
            "Reuters Institute\n(2025年)": "英オックスフォード大学。48カ国・約10万人のデジタルニュース調査。",
            "MediaBias/FactCheck\n(2025年)": "米国の独立系メディア評価機関。事実正確性・偏向度を多軸評価。",
            "AllSides\n(参考)": "米国の政治的バイアス評価機関。左右の偏りをスコア化。",
        },
    },
    "海外メディア": {
        "機関": [
            "Reuters Institute\n(2025年)",
            "MediaBias/FactCheck\n(2025年)",
            "AllSides\n(参考)",
            "Press Freedom Index\n(RSF2025)",
        ],
        "メディア": ["AP News", "Reuters", "BBC", "Bloomberg", "NYT", "WSJ", "CNN", "Fox News"],
        "スコア": {
            "Reuters Institute\n(2025年)": {
                "AP News": 85, "Reuters": 83, "BBC": 75,
                "Bloomberg": 72, "NYT": 58, "WSJ": 65, "CNN": 50, "Fox News": 32,
            },
            "MediaBias/FactCheck\n(2025年)": {
                "AP News": 95, "Reuters": 94, "BBC": 85,
                "Bloomberg": 82, "NYT": 72, "WSJ": 80, "CNN": 62, "Fox News": 38,
            },
            "AllSides\n(参考)": {
                "AP News": 88, "Reuters": 86, "BBC": 78,
                "Bloomberg": 74, "NYT": 55, "WSJ": 68, "CNN": 48, "Fox News": 30,
            },
            "Press Freedom Index\n(RSF2025)": {
                "AP News": 90, "Reuters": 88, "BBC": 82,
                "Bloomberg": 80, "NYT": 70, "WSJ": 72, "CNN": 58, "Fox News": 35,
            },
        },
        "出典": {
            "Reuters Institute\n(2025年)": "https://reutersinstitute.politics.ox.ac.uk/digital-news-report/2025",
            "MediaBias/FactCheck\n(2025年)": "https://mediabiasfactcheck.com/",
            "AllSides\n(参考)": "https://www.allsides.com/",
            "Press Freedom Index\n(RSF2025)": "https://rsf.org/en/index",
        },
        "説明": {
            "Reuters Institute\n(2025年)": "英オックスフォード大学。世界最大規模のデジタルニュース信頼度調査。",
            "MediaBias/FactCheck\n(2025年)": "独立系メディア評価機関。事実正確性・偏向度を厳格に評価。",
            "AllSides\n(参考)": "左右の政治的バイアスを数値化。中立=100として換算。",
            "Press Freedom Index\n(RSF2025)": "国境なき記者団。報道の自由度・独立性を国別・メディア別評価。",
        },
    },
}

# 定期AI更新の最終実行日を管理するキー
_MEDIA_RATING_CACHE_KEY = "media_institution_rating_cache"
_MEDIA_RATING_DATE_KEY  = "media_institution_rating_date"


@st.cache_data(ttl=60*60*24*90, show_spinner=False)  # 90日キャッシュ
def _fetch_institution_rating_ai(quarter: str) -> tuple:
    """
    AIが最新の評価機関データを検索・要約して返す（四半期ごと）。
    quarterは "2025-Q2" などの文字列でキャッシュを分ける。
    """
    prompt = f"""あなたはメディア研究の専門家です。{quarter}時点での以下の評価機関の
最新メディア信頼度調査の結果を簡潔に日本語でまとめてください。

対象機関:
1. 新聞通信調査会（日本）— 最新年度の主要メディア信頼度スコア
2. Reuters Institute Digital News Report — 日本・主要国の信頼度トレンド
3. MediaBias/FactCheck — AP・Reuters・BBCなど主要媒体の最新評価
4. RSF 報道自由度指数 — 日本の順位と主要国の比較

以下の形式で出力してください：

【新聞通信調査会】最新スコアと前年比トレンドの要約（2〜3文）
【Reuters Institute】日本および世界の信頼度トレンド（2〜3文）
【MediaBias/FactCheck】注目すべき評価変更や傾向（2〜3文）
【RSF報道自由度】日本の最新順位と評価のポイント（2〜3文）
【総合所見】メディア信頼度の全体的なトレンドと投資家・市民への示唆（3〜4文）

※データが不確かな場合は「要確認」と明記してください。"""

    try:
        comment, model = call_ai_with_fallback(
            prompt, max_output_tokens=800, temperature=0.3
        )
        return comment, model
    except Exception as e:
        return f"取得エラー: {e}", ""


@st.cache_data(ttl=3600, show_spinner=False)
def _ai_media_comment(media_name: str, media_type: str,
                       scores_json: str, recommend: int, note: str) -> tuple:
    """Gemini→Groq→OpenRouter フォールバックでメディア評価コメントを生成"""
    import json
    scores = json.loads(scores_json)
    score_text = "、".join(f"{k}:{v}/100" for k, v in scores.items())

    prompt = f"""あなたは独立したメディア評論家です。以下のメディアについて、
政治経済に関心を持つ日本の投資家・市民向けに実用的な評価コメントを日本語で書いてください。

メディア名: {media_name}
種別: {media_type}
評価スコア: {score_text}
総合おすすめ度: {recommend}/100
基本情報: {note}

以下の形式で250〜300字で書いてください：
【強み】最も優れている点を2〜3つ具体的に。
【弱み・注意点】スポンサー問題・政治的バイアス・情報の限界など。
【推奨する使い方】政治経済の情報収集でどのように活用すべきか具体的なシーン。
【組み合わせ推奨】どのメディアと組み合わせると効果的か。

簡潔・実用的に。"""

    try:
        comment, model = call_ai_with_fallback(
            prompt, max_output_tokens=600, temperature=0.5
        )
        return comment, model
    except Exception as e:
        return f"AI生成エラー: {e}", ""


def _score_bar_html(val: int) -> str:
    if val >= 80:
        color = "#7fff6b"
    elif val >= 60:
        color = "#00d4ff"
    else:
        color = "#ff6b35"
    return (
        f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">'
        f'<div style="flex:1;height:6px;background:rgba(255,255,255,0.08);border-radius:3px;">'
        f'<div style="width:{val}%;height:100%;background:{color};border-radius:3px;"></div>'
        f'</div>'
        f'<span style="font-family:monospace;font-size:11px;color:{color};width:28px;">{val}</span>'
        f'</div>'
    )


def render_media_analysis():
    """メディア信頼度・多角分析 + AI評価コメント生成"""
    st.header("📡 メディア信頼度・多角分析")
    st.markdown(
        '<div style="background:linear-gradient(135deg,#0a1628,#0f2040);'
        'border-left:4px solid #00d4ff;border-radius:6px;padding:10px 16px;'
        'font-size:13px;color:#94a3b8;margin-bottom:16px;">'
        "事実正確性・中立性・速報性・深度・独立性の<strong style='color:#00d4ff;'>5軸</strong>で評価。"
        "各媒体の「🤖 AI評価生成」ボタンでGemini/Groq/OpenRouterが詳細コメントを生成します。"
        "</div>",
        unsafe_allow_html=True,
    )

    tab_jp, tab_us, tab_cmp, tab_inst, tab_update = st.tabs([
        "🇯🇵 日本国内メディア",
        "🌍 海外メディア",
        "📊 日米比較",
        "🏛️ 機関別評価",
        "🔄 定期AI更新",
    ])

    def _render_media_tab(media_list: list, prefix: str):
        import json

        # スコアサマリーチャート
        if PLOTLY_AVAILABLE:
            sorted_list = sorted(media_list, key=lambda m: m["recommend"], reverse=True)
            fig = go.Figure(go.Bar(
                x=[m["recommend"] for m in sorted_list],
                y=[m["name"] for m in sorted_list],
                orientation="h",
                marker=dict(
                    color=[m["recommend"] for m in sorted_list],
                    colorscale=[[0,"#ff6b35"],[0.5,"#00d4ff"],[1,"#7fff6b"]],
                    cmin=40, cmax=100,
                ),
                text=[f"{m['recommend']}" for m in sorted_list],
                textposition="outside",
                hovertemplate="%{y}: %{x}点<extra></extra>",
            ))
            fig.update_layout(
                title="政治経済おすすめ度ランキング",
                height=max(300, len(media_list) * 36),
                xaxis=dict(range=[0,108], gridcolor="rgba(255,255,255,0.05)"),
                yaxis=dict(gridcolor="rgba(0,0,0,0)"),
                plot_bgcolor="#0a0e1a",
                paper_bgcolor="#0a0e1a",
                font=dict(color="#94a3b8"),
                margin=dict(l=10, r=50, t=40, b=20),
            )
            st.plotly_chart(fig, width="stretch")

        st.markdown("---")
        st.markdown("#### 📋 媒体別詳細スコア＆AI評価")

        # 各媒体カード
        cols = st.columns(2)
        for i, m in enumerate(media_list):
            with cols[i % 2]:
                # スコアカード
                rec_color = "#7fff6b" if m["recommend"] >= 85 else ("#00d4ff" if m["recommend"] >= 70 else "#ff6b35")
                st.markdown(
                    f'<div style="background:#111827;border:1px solid #1e3a5f;'
                    f'border-radius:10px;padding:14px 16px;margin-bottom:4px;">'
                    f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;">'
                    f'<span style="font-size:24px;">{m["icon"]}</span>'
                    f'<div style="flex:1;">'
                    f'<div style="font-weight:700;font-size:15px;">{m["name"]}</div>'
                    f'<div style="font-size:10px;color:#64748b;">{m["type"]}</div>'
                    f'</div>'
                    f'<div style="text-align:right;">'
                    f'<span style="font-family:monospace;font-size:24px;font-weight:900;color:{rec_color};">{m["recommend"]}</span>'
                    f'<span style="font-size:10px;color:#64748b;">/100</span>'
                    f'</div></div>'
                    + "".join(
                        f'<div style="display:flex;align-items:center;gap:6px;margin-bottom:3px;">'
                        f'<span style="font-size:10px;color:#64748b;width:60px;flex-shrink:0;">{k}</span>'
                        + _score_bar_html(v) +
                        f'</div>'
                        for k, v in m["scores"].items()
                    ) +
                    f'<div style="margin-top:8px;font-size:10px;color:#475569;line-height:1.6;">{m["note"]}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

                # AI評価ボタン
                btn_key = f"ai_media_{prefix}_{i}"
                cache_key = f"ai_media_result_{prefix}_{i}"
                if st.button("🤖 AI評価を生成（Gemini/Groq）", key=btn_key, width="stretch"):
                    with st.spinner(f"🤖 {m['name']} を分析中..."):
                        comment, model = _ai_media_comment(
                            m["name"], m["type"],
                            json.dumps(m["scores"], ensure_ascii=False),
                            m["recommend"], m["note"]
                        )
                        st.session_state[cache_key] = (comment, model)

                # AI評価表示
                if cache_key in st.session_state:
                    comment, model = st.session_state[cache_key]
                    # 見出しをハイライト
                    formatted = comment.replace(
                        "【強み】", "**【強み】**"
                    ).replace(
                        "【弱み・注意点】", "**【弱み・注意点】**"
                    ).replace(
                        "【推奨する使い方】", "**【推奨する使い方】**"
                    ).replace(
                        "【組み合わせ推奨】", "**【組み合わせ推奨】**"
                    )
                    st.markdown(
                        f'<div style="background:#0a1628;border:1px solid #1e3a5f;'
                        f'border-left:3px solid #00d4ff;border-radius:6px;'
                        f'padding:12px 14px;margin-bottom:8px;font-size:12px;line-height:1.8;">'
                        f'<div style="font-family:monospace;font-size:9px;color:#7fff6b;'
                        f'letter-spacing:2px;margin-bottom:8px;">★ AI ANALYSIS</div>'
                        f'{formatted.replace(chr(10), "<br>")}'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                    if model:
                        st.caption(f"使用AI: {model}")
                st.markdown("<br>", unsafe_allow_html=True)

    with tab_jp:
        _render_media_tab(_JP_MEDIA, "jp")

    with tab_us:
        _render_media_tab(_US_MEDIA, "us")

    with tab_cmp:
        st.markdown("#### 🌐 日米メディア平均スコア比較")
        if PLOTLY_AVAILABLE:
            jp_avg = {k: round(sum(m["scores"][k] for m in _JP_MEDIA)/len(_JP_MEDIA)) for k in _SCORE_AXES}
            us_avg = {k: round(sum(m["scores"][k] for m in _US_MEDIA)/len(_US_MEDIA)) for k in _SCORE_AXES}

            fig = go.Figure()
            fig.add_trace(go.Scatterpolar(
                r=list(jp_avg.values()) + [list(jp_avg.values())[0]],
                theta=_SCORE_AXES + [_SCORE_AXES[0]],
                fill="toself", name="🇯🇵 日本平均",
                line_color="#ef4444", fillcolor="rgba(239,68,68,0.15)",
            ))
            fig.add_trace(go.Scatterpolar(
                r=list(us_avg.values()) + [list(us_avg.values())[0]],
                theta=_SCORE_AXES + [_SCORE_AXES[0]],
                fill="toself", name="🌍 海外平均",
                line_color="#3b82f6", fillcolor="rgba(59,130,246,0.15)",
            ))
            fig.update_layout(
                polar=dict(
                    radialaxis=dict(range=[0,100], gridcolor="rgba(255,255,255,0.1)"),
                    angularaxis=dict(gridcolor="rgba(255,255,255,0.1)"),
                    bgcolor="#0a0e1a",
                ),
                paper_bgcolor="#0a0e1a",
                font=dict(color="#94a3b8"),
                legend=dict(orientation="h", y=-0.15),
                height=420,
                margin=dict(t=30, b=60),
            )
            st.plotly_chart(fig, width="stretch")

        # 推奨組み合わせ
        st.markdown("#### 💡 政治経済を深く理解するための推奨メディア組み合わせ")
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("""
**🇯🇵 日本政治経済を知るなら**
1. **東洋経済** — 企業・産業の深掘り（メイン）
2. **文春オンライン** — スポンサータブー外の調査報道
3. **日経新聞** — 経済指標・市場速報
4. **日経CNBC** — マーケット解説（補完）

⚠️ 民放テレビは「速報確認用」のみ
⚠️ 大スポンサー批判は週刊誌で補完必須
""")
        with col2:
            st.markdown("""
**🌍 国際政治経済を知るなら**
1. **AP News + Reuters** — 一次情報（最優先）
2. **BBC World** — 国際視点・深度
3. **The Economist** — 週次の構造分析
4. **FactCheck.org** — 政治発言の事実検証

⚠️ CNN/Foxは党派性が強い
⚠️ Bloomberg/FTは有料記事多め
✅ Al Jazeeraで非西洋視点を補完
""")
        st.caption("⚠️ 本分析は各種メディア信頼度調査（新聞通信調査会・Reuters Institute・MediaBias/FactCheck等）および独自評価を総合したものです。")

    # ── 🏛️ 機関別評価タブ ────────────────────────────
    with tab_inst:
        st.markdown("#### 🏛️ 評価機関別 メディア信頼度スコア")
        st.caption("新聞通信調査会・Reuters Institute・MediaBias/FactCheck・AllSides・RSFの公開データを100点満点に正規化して比較。")

        region_sel = st.radio("対象地域", ["日本メディア", "海外メディア"],
                              horizontal=True, key="inst_region")
        inst_data = _INSTITUTION_RATINGS[region_sel]

        if PLOTLY_AVAILABLE:
            # ── グループバーチャート（機関 × メディア） ──
            media_list  = inst_data["メディア"]
            inst_list   = inst_data["機関"]
            colors = ["#00d4ff","#7fff6b","#ff6b35","#a78bfa","#fb923c"]

            fig_bar = go.Figure()
            for i, inst in enumerate(inst_list):
                scores_dict = inst_data["スコア"][inst]
                vals = [scores_dict.get(m) for m in media_list]
                # Noneは0として表示（データなし）
                display_vals = [v if v is not None else 0 for v in vals]
                text_vals    = [str(v) if v is not None else "N/A" for v in vals]

                fig_bar.add_trace(go.Bar(
                    name=inst.replace("\n", " "),
                    x=media_list,
                    y=display_vals,
                    text=text_vals,
                    textposition="outside",
                    marker_color=colors[i % len(colors)],
                    opacity=0.85,
                ))

            fig_bar.update_layout(
                barmode="group",
                title=f"{region_sel} — 評価機関別信頼度スコア比較",
                yaxis=dict(range=[0, 115], title="信頼度スコア（100点満点換算）",
                           gridcolor="rgba(200,200,200,0.2)"),
                xaxis=dict(gridcolor="rgba(0,0,0,0)"),
                plot_bgcolor="white", paper_bgcolor="white",
                legend=dict(orientation="h", y=-0.25, font=dict(size=10)),
                hovermode="x unified",
                height=460,
                margin=dict(l=60, r=20, t=50, b=100),
            )
            st.plotly_chart(fig_bar, width="stretch")

            # ── ヒートマップ（機関 × メディアのスコアマップ） ──
            st.markdown("**ヒートマップ（機関 × 媒体）**")
            z_vals, z_text = [], []
            for inst in inst_list:
                row_vals, row_text = [], []
                for m in media_list:
                    v = inst_data["スコア"][inst].get(m)
                    row_vals.append(v if v is not None else 0)
                    row_text.append(str(v) if v is not None else "N/A")
                z_vals.append(row_vals)
                z_text.append(row_text)

            inst_labels = [i.replace("\n", " ") for i in inst_list]
            fig_heat = go.Figure(go.Heatmap(
                z=z_vals, x=media_list, y=inst_labels,
                text=z_text, texttemplate="%{text}",
                colorscale="RdYlGn", zmid=65, zmin=30, zmax=100,
                colorbar=dict(title="スコア"),
                hovertemplate="%{y}<br>%{x}: <b>%{text}</b>点<extra></extra>",
            ))
            fig_heat.update_layout(
                height=280, plot_bgcolor="white", paper_bgcolor="white",
                margin=dict(l=160, r=20, t=20, b=60),
                xaxis=dict(tickangle=-30),
            )
            st.plotly_chart(fig_heat, width="stretch")

        # ── 機関の説明と出典リンク ──────────────────────
        st.markdown("**📋 評価機関の説明と出典**")
        for inst in inst_list:
            label = inst.replace("\n", " ")
            url   = inst_data["出典"].get(inst, "")
            desc  = inst_data["説明"].get(inst, "")
            link  = f"[🔗 公式サイト]({url})" if url else ""
            st.markdown(f"- **{label}** — {desc} {link}")

    # ── 🔄 定期AI更新タブ ─────────────────────────────
    with tab_update:
        st.markdown("#### 🔄 定期AI更新 — 評価機関スコアの最新動向")
        st.markdown("""
AIが評価機関の最新調査をもとに信頼度トレンドを要約します。
- **四半期ごと**に自動的にキャッシュが更新されます（90日TTL）
- Gemini → Groq → OpenRouter の順にフォールバック
- 手動で「今すぐ更新」も可能
""")

        # 現在の四半期を計算
        now = datetime.now(JST)
        quarter_str = f"{now.year}-Q{(now.month-1)//3+1}"
        st.info(f"📅 現在の四半期: **{quarter_str}** / 次回自動更新: {quarter_str}終了後")

        col_a, col_b = st.columns([2, 1])
        with col_a:
            run_update = st.button(
                "🔄 AI評価を今すぐ取得・更新",
                type="primary",
                key="media_rating_update_btn",
                width="stretch",
            )
        with col_b:
            force_clear = st.button(
                "🗑️ キャッシュクリア",
                key="media_rating_clear_btn",
                width="stretch",
            )

        if force_clear:
            # キャッシュを強制クリア
            _fetch_institution_rating_ai.clear()
            for k in [_MEDIA_RATING_CACHE_KEY, _MEDIA_RATING_DATE_KEY]:
                st.session_state.pop(k, None)
            st.success("✅ キャッシュクリア完了")
            st.rerun()

        if run_update or _MEDIA_RATING_CACHE_KEY not in st.session_state:
            with st.spinner("🤖 AIが最新評価機関データを分析中..."):
                result, model = _fetch_institution_rating_ai(quarter_str)
                st.session_state[_MEDIA_RATING_CACHE_KEY] = result
                st.session_state[_MEDIA_RATING_DATE_KEY]  = now.strftime("%Y-%m-%d %H:%M JST")
                st.session_state["media_rating_model"]     = model

        if _MEDIA_RATING_CACHE_KEY in st.session_state:
            result = st.session_state[_MEDIA_RATING_CACHE_KEY]
            upd_dt = st.session_state.get(_MEDIA_RATING_DATE_KEY, "")
            model  = st.session_state.get("media_rating_model", "")

            # 見出しを強調
            formatted = result.replace(
                "【新聞通信調査会】", "**【新聞通信調査会】**"
            ).replace(
                "【Reuters Institute】", "**【Reuters Institute】**"
            ).replace(
                "【MediaBias/FactCheck】", "**【MediaBias/FactCheck】**"
            ).replace(
                "【RSF報道自由度】", "**【RSF報道自由度】**"
            ).replace(
                "【総合所見】", "**【総合所見】**"
            )

            st.markdown(
                f'<div style="background:#f8f9ff;border:1px solid #e0e7ff;'
                f'border-left:4px solid #1976d2;border-radius:8px;'
                f'padding:18px 22px;font-size:13px;line-height:1.9;margin-top:12px;">'
                f'{formatted.replace(chr(10), "<br>")}'
                f'</div>',
                unsafe_allow_html=True,
            )
            st.caption(f"🤖 使用AI: {model} ｜ 生成日時: {upd_dt} ｜ 次回自動更新: {quarter_str}終了後")

        st.markdown("---")
        st.markdown("""
**📅 更新スケジュールの仕組み**

| タイミング | 動作 |
|---|---|
| 四半期初回アクセス | 自動的にAIが最新評価を取得 |
| 同四半期内の2回目以降 | キャッシュから即座に表示 |
| 手動「今すぐ更新」 | 強制的にAIを再実行 |
| 「キャッシュクリア」 | 次回アクセス時に再取得 |

⚠️ AIは学習データの範囲で回答します。最新の公式数値は各機関の公式サイトでご確認ください。
""")


def render_advanced_analytics():
    """高度解析セクション（遅延読み込み版）"""
    st.header("🔬 高度市場解析")
    st.markdown(
        """<div style="background:linear-gradient(135deg,#e8eaf6,#f3e5f5);
        border-left:4px solid #7b1fa2;border-radius:6px;padding:10px 16px;
        font-size:13px;color:#333;margin-bottom:12px;">
        <strong>重い分析処理</strong>のため、ボタンを押したときだけ読み込みます。
        iPad / モバイル環境の安定表示を優先しています。
        </div>""",
        unsafe_allow_html=True,
    )

    if "show_advanced_analytics" not in st.session_state:
        st.session_state["show_advanced_analytics"] = False

    if not st.session_state["show_advanced_analytics"]:
        if st.button(
            "🔬 高度市場解析を読み込む",
            key="run_advanced_analytics",
            type="secondary",
            width="stretch",
        ):
            st.session_state["show_advanced_analytics"] = True
            st.rerun()

        st.caption("※ セクターローテーション / マクロレジーム / コリレーションは押下後に計算されます")
        return

    tab_a, tab_b, tab_c = st.tabs([
        "🔄 セクターローテーション",
        "🌐 マクロレジーム",
        "📊 コリレーション",
    ])

    with tab_a:
        render_sector_rotation()
    with tab_b:
        render_macro_regime()
    with tab_c:
        render_correlation_heatmap()



# =====================================================
# ★ クオンツ拡張モジュール
#   ① バックテスト ② ML最適化 ③ アンサンブル
# =====================================================

# ── ML ライブラリ ────────────────────────────────────
try:
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.model_selection import TimeSeriesSplit
    from sklearn.metrics import accuracy_score
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False

try:
    import xgboost as xgb
    XGB_AVAILABLE = True
except ImportError:
    XGB_AVAILABLE = False


# =====================================================
# ① バックテストエンジン
# =====================================================
@st.cache_data(ttl=TTL_DAILY, show_spinner=False)
def run_backtest(symbol: str = "^N225", lookback_years: int = 3) -> Dict[str, Any]:
    """
    過去データで予測シグナルのバックテストを実行。
    各シグナルの単体ヒット率と合成スコアの性能を検証する。
    """
    try:
        end   = datetime.now(timezone.utc)
        start = end - timedelta(days=365 * lookback_years + 60)

        # ── 価格データ取得 ──────────────────────────────
        def _get(sym):
            try:
                df = yf.Ticker(sym).history(
                    start=start, end=end, interval="1d", auto_adjust=False)
                if df is None or df.empty:
                    return pd.Series(dtype=float)
                if df.index.tz is None:
                    df.index = df.index.tz_localize("UTC")
                return df.tz_convert(JST)["Close"].dropna()
            except Exception:
                return pd.Series(dtype=float)

        nk    = _get("^N225")
        sp    = _get("^GSPC")
        vix   = _get("^VIX")
        fx    = _get("USDJPY=X")
        tnx   = _get("^TNX")
        ewj   = _get("EWJ")
        oil   = _get("CL=F")
        nkd   = _get("NKD=F")

        if len(nk) < 100:
            return {"ok": False, "reason": "データ不足"}

        # ── 特徴量（シグナル）を日次で計算 ──────────────
        dates = nk.index[50:]   # MA50が有効になる点から
        features = []
        labels   = []

        for i in range(50, len(nk) - 1):
            date = nk.index[i]
            feats = {}

            # SP500 前日リターン
            if len(sp) >= i+1:
                try:
                    sp_idx = sp.index.get_indexer([date], method="nearest")[0]
                    if sp_idx > 0:
                        feats["sp_ret1"] = float(
                            np.tanh((sp.iloc[sp_idx] / sp.iloc[sp_idx-1] - 1) * 100 / 1.5))
                    else:
                        feats["sp_ret1"] = 0.0
                except Exception:
                    feats["sp_ret1"] = 0.0
            else:
                feats["sp_ret1"] = 0.0

            # VIX 水準
            if len(vix) > 0:
                try:
                    v_idx = vix.index.get_indexer([date], method="nearest")[0]
                    feats["vix_level"] = float(np.tanh(-(vix.iloc[v_idx] - 20) / 8))
                    if v_idx > 0:
                        vix_ch = (vix.iloc[v_idx] / vix.iloc[v_idx-1] - 1) * 100
                        feats["vix_ch1"] = float(np.tanh(-vix_ch / 8))
                    else:
                        feats["vix_ch1"] = 0.0
                except Exception:
                    feats["vix_level"] = 0.0
                    feats["vix_ch1"]   = 0.0
            else:
                feats["vix_level"] = 0.0
                feats["vix_ch1"]   = 0.0

            # USD/JPY モメンタム
            if len(fx) > 0:
                try:
                    f_idx = fx.index.get_indexer([date], method="nearest")[0]
                    if f_idx > 0:
                        feats["fx_ret1"] = float(
                            np.tanh((fx.iloc[f_idx] / fx.iloc[f_idx-1] - 1) * 100 / 0.8))
                    else:
                        feats["fx_ret1"] = 0.0
                except Exception:
                    feats["fx_ret1"] = 0.0
            else:
                feats["fx_ret1"] = 0.0

            # 日経 RSI14
            nk_slice = nk.iloc[max(0, i-20):i+1]
            if len(nk_slice) >= 15:
                rsi = _calc_rsi(nk_slice, 14)
                feats["nk_rsi"] = float(np.tanh(-(rsi - 50) / 50 * 1.5))
            else:
                feats["nk_rsi"] = 0.0

            # 日経 5日モメンタム
            if i >= 5:
                mom5 = (nk.iloc[i] / nk.iloc[i-5] - 1) * 100
                feats["nk_mom5"] = float(np.tanh(mom5 / 3))
            else:
                feats["nk_mom5"] = 0.0

            # MA50乖離
            if i >= 50:
                ma50  = float(nk.iloc[i-50:i].mean())
                diff  = (float(nk.iloc[i]) / ma50 - 1) * 100
                feats["nk_ma50"] = float(np.tanh(diff / 5))
            else:
                feats["nk_ma50"] = 0.0

            # 金利変化
            if len(tnx) > 0:
                try:
                    t_idx = tnx.index.get_indexer([date], method="nearest")[0]
                    if t_idx > 0:
                        feats["tnx_ch1"] = float(
                            np.tanh(-(tnx.iloc[t_idx] - tnx.iloc[t_idx-1]) / 0.1))
                    else:
                        feats["tnx_ch1"] = 0.0
                except Exception:
                    feats["tnx_ch1"] = 0.0
            else:
                feats["tnx_ch1"] = 0.0

            # EWJ リターン
            if len(ewj) > 0:
                try:
                    e_idx = ewj.index.get_indexer([date], method="nearest")[0]
                    if e_idx > 0:
                        feats["ewj_ret1"] = float(
                            np.tanh((ewj.iloc[e_idx] / ewj.iloc[e_idx-1] - 1) * 100 / 1.5))
                    else:
                        feats["ewj_ret1"] = 0.0
                except Exception:
                    feats["ewj_ret1"] = 0.0
            else:
                feats["ewj_ret1"] = 0.0

            # 翌日の実際の騰落（ラベル）
            actual_up = int(nk.iloc[i+1] > nk.iloc[i])

            features.append(feats)
            labels.append(actual_up)

        if len(features) < 50:
            return {"ok": False, "reason": "特徴量データ不足"}

        feat_names = list(features[0].keys())
        X = np.array([[f.get(k, 0.0) for k in feat_names] for f in features])
        y = np.array(labels)

        # ── 単体シグナルのヒット率 ──────────────────────
        signal_stats = {}
        for j, fname in enumerate(feat_names):
            col = X[:, j]
            # シグナル > 0 を「上昇予測」として扱う
            pred_up   = (col > 0).astype(int)
            pred_down = (col < 0).astype(int)
            n_up      = pred_up.sum()
            n_down    = pred_down.sum()

            hit_up   = float((pred_up   * y).sum()   / n_up)   if n_up   > 0 else 0.5
            hit_down = float((pred_down * (1-y)).sum() / n_down) if n_down > 0 else 0.5
            hit_total = float((
                (pred_up * y).sum() + (pred_down * (1-y)).sum()
            ) / max(n_up + n_down, 1))

            signal_stats[fname] = {
                "hit_rate":   round(hit_total * 100, 1),
                "hit_up":     round(hit_up    * 100, 1),
                "hit_down":   round(hit_down  * 100, 1),
                "n_signals":  int(n_up + n_down),
            }

        # ── 合成スコアのヒット率（等重み） ───────────────
        composite_scores = X.mean(axis=1)
        pred_composite   = (composite_scores > 0).astype(int)
        overall_hit = float(accuracy_score(y, pred_composite)) if SKLEARN_AVAILABLE else                       float((pred_composite == y).mean())

        # ── 仮想P&L（シグナル>0で翌日保有）──────────────
        nk_arr = nk.values
        returns = []
        for i in range(50, min(len(nk_arr)-1, 50+len(composite_scores))):
            idx = i - 50
            if idx >= len(composite_scores):
                break
            daily_ret = (nk_arr[i+1] / nk_arr[i] - 1)
            if composite_scores[idx] > 0:
                returns.append(daily_ret)
            else:
                returns.append(-daily_ret)  # ショート

        returns = np.array(returns)

        # シャープレシオ（年率）
        if returns.std() > 0:
            sharpe = float(returns.mean() / returns.std() * np.sqrt(252))
        else:
            sharpe = 0.0

        # 最大ドローダウン
        cumret = np.cumprod(1 + returns)
        running_max = np.maximum.accumulate(cumret)
        drawdown    = (cumret - running_max) / running_max
        max_dd      = float(drawdown.min() * 100)

        # 総リターン
        total_ret = float((cumret[-1] - 1) * 100) if len(cumret) > 0 else 0.0

        # ── 日別ヒット率推移（チャート用）───────────────
        window = 60
        rolling_hits = []
        rolling_dates = []
        for i in range(window, len(pred_composite)):
            window_hit = float((pred_composite[i-window:i] == y[i-window:i]).mean())
            rolling_hits.append(window_hit * 100)
            if i < len(dates):
                rolling_dates.append(dates[i])

        hit_df = pd.DataFrame({
            "date":     pd.to_datetime(rolling_dates) if rolling_dates else [],
            "hit_rate": rolling_hits,
        })

        return {
            "ok":            True,
            "overall_hit":   round(overall_hit * 100, 1),
            "sharpe":        round(sharpe, 3),
            "max_dd":        round(max_dd, 1),
            "total_ret":     round(total_ret, 1),
            "n_samples":     len(y),
            "up_rate":       round(float(y.mean()) * 100, 1),
            "signal_stats":  signal_stats,
            "feat_names":    feat_names,
            "hit_df":        hit_df,
            "X":             X,
            "y":             y,
            "dates":         dates[:len(X)],
            "composite_scores": composite_scores,
        }

    except Exception as e:
        logger.error(f"run_backtest error: {e}", exc_info=True)
        return {"ok": False, "reason": str(e)[:200]}


# =====================================================
# ② ML重み最適化エンジン
# =====================================================
def optimize_weights_ml(bt_result: Dict) -> Dict[str, Any]:
    """
    バックテスト結果を使って機械学習で重みを最適化する。
    Walk-forward検証で過学習を防ぐ。
    """
    if not bt_result.get("ok") or not SKLEARN_AVAILABLE:
        return {"ok": False, "reason": "データ不足またはscikit-learn未インストール"}

    try:
        X = bt_result["X"]
        y = bt_result["y"]
        feat_names = bt_result["feat_names"]

        if len(X) < 100:
            return {"ok": False, "reason": "サンプル不足（100件以上必要）"}

        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)

        # ── Walk-forward検証（時系列対応）───────────────
        tscv = TimeSeriesSplit(n_splits=5)
        fold_scores = []
        fold_details = []

        for fold, (train_idx, test_idx) in enumerate(tscv.split(X_scaled)):
            X_train, X_test = X_scaled[train_idx], X_scaled[test_idx]
            y_train, y_test = y[train_idx],        y[test_idx]

            # ロジスティック回帰
            lr = LogisticRegression(C=0.1, max_iter=500, random_state=42)
            lr.fit(X_train, y_train)
            lr_score = accuracy_score(y_test, lr.predict(X_test))

            fold_scores.append(lr_score)
            fold_details.append({
                "fold":    fold + 1,
                "train_n": len(train_idx),
                "test_n":  len(test_idx),
                "accuracy": round(lr_score * 100, 1),
            })

        # ── 全データで最終モデル学習 ─────────────────────
        final_lr = LogisticRegression(C=0.1, max_iter=500, random_state=42)
        final_lr.fit(X_scaled, y)
        coefs = final_lr.coef_[0]

        # XGBoostが使える場合は追加
        xgb_coefs = None
        if XGB_AVAILABLE:
            try:
                xgb_model = xgb.XGBClassifier(
                    n_estimators=100, max_depth=3,
                    learning_rate=0.05, random_state=42,
                    eval_metric="logloss", verbosity=0
                )
                xgb_model.fit(X_scaled, y)
                xgb_coefs = xgb_model.feature_importances_
            except Exception:
                pass

        # 特徴量重要度DataFrame
        importance_data = []
        for i, fname in enumerate(feat_names):
            row = {
                "シグナル":    fname,
                "LR Coef":    round(float(coefs[i]), 4),
                "Importance": round(abs(float(coefs[i])), 4),
                "Direction":  "Bullish" if coefs[i] > 0 else "Bearish",
            }
            if xgb_coefs is not None:
                row["XGB Importance"] = round(float(xgb_coefs[i]), 4)
            importance_data.append(row)

        importance_df = pd.DataFrame(importance_data).sort_values(
            "Importance", ascending=False)

        # 最適化後のスコア予測（最新データ）
        latest_x = X_scaled[-1:] if len(X_scaled) > 0 else None
        optimized_prob = None
        if latest_x is not None:
            optimized_prob = float(final_lr.predict_proba(latest_x)[0][1] * 100)

        cv_mean  = float(np.mean(fold_scores) * 100)
        cv_std   = float(np.std(fold_scores)  * 100)

        return {
            "ok":              True,
            "cv_mean":         round(cv_mean, 1),
            "cv_std":          round(cv_std,  1),
            "importance_df":   importance_df,
            "fold_details":    fold_details,
            "optimized_prob":  optimized_prob,
            "model":           final_lr,
            "scaler":          scaler,
            "feat_names":      feat_names,
            "has_xgb":         XGB_AVAILABLE and xgb_coefs is not None,
        }

    except Exception as e:
        logger.error(f"optimize_weights_ml error: {e}", exc_info=True)
        return {"ok": False, "reason": str(e)[:200]}


# =====================================================
# ③ アンサンブルモデル
# =====================================================
@st.cache_data(ttl=TTL_INTRADAY, show_spinner=False)
def compute_ensemble_prediction() -> Dict[str, Any]:
    """
    3つのモデルを独立して計算し加重平均でアンサンブル予測を行う。

    短期モデル（weight=0.4）: モメンタム・VIX・先物重視
    中期モデル（weight=0.35）: マクロ・テクニカル・金利重視
    トレンドモデル（weight=0.25）: MA・MACD・EWJ重視
    """
    try:
        end   = datetime.now(timezone.utc)
        start = end - timedelta(days=420)

        def _h(sym):
            try:
                df = yf.Ticker(sym).history(
                    start=start, end=end, interval="1d", auto_adjust=False)
                if df is None or df.empty:
                    return pd.Series(dtype=float)
                if df.index.tz is None:
                    df.index = df.index.tz_localize("UTC")
                return df.tz_convert(JST)["Close"].dropna()
            except Exception:
                return pd.Series(dtype=float)

        nk  = _h("^N225")
        sp  = _h("^GSPC")
        vix = _h("^VIX")
        fx  = _h("USDJPY=X")
        tnx = _h("^TNX")
        ewj = _h("EWJ")
        nkd = _h("NKD=F")

        if len(nk) < 30:
            return {"ok": False, "reason": "データ不足"}

        # ─────────────────────────────────────────────
        # 短期モデル: 1日予測 / モメンタム・VIX重視
        # ─────────────────────────────────────────────
        short_signals = {}

        # SP500前日騰落（最重要）
        if len(sp) >= 2:
            sp_r1 = (float(sp.iloc[-1]) / float(sp.iloc[-2]) - 1) * 100
            short_signals["sp_ret1"]   = (np.tanh(sp_r1 / 1.5), 3.0)

        # VIX 水準・変化
        if len(vix) >= 4:
            vix_v  = float(vix.iloc[-1])
            vix_c1 = (vix.iloc[-1] / vix.iloc[-2] - 1) * 100
            vix_c3 = (vix.iloc[-1] / vix.iloc[-4] - 1) * 100
            short_signals["vix_level"] = (np.tanh(-(vix_v - 20) / 8),  2.5)
            short_signals["vix_ch1"]   = (np.tanh(-vix_c1 / 8),        2.0)
            short_signals["vix_ch3"]   = (np.tanh(-vix_c3 / 12),       1.0)

        # 先物乖離（CMEギャップ）
        if len(nkd) >= 2 and len(nk) >= 2:
            nkd_r = (float(nkd.iloc[-1]) / float(nkd.iloc[-2]) - 1) * 100
            nk_r  = (float(nk.iloc[-1])  / float(nk.iloc[-2])  - 1) * 100
            short_signals["futures_gap"] = (np.tanh((nkd_r - nk_r) / 1.5), 2.5)

        # USD/JPY 前日変化
        if len(fx) >= 2:
            fx_r1 = (float(fx.iloc[-1]) / float(fx.iloc[-2]) - 1) * 100
            short_signals["fx_ret1"] = (np.tanh(fx_r1 / 0.8), 2.0)

        # 短期モメンタム（日経3日）
        if len(nk) >= 4:
            nk_r3 = (float(nk.iloc[-1]) / float(nk.iloc[-4]) - 1) * 100
            short_signals["nk_mom3"] = (np.tanh(nk_r3 / 2), 1.5)

        short_score = _weighted_score(short_signals)

        # ─────────────────────────────────────────────
        # 中期モデル: 1週間予測 / マクロ・テクニカル重視
        # ─────────────────────────────────────────────
        mid_signals = {}

        # 金利変化（5日）
        if len(tnx) >= 6:
            tnx_ch5 = float(tnx.iloc[-1]) - float(tnx.iloc[-6])
            mid_signals["tnx_ch5"] = (np.tanh(-tnx_ch5 / 0.25), 2.0)

        # RSI14
        if len(nk) >= 20:
            rsi = _calc_rsi(nk, 14)
            mid_signals["rsi14"] = (np.tanh(-(rsi - 50) / 50 * 1.5), 1.5)

        # MACD
        if len(nk) >= 30:
            macd_v, macd_s = _calc_macd(nk)
            nk_price = float(nk.iloc[-1])
            mid_signals["macd"] = (np.tanh((macd_v - macd_s) /
                                   (nk_price * 0.005 or 1)), 1.5)

        # SP500 20日モメンタム
        if len(sp) >= 22:
            sp_r20 = (float(sp.iloc[-1]) / float(sp.iloc[-21]) - 1) * 100
            mid_signals["sp_mom20"] = (np.tanh(sp_r20 / 8), 2.0)

        # EWJ 5日
        if len(ewj) >= 6:
            ewj_r5 = (float(ewj.iloc[-1]) / float(ewj.iloc[-6]) - 1) * 100
            mid_signals["ewj_mom5"] = (np.tanh(ewj_r5 / 3), 1.5)

        # BB %B
        if len(nk) >= 22:
            bb = _calc_bb_pct(nk, 20)
            mid_signals["bb_pct"] = (np.tanh(-(bb - 0.5) * 2), 1.0)

        mid_score = _weighted_score(mid_signals)

        # ─────────────────────────────────────────────
        # トレンドモデル: MA・長期傾向重視
        # ─────────────────────────────────────────────
        trend_signals = {}

        # MA25・MA50・MA200 乖離
        for window, weight in [(25, 1.5), (50, 1.5), (200, 1.0)]:
            if len(nk) >= window:
                ma  = float(nk.rolling(window).mean().iloc[-1])
                diff = (float(nk.iloc[-1]) / ma - 1) * 100
                sig  = np.tanh(-diff / 10) if abs(diff) > 10 else np.tanh(diff / 5)
                trend_signals[f"ma{window}"] = (sig, weight)

        # USD/JPY 5日モメンタム
        if len(fx) >= 6:
            fx_r5 = (float(fx.iloc[-1]) / float(fx.iloc[-6]) - 1) * 100
            trend_signals["fx_mom5"] = (np.tanh(fx_r5 / 1.5), 1.5)

        # SP500 MA125乖離
        if len(sp) >= 130:
            sp_ma125 = float(sp.rolling(125).mean().iloc[-1])
            sp_diff  = (float(sp.iloc[-1]) / sp_ma125 - 1) * 100
            trend_signals["sp_ma125"] = (np.tanh(sp_diff * 5), 2.0)

        trend_score = _weighted_score(trend_signals)

        # ─────────────────────────────────────────────
        # アンサンブル合成
        # ─────────────────────────────────────────────
        w_short = 0.40
        w_mid   = 0.35
        w_trend = 0.25
        ensemble_score = (
            short_score * w_short +
            mid_score   * w_mid   +
            trend_score * w_trend
        )

        # 確率変換
        prob_short   = float(1 / (1 + np.exp(-short_score  * 2.8)) * 100)
        prob_mid     = float(1 / (1 + np.exp(-mid_score    * 1.6)) * 100)
        prob_trend   = float(1 / (1 + np.exp(-trend_score  * 1.2)) * 100)
        prob_ensemble = float(1 / (1 + np.exp(-ensemble_score * 2.5)) * 100)

        # 多数決
        votes_up   = sum([prob_short > 50, prob_mid > 50, prob_trend > 50])
        consensus  = "強気 📈" if votes_up >= 2 else "弱気 📉"
        confidence = "高" if votes_up == 3 or votes_up == 0 else "中"

        return {
            "ok":             True,
            "ensemble_score": round(ensemble_score, 4),
            "prob_ensemble":  round(prob_ensemble, 1),
            "models": {
                "短期（1日）": {
                    "score": round(short_score, 4),
                    "prob":  round(prob_short, 1),
                    "weight": w_short,
                    "n_signals": len(short_signals),
                    "desc": "モメンタム・VIX・先物乖離重視",
                    "color": "#1976d2",
                },
                "中期（1週）": {
                    "score": round(mid_score, 4),
                    "prob":  round(prob_mid, 1),
                    "weight": w_mid,
                    "n_signals": len(mid_signals),
                    "desc": "マクロ・テクニカル・EWF重視",
                    "color": "#2e7d32",
                },
                "トレンド": {
                    "score": round(trend_score, 4),
                    "prob":  round(prob_trend, 1),
                    "weight": w_trend,
                    "n_signals": len(trend_signals),
                    "desc": "MA乖離・長期傾向重視",
                    "color": "#6a1b9a",
                },
            },
            "votes_up":  votes_up,
            "consensus": consensus,
            "confidence": confidence,
            "updated_at": datetime.now(JST).strftime("%Y-%m-%d %H:%M JST"),
        }

    except Exception as e:
        logger.error(f"compute_ensemble_prediction error: {e}", exc_info=True)
        return {"ok": False, "reason": str(e)[:200]}


def _weighted_score(signals: Dict[str, tuple]) -> float:
    """シグナル辞書 {name: (value, weight)} から重み付きスコアを計算"""
    if not signals:
        return 0.0
    s = sum(float(np.clip(v, -1, 1)) * w for _, (v, w) in signals.items())
    total_w = sum(w for _, (_, w) in signals.items())
    return float(s / total_w) if total_w > 0 else 0.0


# =====================================================
# 描画: バックテスト結果
# =====================================================
def render_backtest_section():
    st.markdown("#### 📊 ① バックテスト結果")
    st.caption("過去データで各シグナルのヒット率・シャープレシオを検証します（約3年分）")

    with st.spinner("バックテスト実行中（初回30秒ほどかかります）..."):
        bt = run_backtest("^N225", lookback_years=3)

    if not bt.get("ok"):
        st.error(f"バックテスト失敗: {bt.get('reason')}")
        return

    # KPIカード
    k1, k2, k3, k4, k5 = st.columns(5)
    hit_color = "#1a7f37" if bt["overall_hit"] > 55 else ("#d1242f" if bt["overall_hit"] < 48 else "#888")
    k1.markdown(f'<div style="text-align:center;padding:10px;background:#f8f9fa;border-radius:8px;">'
                f'<div style="font-size:11px;color:#666;">総合ヒット率</div>'
                f'<div style="font-size:28px;font-weight:900;color:{hit_color};">{bt["overall_hit"]}%</div></div>',
                unsafe_allow_html=True)
    sharpe_color = "#1a7f37" if bt["sharpe"] > 0.5 else ("#d1242f" if bt["sharpe"] < 0 else "#888")
    k2.markdown(f'<div style="text-align:center;padding:10px;background:#f8f9fa;border-radius:8px;">'
                f'<div style="font-size:11px;color:#666;">シャープレシオ</div>'
                f'<div style="font-size:28px;font-weight:900;color:{sharpe_color};">{bt["sharpe"]:.2f}</div></div>',
                unsafe_allow_html=True)
    dd_color = "#d1242f" if bt["max_dd"] < -20 else ("#ff8800" if bt["max_dd"] < -10 else "#888")
    k3.markdown(f'<div style="text-align:center;padding:10px;background:#f8f9fa;border-radius:8px;">'
                f'<div style="font-size:11px;color:#666;">最大ドローダウン</div>'
                f'<div style="font-size:28px;font-weight:900;color:{dd_color};">{bt["max_dd"]:.1f}%</div></div>',
                unsafe_allow_html=True)
    ret_color = "#1a7f37" if bt["total_ret"] > 0 else "#d1242f"
    k4.markdown(f'<div style="text-align:center;padding:10px;background:#f8f9fa;border-radius:8px;">'
                f'<div style="font-size:11px;color:#666;">仮想総リターン</div>'
                f'<div style="font-size:28px;font-weight:900;color:{ret_color};">{bt["total_ret"]:+.1f}%</div></div>',
                unsafe_allow_html=True)
    k5.markdown(f'<div style="text-align:center;padding:10px;background:#f8f9fa;border-radius:8px;">'
                f'<div style="font-size:11px;color:#666;">サンプル数</div>'
                f'<div style="font-size:28px;font-weight:900;color:#333;">{bt["n_samples"]}日</div></div>',
                unsafe_allow_html=True)

    st.caption(f"※ 仮想P&L: スコア>0で翌日ロング、<0でショートした場合の累積リターン（手数料なし）")

    # ローリングヒット率チャート
    hit_df = bt.get("hit_df", pd.DataFrame())
    if not hit_df.empty:
        st.markdown("**📈 60日ローリングヒット率推移**")
        fig, ax = plt.subplots(figsize=(12, 3.5))
        ax.plot(hit_df["date"], hit_df["hit_rate"],
                color="#1976d2", linewidth=1.8)
        ax.axhline(50, color="gray", linestyle="--", alpha=0.6, linewidth=1)
        ax.axhline(bt["overall_hit"], color="#e91e63",
                   linestyle=":", alpha=0.8, linewidth=1.5,
                   label=f"平均 {bt['overall_hit']}%")
        ax.fill_between(hit_df["date"], hit_df["hit_rate"], 50,
                        where=(hit_df["hit_rate"] >= 50),
                        alpha=0.15, color="#1a7f37")
        ax.fill_between(hit_df["date"], hit_df["hit_rate"], 50,
                        where=(hit_df["hit_rate"] < 50),
                        alpha=0.15, color="#d1242f")
        ax.set_ylabel("Hit Rate (%)", fontsize=10)
        ax.set_ylim(30, 75)
        ax.legend(fontsize=9)
        ax.grid(True, alpha=0.25)
        import matplotlib.dates as mdates
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y/%m"))
        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
        plt.xticks(rotation=45)
        plt.tight_layout()
        st.pyplot(fig, clear_figure=True)

    # シグナル別ヒット率
    with st.expander("🔍 シグナル別ヒット率詳細", expanded=False):
        stats = bt.get("signal_stats", {})
        if stats:
            rows = []
            label_map = {
                "sp_ret1":   "S&P500 1D",
                "vix_level": "VIX Level",
                "vix_ch1":   "VIX 1D Chg",
                "fx_ret1":   "USD/JPY 1D",
                "nk_rsi":    "Nikkei RSI14",
                "nk_mom5":   "Nikkei 5D Mom",
                "nk_ma50":   "Nikkei MA50 Dev",
                "tnx_ch1":   "US10Y 1D Chg",
                "ewj_ret1":  "EWJ 1D",
            }
            for k, v in sorted(stats.items(),
                                key=lambda x: x[1]["hit_rate"], reverse=True):
                rows.append({
                    "Signal": label_map.get(k, k),
                    "Hit Rate": f"{v['hit_rate']}%",
                    "Up HIT": f"{v['hit_up']}%",
                    "Down HIT": f"{v['hit_down']}%",
                    "Count": v["n_signals"],
                })
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


# =====================================================
# 描画: ML最適化結果
# =====================================================
def render_ml_optimization_section():
    st.markdown("#### 🤖 ② ML Weight Optimization")
    st.caption("Logistic regression with Walk-forward validation to prevent overfitting")

    if not SKLEARN_AVAILABLE:
        st.warning("⚠️ scikit-learnが未インストールです。requirements.txtに `scikit-learn` を追加してください。")
        return

    with st.spinner("ML最適化実行中..."):
        bt = run_backtest("^N225", lookback_years=2)
        if not bt.get("ok"):
            st.error("バックテストデータが必要です")
            return
        ml = optimize_weights_ml(bt)

    if not ml.get("ok"):
        st.error(f"ML最適化失敗: {ml.get('reason')}")
        return

    # CV精度
    cv_color = "#1a7f37" if ml["cv_mean"] > 54 else ("#d1242f" if ml["cv_mean"] < 50 else "#888")
    col1, col2, col3 = st.columns(3)
    col1.markdown(
        f'<div style="text-align:center;padding:12px;background:#f8f9fa;border-radius:8px;">'
        f'<div style="font-size:11px;color:#666;">CV Accuracy</div>'
        f'<div style="font-size:26px;font-weight:900;color:{cv_color};">{ml["cv_mean"]}%</div>'
        f'<div style="font-size:11px;color:#999;">±{ml["cv_std"]}%</div></div>',
        unsafe_allow_html=True)
    col2.markdown(
        f'<div style="text-align:center;padding:12px;background:#f8f9fa;border-radius:8px;">'
        f'<div style="font-size:11px;color:#666;">ML Optimized Pred</div>'
        f'<div style="font-size:26px;font-weight:900;color:#1976d2;">{ml["optimized_prob"]:.1f}%</div>'
        f'<div style="font-size:11px;color:#999;">Prob. Up</div></div>',
        unsafe_allow_html=True)
    col3.markdown(
        f'<div style="text-align:center;padding:12px;background:#f8f9fa;border-radius:8px;">'
        f'<div style="font-size:11px;color:#666;">Model</div>'
        f'<div style="font-size:16px;font-weight:700;color:#333;">Logistic{"+ XGB" if ml["has_xgb"] else ""}</div>'
        f'<div style="font-size:11px;color:#999;">Walk-forward 5-fold</div></div>',
        unsafe_allow_html=True)

    # 特徴量重要度チャート
    imp_df = ml.get("importance_df")
    if imp_df is not None and not imp_df.empty:
        st.markdown("**📊 Signal Importance (LR Coefficient)**")
        label_map = {
            "sp_ret1": "S&P500 1D", "vix_level": "VIX Level",
            "vix_ch1": "VIX 1D Chg", "fx_ret1": "USD/JPY 1D",
            "nk_rsi":  "Nikkei RSI14", "nk_mom5": "Nikkei 5D Mom",
            "nk_ma50": "Nikkei MA50 Dev", "tnx_ch1": "US10Y 1D Chg",
            "ewj_ret1": "EWJ 1D",
        }
        imp_df["Label"] = imp_df["シグナル"].map(
            lambda x: label_map.get(x, x))
        imp_df_sorted = imp_df.sort_values("LR Coef")

        fig, ax = plt.subplots(figsize=(10, max(3, len(imp_df_sorted)*0.45)))
        colors = ["#1a7f37" if v > 0 else "#d1242f"
                  for v in imp_df_sorted["LR Coef"]]
        ax.barh(imp_df_sorted["Label"], imp_df_sorted["LR Coef"],
                color=colors, height=0.6)
        ax.axvline(0, color="black", linewidth=0.8)
        ax.set_xlabel("LR Coefficient (+ = Bullish, - = Bearish)", fontsize=9)
        ax.grid(True, axis="x", alpha=0.3)
        plt.tight_layout()
        st.pyplot(fig, clear_figure=True)

    # Walk-forward詳細
    with st.expander("📋 Walk-forward Detail", expanded=False):
        folds = ml.get("fold_details", [])
        if folds:
            st.dataframe(pd.DataFrame(folds), width="stretch", hide_index=True)
        st.caption(
            "Walk-forward検証: 時系列を5分割し、過去データで学習→未来データで検証を繰り返す。"
            "CV平均>53%であれば統計的に有意なシグナルが存在すると考えられます。"
        )


# =====================================================
# 描画: アンサンブル結果
# =====================================================
def render_ensemble_section():
    st.markdown("#### 🎯 ③ アンサンブル予測")
    st.caption("3つの独立モデルの多数決で最終予測を決定します")

    with st.spinner("3モデル計算中..."):
        ens = compute_ensemble_prediction()

    if not ens.get("ok"):
        st.error(f"アンサンブル計算失敗: {ens.get('reason')}")
        return

    # 総合判定
    votes = ens["votes_up"]
    consensus = ens["consensus"]
    confidence = ens["confidence"]
    prob_ens = ens["prob_ensemble"]
    prob_color = "#1a7f37" if prob_ens > 55 else ("#d1242f" if prob_ens < 45 else "#888")

    st.markdown(
        f'<div style="background:linear-gradient(135deg,#f8f9fa,#e8f5e9 70%);'
        f'border:2px solid #{"1a7f37" if votes >= 2 else "d1242f"};'
        f'border-radius:12px;padding:16px 20px;margin-bottom:16px;">'
        f'<div style="display:flex;align-items:center;gap:20px;flex-wrap:wrap;">'
        f'<div style="text-align:center;">'
        f'<div style="font-size:11px;color:#666;">アンサンブルProb. Up</div>'
        f'<div style="font-size:36px;font-weight:900;color:{prob_color};">{prob_ens:.1f}%</div>'
        f'</div>'
        f'<div style="text-align:center;">'
        f'<div style="font-size:11px;color:#666;">総合判定</div>'
        f'<div style="font-size:22px;font-weight:700;">{consensus}</div>'
        f'</div>'
        f'<div style="text-align:center;">'
        f'<div style="font-size:11px;color:#666;">多数決</div>'
        f'<div style="font-size:22px;font-weight:700;">{votes}/3モデル 強気</div>'
        f'</div>'
        f'<div style="text-align:center;">'
        f'<div style="font-size:11px;color:#666;">コンセンサス</div>'
        f'<div style="font-size:22px;font-weight:700;">{"🔥 高" if confidence == "高" else "📊 中"}</div>'
        f'</div>'
        f'</div></div>',
        unsafe_allow_html=True
    )

    # 3モデル比較
    models = ens["models"]
    cols = st.columns(3)
    for col, (model_name, model_data) in zip(cols, models.items()):
        prob = model_data["prob"]
        score = model_data["score"]
        color = model_data["color"]
        prob_c = "#1a7f37" if prob > 55 else ("#d1242f" if prob < 45 else "#888")
        with col:
            st.markdown(
                f'<div style="border:2px solid {color};border-radius:10px;'
                f'padding:12px;text-align:center;">'
                f'<div style="font-size:13px;font-weight:700;color:{color};">{model_name}</div>'
                f'<div style="font-size:10px;color:#888;margin:3px 0;">{model_data["desc"]}</div>'
                f'<div style="font-size:28px;font-weight:900;color:{prob_c};">{prob:.1f}%</div>'
                f'<div style="font-size:11px;color:#999;">Prob. Up</div>'
                f'<div style="font-size:11px;color:#555;margin-top:4px;">'
                f'スコア: {score:+.3f} / 重み: {model_data["weight"]:.0%}</div>'
                f'</div>',
                unsafe_allow_html=True
            )

    st.caption(f"更新: {ens['updated_at']}")

    # ゲージ表示
    st.markdown("**アンサンブルゲージ**")
    st.image(render_prediction_gauge(prob_ens), width=300)


def render_quant_analysis():
    """
    クオンツ分析セクション（バックテスト + ML + アンサンブル）
    ボタンを押したときだけ計算する遅延読み込み方式
    """
    st.markdown("---")
    st.markdown(
        """<div style="background:linear-gradient(135deg,#e8eaf6,#fce4ec);
        border-left:4px solid #9c27b0;border-radius:6px;padding:10px 16px;
        font-size:13px;color:#333;margin-bottom:12px;">
        <strong>🔬 クオンツ分析</strong>: バックテスト・ML最適化・アンサンブルで予測精度を検証します
        </div>""",
        unsafe_allow_html=True,
    )

    # ボタンを押したときだけ計算（遅延読み込み）
    if "show_quant_jp" not in st.session_state:
        st.session_state["show_quant_jp"] = False

    if not st.session_state["show_quant_jp"]:
        if st.button("🔬 クオンツ分析を実行（日本株）",
                     key="run_quant_jp", type="secondary",
                     width="stretch"):
            st.session_state["show_quant_jp"] = True
            st.rerun()
        st.caption("※ バックテスト・ML最適化・アンサンブルを実行します（初回30〜60秒）")
        return

    tab_bt, tab_ml, tab_ens = st.tabs(
        ["① バックテスト", "② ML最適化", "③ アンサンブル"]
    )
    with tab_bt:
        render_backtest_section()
    with tab_ml:
        render_ml_optimization_section()
    with tab_ens:
        render_ensemble_section()


GLOBAL_RESPONSIVE_CSS = """
<style>
@media screen and (max-width: 1100px) {
    [data-testid="stHorizontalBlock"] {
        display: grid !important;
        grid-template-columns: repeat(3, 1fr) !important;
        gap: 12px !important;
    }
    [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
        width: 100% !important;
        min-width: 0 !important;
        flex: none !important;
    }
}
@media screen and (max-width: 820px) {
    [data-testid="stHorizontalBlock"] {
        display: grid !important;
        grid-template-columns: repeat(2, 1fr) !important;
        gap: 10px !important;
    }
    [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
        width: 100% !important;
        min-width: 0 !important;
        flex: none !important;
    }
    .wk-name { font-size: 14px !important; }
    .wk-sym  { font-size: 10px !important; }
    .wk-pct  { font-size: 19px !important; }
    .wk-now  { font-size: 12px !important; }
    .wk-foot { font-size: 10px !important; }
    .wk-link { font-size: 10px !important; padding: 5px 8px !important; }
    .wk-card { padding: 10px !important; }
}
@media screen and (max-width: 480px) {
    [data-testid="stHorizontalBlock"] {
        grid-template-columns: 1fr !important;
    }
    .wk-name { font-size: 15px !important; }
    .wk-pct  { font-size: 20px !important; }
}
</style>
"""

def inject_global_css():
    if "global_css_injected" not in st.session_state:
        st.markdown(GLOBAL_RESPONSIVE_CSS, unsafe_allow_html=True)
        st.session_state["global_css_injected"] = True

def card_css(bg: str) -> str:
    return f"""
    <style>
    .wk-card {{ border: 1px solid rgba(0,0,0,0.08); border-radius: 10px; padding: 12px 14px; background: {bg}; box-shadow: 0 2px 4px rgba(0,0,0,0.06); }}
    .wk-card:hover {{ box-shadow: 0 4px 8px rgba(0,0,0,0.12); }}
    .wk-head {{ display: flex; align-items: baseline; justify-content: space-between; gap: 6px; margin-bottom: 8px; flex-wrap: wrap; }}
    .wk-name {{ font-weight: 900; font-size: 17px; line-height: 1.2; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 65%; }}
    .wk-sym  {{ font-weight: 700; font-size: 11px; color: rgba(0,0,0,0.55); margin-left: 4px; }}
    .wk-pct  {{ font-weight: 900; font-size: 24px; line-height: 1; white-space: nowrap; }}
    .wk-now  {{ font-size: 17px; font-weight: 700; color: rgba(0,0,0,0.82); margin-bottom: 4px; word-break: break-all; }}
    .wk-foot {{ font-size: 11px; color: rgba(0,0,0,0.5); margin-top: 4px; word-break: break-word; }}
    .wk-link {{ display: inline-block; margin-top: 6px; padding: 5px 10px; background: rgba(0,0,0,0.05); border-radius: 4px; text-decoration: none; font-size: 11px; color: #0066cc; min-height: 32px; line-height: 22px; }}
    .wk-link:hover {{ background: rgba(0,0,0,0.1); }}
    </style>
    """

def get_responsive_cols() -> int:
    return st.session_state.get("detected_cols", 4)

# ===========================
# マーケット行レンダリング
# ===========================
@st.cache_data(ttl=300, show_spinner=False)
def _fetch_usdjpy_rate() -> Optional[float]:
    """USD/JPY レートを取得（ADR円換算用）"""
    try:
        t = yf.Ticker("USDJPY=X")
        hist = t.history(period="2d", interval="1d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception:
        pass
    return None


# ADRシンボルセット（円換算を表示する対象）
ADR_SYMBOLS = {
    "FJIKY", "MHVIY", "IHICY", "TOYOY", "SONY", "NTDOY",
    "DNZOY", "SMFGY", "MUFGY", "DNNGY", "KYOCY", "PCRFY",
}


def render_market_row(items: List[Dict], cols: int = 4):
    columns = st.columns(cols)

    def fetch_card_data(item):
        data = compute_card(item["symbol"], item.get("rt_symbol"), item.get("provider", "yahoo"))
        if not data.get("ok") and item.get("fallback_symbol"):
            fb = item["fallback_symbol"]
            fb_data = compute_card(fb, None, "yahoo")
            if fb_data.get("ok"):
                fb_data["used_fallback"] = fb
                return item, fb_data
        return item, data

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(fetch_card_data, it) for it in items]
        results = [f.result() for f in as_completed(futures)]

    item_to_data = {it["symbol"]: (it, data) for it, data in results}

    for i, it in enumerate(items):
        col = columns[i % cols]
        _, data = item_to_data[it["symbol"]]
        note = it.get("note", "")
        used_fallback = data.get("used_fallback", "")

        with col:
            if not data.get("ok"):
                st.markdown(card_css(BG_NEUTRAL), unsafe_allow_html=True)
                reason = sanitize_html(data.get("reason", "取得失敗"))
                st.markdown(
                    f"""<div class="wk-card">
                      <div class="wk-head">
                        <div class="wk-name">{sanitize_html(it['name'])}<span class="wk-sym">{sanitize_html(it['symbol'])}</span></div>
                        <div class="wk-pct" style="color:#999;">N/A</div>
                      </div>
                      <div class="wk-now" style="color:#d1242f;">⚠️ {reason}</div>
                    </div>""",
                    unsafe_allow_html=True,
                )
                if note:
                    st.caption(f"ℹ️ {note}")
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
            display_symbol = used_fallback or it["symbol"]
            market = "US" if it["flag"] == "US" else "JP"
            yahoo_url = get_yahoo_chart_url(display_symbol, market=market)
            sym_label = (
                f"{sanitize_html(it['symbol'])} <small style='color:#aaa;'>→{sanitize_html(used_fallback)}</small>"
                if used_fallback else sanitize_html(it['symbol'])
            )
            st.markdown(card_css(bg), unsafe_allow_html=True)
            st.markdown(
                f"""<div class="wk-card">
                  <div class="wk-head">
                    <div class="wk-name">{sanitize_html(it['name'])}<span class="wk-sym">{sym_label}</span></div>
                    <div class="wk-pct" style="color:{color};">{pct:+.2f}%</div>
                  </div>
                  <div class="wk-now">Now: {now:,.2f} &nbsp;&nbsp; Chg: {chg:+,.2f}</div>
                  <div class="wk-foot">Date: {date_label} / Last: {last_ts} / {mode}</div>
                  <a href="{yahoo_url}" target="_blank" class="wk-link">📈 Yahooで開く</a>
                </div>""",
                unsafe_allow_html=True,
            )
            if note:
                st.caption(f"ℹ️ {note}")
            png_bytes = make_sparkline(data["series"], data["base"], data["mode"], up=up)
            if isinstance(png_bytes, bytes):
                st.image(png_bytes, width="stretch")
            else:
                st.pyplot(png_bytes, clear_figure=True)

# ===========================
# RSSニュース表示
# ===========================
def render_rss_news(translate_mode: bool = True):
    st.subheader("📰 経済ニュース")
    if not RSS_FEEDS:
        st.info("RSSフィードが設定されていません")
        return

    # カテゴリ定義
    CATEGORIES = {
        "🇯🇵 国内": [
            "Yahoo!ニュース（経済）", "NHKニュース（経済）",
            "みんかぶ", "東証TDnet",
        ],
        "🌐 海外": [
            "AP News", "BBC World", "NPR News",
            "ブルームバーグ", "Reuters Business", "MarketWatch", "CNBC",
        ],
        "🔦 光通信・AI インフラ": [
            "光通信(COHR)", "光通信(LITE)", "光通信(GLW)", "光通信(AAOI)",
        ],
        "✅ ファクトチェック": [
            "FactCheck.org", "Snopes",
        ],
        "🏛️ 議員トレード": [
            "議員トレード(QuiverQuant)",
        ],
    }

    cat_names = list(CATEGORIES.keys())

    # アクティブカテゴリをsession_stateで管理
    if "rss_active_cat" not in st.session_state:
        st.session_state["rss_active_cat"] = cat_names[0]

    # カテゴリ選択ボタン（タブ風）
    btn_cols = st.columns(len(cat_names))
    for col, cat_name in zip(btn_cols, cat_names):
        with col:
            is_active = st.session_state["rss_active_cat"] == cat_name
            if st.button(
                cat_name,
                key=f"rss_cat_btn_{cat_name}",
                width="stretch",
                type="primary" if is_active else "secondary",
            ):
                st.session_state["rss_active_cat"] = cat_name
                # アクティブフィードもリセット
                st.session_state.pop("rss_active_feed", None)
                st.rerun()

    active_cat   = st.session_state["rss_active_cat"]
    feed_names   = CATEGORIES.get(active_cat, [])
    feeds_in_cat = {
        name: RSS_FEEDS[name]
        for name in feed_names
        if name in RSS_FEEDS
    }

    if not feeds_in_cat:
        st.info("フィードがありません")
        return

    feed_name_list = list(feeds_in_cat.keys())

    # アクティブフィードをsession_stateで管理
    if "rss_active_feed" not in st.session_state or \
       st.session_state.get("rss_active_feed") not in feed_name_list:
        st.session_state["rss_active_feed"] = feed_name_list[0]

    # フィード選択ボタン
    st.markdown('<div style="margin-top:6px;">', unsafe_allow_html=True)
    feed_btn_cols = st.columns(len(feed_name_list))
    for col, fname in zip(feed_btn_cols, feed_name_list):
        with col:
            is_active_feed = st.session_state["rss_active_feed"] == fname
            if st.button(
                fname,
                key=f"rss_feed_btn_{fname}",
                width="stretch",
                type="primary" if is_active_feed else "secondary",
            ):
                st.session_state["rss_active_feed"] = fname
                st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

    # アクティブフィードのみ取得・表示
    active_feed  = st.session_state["rss_active_feed"]
    feed_config  = feeds_in_cat[active_feed]

    col_f1, col_f2 = st.columns([4, 1])
    with col_f2:
        if st.button("🔄 更新", key=f"rss_refresh_{active_feed}",
                     width="stretch"):
            fetch_rss_feed.clear()
            st.rerun()

    with st.spinner(f"{active_feed} を読み込み中..."):
        articles = fetch_rss_feed(
            feed_config["url"],
            max_items=feed_config.get("max_items", 10),
            translate=(feed_config.get("translate", False) and translate_mode),
        )

    if not articles:
        st.warning(f"⚠️ {active_feed} のフィードを取得できませんでした")
        return

    st.caption(f"📊 {len(articles)}件 | キャッシュ: {feed_config.get('max_items',10)}件上限")
    for i, article in enumerate(articles, 1):
        if article["link"]:
            st.markdown(
                f"**{i}. [{sanitize_html(article['title'])}]({article['link']})**"
            )
        else:
            st.markdown(f"**{i}. {sanitize_html(article['title'])}**")
        if article["summary"]:
            st.caption(sanitize_html(article["summary"]))
        if article["published"]:
            st.caption(f"📅 {article['published']}")
        st.divider()

# ===========================
# メイン処理
# ===================================================
# ★ 日米業種リードラグシグナル（論文手法 PCA_SUB 準拠）
# 部分空間正則化付きPCAによる翌日日本株予測
# 参考: 中川ら (2026) SIG-FIN-036-13
# ===================================================

US_SECTOR_ETFS = {
    "XLB":  "Materials",
    "XLE":  "Energy",
    "XLF":  "Financials",
    "XLI":  "Industrials",
    "XLK":  "Info Tech",
    "XLP":  "Cons Staples",
    "XLRE": "Real Estate",
    "XLU":  "Utilities",
    "XLV":  "Health Care",
    "XLY":  "Cons Discret",
    "XLC":  "Comm Services",
}

JP_SECTOR_ETFS = {
    "1617.T": "Foods",
    "1618.T": "Energy Res.",
    "1619.T": "Construction",
    "1620.T": "Materials/Chem",
    "1621.T": "Pharma",
    "1622.T": "Auto/Transport",
    "1623.T": "Steel/NonFerr",
    "1624.T": "Machinery",
    "1625.T": "Electric/Prec",
    "1626.T": "IT/Services",
    "1627.T": "Electric/Gas",
    "1628.T": "Transport/Log",
    "1629.T": "Trading Co.",
    "1630.T": "Retail",
    "1631.T": "Banks",
    "1632.T": "Finance(ex-Bk)",
    "1633.T": "Real Estate",
}

@st.cache_data(ttl=TTL_DAILY, show_spinner=False)
def fetch_leadlag_data(window: int = 120):
    import numpy as np
    all_tickers = list(US_SECTOR_ETFS.keys()) + list(JP_SECTOR_ETFS.keys())
    try:
        raw = yf.download(
            all_tickers,
            period=f"{window + 30}d",
            interval="1d",
            auto_adjust=True,
            progress=False,
        )["Close"]
        ret = raw.pct_change().dropna(how="all")
        ret = ret.dropna(axis=1, thresh=int(window * 0.8))
        ret = ret.fillna(0)
        if len(ret) > window:
            ret = ret.iloc[-window:]
        return ret
    except Exception as e:
        logger.warning(f"fetch_leadlag_data error: {e}")
        return None


def compute_leadlag_signal(ret_df, K: int = 5, lam: float = 0.9):
    import numpy as np
    us_cols = [c for c in ret_df.columns if c in US_SECTOR_ETFS]
    jp_cols = [c for c in ret_df.columns if c in JP_SECTOR_ETFS]
    if len(us_cols) < 5 or len(jp_cols) < 5:
        return None
    ret_all = ret_df[us_cols + jp_cols].values
    T, N = ret_all.shape
    N_U = len(us_cols)
    N_J = len(jp_cols)
    mu = ret_all.mean(axis=0)
    sig = ret_all.std(axis=0) + 1e-8
    Z = (ret_all - mu) / sig
    v1 = np.ones(N) / np.sqrt(N)
    v2 = np.zeros(N)
    v2[:N_U] = 1.0 / np.sqrt(N_U)
    v2[N_U:] = -1.0 / np.sqrt(N_J)
    cycl_us = ["XLB", "XLE", "XLF", "XLI", "XLRE"]
    deff_us = ["XLP", "XLU", "XLV"]
    v3 = np.zeros(N)
    for i, c in enumerate(us_cols):
        if c in cycl_us:
            v3[i] = 1.0
        elif c in deff_us:
            v3[i] = -1.0
    nv3 = np.linalg.norm(v3)
    if nv3 > 1e-8:
        v3 /= nv3
    V0 = np.stack([v1, v2, v3], axis=1)
    C0_raw = V0 @ V0.T
    C_raw = np.corrcoef(Z.T)
    C_reg = (1 - lam) * C_raw + lam * C0_raw
    eigvals, eigvecs = np.linalg.eigh(C_reg)
    idx = np.argsort(eigvals)[::-1]
    eigvals = eigvals[idx]
    eigvecs = eigvecs[:, idx]
    V_K = eigvecs[:, :K]
    V_U = V_K[:N_U, :]
    V_J = V_K[N_U:, :]
    explained_var = eigvals[:K].sum() / eigvals.sum()
    z_U = Z[-1, :N_U]
    f = V_U.T @ z_U
    s_hat = V_J @ f
    signal = pd.Series(s_hat, index=jp_cols)
    us_ret_today = pd.Series(ret_df[us_cols].iloc[-1].values, index=us_cols)
    return {
        "signal":        signal,
        "factor_scores": f,
        "explained_var": explained_var,
        "us_ret_today":  us_ret_today,
        "jp_cols":       jp_cols,
        "us_cols":       us_cols,
        "latest_date":   ret_df.index[-1],
    }



# =====================================================
# ★ マーケットリサーチAI（セクター分析）
# =====================================================

# =====================================================
# ① Finnhub 銘柄別ニュース取得
# =====================================================
# 注目銘柄リスト（光ファイバー・DC・半導体）
FINNHUB_WATCH_SYMBOLS = {
    # ── 国内光ケーブル・非鉄（ADR） ──────────────────────
    "FJIKY":  "フジクラ",
    "SMWAY":  "住友電工",
    "FURWY":  "古河電工",
    # ── 国内電機・重工 ────────────────────────────────────
    "MIELY":  "三菱電機",
    "MHVIY":  "三菱重工",
    "IHICY":  "IHI",
    "MITSF":  "三菱商事",
    # ── 海外光ファイバー ──────────────────────────────────
    "LITE":   "Lumentum",
    "COHR":   "Coherent",
    "GLW":    "Corning",
    "AAOI":   "Applied Optoelectronics",
    # ── DC電源・冷却 ──────────────────────────────────────
    "VRT":    "Vertiv",
    "ETN":    "Eaton",
    # ── AI半導体 ──────────────────────────────────────────
    "NVDA":   "NVIDIA",
    "AMD":    "AMD",
    "AVGO":   "Broadcom",
    # ── クラウド ──────────────────────────────────────────
    "MSFT":   "Microsoft",
    "GOOGL":  "Alphabet",
    "AMZN":   "Amazon",
}

# Yahoo Finance ニュース取得（Finnhubフォールバック用）
def fetch_yahoo_finance_news(symbol: str, name: str, max_items: int = 3) -> List[str]:
    """
    Yahoo Finance の銘柄ページからニュースをスクレイピング。
    Finnhubで取得できなかった場合のフォールバック。
    """
    import urllib.request
    from html.parser import HTMLParser

    POSITIVE_KW = ["beat","surge","record","upgrade","growth","win",
                   "contract","expand","raise","rally","increase","positive"]
    NEGATIVE_KW = ["miss","loss","decline","cut","downgrade","layoff",
                   "tariff","ban","sanction","recall","warning","decrease","negative"]

    def _stag(text: str) -> str:
        t = text.lower()
        pos = sum(1 for kw in POSITIVE_KW if kw in t)
        neg = sum(1 for kw in NEGATIVE_KW if kw in t)
        if pos > neg:   return "📈+"
        elif neg > pos: return "📉-"
        return "➡️"

    results = []
    # Yahoo Finance RSSフィード（銘柄別）
    rss_url = f"https://finance.yahoo.com/rss/headline?s={symbol}"
    try:
        req = urllib.request.Request(
            rss_url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
        )
        with urllib.request.urlopen(req, timeout=8) as res:
            raw = res.read()
        feed = feedparser.parse(raw)
        for entry in feed.entries[:max_items]:
            title = entry.get("title", "").strip()
            if not title:
                continue
            stag = _stag(title)
            results.append(
                f"[🔦{name}({symbol})][{stag}][Yahoo Finance] {title}"
            )
        if results:
            logger.info(f"[yahoo_news] {symbol}: {len(results)}件取得")
    except Exception as e:
        logger.debug(f"[yahoo_news] {symbol} 失敗: {e}")

    return results


def fetch_finnhub_company_news(
    symbols: Dict[str, str] = None,
    days_back: int = 5,
) -> List[str]:
    """
    Finnhub APIで銘柄別ニュースを取得。
    戻り値: [テーマ][センチメント][銘柄名] タイトル 形式のリスト
    """
    # secrets から毎回取得（起動時キャッシュ問題を回避）
    api_key = get_env_var("FINNHUB_API_KEY", "")
    if not api_key:
        logger.warning("[finnhub] FINNHUB_API_KEY が未設定")
        return []

    if symbols is None:
        symbols = FINNHUB_WATCH_SYMBOLS

    POSITIVE_KW = [
        "beat", "surge", "record", "upgrade", "growth", "expand",
        "contract", "win", "partnership", "raise", "rally",
    ]
    NEGATIVE_KW = [
        "miss", "loss", "decline", "cut", "downgrade", "layoff",
        "tariff", "ban", "sanction", "recall", "warning", "risk",
    ]

    def _stag(text: str) -> str:
        t = text.lower()
        pos = sum(1 for kw in POSITIVE_KW if kw in t)
        neg = sum(1 for kw in NEGATIVE_KW if kw in t)
        if pos > neg:   return "📈+"
        elif neg > pos: return "📉-"
        return "➡️"

    end_dt   = datetime.now()
    start_dt = end_dt - timedelta(days=days_back)

    results = []
    errors  = []
    for symbol, name in symbols.items():
        try:
            url = (
                f"https://finnhub.io/api/v1/company-news"
                f"?symbol={symbol}"
                f"&from={start_dt.strftime('%Y-%m-%d')}"
                f"&to={end_dt.strftime('%Y-%m-%d')}"
                f"&token={api_key}"
            )
            r = requests.get(url, timeout=10)
            logger.info(f"[finnhub] {symbol}: HTTP {r.status_code}")

            if r.status_code == 401:
                errors.append(f"{symbol}: 認証エラー(401)")
                break
            if r.status_code == 429:
                errors.append(f"{symbol}: レート制限(429)")
                break
            if r.status_code != 200:
                errors.append(f"{symbol}: HTTP {r.status_code}")
                continue

            articles = r.json()
            if not isinstance(articles, list):
                errors.append(f"{symbol}: 形式エラー")
                continue
            if len(articles) == 0:
                logger.info(f"[finnhub] {symbol}: ニュース0件 → Yahoo Financeフォールバック")
                # Finnhubで0件 → Yahoo Financeで補完
                yf_news = fetch_yahoo_finance_news(symbol, name, max_items=3)
                results.extend(yf_news)
                continue

            for art in articles[:3]:
                headline = art.get("headline", "").strip()
                summary  = art.get("summary",  "").strip()[:150]
                if not headline:
                    continue
                stag = _stag(headline + " " + summary)
                results.append(
                    f"[🔦{name}({symbol})][{stag}][Finnhub] {headline}"
                )

            time.sleep(0.15)

        except Exception as e:
            errors.append(f"{symbol}: {str(e)[:80]}")
            logger.warning(f"[finnhub] {symbol} 失敗: {e}")
            # 例外発生時もYahoo Financeで補完
            yf_news = fetch_yahoo_finance_news(symbol, name, max_items=2)
            results.extend(yf_news)

    if errors:
        logger.warning(f"[finnhub] エラー: {errors}")
    logger.info(f"[finnhub] 完了: {len(results)}件 エラー:{len(errors)}件")
    return results


# =====================================================
# ② Alpha Vantage ニュースセンチメント取得
# =====================================================
@st.cache_data(ttl=TTL_RSS, show_spinner=False)
def fetch_av_news_sentiment(
    tickers: str = "NVDA,COHR,LITE,GLW,AAOI,VRT",
) -> Dict[str, Any]:
    """
    Alpha Vantage NEWS_SENTIMENT APIで銘柄別感情スコアを取得。
    戻り値:
      {
        "headlines": [...],          # LLMへ渡すニュースリスト
        "ticker_scores": {           # 銘柄別平均センチメントスコア
            "NVDA": {"score": 0.23, "label": "Somewhat-Bullish", "count": 5},
            ...
        },
        "overall_score": 0.15,      # 全体平均センチメント
      }
    """
    if not ALPHA_VANTAGE_KEY:
        return {"headlines": [], "ticker_scores": {}, "overall_score": None}

    try:
        url = (
            f"https://www.alphavantage.co/query"
            f"?function=NEWS_SENTIMENT"
            f"&tickers={tickers}"
            f"&limit=30"
            f"&apikey={ALPHA_VANTAGE_KEY}"
        )
        r = requests.get(url, timeout=12)
        if r.status_code != 200:
            return {"headlines": [], "ticker_scores": {}, "overall_score": None}

        data = r.json()
        feed = data.get("feed", [])
        if not feed:
            return {"headlines": [], "ticker_scores": {}, "overall_score": None}

        headlines    = []
        ticker_acc   = {}   # {ticker: [score, ...]}

        for art in feed[:20]:
            title        = art.get("title", "").strip()
            overall_sent = float(art.get("overall_sentiment_score", 0))
            sent_label   = art.get("overall_sentiment_label", "Neutral")

            # センチメント絵文字
            if overall_sent >= 0.15:
                stag = "📈+"
            elif overall_sent <= -0.15:
                stag = "📉-"
            else:
                stag = "➡️"

            headlines.append(
                f"[AV-Sentiment:{overall_sent:+.2f}][{stag}] {title}"
            )

            # 銘柄別スコア集計
            for ts in art.get("ticker_sentiment", []):
                tk  = ts.get("ticker", "")
                sc  = float(ts.get("ticker_sentiment_score", 0))
                if tk:
                    ticker_acc.setdefault(tk, []).append(sc)

        # 銘柄別平均
        ticker_scores = {}
        for tk, scores in ticker_acc.items():
            avg = sum(scores) / len(scores)
            if avg >= 0.35:   label = "Bullish"
            elif avg >= 0.15: label = "Somewhat-Bullish"
            elif avg >= -0.15:label = "Neutral"
            elif avg >= -0.35:label = "Somewhat-Bearish"
            else:             label = "Bearish"
            ticker_scores[tk] = {
                "score": round(avg, 3),
                "label": label,
                "count": len(scores),
            }

        overall = (
            sum(v["score"] for v in ticker_scores.values()) / len(ticker_scores)
            if ticker_scores else None
        )

        logger.info(f"[av_sentiment] {len(headlines)}件, 銘柄数: {len(ticker_scores)}")
        return {
            "headlines":    headlines,
            "ticker_scores": ticker_scores,
            "overall_score": round(overall, 3) if overall is not None else None,
        }

    except Exception as e:
        logger.error(f"fetch_av_news_sentiment error: {e}")
        return {"headlines": [], "ticker_scores": {}, "overall_score": None}


# =====================================================
# ③ Alpha Vantage 経済指標取得
# =====================================================
@st.cache_data(ttl=TTL_DAILY, show_spinner=False)
def fetch_av_economic_indicators() -> Dict[str, Any]:
    """
    Alpha Vantage で主要経済指標を取得。
    取得指標: Federal Funds Rate / CPI / Unemployment / GDP / Treasury Yield
    """
    if not ALPHA_VANTAGE_KEY:
        return {}

    INDICATORS = {
        "FF金利(%)":     "FEDERAL_FUNDS_RATE",
        "CPI(前年比%)":  "CPI",
        "失業率(%)":     "UNEMPLOYMENT",
        "GDP成長率(%)":  "REAL_GDP",
        "米10年金利(%)": "TREASURY_YIELD",
    }

    results = {}
    for label, func in INDICATORS.items():
        try:
            params = {
                "function": func,
                "apikey":   ALPHA_VANTAGE_KEY,
            }
            if func == "TREASURY_YIELD":
                params["maturity"] = "10year"
            if func in ("REAL_GDP", "CPI"):
                params["interval"] = "quarterly" if func == "REAL_GDP" else "monthly"

            r = requests.get(
                "https://www.alphavantage.co/query",
                params=params, timeout=10,
            )
            if r.status_code != 200:
                continue

            data = r.json()
            series = data.get("data", [])
            if not series:
                continue

            # 最新値と前回値を取得
            latest   = series[0]
            previous = series[1] if len(series) >= 2 else None

            val      = float(latest.get("value", 0))
            prev_val = float(previous.get("value", 0)) if previous else None
            chg      = round(val - prev_val, 3) if prev_val is not None else None

            results[label] = {
                "value":    round(val, 3),
                "prev":     round(prev_val, 3) if prev_val else None,
                "change":   chg,
                "date":     latest.get("date", ""),
                "trend":    "↑" if chg and chg > 0 else ("↓" if chg and chg < 0 else "→"),
            }
            time.sleep(0.3)   # AV レート制限（free: 25req/day）

        except Exception as e:
            logger.debug(f"[av_econ] {label} 取得失敗: {e}")

    logger.info(f"[av_econ] 経済指標取得: {len(results)}件")
    return results


def render_optical_basket():
    """光ケーブル×AI インフラ カスタムバスケットインデックス"""
    st.header("🔦 光通信・AI インフラ バスケット")
    st.markdown(
        '<div style="background:linear-gradient(135deg,#f0f9ff,#e0f2fe);'
        'border-left:4px solid #0284c7;border-radius:6px;padding:10px 16px;'
        'font-size:13px;color:#333;margin-bottom:14px;">'
        "COHR・LITE・GLW・AAOIの光通信4銘柄＋日本の光関連2銘柄を等加重合成した"
        "カスタムバスケットと日経平均・SMH（半導体ETF）を比較します。"
        "</div>",
        unsafe_allow_html=True,
    )

    # バスケット定義
    OPTICAL_BASKET = {
        "COHR":   {"name": "Coherent",              "flag": "🇺🇸", "weight": 1.0},
        "LITE":   {"name": "Lumentum",              "flag": "🇺🇸", "weight": 1.0},
        "GLW":    {"name": "Corning",               "flag": "🇺🇸", "weight": 1.0},
        "AAOI":   {"name": "Applied Optoelectronics","flag": "🇺🇸", "weight": 1.0},
        "5803.T": {"name": "フジクラ",              "flag": "🇯🇵", "weight": 1.0},
        "5901.T": {"name": "住友電工",              "flag": "🇯🇵", "weight": 1.0},
    }
    BENCHMARKS = {
        "^N225": "日経平均",
        "SMH":   "半導体ETF(SMH)",
        "^IXIC": "NASDAQ",
    }

    col_b1, col_b2 = st.columns([3, 1])
    with col_b2:
        period_sel = st.selectbox(
            "期間",
            ["1か月", "3か月", "6か月", "1年"],
            index=2, key="optical_basket_period",
        )
    period_map = {"1か月": "1mo", "3か月": "3mo", "6か月": "6mo", "1年": "1y"}
    yf_period  = period_map[period_sel]

    with st.spinner("価格データ取得中..."):
        basket_data = {}
        errors = []
        all_symbols = list(OPTICAL_BASKET.keys()) + list(BENCHMARKS.keys())

        for sym in all_symbols:
            try:
                raw = yf.Ticker(sym).history(period=yf_period, interval="1d", auto_adjust=True)
                if raw.empty:
                    errors.append(sym)
                    continue
                s = raw["Close"].copy()
                if hasattr(s.index, "tz") and s.index.tz is not None:
                    s.index = s.index.tz_localize(None)
                s.index = pd.to_datetime(s.index).normalize()
                basket_data[sym] = s
            except Exception as e:
                errors.append(sym)
                logger.debug(f"[optical_basket] {sym}: {e}")

    if errors:
        st.caption(f"⚠️ 取得できなかった銘柄: {', '.join(errors)}")

    # バスケット合成（等加重・基準日=100）
    basket_syms = [s for s in OPTICAL_BASKET if s in basket_data]
    bench_syms  = [s for s in BENCHMARKS    if s in basket_data]

    if len(basket_syms) < 2:
        st.warning("バスケット銘柄のデータを十分に取得できませんでした")
        return

    # 共通日付でアライン
    basket_df = pd.DataFrame({s: basket_data[s] for s in basket_syms})
    basket_df = basket_df.dropna(how="all").ffill()
    # 等加重正規化（各銘柄を初日=100に基準化して平均）
    norm = basket_df.div(basket_df.iloc[0]) * 100
    basket_index = norm.mean(axis=1)

    # ベンチマーク正規化
    bench_series = {}
    for sym in bench_syms:
        s = basket_data[sym].reindex(basket_index.index).ffill()
        bench_series[sym] = s / s.iloc[0] * 100

    # ── チャート描画 ───────────────────────────────
    if PLOTLY_AVAILABLE:
        import plotly.graph_objects as go
        fig = go.Figure()

        # バスケット（太線）
        fig.add_trace(go.Scatter(
            x=basket_index.index, y=basket_index.values,
            name="🔦 光通信バスケット",
            line=dict(color="#0284c7", width=3),
            fill="tozeroy", fillcolor="rgba(2,132,199,0.08)",
        ))

        # ベンチマーク
        bench_colors = {"^N225": "#f59e0b", "SMH": "#8b5cf6", "^IXIC": "#6b7280"}
        for sym, s in bench_series.items():
            fig.add_trace(go.Scatter(
                x=s.index, y=s.values,
                name=BENCHMARKS[sym],
                line=dict(color=bench_colors.get(sym, "#999"), width=1.5, dash="dot"),
            ))

        # 基準線
        fig.add_hline(y=100, line_dash="dash", line_color="rgba(0,0,0,0.2)")

        fig.update_layout(
            height=420,
            margin=dict(l=0, r=0, t=30, b=0),
            hovermode="x unified",
            legend=dict(orientation="h", y=1.08),
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            yaxis=dict(title="基準日=100"),
        )
        st.plotly_chart(fig, width="stretch")

    # ── 個別銘柄パフォーマンス ───────────────────────
    st.markdown("#### 📊 構成銘柄パフォーマンス")
    perf_rows = []
    for sym in basket_syms:
        s = basket_data[sym]
        ret = (s.iloc[-1] / s.iloc[0] - 1) * 100
        info = OPTICAL_BASKET[sym]
        perf_rows.append({
            "銘柄": f"{info['flag']} {sym}",
            "名称": info["name"],
            f"リターン({period_sel})": f"{ret:+.1f}%",
            "最新値": f"{s.iloc[-1]:,.2f}",
        })

    # バスケット合計
    basket_ret = (basket_index.iloc[-1] / basket_index.iloc[0] - 1) * 100
    perf_rows.append({
        "銘柄": "🔦 バスケット合計",
        "名称": "等加重平均",
        f"リターン({period_sel})": f"{basket_ret:+.1f}%",
        "最新値": "—",
    })
    # ベンチマーク
    for sym, s in bench_series.items():
        b_ret = (s.iloc[-1] / s.iloc[0] - 1) * 100
        perf_rows.append({
            "銘柄": sym,
            "名称": BENCHMARKS[sym],
            f"リターン({period_sel})": f"{b_ret:+.1f}%",
            "最新値": "—",
        })

    perf_df = pd.DataFrame(perf_rows)

    def _color_ret2(val):
        if isinstance(val, str) and val.startswith("+"):
            return "color:#16a34a;font-weight:700"
        if isinstance(val, str) and val.startswith("-"):
            return "color:#dc2626;font-weight:700"
        return ""

    st.dataframe(
        perf_df.style.map(_color_ret2, subset=[f"リターン({period_sel})"]),
        width="stretch",
        hide_index=True,
    )

    st.caption(
        "🔦 光通信バスケット = COHR・LITE・GLW・AAOI・フジクラ・住友電工の等加重インデックス（基準日=100） | "
        "⚠️ 情報提供目的のみ。投資判断はご自身の責任で。"
    )


# =====================================================
# ★ AIインフラ投資マネーフロー監視
# =====================================================

# 監視テーマとRSSソース定義
AI_INFRA_THEMES = {
    "🔦 光通信・フォトニクス": {
        "color": "#0284c7",
        "bg":    "#f0f9ff",
        "keywords": ["photonics", "optical", "transceiver", "fiber", "COHR", "LITE", "AAOI", "GLW",
                     "Coherent", "Lumentum", "Corning", "silicon photonics", "co-packaged optics", "CPO"],
        "tickers": ["COHR", "LITE", "AAOI", "GLW", "CIEN", "VIAV", "MRVL"],
        "rss_symbols": "COHR,LITE,AAOI,GLW,CIEN",
    },
    "💾 半導体・製造装置": {
        "color": "#7c3aed",
        "bg":    "#faf5ff",
        "keywords": ["semiconductor", "chip", "wafer", "NVIDIA", "TSMC", "ASML", "Broadcom",
                     "Advanced Micro", "Micron", "Intel", "foundry", "HBM", "CoWoS"],
        "tickers": ["NVDA", "AVGO", "AMAT", "LRCX", "KLAC", "ASML", "MU", "TSM"],
        "rss_symbols": "NVDA,AVGO,AMAT,LRCX,TSM",
    },
    "🤖 AI投資・インフラ全般": {
        "color": "#059669",
        "bg":    "#f0fdf4",
        "keywords": ["artificial intelligence", "AI investment", "data center", "hyperscaler",
                     "Microsoft", "Google", "Meta", "Amazon", "capex", "infrastructure"],
        "tickers": ["MSFT", "GOOGL", "META", "AMZN", "ORCL", "CRM"],
        "rss_symbols": "MSFT,GOOGL,META,AMZN",
    },
    "⚡ 電力・データセンターインフラ": {
        "color": "#d97706",
        "bg":    "#fffbeb",
        "keywords": ["power", "nuclear", "data center cooling", "Vertiv", "Vistra",
                     "電力", "原子力", "冷却", "VRT", "VST", "ETN", "GEV"],
        "tickers": ["VRT", "VST", "ETN", "CEG", "NEE", "GEV", "OKLO"],
        "rss_symbols": "VRT,VST,ETN,CEG,GEV",
    },
}


@st.cache_data(ttl=60 * 60 * 3, show_spinner=False)
def fetch_ai_infra_news(rss_symbols: str, max_items: int = 15) -> List[Dict]:
    """Yahoo Finance RSSから銘柄関連ニュースを取得"""
    import urllib.request
    results = []

    urls = [
        f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={rss_symbols}&region=US&lang=en-US",
        f"https://finance.yahoo.com/rss/headline?s={rss_symbols}&lang=en-US&region=US",
    ]

    for url in urls:
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            )
            with urllib.request.urlopen(req, timeout=10) as res:
                raw = res.read()
            feed = feedparser.parse(raw)
            if not feed.entries:
                continue
            for entry in feed.entries[:max_items]:
                title   = entry.get("title",   "").strip()
                summary = entry.get("summary", "").strip()
                link    = entry.get("link",    "")
                pub     = entry.get("published", "")[:16]
                if not title:
                    continue
                results.append({
                    "title":   title,
                    "summary": summary[:250],
                    "link":    link,
                    "pub":     pub,
                })
            if results:
                break
        except Exception as e:
            logger.debug(f"[ai_infra_news] {rss_symbols}: {e}")
            continue

    return results[:max_items]


@st.cache_data(ttl=60 * 60 * 24, show_spinner=False)
def ai_infra_commentary(theme: str, headlines: str, quarter: str, lang: str = "ja") -> tuple:
    """AIがテーマ別に投資マネーフローをコメント"""
    _lang_suffix = "\n\nIMPORTANT: Respond entirely in English. Do not use Japanese." if lang == "en" else ""
    if lang == "en":
        prompt = f"""You are an AI infrastructure investment analyst.
Analyze the following theme and latest news headlines, and provide a concise investment commentary for global investors.

Theme: {theme}
Period: {quarter}

[Latest News Headlines]
{headlines}

Summarize in 150 words or fewer using this format:
[Money Flow] Major investment trends and capital movements
[Key Stocks] Companies gaining prominence in this theme
[Japan Stocks Impact] Related Japanese stocks / supply chain implications

*For informational purposes only. Not investment advice.*{_lang_suffix}"""
    else:
        prompt = f"""あなたはAIインフラ投資を専門とする日本人アナリストです。
以下のテーマと最新ニュースを分析し、日本の投資家向けに日本語で簡潔にコメントしてください。

テーマ: {theme}
分析期間: {quarter}

【最新ニュースヘッドライン】
{headlines}

以下の形式で250字以内でまとめてください：
【マネーフロー】大手の投資動向・資金の流れ
【注目銘柄】このテーマで存在感を増している企業
【日本株への波及】関連する日本株・サプライチェーンへの影響

※情報提供目的のみ。投資助言ではありません。"""

    try:
        comment, model = call_ai_with_fallback(prompt, max_output_tokens=400, temperature=0.4)
        return comment, model
    except Exception as e:
        return f"AI generation error: {e}" if lang == "en" else f"AI生成エラー: {e}", ""


def render_ai_infra_moneyflow():
    """AIインフラ投資マネーフロー監視セクション"""
    st.header("💸 AIインフラ 投資マネーフロー")
    st.markdown(
        '<div style="background:linear-gradient(135deg,#fefce8,#fef3c7);'
        'border-left:4px solid #d97706;border-radius:6px;padding:10px 16px;'
        'font-size:13px;color:#333;margin-bottom:14px;">'
        "光通信・半導体・AI・電力インフラへのビッグマネーの動きをリアルタイムで追跡。"
        "Yahoo Financeニュース＋AIコメントで投資テーマを把握します。"
        "</div>",
        unsafe_allow_html=True,
    )

    now     = datetime.now(JST)
    quarter = f"{now.year}-Q{(now.month-1)//3+1}"

    # タブ作成
    theme_names = list(AI_INFRA_THEMES.keys())
    tabs = st.tabs(theme_names)

    for tab, theme_name in zip(tabs, theme_names):
        theme = AI_INFRA_THEMES[theme_name]
        with tab:
            col_l, col_r = st.columns([3, 1])
            with col_l:
                # ティッカーバッジ
                badges = " ".join(
                    f'<span style="background:{theme["color"]};color:white;'
                    f'font-size:11px;padding:2px 7px;border-radius:10px;'
                    f'margin-right:4px;">{t}</span>'
                    for t in theme["tickers"][:6]
                )
                st.markdown(badges, unsafe_allow_html=True)
            with col_r:
                refresh_btn = st.button(
                    "🔄 更新",
                    key=f"infra_refresh_{theme_name}",
                    width="stretch",
                )
                if refresh_btn:
                    fetch_ai_infra_news.clear()
                    st.rerun()

            # ── ニュース取得 ───────────────────────────
            with st.spinner("ニュース取得中..."):
                news = fetch_ai_infra_news(theme["rss_symbols"])

            if news:
                # キーワードマッチで関連度スコアリング
                def _relevance(item):
                    text = (item["title"] + item["summary"]).lower()
                    return sum(1 for kw in theme["keywords"] if kw.lower() in text)

                news_sorted = sorted(news, key=_relevance, reverse=True)

                st.markdown(f"**📰 最新ニュース（{len(news_sorted)}件）**")
                for item in news_sorted[:8]:
                    rel = _relevance(item)
                    dot = "🔴" if rel >= 3 else "🟡" if rel >= 1 else "⚪"
                    st.markdown(
                        f'<div style="background:{theme["bg"]};border-left:3px solid {theme["color"]};'
                        f'border-radius:6px;padding:8px 12px;margin-bottom:6px;">'
                        f'<div style="font-size:13px;font-weight:600;">{dot} '
                        f'<a href="{item["link"]}" target="_blank" '
                        f'style="color:#1e293b;text-decoration:none;">{item["title"]}</a></div>'
                        f'<div style="font-size:11px;color:#64748b;margin-top:3px;">'
                        f'{item["summary"][:120]}{"..." if len(item["summary"])>120 else ""}</div>'
                        f'<div style="font-size:10px;color:#94a3b8;">{item["pub"]}</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
            else:
                st.info("ニュースを取得できませんでした")
                st.markdown(
                    f"🔗 [Yahoo Finance で確認]"
                    f"(https://finance.yahoo.com/quote/{theme['tickers'][0]}/news/)"
                )

            # ── AIコメント ────────────────────────────
            st.markdown("---")
            ai_key = f"infra_ai_{theme_name}_{quarter}"

            col_ai1, col_ai2 = st.columns([3, 1])
            with col_ai2:
                run_ai = st.button(
                    "🤖 AIコメント生成",
                    key=f"infra_ai_btn_{theme_name}",
                    type="primary",
                    width="stretch",
                )
                if st.button(
                    "🗑️ クリア",
                    key=f"infra_ai_clear_{theme_name}",
                    width="stretch",
                ):
                    st.session_state.pop(ai_key, None)
                    ai_infra_commentary.clear()
                    st.rerun()

            if run_ai or ai_key not in st.session_state:
                if run_ai:
                    headlines = "\n".join(
                        f"・{n['title']}" for n in (news_sorted[:5] if news else [])
                    ) or "（ニュース取得なし）"
                    with st.spinner(f"🤖 {theme_name} をAI分析中..."):
                        comment, model = ai_infra_commentary(
                            theme_name, headlines, quarter,
                            lang=st.session_state.get("lang", "ja"),
                        )
                    st.session_state[ai_key] = (comment, model)

            if ai_key in st.session_state:
                comment, model = st.session_state[ai_key]
                for kw in ["【マネーフロー】", "【注目銘柄】", "【日本株への波及】"]:
                    comment = comment.replace(kw, f"\n**{kw}**")
                st.markdown(
                    f'<div style="background:{theme["bg"]};'
                    f'border:1px solid {theme["color"]}40;'
                    f'border-left:4px solid {theme["color"]};'
                    f'border-radius:8px;padding:14px 18px;'
                    f'font-size:13px;line-height:1.9;">'
                    f'{comment.replace(chr(10), "<br>")}'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                if model:
                    st.caption(f"🤖 {model} | {quarter} | ⚠️ 情報提供目的のみ")
            else:
                st.info("「🤖 AIコメント生成」を押すと、このテーマのマネーフロー分析が表示されます")



@st.cache_data(ttl=60 * 60 * 6, show_spinner=False)
def fetch_jpx_short_positions(code: str) -> List[Dict]:
    """
    東証の空売り残高Excelから特定銘柄の証券会社別残高を取得。
    code: 4桁（例: "5803"）
    毎営業日更新。残高割合0.5%以上のみ公表。
    """
    import io, datetime as dt
    try:
        # irbank.net のAPIを利用（より信頼性が高い）
        code_clean = code.replace(".T", "")
        url_irbank = f"https://irbank.net/short/{code_clean}"
        r = requests.get(
            url_irbank,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            timeout=12,
        )
        if r.status_code != 200:
            return []

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(r.text, "html.parser")
        results = []

        # テーブル行を解析
        tables = soup.find_all("table")
        for table in tables:
            rows = table.find_all("tr")
            for row in rows[1:]:  # ヘッダースキップ
                cells = row.find_all(["td", "th"])
                if len(cells) >= 4:
                    texts = [c.get_text(strip=True) for c in cells]
                    # 機関名・残高割合・残高株数・報告日 を抽出
                    institution = texts[0] if texts[0] else ""
                    if not institution or institution in ["機関投資家", "報告者"]:
                        continue
                    # 数値を含む行のみ
                    try:
                        ratio_str = next(
                            (t for t in texts[1:] if "%" in t), ""
                        ).replace("%", "")
                        ratio = float(ratio_str) if ratio_str else 0
                        # 株数（千株単位で表示）
                        shares_str = next(
                            (t for t in texts[1:] if t.replace(",", "").replace(".", "").isdigit()), "0"
                        )
                        shares = int(shares_str.replace(",", "")) if shares_str else 0
                        date_val = texts[-1] if texts[-1] else ""
                        if ratio > 0 or shares > 0:
                            results.append({
                                "institution": institution,
                                "ratio":       ratio,
                                "shares":      shares,
                                "date":        date_val,
                            })
                    except Exception:
                        continue

        # 重複除去・ソート
        seen = set()
        unique = []
        for r in results:
            key = r["institution"]
            if key not in seen:
                seen.add(key)
                unique.append(r)

        return sorted(unique, key=lambda x: x["ratio"], reverse=True)[:15]

    except Exception as e:
        logger.debug(f"[jpx_short] {code}: {e}")
        return []


@st.cache_data(ttl=60 * 60 * 6, show_spinner=False)
def fetch_jpx_short_excel(code: str) -> List[Dict]:
    """
    東証公式Excelから直接取得（フォールバック）。
    JPXページをスクレイピングして最新ExcelのURLを取得し解析。
    """
    import io
    try:
        # JPXページから最新ExcelのURLを取得
        page = requests.get(
            "https://www.jpx.co.jp/markets/public/short-selling/index.html",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        if page.status_code != 200:
            return []

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(page.text, "html.parser")

        # 最新のExcelリンクを取得
        excel_url = ""
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "Short_Positions.xls" in href:
                excel_url = "https://www.jpx.co.jp" + href if href.startswith("/") else href
                break

        if not excel_url:
            return []

        # Excelをダウンロード
        r = requests.get(
            excel_url,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        if r.status_code != 200:
            return []

        # openpyxlで解析
        try:
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(r.content), read_only=True, data_only=True)
        except Exception:
            # xlrdでxls形式を試みる
            try:
                import xlrd
                wb_xls = xlrd.open_workbook(file_contents=r.content)
                ws = wb_xls.sheet_by_index(0)
                code_clean = code.replace(".T", "")
                results = []
                for row_idx in range(1, ws.nrows):
                    row_vals = [str(ws.cell_value(row_idx, c)).strip() for c in range(ws.ncols)]
                    # 銘柄コード列を検索
                    if any(code_clean in v for v in row_vals[:3]):
                        institution = row_vals[1] if len(row_vals) > 1 else ""
                        try:
                            ratio = float(row_vals[3]) if len(row_vals) > 3 else 0
                            shares = int(float(row_vals[4])) if len(row_vals) > 4 else 0
                        except ValueError:
                            continue
                        results.append({
                            "institution": institution,
                            "ratio":       ratio,
                            "shares":      shares,
                            "date":        row_vals[-1] if row_vals else "",
                        })
                return sorted(results, key=lambda x: x["ratio"], reverse=True)[:15]
            except Exception:
                return []

        # openpyxl解析
        ws = wb.active
        code_clean = code.replace(".T", "")
        results = []
        for row in ws.iter_rows(min_row=2, values_only=True):
            if not row or not any(row):
                continue
            row_str = [str(v).strip() if v is not None else "" for v in row]
            if any(code_clean == v or code_clean in v for v in row_str[:3]):
                try:
                    institution = row_str[1] if len(row_str) > 1 else ""
                    ratio  = float(row_str[3]) if len(row_str) > 3 and row_str[3] else 0
                    shares = int(float(row_str[4])) if len(row_str) > 4 and row_str[4] else 0
                    results.append({
                        "institution": institution,
                        "ratio":       ratio,
                        "shares":      shares,
                        "date":        row_str[-1] if row_str else "",
                    })
                except (ValueError, IndexError):
                    continue
        return sorted(results, key=lambda x: x["ratio"], reverse=True)[:15]

    except Exception as e:
        logger.debug(f"[jpx_short_excel] {code}: {e}")
        return []


def render_short_position_ranking(code: str, color: str):
    """証券会社別空売り残高ランキングを表示"""
    st.markdown("---")
    st.markdown("**🏦 証券会社別 空売り残高ランキング**")
    st.caption("東証開示データ（残高割合0.5%以上を公表）/ 毎営業日更新")

    col_sp1, col_sp2 = st.columns([3, 1])
    with col_sp2:
        if st.button("🔄 更新", key=f"short_rank_refresh_{code}",
                     width="stretch"):
            fetch_jpx_short_positions.clear()
            fetch_jpx_short_excel.clear()
            st.rerun()

    with st.spinner("空売り残高データ取得中..."):
        positions = fetch_jpx_short_positions(code)
        if not positions:
            positions = fetch_jpx_short_excel(code)

    if not positions:
        st.info(
            "空売り残高データを取得できませんでした。\n"
            "残高割合が0.5%未満の場合は公表されません。"
        )
        code_clean = code.replace(".T", "")
        st.markdown(
            f"🔗 [irbank.net で確認](https://irbank.net/short/{code_clean}) | "
            f"[東証 空売り残高](https://www.jpx.co.jp/markets/public/short-selling/index.html)"
        )
        return

    # ランキング表示
    max_ratio = max(p["ratio"] for p in positions) or 1

    # 有名機関のアイコン
    INSTITUTION_ICONS = {
        "goldman": "🇺🇸", "gs ": "🇺🇸",
        "morgan stanley": "🇺🇸", "ms ": "🇺🇸",
        "jpmorgan": "🇺🇸", "j.p. morgan": "🇺🇸", "jpm": "🇺🇸",
        "citadel": "🏰", "two sigma": "🔢", "renaissance": "🔬",
        "barclays": "🇬🇧", "ubs": "🇨🇭", "credit suisse": "🇨🇭",
        "deutsche": "🇩🇪", "societe": "🇫🇷", "bnp": "🇫🇷",
        "nomura": "🇯🇵", "野村": "🇯🇵",
        "daiwa": "🇯🇵", "大和": "🇯🇵",
        "nikko": "🇯🇵", "日興": "🇯🇵",
        "mizuho": "🇯🇵", "みずほ": "🇯🇵",
        "blackrock": "⬛", "vanguard": "🟠",
        "citadel": "🏰", "millennium": "🌀",
        "jane street": "📊", "jump trading": "🚀",
    }

    def _get_icon(name: str) -> str:
        name_lower = name.lower()
        for key, icon in INSTITUTION_ICONS.items():
            if key in name_lower:
                return icon
        return "🏛️"

    for i, pos in enumerate(positions[:10], 1):
        icon    = _get_icon(pos["institution"])
        ratio   = pos["ratio"]
        shares  = pos["shares"]
        bar_pct = ratio / max_ratio * 100
        # 順位バッジ色
        rank_color = (
            "#dc2626" if i == 1 else
            "#ea580c" if i == 2 else
            "#d97706" if i == 3 else
            "#64748b"
        )
        shares_str = f"{shares/1000:,.1f}千株" if shares >= 1000 else f"{shares:,}株"
        st.markdown(
            f'<div style="background:#fff;border:1px solid #e5e7eb;'
            f'border-radius:8px;padding:10px 14px;margin-bottom:6px;">'
            f'<div style="display:flex;align-items:center;gap:10px;">'
            f'<span style="background:{rank_color};color:white;font-size:12px;'
            f'font-weight:700;width:24px;height:24px;border-radius:50%;'
            f'display:flex;align-items:center;justify-content:center;">{i}</span>'
            f'<span style="font-size:13px;">{icon}</span>'
            f'<span style="font-size:13px;font-weight:600;flex:1;">'
            f'{pos["institution"]}</span>'
            f'<span style="font-size:14px;font-weight:800;color:{color};">'
            f'{ratio:.2f}%</span>'
            f'</div>'
            f'<div style="margin-top:5px;background:#f1f5f9;border-radius:3px;height:6px;">'
            f'<div style="background:{color};height:100%;border-radius:3px;'
            f'width:{bar_pct:.1f}%;"></div></div>'
            f'<div style="font-size:10px;color:#94a3b8;margin-top:2px;">'
            f'{shares_str} | {pos["date"]}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    total_ratio = sum(p["ratio"] for p in positions)
    st.caption(
        f"📊 公表機関合計: {total_ratio:.1f}% | "
        f"⚠️ 0.5%未満は非公表 | "
        f"⚠️ 証券会社の自己売買と顧客注文の代行が混在"
    )


@st.cache_data(ttl=60 * 60 * 6, show_spinner=False)
def fetch_us_institutional_holders(symbol: str) -> Dict:
    """
    yfinanceから米国株の機関保有・インサイダー・空売り詳細を取得。
    """
    try:
        tk = yf.Ticker(symbol)

        # 機関保有上位
        inst_df = tk.institutional_holders
        inst_rows = []
        if inst_df is not None and not inst_df.empty:
            for _, row in inst_df.head(10).iterrows():
                holder  = str(row.get("Holder", ""))
                shares  = int(row.get("Shares", 0))
                pct     = float(row.get("% Out", 0)) * 100
                val     = float(row.get("Value", 0))
                date    = str(row.get("Date Reported", ""))[:10]
                inst_rows.append({
                    "holder": holder, "shares": shares,
                    "pct": pct, "value": val, "date": date,
                })

        # ミューチュアルファンド保有上位
        mf_df = tk.mutualfund_holders
        mf_rows = []
        if mf_df is not None and not mf_df.empty:
            for _, row in mf_df.head(5).iterrows():
                mf_rows.append({
                    "holder": str(row.get("Holder", "")),
                    "shares": int(row.get("Shares", 0)),
                    "pct":    float(row.get("% Out", 0)) * 100,
                    "date":   str(row.get("Date Reported", ""))[:10],
                })

        # インサイダー取引履歴
        ins_df = tk.insider_transactions
        ins_rows = []
        if ins_df is not None and not ins_df.empty:
            for _, row in ins_df.head(8).iterrows():
                txn_text = str(row.get("Transaction", row.get("Text", "")))
                shares   = row.get("Shares", 0)
                value    = row.get("Value", 0)
                insider  = row.get("Insider", row.get("Name", ""))
                position = row.get("Position", row.get("Title", ""))
                date     = str(row.get("Start Date", row.get("Date", "")))[:10]
                is_buy   = any(kw in txn_text.lower()
                               for kw in ["purchase", "buy", "acquisition", "award"])
                ins_rows.append({
                    "insider":   str(insider),
                    "position":  str(position),
                    "txn":       txn_text,
                    "shares":    int(shares) if pd.notna(shares) else 0,
                    "value":     float(value) if pd.notna(value) else 0,
                    "date":      date,
                    "is_buy":    is_buy,
                })

        return {
            "ok":           True,
            "institutions": inst_rows,
            "mutual_funds": mf_rows,
            "insiders":     ins_rows,
        }
    except Exception as e:
        logger.debug(f"[us_holders] {symbol}: {e}")
        return {"ok": False}


def render_us_supply_demand(symbol: str, d: Dict, color: str):
    """米国株の需給詳細UI（機関保有ランキング・インサイダー売買）"""
    info = WATCHLIST_STOCKS.get(symbol, {})
    name = info.get("name", symbol)
    flag = info.get("flag", "🇺🇸")

    st.markdown("---")
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:4px;">'
        f'<span style="font-weight:700;">🏦 機関投資家・インサイダー 保有・売買動向</span>'
        f'<span style="background:{color};color:white;font-size:12px;'
        f'padding:2px 9px;border-radius:12px;font-weight:600;">'
        f'{flag} {symbol} — {name}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )
    st.caption("yfinance / SEC EDGAR 13Fデータ（四半期更新）")

    with st.spinner("機関保有データ取得中..."):
        data = fetch_us_institutional_holders(symbol)

    if not data.get("ok"):
        st.info("機関保有データを取得できませんでした")
        return

    tab_inst, tab_mf, tab_insider = st.tabs([
        "🏛️ 機関投資家TOP10", "📊 ファンドTOP5", "👤 インサイダー売買"
    ])

    # ── 機関投資家ランキング ────────────────────────
    with tab_inst:
        inst = data["institutions"]
        if inst:
            max_pct = max(r["pct"] for r in inst) or 1

            # 有名機関アイコン
            INST_ICONS = {
                "vanguard": "🟠", "blackrock": "⬛", "fidelity": "🟢",
                "state street": "🔵", "invesco": "🔴", "t. rowe": "💜",
                "wellington": "🏔️", "capital group": "🌐",
                "jpmorgan": "🇺🇸", "goldman": "🇺🇸", "morgan stanley": "🇺🇸",
                "citadel": "🏰", "renaissance": "🔬", "two sigma": "🔢",
                "millennium": "🌀", "bridgewater": "🌊", "aqr": "📐",
                "coatue": "🦅", "tiger": "🐯", "dragoneer": "🐉",
            }
            def _icon(name: str) -> str:
                n = name.lower()
                for k, v in INST_ICONS.items():
                    if k in n:
                        return v
                return "🏛️"

            for i, row in enumerate(inst, 1):
                bar_pct = row["pct"] / max_pct * 100
                rank_color = (
                    "#dc2626" if i == 1 else "#ea580c" if i == 2 else
                    "#d97706" if i == 3 else "#64748b"
                )
                val_str = (
                    f"${row['value']/1e9:.2f}B" if row["value"] >= 1e9 else
                    f"${row['value']/1e6:.0f}M" if row["value"] >= 1e6 else
                    f"${row['value']:,.0f}"
                ) if row["value"] else "—"
                shares_str = (
                    f"{row['shares']/1e6:.2f}M株" if row["shares"] >= 1e6 else
                    f"{row['shares']/1e3:.0f}K株"
                ) if row["shares"] else "—"
                st.markdown(
                    f'<div style="background:#fff;border:1px solid #e5e7eb;'
                    f'border-radius:8px;padding:10px 14px;margin-bottom:6px;">'
                    f'<div style="display:flex;align-items:center;gap:8px;">'
                    f'<span style="background:{rank_color};color:white;font-size:11px;'
                    f'font-weight:700;width:22px;height:22px;border-radius:50%;'
                    f'display:flex;align-items:center;justify-content:center;">{i}</span>'
                    f'<span style="font-size:13px;">{_icon(row["holder"])}</span>'
                    f'<span style="font-size:13px;font-weight:600;flex:1;">'
                    f'{row["holder"]}</span>'
                    f'<span style="font-size:14px;font-weight:800;color:{color};">'
                    f'{row["pct"]:.2f}%</span>'
                    f'</div>'
                    f'<div style="margin-top:4px;background:#f1f5f9;'
                    f'border-radius:3px;height:5px;">'
                    f'<div style="background:{color};height:100%;border-radius:3px;'
                    f'width:{bar_pct:.1f}%;"></div></div>'
                    f'<div style="font-size:10px;color:#94a3b8;margin-top:2px;">'
                    f'{shares_str} | {val_str} | {row["date"]}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            total_inst = sum(r["pct"] for r in inst)
            st.caption(
                f"📊 上位{len(inst)}機関合計: {total_inst:.1f}% | "
                f"13Fは四半期ごとの開示（最大45日遅延）"
            )
        else:
            st.info("機関投資家データなし")

    # ── ファンド保有 ─────────────────────────────
    with tab_mf:
        mf = data["mutual_funds"]
        if mf:
            max_pct_mf = max(r["pct"] for r in mf) or 1
            for i, row in enumerate(mf, 1):
                bar_pct = row["pct"] / max_pct_mf * 100
                shares_str = (
                    f"{row['shares']/1e6:.2f}M株" if row["shares"] >= 1e6 else
                    f"{row['shares']/1e3:.0f}K株"
                ) if row["shares"] else "—"
                st.markdown(
                    f'<div style="background:#faf5ff;border:1px solid #e9d5ff;'
                    f'border-radius:8px;padding:10px 14px;margin-bottom:6px;">'
                    f'<div style="display:flex;align-items:center;gap:8px;">'
                    f'<span style="background:#7c3aed;color:white;font-size:11px;'
                    f'font-weight:700;width:22px;height:22px;border-radius:50%;'
                    f'display:flex;align-items:center;justify-content:center;">{i}</span>'
                    f'<span style="font-size:13px;font-weight:600;flex:1;">'
                    f'{row["holder"]}</span>'
                    f'<span style="font-size:14px;font-weight:800;color:#7c3aed;">'
                    f'{row["pct"]:.2f}%</span>'
                    f'</div>'
                    f'<div style="margin-top:4px;background:#f1f5f9;'
                    f'border-radius:3px;height:5px;">'
                    f'<div style="background:#7c3aed;height:100%;border-radius:3px;'
                    f'width:{bar_pct:.1f}%;"></div></div>'
                    f'<div style="font-size:10px;color:#94a3b8;margin-top:2px;">'
                    f'{shares_str} | {row["date"]}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
        else:
            st.info("ファンド保有データなし")

    # ── インサイダー売買 ─────────────────────────
    with tab_insider:
        ins = data["insiders"]
        if ins:
            for row in ins:
                is_buy   = row["is_buy"]
                bg_color = "#f0fdf4" if is_buy else "#fef2f2"
                bd_color = "#16a34a" if is_buy else "#dc2626"
                tx_color = "#15803d" if is_buy else "#b91c1c"
                badge    = "🟢 買い" if is_buy else "🔴 売り"
                val_str  = (
                    f"${row['value']/1e6:.1f}M" if row["value"] >= 1e6 else
                    f"${row['value']:,.0f}"
                ) if row["value"] else "—"
                shares_str = f"{row['shares']:,}株" if row["shares"] else "—"
                st.markdown(
                    f'<div style="background:{bg_color};border-left:4px solid {bd_color};'
                    f'border-radius:8px;padding:10px 14px;margin-bottom:6px;">'
                    f'<div style="display:flex;align-items:center;gap:8px;">'
                    f'<span style="font-size:12px;font-weight:700;color:{tx_color};">'
                    f'{badge}</span>'
                    f'<span style="font-size:13px;font-weight:600;">'
                    f'{row["insider"]}</span>'
                    f'<span style="font-size:11px;color:#64748b;">'
                    f'({row["position"]})</span>'
                    f'</div>'
                    f'<div style="font-size:12px;color:#374151;margin-top:3px;">'
                    f'{row["txn"]}</div>'
                    f'<div style="font-size:10px;color:#94a3b8;margin-top:2px;">'
                    f'{shares_str} | {val_str} | {row["date"]}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
        else:
            st.info("インサイダー取引データなし")

    st.caption(
        "⚠️ 13Fは100M$以上運用の機関が四半期ごとに開示（最大45日遅延）。"
        "インサイダー売買はForm 4開示ベース。投資判断の参考情報です。"
    )


# =====================================================
# ★ 米国株 需給改善スコア
# =====================================================

@st.cache_data(ttl=60 * 60 * 6, show_spinner=False)
def calc_us_supply_demand_score(symbol: str) -> Dict:
    """
    米国株の需給改善スコア（0〜100）を5指標から計算。
    ① 空売り残高トレンド
    ② 機関保有変化
    ③ インサイダー売買バランス
    ④ 出来高の質（上昇日vs下落日）
    ⑤ 空売り比率水準
    """
    try:
        tk   = yf.Ticker(symbol)
        info = tk.info or {}

        scores   = {}
        details  = {}

        # ① 空売り残高トレンド（short_interest の前月比）
        short_cur  = info.get("sharesShort", 0) or 0
        short_prev = info.get("sharesShortPriorMonth", 0) or 0
        if short_prev > 0:
            short_chg = (short_cur / short_prev - 1) * 100
            # 減少=-改善。-20%以上の減少で満点
            s1 = int(max(0, min(100, 50 - short_chg * 2.5)))
        else:
            s1, short_chg = 50, 0
        scores["空売り残高変化"] = s1
        details["空売り残高変化"] = {
            "score": s1,
            "value": f"{short_chg:+.1f}%（前月比）",
            "raw":   short_chg,
            "good":  short_chg < -5,
            "bad":   short_chg > 10,
            "icon":  "📉" if short_chg < -5 else "📈" if short_chg > 10 else "➡️",
            "label": "減少（ショートカバー）" if short_chg < -5 else
                     "増加（売り圧力増）" if short_chg > 10 else "横ばい",
        }

        # ② 空売り比率水準（floatに対する割合）
        short_float = (info.get("shortPercentOfFloat") or 0) * 100
        # 5%未満=良好 20%超=悪化
        s2 = int(max(0, min(100, 100 - short_float * 4)))
        scores["空売り比率"] = s2
        details["空売り比率"] = {
            "score": s2,
            "value": f"{short_float:.1f}%",
            "raw":   short_float,
            "good":  short_float < 5,
            "bad":   short_float > 15,
            "icon":  "🟢" if short_float < 5 else "🔴" if short_float > 15 else "🟡",
            "label": "低水準（需給良好）" if short_float < 5 else
                     "高水準（売り圧力強）" if short_float > 15 else "中程度",
        }

        # ③ 機関保有変化（前四半期比）
        inst_df = tk.institutional_holders
        inst_chg_pct = 0
        if inst_df is not None and not inst_df.empty:
            try:
                total_inst = float(info.get("heldPercentInstitutions", 0)) * 100
                # 前四半期と比較できないためshares変化で代替
                inst_chg_pct = 0  # yfinanceでは直接取れない
            except Exception:
                pass
        # institutional_purchases_lastQtr が使えれば
        net_inst = info.get("netSharesAcquiredLastQtr") or info.get("buyPercentInsiderShares") or 0
        s3 = 50  # ベースライン（変化不明時）
        if net_inst > 0:
            s3 = int(min(100, 50 + net_inst * 10))
        elif net_inst < 0:
            s3 = int(max(0, 50 + net_inst * 10))
        inst_pct = float(info.get("heldPercentInstitutions", 0)) * 100
        # 機関保有60%以上は安定的
        if inst_pct >= 60:
            s3 = int(min(100, s3 + 15))
        elif inst_pct < 30:
            s3 = int(max(0, s3 - 15))
        scores["機関保有"] = s3
        details["機関保有"] = {
            "score": s3,
            "value": f"{inst_pct:.1f}%",
            "raw":   inst_pct,
            "good":  inst_pct >= 60,
            "bad":   inst_pct < 30,
            "icon":  "🟢" if inst_pct >= 60 else "🔴" if inst_pct < 30 else "🟡",
            "label": "高水準（機関の信頼厚い）" if inst_pct >= 60 else
                     "低水準" if inst_pct < 30 else "中程度",
        }

        # ④ インサイダー売買バランス
        ins_df = tk.insider_transactions
        buy_cnt = sell_cnt = 0
        buy_val = sell_val = 0
        if ins_df is not None and not ins_df.empty:
            for _, row in ins_df.head(10).iterrows():
                txn = str(row.get("Transaction", row.get("Text", ""))).lower()
                val = float(row.get("Value", 0)) if pd.notna(row.get("Value", 0)) else 0
                if any(k in txn for k in ["purchase", "buy", "acquisition"]):
                    buy_cnt += 1; buy_val += val
                elif any(k in txn for k in ["sale", "sell"]):
                    sell_cnt += 1; sell_val += val
        total_ins = buy_cnt + sell_cnt
        if total_ins > 0:
            buy_ratio = buy_cnt / total_ins
            s4 = int(buy_ratio * 100)
        else:
            s4 = 50
        scores["インサイダー売買"] = s4
        details["インサイダー売買"] = {
            "score": s4,
            "value": f"買{buy_cnt}件 / 売{sell_cnt}件",
            "raw":   buy_cnt - sell_cnt,
            "good":  buy_cnt > sell_cnt,
            "bad":   sell_cnt > buy_cnt * 2,
            "icon":  "🟢" if buy_cnt > sell_cnt else "🔴" if sell_cnt > buy_cnt * 2 else "🟡",
            "label": "買い越し（経営者強気）" if buy_cnt > sell_cnt else
                     "売り大幅超過" if sell_cnt > buy_cnt * 2 else "中立",
        }

        # ⑤ 出来高の質（上昇日の出来高 vs 下落日）
        hist = tk.history(period="3mo", interval="1d", auto_adjust=True)
        s5 = 50
        vol_label = "データなし"
        if not hist.empty and len(hist) >= 20:
            hist = hist.copy()
            hist["up"] = hist["Close"] > hist["Close"].shift(1)
            up_vol   = hist[hist["up"]]["Volume"].mean()
            down_vol = hist[~hist["up"]]["Volume"].mean()
            if down_vol > 0:
                vol_ratio = up_vol / down_vol
                # 1.2以上=買い圧力強 0.8未満=売り圧力強
                s5 = int(min(100, max(0, (vol_ratio - 0.5) / 1.0 * 100)))
                vol_label = (
                    f"上昇日{vol_ratio:.2f}x（買い優勢）" if vol_ratio > 1.2 else
                    f"下落日優勢（{vol_ratio:.2f}x）" if vol_ratio < 0.8 else
                    f"中立（{vol_ratio:.2f}x）"
                )
        scores["出来高の質"] = s5
        details["出来高の質"] = {
            "score": s5,
            "value": vol_label,
            "raw":   s5,
            "good":  s5 >= 60,
            "bad":   s5 <= 35,
            "icon":  "🟢" if s5 >= 60 else "🔴" if s5 <= 35 else "🟡",
            "label": vol_label,
        }

        # 総合スコア（加重平均）
        weights = {
            "空売り残高変化": 0.25,
            "空売り比率":     0.20,
            "機関保有":       0.20,
            "インサイダー売買": 0.20,
            "出来高の質":     0.15,
        }
        total = sum(scores[k] * weights[k] for k in scores)
        total_score = int(total)

        return {
            "ok":           True,
            "total_score":  total_score,
            "scores":       scores,
            "details":      details,
        }

    except Exception as e:
        logger.debug(f"[supply_score] {symbol}: {e}")
        return {"ok": False}


def render_us_supply_score(symbol: str, color: str):
    """米国株 需給改善スコアUI"""
    info = WATCHLIST_STOCKS.get(symbol, {})
    name = info.get("name", symbol)
    flag = info.get("flag", "🇺🇸")

    st.markdown("---")
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:4px;">'
        f'<span style="font-size:18px;font-weight:700;">📡 需給改善スコア</span>'
        f'<span style="background:{color};color:white;font-size:12px;'
        f'padding:3px 10px;border-radius:12px;font-weight:600;">'
        f'{flag} {symbol} — {name}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )
    st.caption("5指標から需給が改善方向にあるかを0〜100でスコアリング")

    col_sc1, col_sc2 = st.columns([3, 1])
    with col_sc2:
        if st.button("🔄 更新", key=f"supply_score_refresh_{symbol}",
                     width="stretch"):
            calc_us_supply_demand_score.clear()
            st.rerun()

    with st.spinner("需給スコア計算中..."):
        result = calc_us_supply_demand_score(symbol)

    if not result.get("ok"):
        st.info("需給スコアを計算できませんでした")
        return

    score   = result["total_score"]
    details = result["details"]

    # ── 総合スコア表示 ────────────────────────────
    score_color = (
        "#16a34a" if score >= 65 else
        "#dc2626" if score <= 35 else
        "#d97706"
    )
    score_label = (
        "🟢 需給改善傾向" if score >= 65 else
        "🔴 需給悪化傾向" if score <= 35 else
        "🟡 中立・様子見"
    )
    trend_desc = (
        "売り方が撤退しスマートマネーが買い集めている可能性があります。"
        if score >= 65 else
        "売り圧力が強く、需給の改善にはもう少し時間がかかりそうです。"
        if score <= 35 else
        "需給は中立圏。特定方向への傾きは見られません。"
    )

    st.markdown(
        f'<div style="background:linear-gradient(135deg,#f8fafc,#f0f9ff);'
        f'border:2px solid {score_color}40;border-radius:12px;'
        f'padding:16px 20px;margin-bottom:16px;">'
        f'<div style="display:flex;align-items:center;gap:16px;">'
        f'<div style="text-align:center;">'
        f'<div style="font-size:48px;font-weight:900;color:{score_color};">'
        f'{score}</div>'
        f'<div style="font-size:11px;color:#64748b;">/ 100</div>'
        f'</div>'
        f'<div style="flex:1;">'
        f'<div style="font-size:18px;font-weight:700;">{score_label}</div>'
        f'<div style="background:#e2e8f0;border-radius:6px;height:10px;margin:8px 0;">'
        f'<div style="background:{score_color};height:100%;border-radius:6px;'
        f'width:{score}%;transition:width 0.5s;"></div></div>'
        f'<div style="font-size:12px;color:#64748b;">{trend_desc}</div>'
        f'</div></div></div>',
        unsafe_allow_html=True,
    )

    # ── 指標別詳細 ────────────────────────────────
    st.markdown("**📊 指標別スコア内訳**")

    WEIGHT_LABELS = {
        "空売り残高変化":   "25%",
        "空売り比率":       "20%",
        "機関保有":         "20%",
        "インサイダー売買": "20%",
        "出来高の質":       "15%",
    }

    for key, det in details.items():
        s        = det["score"]
        bar_color = (
            "#16a34a" if s >= 65 else
            "#dc2626" if s <= 35 else
            "#d97706"
        )
        st.markdown(
            f'<div style="background:#fff;border:1px solid #e5e7eb;'
            f'border-radius:8px;padding:10px 14px;margin-bottom:6px;">'
            f'<div style="display:flex;align-items:center;gap:8px;">'
            f'<span style="font-size:16px;">{det["icon"]}</span>'
            f'<span style="font-size:13px;font-weight:600;flex:1;">{key}</span>'
            f'<span style="font-size:11px;color:#94a3b8;">重み{WEIGHT_LABELS.get(key,"")}</span>'
            f'<span style="font-size:14px;font-weight:800;color:{bar_color};">'
            f'{s}</span>'
            f'</div>'
            f'<div style="background:#f1f5f9;border-radius:3px;height:5px;margin:5px 0;">'
            f'<div style="background:{bar_color};height:100%;border-radius:3px;'
            f'width:{s}%;"></div></div>'
            f'<div style="font-size:11px;color:#64748b;">'
            f'{det["value"]} — {det["label"]}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    st.caption(
        "⚠️ スコアは参考情報です。過去の需給状況を元にした統計的指標であり、"
        "将来の株価を保証するものではありません。投資判断はご自身でお願いします。"
    )


def fetch_margin_data_jquants(code: str) -> Dict:
    """
    J-Quantsから信用残・売買動向を取得。
    code: 4桁または5桁（例: "5803" or "5803.T" → "5803"）
    """
    try:
        # codeを正規化（5803.T → 5803）
        code_clean = code.replace(".T", "").replace(".t", "")

        jquants_key = get_env_var("JQUANTS_API_KEY", "")
        if not jquants_key:
            return {"ok": False, "reason": "JQUANTS_API_KEY未設定"}

        # ① リフレッシュトークン取得
        r1 = requests.post(
            "https://api.jquants.com/v1/token/auth_user",
            json={"mailaddress": "", "password": ""},  # APIキー直接利用
            timeout=10,
        )
        # J-QuantsはAPIキーをそのまま使う方式
        headers = {"Authorization": f"Bearer {jquants_key}"}

        # ② 信用残データ取得（週次）
        import datetime as dt
        today = dt.date.today()
        from_date = (today - dt.timedelta(days=30)).strftime("%Y%m%d")
        to_date   = today.strftime("%Y%m%d")

        r_margin = requests.get(
            f"https://api.jquants.com/v1/markets/weekly_margin_interest",
            params={"code": code_clean, "from": from_date, "to": to_date},
            headers=headers,
            timeout=10,
        )

        # ③ 売買内訳データ（日次）
        r_trade = requests.get(
            f"https://api.jquants.com/v1/markets/trades_spec",
            params={"code": code_clean, "from": from_date, "to": to_date},
            headers=headers,
            timeout=10,
        )

        result = {"ok": True, "margin": {}, "trade": {}}

        if r_margin.status_code == 200:
            data = r_margin.json().get("weekly_margin_interest", [])
            if data:
                latest = data[-1]
                result["margin"] = {
                    "date":          latest.get("Date", ""),
                    "long_balance":  latest.get("LongMarginTradeVolume", 0),     # 信用買残
                    "short_balance": latest.get("ShortMarginTradeVolume", 0),    # 信用売残
                    "long_new":      latest.get("LongNewMarginTradeVolume", 0),  # 新規買
                    "long_repay":    latest.get("LongRepayMarginTradeVolume", 0),# 返済売
                    "short_new":     latest.get("ShortNewMarginTradeVolume", 0), # 新規売
                    "short_repay":   latest.get("ShortRepayMarginTradeVolume", 0),# 返済買
                    "ratio":         latest.get("RatioOfMarginBalance", 0),       # 信用倍率
                }
                # 前週比
                if len(data) >= 2:
                    prev = data[-2]
                    result["margin"]["long_diff"]  = (
                        result["margin"]["long_balance"]  - prev.get("LongMarginTradeVolume", 0)
                    )
                    result["margin"]["short_diff"] = (
                        result["margin"]["short_balance"] - prev.get("ShortMarginTradeVolume", 0)
                    )

        if r_trade.status_code == 200:
            data = r_trade.json().get("trades_spec", [])
            if data:
                latest = data[-1]
                result["trade"] = {
                    "date":          latest.get("PublishedDate", ""),
                    "spot_buy":      latest.get("ProprietaryBuying", 0),   # 現物買
                    "spot_sell":     latest.get("ProprietarySelling", 0),  # 現物売
                    "short_sell":    latest.get("ProprietaryShortSelling", 0), # 空売り
                }

        return result

    except Exception as e:
        logger.debug(f"[jquants_margin] {code}: {e}")
        return {"ok": False, "reason": str(e)}


def _bar_html(label: str, value: float, max_val: float,
              color: str, sub_label: str = "") -> str:
    """横棒グラフHTML（kabuステーション風）"""
    pct = min((value / max_val * 100) if max_val > 0 else 0, 100)
    val_str = f"{value/1000:,.1f}千株" if value >= 1000 else f"{value:,.0f}株"
    sub_html = (
        f'<div style="font-size:10px;color:#94a3b8;">{sub_label}</div>'
        if sub_label else ""
    )
    return (
        f'<div style="margin-bottom:6px;">'
        f'<div style="display:flex;justify-content:space-between;'
        f'font-size:12px;color:#374151;margin-bottom:2px;">'
        f'<span>{label}</span>'
        f'<span style="font-weight:700;">{val_str}</span></div>'
        f'<div style="background:#f1f5f9;border-radius:3px;height:14px;">'
        f'<div style="background:{color};height:100%;border-radius:3px;'
        f'width:{pct:.1f}%;transition:width 0.3s;"></div></div>'
        f'{sub_html}'
        f'</div>'
    )


# 監視銘柄リスト
WATCHLIST_STOCKS = {
    "COHR":   {"name": "Coherent",               "sector": "光通信", "flag": "🇺🇸"},
    "LITE":   {"name": "Lumentum",               "sector": "光通信", "flag": "🇺🇸"},
    "GLW":    {"name": "Corning",                "sector": "光通信", "flag": "🇺🇸"},
    "AAOI":   {"name": "Applied Optoelectronics","sector": "光通信", "flag": "🇺🇸"},
    "CIEN":   {"name": "Ciena",                  "sector": "光通信", "flag": "🇺🇸"},
    "NVDA":   {"name": "NVIDIA",                 "sector": "半導体", "flag": "🇺🇸"},
    "AVGO":   {"name": "Broadcom",               "sector": "半導体", "flag": "🇺🇸"},
    "AMAT":   {"name": "Applied Materials",      "sector": "半導体", "flag": "🇺🇸"},
    "TSM":    {"name": "TSMC",                   "sector": "半導体", "flag": "🇺🇸"},
    "MU":     {"name": "Micron",                 "sector": "半導体", "flag": "🇺🇸"},
    "MSFT":   {"name": "Microsoft",              "sector": "AI",     "flag": "🇺🇸"},
    "GOOGL":  {"name": "Alphabet",               "sector": "AI",     "flag": "🇺🇸"},
    "META":   {"name": "Meta",                   "sector": "AI",     "flag": "🇺🇸"},
    "VRT":    {"name": "Vertiv",                 "sector": "電力インフラ", "flag": "🇺🇸"},
    "VST":    {"name": "Vistra",                 "sector": "電力インフラ", "flag": "🇺🇸"},
    "5803.T": {"name": "フジクラ",               "sector": "光通信", "flag": "🇯🇵"},
    "5901.T": {"name": "住友電工",               "sector": "光通信", "flag": "🇯🇵"},
    "6857.T": {"name": "アドバンテスト",         "sector": "半導体", "flag": "🇯🇵"},
    "8035.T": {"name": "東京エレクトロン",       "sector": "半導体", "flag": "🇯🇵"},
    "4063.T": {"name": "東京応化工業",           "sector": "半導体", "flag": "🇯🇵"},
}

SECTOR_COLORS = {
    "光通信":     "#0284c7",
    "半導体":     "#7c3aed",
    "AI":         "#059669",
    "電力インフラ": "#d97706",
}


@st.cache_data(ttl=60 * 60 * 3, show_spinner=False)
def fetch_stock_details(symbol: str) -> Dict:
    try:
        tk   = yf.Ticker(symbol)
        info = tk.info or {}
        hist = tk.history(period="3mo", interval="1d", auto_adjust=True)
        price   = float(info.get("currentPrice") or info.get("regularMarketPrice") or 0)
        prev    = float(info.get("previousClose") or price)
        chg_pct = (price / prev - 1) * 100 if prev else 0
        hi52    = float(info.get("fiftyTwoWeekHigh") or 0)
        lo52    = float(info.get("fiftyTwoWeekLow")  or 0)
        pos52   = ((price - lo52) / (hi52 - lo52) * 100) if (hi52 - lo52) > 0 else 50
        per     = info.get("trailingPE")   or info.get("forwardPE")
        pbr     = info.get("priceToBook")
        psr     = info.get("priceToSalesTrailing12Months")
        ev_eb   = info.get("enterpriseToEbitda")
        mktcap  = info.get("marketCap", 0)
        vol     = info.get("volume", 0)
        avg_vol = info.get("averageVolume", 1) or 1
        vol_ratio   = vol / avg_vol if avg_vol else 1
        short_pct   = info.get("shortPercentOfFloat") or 0
        inst_pct    = info.get("heldPercentInstitutions") or 0
        insider_pct = info.get("heldPercentInsiders") or 0
        target      = info.get("targetMeanPrice") or 0
        upside      = (target / price - 1) * 100 if price and target else 0
        recommend   = info.get("recommendationMean") or 0
        n_analyst   = info.get("numberOfAnalystOpinions") or 0
        rsi = 50.0
        if not hist.empty and len(hist) >= 15:
            rsi = _calc_rsi(hist["Close"])
        ma50_dev = ma200_dev = 0.0
        if not hist.empty and price:
            c = hist["Close"]
            if len(c) >= 50:
                ma50_dev  = (price / c.tail(50).mean() - 1) * 100
            if len(c) >= 60:
                ma200_dev = (price / c.tail(60).mean() - 1) * 100
        return {
            "ok": True, "price": price, "chg_pct": chg_pct,
            "hi52": hi52, "lo52": lo52, "pos52": pos52,
            "per": per, "pbr": pbr, "psr": psr, "ev_eb": ev_eb, "mktcap": mktcap,
            "vol_ratio": vol_ratio, "short_pct": short_pct * 100,
            "inst_pct": inst_pct * 100, "insider_pct": insider_pct * 100,
            "target": target, "upside": upside,
            "recommend": recommend, "n_analyst": n_analyst,
            "rsi": rsi, "ma50_dev": ma50_dev, "ma200_dev": ma200_dev,
        }
    except Exception as e:
        logger.debug(f"[stock_detail] {symbol}: {e}")
        return {"ok": False}


@st.cache_data(ttl=60 * 60 * 24, show_spinner=False)
def ai_stock_evaluation(symbol: str, name: str, sector: str, data: str, quarter: str, lang: str = "ja") -> tuple:
    _lang_suffix = "\n\nIMPORTANT: Respond entirely in English. Do not use Japanese." if lang == "en" else ""
    if lang == "en":
        prompt = f"""You are an AI infrastructure investment analyst.
Analyze the following stock data and provide a concise evaluation for investors.

Stock: {symbol} ({name}) / Sector: {sector}
Period: {quarter}

[Supply/Demand & Valuation Data]
{data}

Summarize in 100 words or fewer using this format:
[Supply/Demand] Balance from volume, short interest, institutional holdings
[Valuation] P/E, P/B, P/S — overvalued or undervalued?
[Overall Rating] ⭐1–5 with a one-line key takeaway

*For informational purposes only. Not investment advice.*"""
    else:
        prompt = f"""あなたはAIインフラ投資専門のアナリストです。
以下の銘柄データを分析し、日本の投資家向けに日本語で総合評価してください。

銘柄: {symbol} ({name}) / セクター: {sector}
分析期間: {quarter}

【需給・バリュエーションデータ】
{data}

以下の形式で200字以内でまとめてください：
【需給評価】出来高・空売り・機関保有から見た需給バランス
【バリュエーション】PER/PBR/PSR等の割高・割安感
【総合評価】⭐1〜5で評価し、注目ポイントを一言で

※情報提供目的のみ。投資助言ではありません。"""
    try:
        comment, model = call_ai_with_fallback(prompt, max_output_tokens=350, temperature=0.3)
        return comment, model
    except Exception as e:
        return f"AI generation error: {e}" if lang == "en" else f"AI生成エラー: {e}", ""





def render_stock_screener():
    """銘柄需給・バリュエーション評価セクション"""
    st.header("🔍 銘柄 需給・バリュエーション評価")
    st.markdown(
        '<div style="background:linear-gradient(135deg,#f8fafc,#f1f5f9);'
        'border-left:4px solid #475569;border-radius:6px;padding:10px 16px;'
        'font-size:13px;color:#333;margin-bottom:14px;">'
        "監視銘柄の需給（出来高・空売り比率・機関保有）と"
        "バリュエーション（PER・PBR・PSR）をAIが総合評価します。"
        "</div>",
        unsafe_allow_html=True,
    )
    now     = datetime.now(JST)
    quarter = f"{now.year}-Q{(now.month-1)//3+1}"

    sectors = ["全て"] + sorted(set(v["sector"] for v in WATCHLIST_STOCKS.values()))
    col_f1, col_f2 = st.columns([2, 1])
    with col_f1:
        sel_sector = st.selectbox("セクターで絞り込み", sectors, key="screener_sector")
    with col_f2:
        sel_symbol = st.selectbox(
            "銘柄を選択",
            [s for s, v in WATCHLIST_STOCKS.items()
             if sel_sector == "全て" or v["sector"] == sel_sector],
            format_func=lambda s: f"{WATCHLIST_STOCKS[s]['flag']} {s} — {WATCHLIST_STOCKS[s]['name']}",
            key="screener_symbol",
        )

    info  = WATCHLIST_STOCKS[sel_symbol]
    color = SECTOR_COLORS.get(info["sector"], "#475569")

    # 一覧サマリー
    with st.expander("📊 セクター内 全銘柄サマリー", expanded=False):
        target_syms = [s for s, v in WATCHLIST_STOCKS.items()
                       if sel_sector == "全て" or v["sector"] == sel_sector]
        summary_rows = []
        prog = st.progress(0)
        for i, sym in enumerate(target_syms):
            d2 = fetch_stock_details(sym)
            inf2 = WATCHLIST_STOCKS[sym]
            if d2["ok"]:
                rec_str = {1:"強い買い",2:"買い",3:"中立",4:"売り",5:"強い売り"}.get(
                    round(d2["recommend"]), "—") if d2["recommend"] else "—"
                summary_rows.append({
                    "銘柄": f"{inf2['flag']} {sym}", "名称": inf2["name"],
                    "価格": f"{d2['price']:,.2f}", "前日比": f"{d2['chg_pct']:+.1f}%",
                    "RSI": f"{d2['rsi']:.0f}", "52週位置": f"{d2['pos52']:.0f}%",
                    "空売り%": f"{d2['short_pct']:.1f}%" if d2['short_pct'] else "—",
                    "PER": f"{d2['per']:.1f}" if d2['per'] else "—",
                    "PSR": f"{d2['psr']:.1f}" if d2['psr'] else "—",
                    "目標↑": f"{d2['upside']:+.0f}%" if d2['target'] else "—",
                    "推奨": rec_str,
                })
            prog.progress((i + 1) / len(target_syms))
        prog.empty()
        if summary_rows:
            df_sum = pd.DataFrame(summary_rows)
            def _color_chg(val):
                if isinstance(val, str) and val.startswith("+"): return "color:#16a34a;font-weight:700"
                if isinstance(val, str) and val.startswith("-"): return "color:#dc2626;font-weight:700"
                return ""
            st.dataframe(df_sum.style.map(_color_chg, subset=["前日比","目標↑"]),
                         width="stretch", hide_index=True)

    st.markdown("---")
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;">'
        f'<span style="background:{color};color:white;font-size:13px;'
        f'padding:3px 10px;border-radius:12px;">{info["sector"]}</span>'
        f'<span style="font-size:20px;font-weight:800;">{info["flag"]} {sel_symbol}</span>'
        f'<span style="font-size:16px;color:#64748b;">{info["name"]}</span></div>',
        unsafe_allow_html=True,
    )

    with st.spinner(f"{sel_symbol} のデータ取得中..."):
        d = fetch_stock_details(sel_symbol)

    if not d["ok"]:
        st.warning("データを取得できませんでした")
        return

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("現在値",    f"{d['price']:,.2f}", f"{d['chg_pct']:+.2f}%")
    col2.metric("52週位置",  f"{d['pos52']:.0f}%", help="52週高値・安値レンジ内での現在位置")
    col3.metric("RSI(14)",   f"{d['rsi']:.1f}",    help="70超=買われ過ぎ / 30未=売られ過ぎ")
    col4.metric("出来高比率", f"{d['vol_ratio']:.2f}x", help="直近出来高÷平均出来高")

    tab_supply, tab_valuation, tab_ai = st.tabs(["📊 需給分析", "💰 バリュエーション", "🤖 AI総合評価"])

    with tab_supply:
        # 日本株の場合はJ-Quantsで信用残データを取得
        is_jp = sel_symbol.endswith(".T")
        mg    = {}
        tr    = {}

        if is_jp:
            with st.spinner("信用残データを取得中..."):
                mg_data = fetch_margin_data_jquants(sel_symbol)
            if mg_data.get("ok"):
                mg = mg_data.get("margin", {})
                tr = mg_data.get("trade",  {})
            else:
                st.caption(f"⚠️ J-Quants: {mg_data.get('reason','取得失敗')}")

        # ── 現物売買動向 ──────────────────────────────
        if tr:
            spot_buy  = tr.get("spot_buy",   0)
            spot_sell = tr.get("spot_sell",  0)
            short_sell= tr.get("short_sell", 0)
            spot_net  = spot_buy - spot_sell
            net_label = "買越し" if spot_net > 0 else "売越し"
            net_color = "#ef4444" if spot_net > 0 else "#3b82f6"
            max_spot  = max(spot_buy, spot_sell + short_sell, 1)
            date_str  = tr.get("date", "")

            st.markdown(
                f'<div style="background:#fff;border:1px solid #e5e7eb;'
                f'border-radius:10px;padding:14px 18px;margin-bottom:12px;">'
                f'<div style="display:flex;justify-content:space-between;align-items:center;">'
                f'<span style="font-size:14px;font-weight:700;">現物　'
                f'<span style="font-size:11px;color:#94a3b8;">{date_str}</span></span>'
                f'<span style="background:{net_color};color:white;font-size:13px;font-weight:700;'
                f'padding:4px 14px;border-radius:20px;">'
                f'{abs(spot_net)/1000:,.0f}千株 {net_label}</span>'
                f'</div><div style="margin-top:10px;">'
                + _bar_html("現物買", spot_buy,  max_spot, "#ef4444")
                + _bar_html("現物売", spot_sell, max_spot, "#93c5fd",
                            sub_label=f"空売り {short_sell/1000:,.1f}千株含む")
                + f'</div></div>',
                unsafe_allow_html=True,
            )

        # ── 信用買残 ────────────────────────────────
        if mg:
            long_bal   = mg.get("long_balance", 0)
            long_new   = mg.get("long_new",     0)
            long_repay = mg.get("long_repay",   0)
            long_diff  = mg.get("long_diff",    0)
            diff_label = "買残増" if long_diff >= 0 else "買残減"
            diff_color = "#ef4444" if long_diff >= 0 else "#3b82f6"
            max_long   = max(long_new, long_repay, 1)

            st.markdown(
                f'<div style="background:#fff0f0;border:1px solid #fecaca;'
                f'border-radius:10px;padding:14px 18px;margin-bottom:12px;">'
                f'<div style="display:flex;justify-content:space-between;align-items:center;">'
                f'<span style="font-size:14px;font-weight:700;color:#dc2626;">信用買残（週次）</span>'
                f'<span style="background:{diff_color};color:white;font-size:13px;font-weight:700;'
                f'padding:4px 14px;border-radius:20px;">'
                f'{abs(long_diff)/1000:,.0f}千株 {diff_label}</span>'
                f'</div>'
                f'<div style="font-size:22px;font-weight:800;color:#dc2626;margin:6px 0;">'
                f'{long_bal/1000:,.1f}千株</div>'
                f'<div style="margin-top:6px;">'
                + _bar_html("新規買", long_new,   max_long, "#ef4444")
                + _bar_html("返済売", long_repay, max_long, "#93c5fd")
                + f'</div></div>',
                unsafe_allow_html=True,
            )

            # 信用売残
            short_bal   = mg.get("short_balance", 0)
            short_new   = mg.get("short_new",     0)
            short_repay = mg.get("short_repay",   0)
            short_diff  = mg.get("short_diff",    0)
            sdiff_label = "売残増" if short_diff >= 0 else "売残減"
            sdiff_color = "#3b82f6" if short_diff >= 0 else "#16a34a"
            max_short   = max(short_new, short_repay, 1)

            st.markdown(
                f'<div style="background:#f0fdf4;border:1px solid #bbf7d0;'
                f'border-radius:10px;padding:14px 18px;margin-bottom:12px;">'
                f'<div style="display:flex;justify-content:space-between;align-items:center;">'
                f'<span style="font-size:14px;font-weight:700;color:#16a34a;">信用売残（週次）</span>'
                f'<span style="background:{sdiff_color};color:white;font-size:13px;font-weight:700;'
                f'padding:4px 14px;border-radius:20px;">'
                f'{abs(short_diff)/1000:,.0f}千株 {sdiff_label}</span>'
                f'</div>'
                f'<div style="font-size:22px;font-weight:800;color:#16a34a;margin:6px 0;">'
                f'{short_bal/1000:,.1f}千株</div>'
                f'<div style="margin-top:6px;">'
                + _bar_html("新規売", short_new,   max_short, "#3b82f6")
                + _bar_html("返済買", short_repay, max_short, "#6ee7b7")
                + f'</div></div>',
                unsafe_allow_html=True,
            )

            # 信用倍率バッジ
            ratio = mg.get("ratio", 0)
            ratio_color = (
                "#dc2626" if ratio > 5 else
                "#d97706" if ratio > 2 else
                "#16a34a"
            )
            ratio_comment = (
                "⚠️ 高倍率（買い方優勢・踏み上げリスクあり）" if ratio > 5 else
                "🟡 やや買い方優勢" if ratio > 2 else
                "🟢 需給バランス良好"
            )
            st.markdown(
                f'<div style="display:flex;align-items:center;gap:12px;'
                f'background:#f8fafc;border-radius:8px;padding:12px 16px;">'
                f'<div style="font-size:13px;color:#374151;">信用倍率</div>'
                f'<div style="font-size:26px;font-weight:800;color:{ratio_color};">'
                f'{ratio:.2f}倍</div>'
                f'<div style="font-size:12px;color:#64748b;">{ratio_comment}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

        elif not is_jp:
            # 米国株はyfinanceのデータで表示
            c1, c2 = st.columns(2)
            with c1:
                st.markdown("**需給指標**")
                for label, val, tip, warn in [
                    ("空売り比率",      f"{d['short_pct']:.1f}%", "高い→売り圧力", d['short_pct'] > 10),
                    ("機関投資家保有",  f"{d['inst_pct']:.1f}%",  "高い→機関の信頼", d['inst_pct'] < 30),
                    ("インサイダー保有",f"{d['insider_pct']:.1f}%","高い→経営者保有中", False),
                    ("50日MA乖離",     f"{d['ma50_dev']:+.1f}%", "プラス=MA上方", d['ma50_dev'] < -10),
                ]:
                    icon = "⚠️" if warn else "✅"
                    st.markdown(
                        f'<div style="background:#f8fafc;border-radius:6px;'
                        f'padding:8px 12px;margin-bottom:6px;">'
                        f'<span style="font-size:12px;color:#64748b;">{icon} {label}</span><br>'
                        f'<span style="font-size:18px;font-weight:700;">{val}</span><br>'
                        f'<span style="font-size:10px;color:#94a3b8;">{tip}</span></div>',
                        unsafe_allow_html=True,
                    )
            with c2:
                st.markdown("**アナリスト評価**")
                if d["n_analyst"] > 0:
                    rec_label = {1:"💚 強い買い",2:"🟢 買い",3:"⚪ 中立",
                                 4:"🔴 売り",5:"💔 強い売り"}.get(round(d["recommend"]), "—")
                    upside_color = "#16a34a" if d["upside"] > 0 else "#dc2626"
                    st.markdown(
                        f'<div style="background:#f0fdf4;border-radius:8px;'
                        f'padding:14px;text-align:center;">'
                        f'<div style="font-size:22px;">{rec_label}</div>'
                        f'<div style="font-size:12px;color:#64748b;">{d["n_analyst"]}人のアナリスト</div>'
                        f'<div style="font-size:20px;font-weight:800;color:{upside_color};margin-top:8px;">'
                        f'目標株価 {d["upside"]:+.0f}%</div>'
                        f'<div style="font-size:12px;color:#64748b;">目標: {d["target"]:,.2f}</div>'
                        f'</div>', unsafe_allow_html=True,
                    )
                else:
                    st.info("アナリストデータなし")

        # 52週レンジ（共通）
        st.markdown(
            f'<div style="background:#f8fafc;border-radius:8px;padding:12px 16px;margin-top:8px;">'
            f'<div style="font-size:13px;font-weight:600;margin-bottom:6px;">📏 52週レンジ</div>'
            f'<div style="display:flex;justify-content:space-between;font-size:11px;color:#64748b;">'
            f'<span>安値 {d["lo52"]:,.1f}</span><span>高値 {d["hi52"]:,.1f}</span></div>'
            f'<div style="background:#e2e8f0;border-radius:4px;height:10px;margin:4px 0;">'
            f'<div style="background:{color};height:100%;border-radius:4px;'
            f'width:{d["pos52"]:.0f}%;"></div></div>'
            f'<div style="font-size:11px;color:#64748b;text-align:center;">'
            f'現在値は52週レンジの {d["pos52"]:.0f}% 位置</div></div>',
            unsafe_allow_html=True,
        )

        # ── 証券会社別空売り残高ランキング（日本株のみ）────
        if is_jp:
            render_short_position_ranking(sel_symbol, color)
        else:
            # 米国株：機関保有・インサイダー
            render_us_supply_demand(sel_symbol, d, color)
            render_us_supply_score(sel_symbol, color)

        # ── AI需給解説 ────────────────────────────────
        st.markdown("---")
        supply_ai_key = f"supply_ai_{sel_symbol}_{quarter}"
        col_sai1, col_sai2 = st.columns([3, 1])
        with col_sai1:
            st.markdown("**🤖 AI需給解説**")
            st.caption("信用残・売買動向・52週位置からAIが需給の雰囲気を解説します")
        with col_sai2:
            run_supply_ai = st.button(
                "🤖 需給を解説",
                key=f"supply_ai_btn_{sel_symbol}",
                type="primary",
                width="stretch",
            )
            if st.button("🗑️ クリア", key=f"supply_ai_clear_{sel_symbol}",
                         width="stretch"):
                st.session_state.pop(supply_ai_key, None)
                st.rerun()

        if run_supply_ai:
            # 需給データを文章化
            supply_text_parts = []
            supply_text_parts.append(
                f"銘柄: {sel_symbol} ({info['name']}) / "
                f"現在値: {d['price']:,.2f} ({d['chg_pct']:+.1f}%)"
            )
            supply_text_parts.append(
                f"52週レンジ位置: {d['pos52']:.0f}% / "
                f"RSI: {d['rsi']:.1f} / 出来高比率: {d['vol_ratio']:.2f}x"
            )
            if mg:
                supply_text_parts.append(
                    f"信用買残: {mg.get('long_balance',0)/1000:,.1f}千株"
                    f"（前週比 {mg.get('long_diff',0)/1000:+,.1f}千株）"
                )
                supply_text_parts.append(
                    f"信用売残: {mg.get('short_balance',0)/1000:,.1f}千株"
                    f"（前週比 {mg.get('short_diff',0)/1000:+,.1f}千株）"
                )
                supply_text_parts.append(
                    f"信用倍率: {mg.get('ratio',0):.2f}倍"
                )
                supply_text_parts.append(
                    f"新規買: {mg.get('long_new',0)/1000:,.1f}千株 / "
                    f"返済売: {mg.get('long_repay',0)/1000:,.1f}千株"
                )
                supply_text_parts.append(
                    f"新規売: {mg.get('short_new',0)/1000:,.1f}千株 / "
                    f"返済買: {mg.get('short_repay',0)/1000:,.1f}千株"
                )
            if tr:
                net = tr.get("spot_buy",0) - tr.get("spot_sell",0)
                supply_text_parts.append(
                    f"現物売買: 買い {tr.get('spot_buy',0)/1000:,.1f}千株 / "
                    f"売り {tr.get('spot_sell',0)/1000:,.1f}千株 "
                    f"（{'買越' if net > 0 else '売越'} {abs(net)/1000:,.1f}千株）"
                )
            if not mg and not tr:
                supply_text_parts.append(
                    f"空売り比率: {d['short_pct']:.1f}% / "
                    f"機関保有: {d['inst_pct']:.1f}% / "
                    f"50日MA乖離: {d['ma50_dev']:+.1f}%"
                )

            supply_data_str = "\n".join(supply_text_parts)
            supply_prompt = f"""あなたは株式需給分析の専門家です。
以下の需給データを見て、個人投資家にもわかりやすい日本語で需給の「雰囲気」を解説してください。

{supply_data_str}

以下の観点で、合計200〜250字程度で自然な文章で解説してください：
・信用買残と信用売残のバランスから見た需給の方向感
・信用倍率の水準（高いと買い方の重荷、低いと踏み上げ余地）
・現物の売買動向から機関や個人の動き
・総じて需給は「強い/やや強い/中立/やや弱い/弱い」のどれか

難しい専門用語は避け、「〜な状況です」という語り口で。
※情報提供目的のみ。投資助言ではありません。"""

            with st.spinner("🤖 需給データを解析中..."):
                comment, model = call_ai_with_fallback(
                    supply_prompt, max_output_tokens=350, temperature=0.4
                )
            st.session_state[supply_ai_key] = (comment, model)

        if supply_ai_key in st.session_state:
            comment, model = st.session_state[supply_ai_key]
            st.markdown(
                f'<div style="background:linear-gradient(135deg,#f0f9ff,#faf5ff);'
                f'border-left:4px solid {color};border-radius:8px;'
                f'padding:14px 18px;font-size:13px;line-height:1.9;margin-top:8px;">'
                f'{comment.replace(chr(10), "<br>")}'
                f'</div>',
                unsafe_allow_html=True,
            )
            if model:
                st.caption(f"🤖 {model} | ⚠️ 情報提供目的のみ")

    with tab_valuation:
        cols_v = st.columns(2)
        for i, (label, val, unit, cheap, expensive, tip) in enumerate([
            ("PER（株価収益率）", d["per"],  "倍", 15, 40, "15倍未満=割安 / 40倍超=割高"),
            ("PBR（株価純資産）", d["pbr"],  "倍",  1,  5, "1倍未満=割安 / 5倍超=割高"),
            ("PSR（株価売上）",   d["psr"],  "倍",  2, 10, "2倍未満=割安 / 10倍超=割高"),
            ("EV/EBITDA",         d["ev_eb"],"倍", 10, 25, "10倍未満=割安 / 25倍超=割高"),
        ]):
            with cols_v[i % 2]:
                if val:
                    judge = ("🟢 割安" if val < cheap else "🔴 割高" if val > expensive else "🟡 適正")
                    st.markdown(
                        f'<div style="background:#f8fafc;border:1px solid #e2e8f0;'
                        f'border-radius:8px;padding:12px;margin-bottom:10px;">'
                        f'<div style="font-size:12px;color:#64748b;">{label}</div>'
                        f'<div style="font-size:26px;font-weight:800;color:{color};">{val:.1f}{unit}</div>'
                        f'<div style="font-size:12px;">{judge}</div>'
                        f'<div style="font-size:10px;color:#94a3b8;">{tip}</div></div>',
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        f'<div style="background:#f8fafc;border:1px solid #e2e8f0;'
                        f'border-radius:8px;padding:12px;margin-bottom:10px;">'
                        f'<div style="font-size:12px;color:#64748b;">{label}</div>'
                        f'<div style="font-size:20px;color:#94a3b8;">データなし</div></div>',
                        unsafe_allow_html=True,
                    )
        mktcap_str = (
            f"{d['mktcap']/1e12:.2f}兆ドル" if d["mktcap"] > 1e12 else
            f"{d['mktcap']/1e9:.0f}十億ドル" if d["mktcap"] > 1e9 else "—"
        ) if d["mktcap"] else "—"
        st.caption(f"時価総額: {mktcap_str}")

    with tab_ai:
        ai_key = f"screener_ai_{sel_symbol}_{quarter}"
        _, col_ai2 = st.columns([3, 1])
        with col_ai2:
            run_ai = st.button("🤖 AI評価を生成", key=f"screener_ai_btn_{sel_symbol}",
                               type="primary", width="stretch")
            if st.button("🗑️ クリア", key=f"screener_ai_clear_{sel_symbol}",
                         width="stretch"):
                st.session_state.pop(ai_key, None)
                ai_stock_evaluation.clear()
                st.rerun()

        if run_ai:
            data_str = (
                f"価格:{d['price']:,.2f} 前日比:{d['chg_pct']:+.1f}% RSI:{d['rsi']:.1f}\n"
                f"52週位置:{d['pos52']:.0f}% 出来高比率:{d['vol_ratio']:.2f}x\n"
                f"空売り:{d['short_pct']:.1f}% 機関保有:{d['inst_pct']:.1f}%\n"
                f"50日MA乖離:{d['ma50_dev']:+.1f}%\n"
                f"PER:{d['per'] or 'N/A'} PBR:{d['pbr'] or 'N/A'} "
                f"PSR:{d['psr'] or 'N/A'} EV/EBITDA:{d['ev_eb'] or 'N/A'}\n"
                f"目標株価乖離:{d['upside']:+.0f}% ({d['n_analyst']}人) 推奨:{d['recommend'] or 'N/A'}"
            )
            with st.spinner(f"🤖 {sel_symbol} をAI評価中..."):
                comment, model = ai_stock_evaluation(
                    sel_symbol, info["name"], info["sector"], data_str, quarter,
                    lang=st.session_state.get("lang", "ja"))
            st.session_state[ai_key] = (comment, model)

        if ai_key in st.session_state:
            comment, model = st.session_state[ai_key]
            for kw in ["【需給評価】", "【バリュエーション】", "【総合評価】"]:
                comment = comment.replace(kw, f"\n**{kw}**")
            st.markdown(
                f'<div style="background:#f8fafc;border:1px solid {color}40;'
                f'border-left:4px solid {color};border-radius:8px;'
                f'padding:16px 20px;font-size:13px;line-height:2.0;">'
                f'{comment.replace(chr(10), "<br>")}</div>',
                unsafe_allow_html=True,
            )
            if model:
                st.caption(f"🤖 {model} | {quarter} | ⚠️ 情報提供目的のみ・投資助言ではありません")
        else:
            st.info("「🤖 AI評価を生成」を押すと総合評価が表示されます")



def render_av_economic_dashboard():
    """Alpha Vantage経済指標をダッシュボード表示（マクロ分析に組み込み）"""
    eco = fetch_av_economic_indicators()
    if not eco:
        return

    st.markdown("#### 📊 Alpha Vantage 経済指標（リアルタイム）")
    cols = st.columns(len(eco))
    for i, (label, v) in enumerate(eco.items()):
        delta_str = f"{v['change']:+.3f}" if v.get("change") is not None else None
        cols[i].metric(
            label,
            f"{v['value']:.2f}",
            delta=delta_str,
            help=f"更新日: {v.get('date', '')}",
        )


def fetch_news_for_research(max_per_feed: int = 8) -> List[str]:
    """
    RSSから最新ニュースのタイトルを収集する。
    光ファイバー（LITE/COHR/GLW/AAOI）・三菱電機・DC・半導体を優先抽出。
    各ニュースにポジティブ/ネガティブのセンチメントタグを付与。
    """
    import urllib.request

    # ── テーマ＆銘柄キーワード定義 ──────────────────────────
    THEME_KEYWORDS = {

        "光ファイバー（海外銘柄）": [
            # Lumentum (LITE)
            "Lumentum", "LITE",
            # Coherent (COHR) 旧II-VI
            "Coherent", "COHR", "II-VI",
            # Corning (GLW)
            "Corning", "GLW", "optical glass",
            # Applied Optoelectronics (AAOI)
            "Applied Optoelectronics", "AAOI",
            # 共通フォトニクスキーワード
            "transceiver", "トランシーバ",
            "optical fiber", "fiber optic", "photonics",
            "DWDM", "400G", "800G", "1.6T",
            "OFC", "光増幅", "光スイッチ",
        ],

        "光ケーブル（国内）": [
            "フジクラ", "Fujikura",
            "住友電工", "住友電気",
            "古河電工", "古河電気",
            "光ファイバ", "光ケーブ", "光通信",
        ],

        "三菱電機・国内電機": [
            # 三菱電機
            "三菱電機", "Mitsubishi Electric",
            # パワー半導体・FA
            "FAシステム", "工場自動化", "インバータ",
            "パワー半導体", "SiC", "IGBT",
            "防衛電子", "宇宙・衛星",
            # 他の国内電機大手
            "日立製作所", "Hitachi",
            "東芝", "Toshiba",
            "富士通", "Fujitsu",
            "NEC", "日本電気",
            "パナソニック",
            "京セラ", "Kyocera",
        ],

        "データセンター電源・冷却": [
            "データセンタ", "data center", "datacenter",
            "液浸冷却", "冷却", "電源装置", "UPS",
            "バーティブ", "Vertiv",
            "イートン", "Eaton",
            "シュナイダー", "Schneider",
            "PDU", "CRAC", "hyperscale",
            "電力消費", "PUE", "グリーンDC",
        ],

        "半導体・AI半導体": [
            "半導体", "NVIDIA", "エヌビディア", "TSMC", "AMD",
            "Broadcom", "ブロードコム", "Intel", "インテル",
            "HBM", "CoWoS", "先端パッケージ", "AI chip",
            "GPU", "NPU", "2nm", "3nm",
            "ラピダス", "Rapidus",
            "東京エレク", "東京エレクトロン",
            "レーザーテック", "アドバンテスト",
            "ルネサス", "Renesas",
        ],

        "AI・クラウドインフラ": [
            "AI投資", "生成AI", "generative AI", "LLM",
            "クラウド投資", "設備投資", "CapEx", "capex",
            "Azure", "AWS", "Google Cloud",
            "超大規模", "hyperscaler",
            "Stargate", "Project Kuiper",
        ],
    }

    # ── センチメント判定キーワード ────────────────────────
    POSITIVE_KW = [
        "増収", "増益", "黒字", "上方修正", "最高益", "過去最高",
        "受注", "獲得", "拡大", "強化", "提携", "新製品", "新契約",
        "好調", "上昇", "急騰", "買い", "増産", "回復",
        "record", "beat", "surge", "rally", "upgrade",
        "partnership", "contract", "expand", "growth", "win",
    ]
    NEGATIVE_KW = [
        "減収", "減益", "赤字", "下方修正", "損失", "リストラ",
        "撤退", "縮小", "警戒", "下落", "急落", "売り",
        "訴訟", "制裁", "禁輸", "規制", "不正", "リコール",
        "miss", "loss", "decline", "cut", "downgrade",
        "tariff", "ban", "sanction", "recall", "layoff",
    ]

    def _sentiment_tag(text: str) -> str:
        t = text.lower()
        pos = sum(1 for kw in POSITIVE_KW if kw.lower() in t)
        neg = sum(1 for kw in NEGATIVE_KW if kw.lower() in t)
        if pos > neg:   return "📈+"
        elif neg > pos: return "📉-"
        return "➡️"

    headlines_priority = []
    headlines_general  = []

    targets = {
        "Yahoo!ニュース（経済）": "https://news.yahoo.co.jp/rss/topics/business.xml",
        "NHK（経済）":           "https://www3.nhk.or.jp/rss/news/cat6.xml",
        "東洋経済":              "https://toyokeizai.net/list/feed/rss",
        "文春オンライン":        "https://bunshun.jp/list/feed/rss",
        "ブルームバーグ":        "https://feeds.bloomberg.com/markets/news.rss",
        "CNBC":                  "https://www.cnbc.com/id/100003114/device/rss/rss.html",
        "CNBC Tech":             "https://www.cnbc.com/id/19854910/device/rss/rss.html",
        "Reuters Tech":          "https://feeds.reuters.com/reuters/technologyNews",
        "Reuters Business":      "https://feeds.reuters.com/reuters/businessNews",
        # ── 一次報道・ファクトチェック ────────────────
        "AP News":               "https://feeds.apnews.com/rss/apf-topnews",
        "BBC World":             "https://feeds.bbci.co.uk/news/world/rss.xml",
        "NPR News":              "https://feeds.npr.org/1001/rss.xml",
        "MarketWatch":           "https://feeds.content.dowjones.io/public/rss/mw_bulletins",
        "FactCheck.org":         "https://www.factcheck.org/feed/",
        "Snopes":                "https://www.snopes.com/feed/",
    }

    for name, url in targets.items():
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0"}
            )
            with urllib.request.urlopen(req, timeout=6) as res:
                raw = res.read()
            feed = feedparser.parse(raw)
            for entry in feed.entries[:max_per_feed * 3]:
                title   = entry.get("title",   "").strip()
                summary = entry.get("summary", "").strip()[:200]
                if not title:
                    continue
                full_text = title + " " + summary

                # テーマキーワードマッチ
                matched_theme = None
                for theme, kws in THEME_KEYWORDS.items():
                    if any(kw.lower() in full_text.lower() for kw in kws):
                        matched_theme = theme
                        break

                stag = _sentiment_tag(full_text)

                if matched_theme:
                    headlines_priority.append(
                        f"[🔦{matched_theme}][{stag}][{name}] {title}"
                    )
                else:
                    if len(headlines_general) < max_per_feed:
                        headlines_general.append(f"[{stag}][{name}] {title}")

        except Exception as e:
            logger.debug(f"[research] RSS取得失敗 {name}: {e}")

    # 重複除去してマージ
    seen = set()
    result = []
    for h in headlines_priority + headlines_general:
        key = h[h.rfind("]")+1:].strip()[:50]
        if key not in seen:
            seen.add(key)
            result.append(h)

    logger.info(
        f"[research] 収集完了: テーマ優先={len(headlines_priority)}件 "
        f"一般={len(headlines_general)}件"
    )
    return result[:60]

def render_market_research_ai():
    """マーケットリサーチAI — ニュース×金利×センチメントからセクター分析"""
    st.header("🔬 マーケットリサーチAI")
    st.markdown(
        '<div style="background:linear-gradient(135deg,#e8f5e9,#e3f2fd);'
        'border-left:4px solid #1976d2;border-radius:6px;padding:10px 16px;'
        'font-size:13px;color:#333;margin-bottom:14px;">'
        "最新ニュース・金利・VIX・為替・AI Sentimentスコア・TDnet決算情報を統合し、"
        "<strong>今注目すべきセクター</strong>をLLMが分析・レポート形式で出力します。"
        "</div>",
        unsafe_allow_html=True,
    )

    col_l, col_r = st.columns([2, 1])
    with col_l:
        market_focus = st.radio(
            "分析対象",
            ["🇯🇵 日本株（TOPIX業種）", "🇺🇸 米国株（S&P500セクター）", "🌐 日米両方"],
            horizontal=True, key="research_market",
        )
    with col_r:
        depth = st.selectbox(
            "レポート深度",
            ["簡潔（箇条書き）", "標準（アナリストレポート）", "詳細（深掘り分析）"],
            index=1, key="research_depth",
        )

    col_btn1, col_btn2 = st.columns([3, 1])
    with col_btn1:
        run_btn = st.button(
            "🔬 AIリサーチを実行",
            type="primary",
            key="research_run_btn",
            width="stretch",
        )
    with col_btn2:
        clear_btn = st.button(
            "🔄 キャッシュクリア",
            key="research_clear_btn",
            width="stretch",
            help="Finnhub/AVのキャッシュをクリアして再取得",
        )
    if clear_btn:
        for fn in [fetch_news_for_research, fetch_av_news_sentiment, fetch_finnhub_company_news]:
            try:
                fn.clear()
            except Exception:
                pass
        for k in ["market_research_result", "market_research_meta", "market_research_model"]:
            st.session_state.pop(k, None)
        st.success("✅ キャッシュクリア完了。再度「AIリサーチを実行」を押してください。")
        st.rerun()

    if not run_btn and "market_research_result" not in st.session_state:
        st.info("「AIリサーチを実行」ボタンを押すと、最新情報を収集してセクター分析を開始します。")
        return

    if run_btn:
        # ── データ収集フェーズ ─────────────────────────────
        progress = st.progress(0, text="📰 最新ニュースを収集中...")

        # ① RSS ニュース収集
        headlines = fetch_news_for_research(max_per_feed=8)
        theme_headlines = [h for h in headlines if h.startswith("[🔦")]
        progress.progress(15, text=f"📰 RSSニュース完了（テーマ関連: {len(theme_headlines)}件）")

        # ①-b Finnhub 銘柄別ニュース
        finnhub_headlines = []
        finnhub_debug_msg = ""
        # APIキーを毎回secrets.tomlから直接取得（起動時キャッシュ問題を回避）
        _finnhub_key = get_env_var("FINNHUB_API_KEY", "")
        if _finnhub_key:
            progress.progress(20, text="🔌 Finnhub 銘柄別ニュース取得中...")
            finnhub_headlines = fetch_finnhub_company_news(days_back=5)
            finnhub_debug_msg = f"✅ {len(finnhub_headlines)}件取得" if finnhub_headlines else "⚠️ 0件（期間内ニュースなし or エラー）"
            progress.progress(30, text=f"🔌 Finnhub完了（{len(finnhub_headlines)}件）")
        else:
            finnhub_debug_msg = "❌ FINNHUB_API_KEY 未検出"
            progress.progress(30, text="⚠️ Finnhub APIキー未設定（スキップ）")

        # 全ニュースをマージ（Finnhubを先頭に）
        all_headlines = finnhub_headlines + headlines

        # ② 市場データ収集
        progress.progress(35, text="📊 市場データを取得中...")
        market_data = {}
        try:
            def _last(sym):
                try:
                    t = yf.Ticker(sym)
                    h = t.history(period="5d", interval="1d")
                    if h is not None and not h.empty:
                        return float(h["Close"].dropna().iloc[-1])
                except Exception:
                    pass
                return None

            market_data = {
                "米10年金利(%)"    : _last("^TNX"),
                "日本10年金利(%)": _last("^JGB10Y") or _last("^TNX"),
                "VIX"            : _last("^VIX"),
                "ドル円"         : _last("USDJPY=X"),
                "S&P500"         : _last("^GSPC"),
                "日経平均"       : _last("^N225"),
                "原油(WTI)"      : _last("CL=F"),
                "ゴールド"       : _last("GC=F"),
                "米国債(TLT)"    : _last("TLT"),
                "HYG(ハイイールド)": _last("HYG"),
            }
        except Exception as e:
            logger.warning(f"[research] 市場データ取得失敗: {e}")

        # ② -b Alpha Vantage 経済指標
        _av_key = get_env_var("ALPHA_VANTAGE_KEY", "")
        av_eco = {}
        if _av_key:
            progress.progress(42, text="📈 Alpha Vantage 経済指標を取得中...")
            av_eco = fetch_av_economic_indicators()
            progress.progress(50, text=f"📈 AV経済指標完了（{len(av_eco)}指標）")
        else:
            progress.progress(50, text="⚠️ Alpha Vantage APIキー未設定（スキップ）")

        progress.progress(53, text="🧠 センチメントスコアを取得中...")

        # ③ AI Sentiment・Fear&Greed
        sentiment_info = {}
        try:
            sent = compute_composite_sentiment()
            if sent.get("ok"):
                sentiment_info["AI Sentiment スコア"] = sent.get("composite", "N/A")
                sentiment_info["AI Sentiment ラベル"] = sent.get("label", "")
                for k, v in sent.get("components", {}).items():
                    sentiment_info[f"  {k}"] = f"{v.get('score', 0):.0f} ({v.get('label', '')})"
        except Exception as e:
            logger.debug(f"[research] sentiment取得失敗: {e}")

        try:
            fg = fetch_fear_greed_index()
            if fg:
                sentiment_info["CNN Fear&Greed スコア"] = fg.get("score", "N/A")
                sentiment_info["CNN Fear&Greed レーティング"] = fg.get("rating", "")
        except Exception as e:
            logger.debug(f"[research] F&G取得失敗: {e}")

        # ③-b Alpha Vantage ニュースセンチメント
        av_sentiment = {"headlines": [], "ticker_scores": {}, "overall_score": None}
        if _av_key:
            progress.progress(60, text="🔍 Alpha Vantage ニュースセンチメント取得中...")
            av_sentiment = fetch_av_news_sentiment(
                tickers="NVDA,COHR,LITE,GLW,AAOI,VRT,AMD,AVGO"
            )
            if av_sentiment.get("overall_score") is not None:
                sentiment_info["AV ニュースセンチメント（全体）"] = (
                    f"{av_sentiment['overall_score']:+.3f}"
                )
            for tk, ts in av_sentiment.get("ticker_scores", {}).items():
                sentiment_info[f"  AV {tk}"] = (
                    f"{ts['score']:+.3f} ({ts['label']}) n={ts['count']}"
                )
            progress.progress(68, text=f"🔍 AVセンチメント完了（{len(av_sentiment['ticker_scores'])}銘柄）")
        else:
            progress.progress(68, text="⚠️ Alpha Vantage APIキー未設定（スキップ）")

        progress.progress(72, text="📄 TDnet決算情報を取得中...")

        # ④ TDnet最新決算ヘッドライン
        tdnet_headlines = []
        try:
            feed = feedparser.parse(
                "https://webapi.yanoshin.jp/webapi/tdnet/list/recent.rss"
            )
            kessan_kw = ["決算", "業績", "配当", "修正", "増益", "減益", "黒字", "赤字"]
            for entry in feed.entries[:50]:
                title = entry.get("title", "")
                if any(kw in title for kw in kessan_kw):
                    tdnet_headlines.append(title)
                if len(tdnet_headlines) >= 15:
                    break
        except Exception as e:
            logger.debug(f"[research] TDnet取得失敗: {e}")

        progress.progress(80, text="🤖 LLMで分析中...")

        # ── プロンプト構築 ─────────────────────────────────
        nl = "\n"

        # 市場データ（AV経済指標があれば追記）
        market_lines = nl.join(
            f"・{k}: {v:.2f}" if isinstance(v, float) else f"・{k}: {v}"
            for k, v in market_data.items() if v is not None
        )
        if av_eco:
            market_lines += "\n\n【Alpha Vantage 経済指標】\n"
            market_lines += nl.join(
                f"・{k}: {v['value']:.2f}% {v['trend']} "
                f"(前回:{v['prev']:.2f}% 変化:{v['change']:+.3f}) [{v['date']}]"
                if v.get("change") is not None else f"・{k}: {v['value']:.2f}%"
                for k, v in av_eco.items()
            )

        sentiment_lines = nl.join(f"・{k}: {v}" for k, v in sentiment_info.items())

        # ニュース（Finnhub銘柄別を先頭に）
        news_lines = (
            nl.join(f"・{h}" for h in all_headlines[:35])
            if all_headlines else "・取得なし"
        )
        # AV センチメントニュースも追加
        if av_sentiment.get("headlines"):
            news_lines += "\n\n【Alpha Vantage センチメントニュース】\n"
            news_lines += nl.join(
                f"・{h}" for h in av_sentiment["headlines"][:10]
            )

        tdnet_lines = nl.join(f"・{h}" for h in tdnet_headlines) if tdnet_headlines else "・取得なし"

        focus_jp = "🇯🇵 日本株（TOPIX業種）" in market_focus
        focus_us = "🇺🇸 米国株（S&P500セクター）" in market_focus

        if focus_jp and not focus_us:
            sector_instruction = """
【分析対象セクター（TOPIX-17業種 + 注目テーマ）】
食品・繊維、化学・素材、医薬品、石油・鉄鋼・非鉄（※光ケーブル含む）、機械、
電気機器・精密、自動車・輸送機器、商社・卸売、小売、
銀行、金融（証券・保険）、不動産、鉄道・バス、陸運・海運・空運・倉庫、
情報通信・サービス、電力・ガス、建設・資材

【特に注目銘柄・テーマセクター】
・光ケーブル（非鉄サブセクター）:
  フジクラ・住友電工・古河電工
  → DC向け光通信需要・AI投資拡大の恩恵を優先分析

・三菱電機（電気機器）:
  FAシステム・パワー半導体(SiC/IGBT)・防衛電子・宇宙衛星
  → ポジティブ/ネガティブ両面のニュースを根拠に評価

・DC電源・冷却（機械・電気機器）:
  hyperscale DC建設ラッシュによる電源・液浸冷却需要

・半導体（電気機器）:
  東京エレク・レーザーテック・ラピダス関連
"""
        elif not focus_jp and focus_us:
            sector_instruction = """
【分析対象セクター（S&P500 11セクター + 注目銘柄）】
情報技術(XLK)、ヘルスケア(XLV)、金融(XLF)、一般消費財(XLY)、
生活必需品(XLP)、エネルギー(XLE)、不動産(XLRE)、公益事業(XLU)、
素材(XLB)、資本財(XLI)、通信サービス(XLC)

【特に注目銘柄・テーマセクター】
・光ファイバー銘柄（素材/情報技術）:
  Lumentum(LITE)・Coherent(COHR)・Corning(GLW)・Applied Optoelectronics(AAOI)
  → 各銘柄のニュース（📈+/📉-）を根拠に個別評価

・DC電源・冷却（資本財）:
  Vertiv(VRT)・Eaton(ETN)・Schneider Electric
  → hyperscale DC需要との関連

・半導体・AI半導体（情報技術）:
  NVIDIA・AMD・Broadcom・Intel・TSMC ADR
  → AI需要の持続性・地政学リスク
"""
        else:
            sector_instruction = """
【分析対象セクター】日本株・米国株の両方を分析してください。

【特に注目銘柄・テーマセクター（日米共通）】
・光ファイバー:
  日本: フジクラ・住友電工・古河電工
  米国: Lumentum(LITE)・Coherent(COHR)・Corning(GLW)・AAOI
  → 収集ニュースの📈+/📉-タグを根拠に各銘柄を個別評価すること

・三菱電機（日本）/ 国内電機大手:
  三菱電機・日立・東芝・富士通・NEC
  → ポジティブ・ネガティブの材料を整理して見通しを示す

・DC電源・冷却:
  日本: 高効率電源・空調関連
  米国: Vertiv・Eaton・Schneider
  → hyperscale DC投資拡大の恩恵

・半導体・AI半導体:
  日本: 東京エレク・レーザーテック・ラピダス関連
  米国: NVIDIA・AMD・Broadcom・TSMC
"""

        depth_instruction = {
            "簡潔（箇条書き）": "箇条書きで簡潔に（各セクター1〜2行）",
            "標準（アナリストレポート）": "アナリストレポート形式で（各セクター3〜5行、根拠を明記）",
            "詳細（深掘り分析）": "詳細な分析で（各セクター5〜8行、マクロ環境・個別要因・リスクまで言及）",
        }[depth]

        prompt = f"""あなたはプロの株式アナリストです。以下のリアルタイム市場情報を総合的に分析し、
投資家向けのセクター分析レポートを日本語で作成してください。

━━━━━━━━━━━━━━━━━━━━━━━━
【現在の市場データ】
{market_lines}

【センチメント指標】
{sentiment_lines}

【最新ニュースヘッドライン】
{news_lines}

【TDnet最新決算情報】
{tdnet_lines}
━━━━━━━━━━━━━━━━━━━━━━━━

{sector_instruction}

【出力形式】({depth_instruction})

以下の構成で必ずレポートを作成してください：

## 📊 マーケット環境サマリー
現在の金利・VIX・為替・センチメントから読み取れる市場環境を2〜3文で要約。

## 🟢 注目セクター（上昇期待）
上昇が期待されるセクターを3〜4個挙げ、それぞれの根拠を記載。
金利・為替・ニュースとの関連を必ず説明すること。

## 🔴 警戒セクター（下落リスク）
下落リスクが高いセクターを2〜3個挙げ、リスク要因を記載。

## 🟡 中立・様子見セクター
明確な方向感がなく様子見が適切なセクターを2〜3個。

## 💡 投資戦略のポイント
現在の市場環境で特に重要な投資判断のポイントを3点箇条書き。

## ⚠️ 注意事項
このレポートは情報提供目的であり、投資助言ではありません。最終的な投資判断は自己責任でお願いします。

※ニュースの具体的な内容を根拠として積極的に引用してください。""" + _lang_prompt_suffix()

        try:
            result, used_model = call_ai_with_fallback(
                prompt,
                max_output_tokens=1800,
                temperature=0.4,
            )
            st.session_state["market_research_result"] = result
            st.session_state["market_research_model"]  = used_model
            st.session_state["market_research_meta"] = {
                "headlines_count":        len(all_headlines),
                "theme_headlines_count":  len(theme_headlines),
                "finnhub_count":          len(finnhub_headlines),
                "finnhub_debug":          finnhub_debug_msg,
                "av_sentiment_count":     len(av_sentiment.get("ticker_scores", {})),
                "av_eco_count":           len(av_eco),
                "tdnet_count":            len(tdnet_headlines),
                "market_data":            market_data,
                "av_eco":                 av_eco,
                "av_ticker_scores":       av_sentiment.get("ticker_scores", {}),
                "timestamp":              datetime.now(JST).strftime("%Y-%m-%d %H:%M JST"),
                "focus":                  market_focus,
                "depth":                  depth,
            }
        except Exception as e:
            st.error(f"❌ AI分析エラー: {e}")
            progress.empty()
            return

        progress.progress(100, text="✅ 分析完了！")
        import time as _time
        _time.sleep(0.5)
        progress.empty()

    # ── レポート表示 ──────────────────────────────────
    if "market_research_result" in st.session_state:
        meta = st.session_state.get("market_research_meta", {})

        # メタ情報バー
        col1, col2, col3, col4, col5, col6 = st.columns(6)
        col1.metric("総ニュース数",           f"{meta.get('headlines_count', 0)}件")
        col2.metric("🔌Finnhub銘柄ニュース",  f"{meta.get('finnhub_count', 0)}件")
        col3.metric("🔦テーマ優先ニュース",   f"{meta.get('theme_headlines_count', 0)}件")
        col4.metric("📊AV感情スコア銘柄数",   f"{meta.get('av_sentiment_count', 0)}銘柄")
        col5.metric("TDnet決算数",            f"{meta.get('tdnet_count', 0)}件")
        col6.metric("更新",                   meta.get("timestamp", "")[:10])

        # Finnhubデバッグ情報
        finnhub_dbg = meta.get("finnhub_debug", "")
        if finnhub_dbg:
            if "✅" in finnhub_dbg:
                st.success(f"🔌 Finnhub: {finnhub_dbg}")
            elif "❌" in finnhub_dbg:
                st.error(f"🔌 Finnhub: {finnhub_dbg} — secrets.tomlの `FINNHUB_API_KEY` を確認してください")
            else:
                st.warning(f"🔌 Finnhub: {finnhub_dbg}")

        # AV銘柄別センチメントスコア表示
        av_scores = meta.get("av_ticker_scores", {})
        if av_scores:
            st.markdown("**📊 Alpha Vantage 銘柄別ニュースセンチメント**")
            score_cols = st.columns(min(len(av_scores), 8))
            for i, (tk, ts) in enumerate(list(av_scores.items())[:8]):
                clr = "normal" if ts["score"] >= 0 else "inverse"
                score_cols[i].metric(
                    tk,
                    ts["label"],
                    f"{ts['score']:+.2f}",
                    delta_color=clr,
                )

        # AV経済指標表示
        av_eco_data = meta.get("av_eco", {})
        if av_eco_data:
            st.markdown("**📈 Alpha Vantage 経済指標**")
            eco_cols = st.columns(min(len(av_eco_data), 5))
            for i, (k, v) in enumerate(list(av_eco_data.items())[:5]):
                delta_str = f"{v['change']:+.3f}" if v.get("change") is not None else None
                eco_cols[i].metric(
                    k, f"{v['value']:.2f}%",
                    delta=delta_str,
                    help=f"更新日: {v.get('date', '')}",
                )

        # レポート本体
        st.markdown(
            f'<div style="background:#fafbff;border:1px solid #e0e7ff;'
            f'border-radius:8px;padding:20px 24px;font-size:14px;'
            f'line-height:1.9;color:#1a1a2e;margin:12px 0;">'
            f'{st.session_state["market_research_result"].replace(chr(10), "<br>")}'
            f'</div>',
            unsafe_allow_html=True,
        )

        model_used = st.session_state.get("market_research_model", "")
        if model_used:
            st.caption(f"🤖 使用AI: {model_used} ｜ データ取得: {meta.get('timestamp', '')}")

        # 使用した市場データを折りたたみ表示
        with st.expander("📊 分析に使用した市場データ", expanded=False):
            md = meta.get("market_data", {})
            if md:
                cols = st.columns(4)
                items = [(k, v) for k, v in md.items() if v is not None]
                for i, (k, v) in enumerate(items):
                    cols[i % 4].metric(k, f"{v:.2f}")

        st.caption("⚠️ 本レポートはAIによる情報提供であり、投資助言ではありません。投資判断は自己責任でお願いします。")



# =====================================================
# ★ 米議員株トレード監視（Congress Stock Tracker）
# =====================================================

# 監視対象議員（STOCK Act開示データ）
CONGRESS_WATCH = {
    "Nancy Pelosi":      {"id": "P000197", "party": "D", "icon": "🔵", "note": "元下院議長・AI株集中"},
    "Dan Crenshaw":      {"id": "C001120", "party": "R", "icon": "🔴", "note": "共和党・防衛・エネルギー"},
    "Michael McCaul":    {"id": "M001157", "party": "R", "icon": "🔴", "note": "外交委員長・テック"},
    "Ro Khanna":         {"id": "K000389", "party": "D", "icon": "🔵", "note": "テック選挙区・半導体"},
    "Mark Green":        {"id": "G000590", "party": "R", "icon": "🔴", "note": "国土安全保障委員長"},
    "Josh Gottheimer":   {"id": "G000583", "party": "D", "icon": "🔵", "note": "金融サービス委員"},
    "Pete Sessions":     {"id": "S000250", "party": "R", "icon": "🔴", "note": "ルール委員会"},
    "David Rouzer":      {"id": "R000603", "party": "R", "icon": "🔴", "note": "農業・エネルギー"},
}

# capitoltrades.com のスクレイピング対象URL
CAPITOLTRADES_BASE = "https://www.capitoltrades.com"


@st.cache_data(ttl=60 * 60 * 6, show_spinner=False)  # 6時間キャッシュ
def fetch_congress_trades_rss(max_items: int = 40) -> List[Dict]:
    """
    複数ソースから議員トレード情報を取得。
    ① capitoltrades.com RSS（最優先）
    ② Beehiiv/QuiverQuant ニュースレターRSS
    ③ housestockwatcher.com（House STOCK Act開示）
    ④ senatestockwatcher.com（Senate STOCK Act開示）
    """
    import urllib.request
    results = []

    # ソース定義（優先順）
    sources = [
        {
            "name": "Capitol Trades",
            "url":  "https://www.capitoltrades.com/rss/trades",
        },
        {
            "name": "QuiverQuant Newsletter",
            "url":  "https://quiverquant.beehiiv.com/feed",
        },
        {
            "name": "House Stock Watcher",
            "url":  "https://housestockwatcher.com/rss",
        },
        {
            "name": "Senate Stock Watcher",
            "url":  "https://senatestockwatcher.com/rss",
        },
    ]

    for src in sources:
        try:
            req = urllib.request.Request(
                src["url"],
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            )
            with urllib.request.urlopen(req, timeout=10) as res:
                raw = res.read()
            feed = feedparser.parse(raw)
            if not feed.entries:
                continue
            for entry in feed.entries[:max_items]:
                title   = entry.get("title",   "").strip()
                summary = entry.get("summary", "").strip()
                link    = entry.get("link",    "")
                pub     = entry.get("published", "")[:16]
                if not title:
                    continue
                # 監視対象議員マッチ
                watched = [
                    name for name in CONGRESS_WATCH
                    if name.split()[-1].lower() in (title + summary).lower()
                ]
                results.append({
                    "title":   title,
                    "summary": summary[:300],
                    "link":    link,
                    "pub":     pub,
                    "watched": watched,
                    "source":  src["name"],
                })
            if results:
                logger.info(f"[congress_rss] {src['name']} から {len(results)}件取得")
                break  # 最初に成功したソースを使用
        except Exception as e:
            logger.debug(f"[congress_rss] {src['name']} 失敗: {e}")
            continue

    # 全RSS失敗時はBeehiivをスクレイピングでフォールバック
    if not results:
        try:
            r = requests.get(
                "https://quiverquant.beehiiv.com/",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10,
            )
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "html.parser")
                for a in soup.find_all("a", href=True)[:30]:
                    title = a.get_text(strip=True)
                    href  = a.get("href", "")
                    if len(title) > 20 and "beehiiv.com" in href:
                        watched = [
                            name for name in CONGRESS_WATCH
                            if name.split()[-1].lower() in title.lower()
                        ]
                        results.append({
                            "title":   title,
                            "summary": "",
                            "link":    href,
                            "pub":     "",
                            "watched": watched,
                            "source":  "QuiverQuant(Web)",
                        })
                logger.info(f"[congress_rss] Beehiivスクレイピング: {len(results)}件")
        except Exception as e:
            logger.debug(f"[congress_rss] Beehiiv scraping 失敗: {e}")

    return results[:max_items]


@st.cache_data(ttl=60 * 60 * 6, show_spinner=False)
def fetch_politician_recent_trades(politician_name: str) -> List[Dict]:
    """
    capitoltrades.com から特定議員の最新トレードをスクレイピング。
    """
    try:
        pid   = CONGRESS_WATCH.get(politician_name, {}).get("id", "")
        url   = f"{CAPITOLTRADES_BASE}/politicians/{pid}"
        r = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            timeout=10,
        )
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        trades = []
        # テーブル行を探す
        rows = soup.select("table tbody tr, .trade-row, [data-trade]")
        for row in rows[:10]:
            cells = row.find_all(["td", "th"])
            if len(cells) >= 3:
                text = " | ".join(c.get_text(strip=True) for c in cells[:5])
                trades.append({"text": text})
        # テーブルが見つからない場合はテキスト抽出
        if not trades:
            for tag in soup.find_all(["p", "li", "div"])[:30]:
                t = tag.get_text(strip=True)
                if any(kw in t for kw in ["$", "call", "put", "purchase", "sale", "stock"]):
                    if len(t) > 20:
                        trades.append({"text": t[:200]})
        return trades[:10]
    except Exception as e:
        logger.debug(f"[congress_trades] {politician_name}: {e}")
        return []


@st.cache_data(ttl=60 * 60 * 24, show_spinner=False)  # 24時間キャッシュ
def ai_congress_analysis(politician_name: str, trades_text: str, quarter: str, lang: str = "ja") -> tuple:
    """AIが議員のトレードを分析してコメントを生成"""
    info = CONGRESS_WATCH.get(politician_name, {})
    if lang == "en":
        prompt = f"""You are a financial analyst monitoring US congressional stock trades.
Analyze the following legislator's recent stock trades and write a concise report for investors.

Legislator: {politician_name} ({info.get('party','?')} Party)
Profile: {info.get('note','')}
Period: {quarter}

[Recent Trades]
{trades_text if trades_text else "No recent disclosures found"}

Summarize in 150–200 words using this format:
[Key Stocks] Main stocks bought/sold and their direction
[Theme] Sectors/themes this legislator is betting on
[Notable Points] Connection between committee role and trades
[Japan Stock Implications] Related Japanese stocks / themes

*Based on public disclosures under the STOCK Act.*"""
    else:
        prompt = f"""あなたは米国議会の株取引を監視する金融アナリストです。
以下の議員の最新株取引情報を分析し、日本の投資家向けに日本語でレポートしてください。

議員: {politician_name}（{info.get('party','?')}党）
特徴: {info.get('note','')}
分析期間: {quarter}

【最新取引情報】
{trades_text if trades_text else "直近の開示情報なし"}

以下の形式で200〜300字でまとめてください：
【注目銘柄】購入・売却した主な銘柄とその方向性
【テーマ】この議員が賭けているセクター・テーマ
【注目ポイント】委員会の所管と取引の関連性など特筆事項
【日本株への示唆】関連する日本株・テーマへの影響

※STOCK Act（議員株取引開示法）に基づく公開情報です。"""

    try:
        comment, model = call_ai_with_fallback(prompt, max_output_tokens=500, temperature=0.4)
        return comment, model
    except Exception as e:
        return f"AI生成エラー: {e}", ""


def render_congress_tracker():
    """米議員株トレード監視セクション"""
    st.header("🏛️ 米議員 株トレード監視")
    st.markdown(
        '<div style="background:linear-gradient(135deg,#fff3e0,#fce4ec);'
        'border-left:4px solid #e65100;border-radius:6px;padding:10px 16px;'
        'font-size:13px;color:#333;margin-bottom:14px;">'
        "STOCK Act（2012年）により米議員は株取引を<strong>45日以内に開示義務</strong>。"
        "委員会の所管と取引の関連性に注目。投資判断の参考情報として活用してください。"
        "</div>",
        unsafe_allow_html=True,
    )

    now = datetime.now(JST)
    quarter = f"{now.year}-Q{(now.month-1)//3+1}"

    tab_feed, tab_politician, tab_ai = st.tabs([
        "📰 最新トレードフィード",
        "👤 議員別ポートフォリオ",
        "🤖 AI分析レポート",
    ])

    # ── ① 最新フィード ──────────────────────────────
    with tab_feed:
        st.markdown("#### 📰 最新議員トレード（QuiverQuant）")
        col_r1, col_r2 = st.columns([3, 1])
        with col_r2:
            if st.button("🔄 更新", key="congress_feed_refresh"):
                fetch_congress_trades_rss.clear()
                st.rerun()
        with st.spinner("最新トレードを取得中..."):
            trades = fetch_congress_trades_rss(max_items=30)

        if not trades:
            st.warning("フィードを取得できませんでした。以下のサイトで直接確認できます。")
            st.markdown(
                "🔗 [Capitol Trades（最新トレード）](https://www.capitoltrades.com/trades) | "
                "[House Stock Watcher](https://housestockwatcher.com) | "
                "[Senate Stock Watcher](https://senatestockwatcher.com) | "
                "[QuiverQuant](https://www.quiverquant.com/congresstrading/)"
            )
        else:
            # 監視対象議員のトレードを先頭に
            watched_trades   = [t for t in trades if t["watched"]]
            unwatched_trades = [t for t in trades if not t["watched"]]

            if watched_trades:
                st.markdown(f"**🚨 監視対象議員のトレード ({len(watched_trades)}件)**")
                for t in watched_trades:
                    names = "・".join(
                        f"{CONGRESS_WATCH[n]['icon']}{n}" for n in t["watched"]
                    )
                    st.markdown(
                        f'<div style="background:#fff3e0;border-left:4px solid #e65100;'
                        f'border-radius:6px;padding:10px 14px;margin-bottom:8px;">'
                        f'<div style="font-weight:700;font-size:13px;">{names}</div>'
                        f'<div style="font-size:13px;margin-top:4px;">'
                        f'<a href="{t["link"]}" target="_blank">{t["title"]}</a></div>'
                        f'<div style="font-size:11px;color:#666;margin-top:4px;">'
                        f'{t["summary"][:150]}</div>'
                        f'<div style="font-size:10px;color:#999;">{t["pub"]}</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                st.divider()

            st.markdown(f"**全トレード ({len(unwatched_trades)}件)**")
            for t in unwatched_trades[:15]:
                st.markdown(
                    f'<div style="border:1px solid #e0e0e0;border-radius:6px;'
                    f'padding:8px 12px;margin-bottom:6px;">'
                    f'<a href="{t["link"]}" target="_blank" style="font-size:13px;">'
                    f'{t["title"]}</a>'
                    f'<div style="font-size:10px;color:#999;">{t["pub"]}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

    # ── ② 議員別ポートフォリオ ───────────────────────
    with tab_politician:
        st.markdown("#### 👤 議員別 最新トレード詳細")
        sel = st.selectbox(
            "議員を選択",
            list(CONGRESS_WATCH.keys()),
            format_func=lambda n: (
                f"{CONGRESS_WATCH[n]['icon']} {n} "
                f"({'民主' if CONGRESS_WATCH[n]['party']=='D' else '共和'}) "
                f"— {CONGRESS_WATCH[n]['note']}"
            ),
            key="congress_politician_sel",
        )

        info = CONGRESS_WATCH[sel]
        pid  = info["id"]

        col_i1, col_i2 = st.columns([3, 1])
        with col_i1:
            st.markdown(
                f"**{info['icon']} {sel}** | "
                f"{'🔵民主党' if info['party']=='D' else '🔴共和党'} | "
                f"{info['note']}"
            )
        with col_i2:
            st.markdown(
                f'<a href="https://www.capitoltrades.com/politicians/{pid}" '
                f'target="_blank" style="font-size:12px;">📊 Capitol Trades →</a>',
                unsafe_allow_html=True,
            )

        with st.spinner(f"{sel} のトレード情報を取得中..."):
            politician_trades = fetch_politician_recent_trades(sel)

        if politician_trades:
            st.markdown("**最近の取引（capitoltrades.com）**")
            for t in politician_trades:
                st.markdown(
                    f'<div style="background:#f8f9fa;border-radius:6px;'
                    f'padding:8px 12px;margin-bottom:6px;font-size:12px;">'
                    f'{t["text"]}'
                    f'</div>',
                    unsafe_allow_html=True,
                )
        else:
            st.info("スクレイピングでのデータ取得ができませんでした。Capitol Tradesで直接確認してください。")
            st.markdown(
                f'[🔗 {sel}のトレード履歴を見る]'
                f'(https://www.capitoltrades.com/politicians/{pid})'
            )

        # ペロシの既知保有銘柄をハードコード表示
        if sel == "Nancy Pelosi":
            st.markdown("**📊 主要保有銘柄（2025〜2026年開示ベース）**")
            pelosi_holdings = [
                {"銘柄": "NVDA（NVIDIA）",   "比率": "約19%", "状況": "最大保有・AI半導体"},
                {"銘柄": "AVGO（Broadcom）", "比率": "約17%", "状況": "2024年6月コール→行使済み+145%"},
                {"銘柄": "GOOGL（Alphabet）","比率": "約17%", "状況": "長期保有・コール複数行使"},
                {"銘柄": "VST（Vistra）",    "比率": "—",     "状況": "原子力・DC電力需要"},
                {"銘柄": "TEM（Tempus AI）", "比率": "—",     "状況": "AI医療・+121%上昇後行使"},
                {"銘柄": "PANW（Palo Alto）","比率": "—",     "状況": "サイバーセキュリティ"},
                {"銘柄": "AMZN（Amazon）",   "比率": "—",     "状況": "2025年新規追加"},
                {"銘柄": "AB（AllianceBernstein）","比率":"—", "状況": "2021年〜長期保有"},
            ]
            st.dataframe(
                pd.DataFrame(pelosi_holdings),
                width="stretch", hide_index=True,
            )

    # ── ③ AI分析レポート ─────────────────────────────
    with tab_ai:
        st.markdown("#### 🤖 AI分析レポート（四半期ごと自動更新）")
        st.caption(
            f"現在の四半期: {quarter} | "
            "Gemini→Groq→OpenRouterでフォールバック | "
            "90日TTLキャッシュ"
        )

        col_a1, col_a2 = st.columns([2, 1])
        with col_a1:
            ai_target = st.selectbox(
                "分析対象議員",
                list(CONGRESS_WATCH.keys()),
                format_func=lambda n: f"{CONGRESS_WATCH[n]['icon']} {n}",
                key="congress_ai_sel",
            )
        with col_a2:
            run_ai = st.button(
                "🤖 AI分析を実行", type="primary",
                key="congress_ai_run", width="stretch",
            )
            clear_ai = st.button(
                "🗑️ キャッシュクリア",
                key="congress_ai_clear", width="stretch",
            )

        if clear_ai:
            ai_congress_analysis.clear()
            st.session_state.pop(f"congress_ai_{ai_target}", None)
            st.success("✅ クリア完了")
            st.rerun()

        cache_key = f"congress_ai_{ai_target}_{quarter}"
        if run_ai or cache_key not in st.session_state:
            with st.spinner(f"🤖 {ai_target} のトレードをAI分析中..."):
                pt = fetch_politician_recent_trades(ai_target)
                trades_text = "\n".join(t["text"] for t in pt) if pt else ""
                # フィードからも補完
                feed_trades = [
                    t["title"] for t in fetch_congress_trades_rss()
                    if ai_target.split()[-1].lower() in t["title"].lower()
                ]
                if feed_trades:
                    trades_text += "\n" + "\n".join(feed_trades[:5])

                comment, model = ai_congress_analysis(
                    ai_target, trades_text, quarter,
                    lang=st.session_state.get("lang", "ja"),
                )
                st.session_state[cache_key] = (comment, model)

        if cache_key in st.session_state:
            comment, model = st.session_state[cache_key]
            for kw in ["【注目銘柄】", "【テーマ】", "【注目ポイント】", "【日本株への示唆】"]:
                comment = comment.replace(kw, f"**{kw}**")
            st.markdown(
                f'<div style="background:#f8f9ff;border:1px solid #e0e7ff;'
                f'border-left:4px solid #1976d2;border-radius:8px;'
                f'padding:16px 20px;font-size:13px;line-height:1.9;">'
                f'{comment.replace(chr(10), "<br>")}'
                f'</div>',
                unsafe_allow_html=True,
            )
            if model:
                st.caption(f"🤖 使用AI: {model} | 四半期: {quarter}")

        st.markdown("---")
        st.markdown("""
**📅 更新スケジュール**
- フィード: 6時間ごと自動更新
- AI分析: 四半期ごと自動更新（手動でも実行可）
- データソース: [QuiverQuant](https://www.quiverquant.com/congresstrading/) / [Capitol Trades](https://www.capitoltrades.com)

⚠️ STOCK Actにより最大45日の開示遅延があります。投資判断は自己責任でお願いします。
        """)

def render_leadlag_section():
    st.header("📡 日米業種リードラグシグナル")
    st.caption(
        "部分空間正則化付きPCA（PCA_SUB）により、"
        "米国当日の業種リターンから日本の翌営業日業種リターンを予測します。"
        "（参考: 中川ら 2026, SIG-FIN-036-13）"
    )
    col_k, col_w, col_lam = st.columns(3)
    with col_k:
        K = st.selectbox("固有ベクトル数 K", [3, 5, 7, 10], index=1, key="leadlag_K")
    with col_w:
        L = st.selectbox("推定ウィンドウ L（営業日）", [60, 120, 250], index=1, key="leadlag_L")
    with col_lam:
        lam = st.slider("正則化パラメータ λ", 0.0, 1.0, 0.9, 0.05, key="leadlag_lam")
    with st.spinner("日米業種データ取得中..."):
        ret_df = fetch_leadlag_data(window=L)
    if ret_df is None or ret_df.empty:
        st.error("⚠️ データ取得失敗。しばらく後にお試しください。")
        return
    result = compute_leadlag_signal(ret_df, K=K, lam=lam)
    if result is None:
        st.warning("⚠️ 十分なデータが取得できませんでした。")
        return
    signal      = result["signal"]
    us_ret      = result["us_ret_today"]
    expl_var    = result["explained_var"]
    latest_date = result["latest_date"]
    jp_cols     = result["jp_cols"]
    us_cols     = result["us_cols"]
    st.caption(
        f"📅 基準日: {pd.Timestamp(latest_date).strftime('%Y-%m-%d')} | "
        f"K={K} | L={L}日 | λ={lam:.2f} | 説明分散: {expl_var*100:.1f}%"
    )
    st.subheader("🇺🇸 米国業種 当日リターン（シグナル入力）")
    us_display = pd.DataFrame({
        "ETF":        us_ret.index,
        "業種":       [US_SECTOR_ETFS.get(t, t) for t in us_ret.index],
        "リターン(%)": (us_ret.values * 100).round(2),
    }).sort_values("リターン(%)", ascending=False).reset_index(drop=True)

    def _color_ret(val):
        if val > 0:   return "color: #1a7f37; font-weight:bold"
        elif val < 0: return "color: #d1242f; font-weight:bold"
        return ""

    st.dataframe(
        us_display.style.map(_color_ret, subset=["リターン(%)"]),
        width="stretch", hide_index=True
    )
    st.subheader("🇯🇵 日本業種 翌営業日 予測シグナル")
    n_jp = len(signal)
    n_ls = max(1, int(n_jp * 0.3))
    jp_sorted = signal.sort_values(ascending=False)
    jp_display = pd.DataFrame({
        "ETF":     jp_sorted.index,
        "業種":    [JP_SECTOR_ETFS.get(t, t) for t in jp_sorted.index],
        "シグナル": jp_sorted.values.round(4),
        "判定":    (
            ["🔼 ロング候補"] * n_ls
            + ["　 ニュートラル"] * (n_jp - 2 * n_ls)
            + ["🔽 ショート候補"] * n_ls
        ),
    }).reset_index(drop=True)

    def _color_signal(val):
        if isinstance(val, float):
            if val > 0.01:    return "color: #1a7f37; font-weight:bold"
            elif val < -0.01: return "color: #d1242f; font-weight:bold"
        return ""

    st.dataframe(
        jp_display.style.map(_color_signal, subset=["シグナル"]),
        width="stretch", hide_index=True
    )
    try:
        fig, axes = plt.subplots(1, 2, figsize=(14, 5))
        ax1 = axes[0]
        colors_us = ["#1a7f37" if v > 0 else "#d1242f" for v in us_ret.values]
        ax1.barh([US_SECTOR_ETFS.get(t, t) for t in us_ret.index],
                 us_ret.values * 100, color=colors_us, alpha=0.85)
        ax1.axvline(0, color="black", linewidth=0.8)
        ax1.set_xlabel("Return (%)")
        ax1.set_title("US Sector Returns (Today)", fontsize=11)
        ax1.grid(True, axis="x", alpha=0.3)
        ax2 = axes[1]
        sig_vals = jp_display["シグナル"].values
        sig_labs = jp_display["業種"].values
        colors_jp = ["#1a7f37" if v > 0 else "#d1242f" for v in sig_vals]
        ax2.barh(sig_labs[::-1], sig_vals[::-1], color=colors_jp[::-1], alpha=0.85)
        ax2.axvline(0, color="black", linewidth=0.8)
        ax2.set_xlabel("Signal Strength")
        ax2.set_title("JP Sector Predicted Signal (Next Day)", fontsize=11)
        ax2.grid(True, axis="x", alpha=0.3)
        plt.tight_layout()
        st.pyplot(fig, clear_figure=True)
    except Exception as e:
        logger.warning(f"leadlag chart error: {e}")
    with st.expander("🔬 ファクタースコア詳細", expanded=False):
        f_scores = result["factor_scores"]
        interp = (["グローバル共通トレンド", "日米国スプレッド", "シクリカル/ディフェンシブ"]
                  + [f"高次ファクター{i}" for i in range(len(f_scores) - 3)])
        st.dataframe(pd.DataFrame({
            "ファクター": [f"F{i+1}" for i in range(len(f_scores))],
            "スコア":    f_scores.round(4),
            "解釈":      interp[:len(f_scores)],
        }), width="stretch", hide_index=True)
    st.info(
        "⚠️ **免責事項**: このシグナルは学術論文の手法を参考にした実験的な指標です。"
        "投資判断は自己責任でお願いします。"
    )

# =====================================================
# 🤖 Claude 個別株トレーディングプロジェクト
# =====================================================

def _trading_ws(tab: str, headers: list):
    """analytics._sheets_ws を流用（リトライ付き）。
    一時的な接続失敗に備えて最大3回試行し、指数バックオフ(1s,2s)で待機する。
    """
    for _attempt in range(3):
        try:
            ws = _anl_sheets_ws(tab, headers)
            if ws is not None:
                return ws
        except Exception as e:
            logger.warning(f"[trading] ws({tab}) 試行{_attempt+1}/3 失敗: {e}")
        if _attempt < 2:
            time.sleep(1.0 * (_attempt + 1))  # 1s → 2s
    logger.warning(f"[trading] ws({tab}) 3回とも失敗")
    return None




_TRADES_HEADERS = [
    "date", "ticker", "name", "action", "quantity", "price",
    "fee", "memo", "ai_target", "ai_stoploss"
]

# 既知銘柄名マスタ（yfinance が返さない/遅い場合のフォールバック）
_KNOWN_NAMES: dict = {
    "MU":      "Micron Technology",
    "NVDA":    "NVIDIA",
    "AMD":     "Advanced Micro Devices",
    "INTC":    "Intel",
    "AVGO":    "Broadcom",
    "QCOM":    "Qualcomm",
    "AAPL":    "Apple",
    "MSFT":    "Microsoft",
    "GOOGL":   "Alphabet",
    "META":    "Meta Platforms",
    "AMZN":    "Amazon",
    "TSLA":    "Tesla",
    "TSM":     "TSMC",
    "ASML":    "ASML Holding",
    "AMAT":    "Applied Materials",
    "LRCX":    "Lam Research",
    "KLAC":    "KLA Corporation",
    "TXN":     "Texas Instruments",
    "ARM":     "Arm Holdings",
    "MRVL":    "Marvell Technology",
    "SMCI":    "Super Micro Computer",
    "285A.T":  "キオクシアHD",
    "8306.T":  "三菱UFJフィナンシャル・グループ",
    "8316.T":  "三井住友フィナンシャルグループ",
    "8411.T":  "みずほフィナンシャルグループ",
    "7203.T":  "トヨタ自動車",
    "6758.T":  "ソニーグループ",
    "6861.T":  "キーエンス",
    "9984.T":  "ソフトバンクグループ",
    "7974.T":  "任天堂",
    "4063.T":  "信越化学工業",
    "6367.T":  "ダイキン工業",
    "8035.T":  "東京エレクトロン",
}


@st.cache_data(ttl=86400, show_spinner=False)
def _get_stock_display_name(ticker: str) -> str:
    """ティッカーから銘柄表示名を取得（既知マスタ→yfinance shortName の順）。
    取得失敗時はティッカーをそのまま返す。TTL=24h。
    """
    if ticker in _KNOWN_NAMES:
        return _KNOWN_NAMES[ticker]
    try:
        info = yf.Ticker(ticker).info or {}
        name = (info.get("shortName") or info.get("longName") or "").strip()
        return name if name else ticker
    except Exception:
        return ticker

def _load_trades() -> tuple[pd.DataFrame, str | None]:
    """取引記録をGoogle Sheetsから読み込む。(DataFrame, error_msg) を返す。"""
    try:
        ws = _trading_ws("claude_trades", _TRADES_HEADERS)
        if not ws:
            return pd.DataFrame(), "Google Sheets接続失敗（3回リトライ後も接続できませんでした）"
        rows = ws.get_all_records()
        if not rows:
            return pd.DataFrame(), None
        df = pd.DataFrame(rows)
        for col in ["quantity", "price", "fee", "ai_target", "ai_stoploss"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df, None
    except Exception as e:
        logger.warning(f"[trading] trades読込失敗: {e}")
        return pd.DataFrame(), f"読込エラー: {str(e)[:80]}"


def _save_trade(date: str, ticker: str, name: str, action: str,
                quantity: float, price: float, fee: float,
                memo: str, ai_target: float, ai_stoploss: float):
    """取引記録をGoogle Sheetsに追加"""
    try:
        ws = _trading_ws("claude_trades", [
            "date", "ticker", "name", "action", "quantity", "price",
            "fee", "memo", "ai_target", "ai_stoploss"
        ])
        if not ws:
            return False, "Google Sheets接続失敗（Secretsを確認）"
        ws.append_row([date, ticker.upper(), name, action,
                       quantity, price, fee, memo, ai_target, ai_stoploss])
        return True, ""
    except Exception as e:
        logger.warning(f"[trading] trade保存失敗: {e}")
        return False, str(e)[:120]


_SIGNALS_HEADERS = [
    "date", "ticker", "name", "judgment", "target_price", "stoploss",
    "trailing_pe", "forward_pe", "eps_surprise", "rev_growth",
    "next_earnings", "summary", "model",
]


def _load_signals(ticker: str) -> list[dict]:
    """過去のAI分析ログを ticker で絞り込んで返す（新しい順）"""
    try:
        ws = _trading_ws("claude_signals", _SIGNALS_HEADERS)
        if not ws:
            return []
        rows = ws.get_all_records()
        matched = [r for r in rows if r.get("ticker", "").upper() == ticker.upper()]
        return list(reversed(matched))   # 新しい順
    except Exception as e:
        logger.warning(f"[trading] signals読込失敗: {e}")
        return []


def _save_signal(ticker: str, name: str, signal: dict, data: dict) -> bool:
    """AI分析結果を claude_signals シートに保存"""
    try:
        ws = _trading_ws("claude_signals", _SIGNALS_HEADERS)
        if not ws:
            return False
        # 判断行だけ抽出（"**判断**: 保有継続" → "保有継続"）
        text = signal.get("text", "")
        judgment = ""
        for line in text.splitlines():
            if line.startswith("**判断**"):
                judgment = line.split(":", 1)[-1].strip().strip("[]").strip()
                break
        # 目標株価と損切りラインも抽出
        target = stoploss = ""
        for line in text.splitlines():
            if "目標株価" in line and not target:
                target = line.split(":", 1)[-1].strip()[:40]
            if "損切り" in line and "ライン" in line and not stoploss:
                stoploss = line.split(":", 1)[-1].strip()[:40]
        # 要約（判断以降の理由部分、最大200字）
        summary = ""
        in_reason = False
        for line in text.splitlines():
            if line.startswith("**理由**"):
                summary = line.split(":", 1)[-1].strip()
                in_reason = True
            elif in_reason and line.startswith("**"):
                break
            elif in_reason:
                summary += " " + line.strip()
        summary = summary.strip()[:200]

        ws.append_row([
            datetime.now(JST).strftime("%Y-%m-%d %H:%M"),
            ticker.upper(),
            name,
            judgment,
            target,
            stoploss,
            str(data.get("trailing_pe") or ""),
            str(data.get("forward_pe") or ""),
            str(data.get("eps_history", [""])[0] if data.get("eps_history") else ""),
            str(data.get("rev_growth") or ""),
            str(data.get("next_earnings") or ""),
            summary,
            signal.get("model", ""),
        ])
        return True
    except Exception as e:
        logger.warning(f"[trading] signal保存失敗: {e}")
        return False


@st.cache_data(ttl=86400, show_spinner=False)
def _fetch_jp_long_name(ticker: str) -> str | None:
    """日本株の正式社名をyfinanceから取得（TTL=1日）"""
    try:
        info = yf.Ticker(ticker).info or {}
        return info.get("longName") or info.get("shortName")
    except Exception:
        return None


@st.cache_data(ttl=3600, show_spinner=False)
def _translate_headlines_to_ja(headlines_tuple: tuple) -> list:
    """英語ニュース見出しを一括で日本語に翻訳する（Groq高速モデル使用、TTL=1h）。
    tuple型を受け取るのは st.cache_data がリストを受け付けないため。
    Returns: list[str] （入力と同じ長さ）
    """
    if not headlines_tuple:
        return []
    numbered = "\n".join(f"{i+1}. {h}" for i, h in enumerate(headlines_tuple))
    prompt = (
        "以下の英語ニュース見出しを自然な日本語に翻訳してください。\n"
        "企業名・人名・製品名はカタカナまたは原文のまま。\n"
        "番号付きリスト（1. 〜\\n2. 〜）の形式で翻訳のみ返してください。余分な説明不要。\n\n"
        + numbered
    )
    try:
        text, _ = _call_ai_for_trading(
            prompt, model_pref="groq", max_output_tokens=500, temperature=0.1
        )
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        result = []
        for line in lines:
            m = re.match(r"^\d+[\.\)]\s*(.+)$", line)
            if m:
                result.append(m.group(1))
        while len(result) < len(headlines_tuple):
            result.append(headlines_tuple[len(result)])
        return result[:len(headlines_tuple)]
    except Exception:
        return list(headlines_tuple)


_STOCK_CACHE_HEADERS    = ["date", "ticker", "data_json", "updated_at"]
_AI_SCORE_CACHE_HEADERS = ["date", "tickers_key", "mode", "scores_json", "model", "created_at"]


def _load_ai_score_cache(date: str, tickers_key: str, mode: str) -> dict | None:
    """AIスコアキャッシュをSheetsから取得"""
    import json as _json
    try:
        ws = _trading_ws("ai_score_cache", _AI_SCORE_CACHE_HEADERS)
        if not ws:
            return None
        rows = ws.get_all_records()
        for r in reversed(rows):
            if (r.get("date") == date and
                    r.get("tickers_key") == tickers_key and
                    r.get("mode") == mode):
                raw = r.get("scores_json", "")
                return {"scores": _json.loads(raw), "model": r.get("model", "")} if raw else None
    except Exception as e:
        logger.warning(f"[trading] ai_score_cache読込失敗: {e}")
    return None


def _save_ai_score_cache(date: str, tickers_key: str, mode: str,
                          scores: dict, model: str) -> bool:
    """AIスコアをSheetsにキャッシュ保存"""
    import json as _json
    try:
        ws = _trading_ws("ai_score_cache", _AI_SCORE_CACHE_HEADERS)
        if not ws:
            return False
        ws.append_row([
            date, tickers_key, mode,
            _json.dumps(scores, ensure_ascii=False),
            model,
            datetime.now(JST).strftime("%Y-%m-%d %H:%M"),
        ])
        return True
    except Exception as e:
        logger.warning(f"[trading] ai_score_cache保存失敗: {e}")
        return False


def _generate_ai_allocation_scores(
    positions: dict,
    stock_data_map: dict,
    market_ctx: dict,
    mode: str = "growth",
    model_pref: str = "auto",
) -> dict:
    """全保有銘柄を一括でAI評価し、各銘柄に -5〜+5 のスコアを返す。
    Returns: {
        ticker: {"score": float, "key_factors": [str,...], "reasoning": str},
        "_model": str
    }
    """
    import json as _json, re as _re

    mode_desc = {
        "growth":     "長期育成（ファンダ重視・6ヶ月〜2年）",
        "momentum":   "モメンタム（テクニカル重視・1〜4週間）",
        "autonomous": "AI自律（分析軸をAI決定）",
    }.get(mode, "長期育成")

    # 銘柄サマリー
    stock_blocks = []
    for ticker, pos in positions.items():
        data   = stock_data_map.get(ticker, {})
        is_jp  = ticker.endswith(".T")
        cur    = "円" if is_jp else "USD"
        flag   = "🇯🇵" if is_jp else "🇺🇸"
        price  = data.get("price", "-")
        rsi    = data.get("rsi", "-")
        ma25   = data.get("ma25", "-")
        ma75   = data.get("ma75", "-")
        ret_20d= data.get("ret_20d")
        sec    = data.get("sector") or pos.get("sector", "その他")
        news   = (data.get("news") or [])[:3]
        news_str = " / ".join(n[:50] for n in news if n) or "なし"
        mval   = pos.get("market_value") or 0
        cost   = pos.get("cost") or 0
        gp     = (mval - cost) / cost * 100 if cost > 0 else 0
        ret_str = f"{ret_20d:+.1f}%" if ret_20d is not None else "?"
        _disp = data.get("name") or pos.get("name") or ticker
        if _disp == ticker:
            _disp = _get_stock_display_name(ticker)
        stock_blocks.append(
            f"{flag} {ticker}({_disp}) | "
            f"株価:{price}{cur} RSI:{rsi} MA25:{ma25}{cur} MA75:{ma75}{cur} | "
            f"20日:{ret_str} 含み:{gp:+.1f}% | セクター:{sec} | "
            f"IR/ニュース: {news_str}"
        )

    prompt = f"""あなたはプロの株式アナリストです。以下の保有銘柄を評価し、
投資戦略モード「{mode_desc}」の観点で各銘柄に -5〜+5 のスコアを付けてください。

スコア基準:
+5: 強い買い増し（テクニカル・ファンダ・市場環境すべて強気）
+3〜+4: 買い増し推奨
+1〜+2: やや強気・保有継続
 0: 中立
-1〜-2: やや弱気・比重軽減
-3〜-4: 削減推奨
-5: 即売却

【市場環境】
{_format_market_ctx_for_prompt(market_ctx)}

【保有銘柄データ】
{chr(10).join(stock_blocks)}

必ず以下のJSONのみ返してください（コードブロックや説明文は不要）:
{{
  "scores": {{
    "<ticker>": {{
      "score": <float -5〜5>,
      "key_factors": ["<判断根拠1>", "<判断根拠2>", "<判断根拠3>"],
      "reasoning": "<50字以内の一言>"
    }}
  }}
}}
"""
    try:
        text, model_used = _call_ai_for_trading(
            prompt, model_pref=model_pref, max_output_tokens=800, temperature=0.2
        )
        m = _re.search(r'\{[\s\S]*\}', text)
        if m:
            parsed = _json.loads(m.group())
            scores = parsed.get("scores", {})
            scores["_model"] = model_used
            return scores
    except Exception as e:
        logger.warning(f"[trading] AI allocation score生成失敗: {e}")
    return {"_model": ""}


@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_benchmark_tech_data() -> dict:
    """QQQ(NDX100)と^N225(日経225)のテクニカルデータを取得（ベンチマーク比較用）"""
    import yfinance as _yf2
    out = {}
    for ticker, key in [("QQQ", "NDX"), ("^N225", "N225")]:
        try:
            hist = _yf2.Ticker(ticker).history(period="6mo")
            if hist.empty or len(hist) < 30:
                continue
            close = hist["Close"].dropna()
            price = float(close.iloc[-1])
            ma25  = float(close.rolling(25).mean().iloc[-1])
            ma75  = float(close.rolling(75).mean().iloc[-1])
            delta = close.diff()
            gain  = delta.clip(lower=0).rolling(14).mean()
            loss  = (-delta.clip(upper=0)).rolling(14).mean()
            _lv   = float(loss.iloc[-1])
            rs    = float(gain.iloc[-1]) / _lv if (_lv != 0 and _lv == _lv) else 100
            rsi   = 100 - (100 / (1 + rs))
            ret_20d = (price / float(close.iloc[-21]) - 1) * 100 if len(close) >= 21 else 0.0
            out[key] = {
                "price": price, "ma25": ma25, "ma75": ma75,
                "rsi": float(rsi), "ret_20d": float(ret_20d),
                "sector": "Technology",
            }
        except Exception as _e:
            logger.warning(f"[trading] benchmark fetch {ticker}: {_e}")
    return out


def _score_single_rule(data: dict, market_ctx: dict) -> float:
    """単一銘柄のルールベーススコアを計算（ベンチマーク比較用）"""
    fg_sc   = float(market_ctx.get("fg_score") or 50)
    fg_mult = (1.15 if fg_sc < 25 else 1.05 if fg_sc < 45 else
               1.00 if fg_sc < 55 else 0.92 if fg_sc < 75 else 0.82)
    sector_quad  = market_ctx.get("sector_quad") or {}
    sq_leading   = sector_quad.get("Leading",   [])
    sq_improving = sector_quad.get("Improving", [])
    sq_weakening = sector_quad.get("Weakening", [])
    sq_lagging   = sector_quad.get("Lagging",   [])
    price   = float(data.get("price")   or 0)
    ma25    = float(data.get("ma25")    or 0)
    ma75    = float(data.get("ma75")    or 0)
    rsi     = float(data.get("rsi")     or 50)
    ret_20d = float(data.get("ret_20d") or 0)
    sector  = (data.get("sector") or "").lower()
    score   = 0.0
    if price and ma25:
        score += 1.0 if price > ma25 else -1.0
    if ma25 and ma75:
        score += 1.0 if ma25 > ma75 else -1.5
    if rsi:
        score += (2.0 if rsi < 30 else 1.0 if rsi < 45 else
                  0.5 if rsi < 60 else 0.0 if rsi < 70 else -1.0)
    if ret_20d:
        score += (1.5 if ret_20d > 10 else 0.5 if ret_20d > 3 else
                  -1.5 if ret_20d < -10 else -0.5 if ret_20d < -3 else 0.0)
    for s in sq_leading:
        if sector in s.lower() or s.lower() in sector:
            score += 2.0; break
    else:
        for s in sq_improving:
            if sector in s.lower() or s.lower() in sector:
                score += 1.0; break
        else:
            for s in sq_weakening:
                if sector in s.lower() or s.lower() in sector:
                    score -= 1.0; break
            else:
                for s in sq_lagging:
                    if sector in s.lower() or s.lower() in sector:
                        score -= 2.0; break
    return round(score * fg_mult, 2)


def _calc_allocation_recommendations(
    positions: dict,
    stock_data_map: dict,
    market_ctx: dict,
    ai_scores: dict | None = None,   # {ticker: {"score": float, "key_factors": [...], "reasoning": str}}
    ai_blend: float = 0.5,           # AIスコアの重み（0=ルールのみ, 1=AIのみ, 0.5=均等ブレンド）
) -> dict:
    """テクニカル・セクターRRG・F&G + オプションでAIスコアをブレンドして推奨アロケーションを算出。
    ai_scores: _generate_ai_allocation_scores() の戻り値（任意）
    ai_blend: 0.0〜1.0。AIスコアの重み（0=ルールのみ、0.5=均等）
    Returns: {
        ticker: {score, adjusted_score, ai_score, combined_score, signals, current_alloc, rec_alloc, delta, sector},
        "_meta": {fg_score, fg_label, fg_mult, fg_desc, total_mkt, has_ai}
    }
    """
    total_mkt = sum(p.get("market_value") or 0 for p in positions.values())

    # ── Fear & Greed 乗数 ─────────────────────────────────────────
    fg      = (market_ctx.get("fear_greed") or {})
    fg_sc   = float(fg.get("score") or 50)
    fg_lbl  = fg.get("rating") or "Neutral"
    if fg_sc < 25:
        fg_mult, fg_desc = 1.15, f"極度の恐怖({fg_sc:.0f}) — 逆張り機会・リスクオン"
    elif fg_sc < 45:
        fg_mult, fg_desc = 1.05, f"恐怖({fg_sc:.0f}) — 積極買い寄り"
    elif fg_sc < 55:
        fg_mult, fg_desc = 1.00, f"中立({fg_sc:.0f})"
    elif fg_sc < 75:
        fg_mult, fg_desc = 0.92, f"強欲({fg_sc:.0f}) — やや慎重"
    else:
        fg_mult, fg_desc = 0.82, f"極度の強欲({fg_sc:.0f}) — リスク低減推奨"

    # ── セクターRRG ───────────────────────────────────────────────
    sector_quad = market_ctx.get("sector_quad") or {}
    _sq_leading   = sector_quad.get("Leading", [])
    _sq_improving = sector_quad.get("Improving", [])
    _sq_weakening = sector_quad.get("Weakening", [])
    _sq_lagging   = sector_quad.get("Lagging", [])

    def _sector_score_and_label(sec: str):
        if not sec:
            return 0.0, ""
        sl = sec.lower()
        for s in _sq_leading:
            if sl in s.lower() or s.lower() in sl:
                return 2.0, f"🟢 RRG Leading"
        for s in _sq_improving:
            if sl in s.lower() or s.lower() in sl:
                return 1.0, f"🔵 RRG Improving"
        for s in _sq_weakening:
            if sl in s.lower() or s.lower() in sl:
                return -1.0, f"🟡 RRG Weakening"
        for s in _sq_lagging:
            if sl in s.lower() or s.lower() in sl:
                return -2.0, f"🔴 RRG Lagging"
        return 0.0, ""

    # ── 銘柄別スコア計算 ─────────────────────────────────────────
    raw_weights: dict = {}
    result: dict      = {}

    for ticker, pos in positions.items():
        data    = stock_data_map.get(ticker, {})
        score   = 0.0
        signals = []

        price   = float(data.get("price")   or 0)
        ma25    = float(data.get("ma25")    or 0)
        ma75    = float(data.get("ma75")    or 0)
        rsi     = float(data.get("rsi")     or 50)
        ret_5d  = float(data.get("ret_5d")  or 0)
        ret_20d = float(data.get("ret_20d") or 0)
        sector  = data.get("sector") or pos.get("sector", "")

        # 1. 株価 vs MA25
        if price and ma25:
            pct = (price / ma25 - 1) * 100
            if price > ma25:
                score += 1.0
                signals.append(f"📈 株価 > MA25({pct:+.1f}%)")
            else:
                score -= 1.0
                signals.append(f"📉 株価 < MA25({pct:+.1f}%)")

        # 2. MA25 vs MA75（ゴールデン/デッドクロス）
        if ma25 and ma75:
            if ma25 > ma75:
                score += 1.0
                signals.append("✨ ゴールデンクロス(MA25>MA75)")
            else:
                score -= 1.5
                signals.append("☠️ デッドクロス(MA25<MA75)")

        # 3. RSI
        if rsi:
            if rsi < 30:
                score += 2.0; signals.append(f"💙 RSI売られ過ぎ({rsi:.0f})")
            elif rsi < 45:
                score += 1.0; signals.append(f"🟢 RSI低め({rsi:.0f})")
            elif rsi < 60:
                score += 0.5; signals.append(f"🟡 RSI適正({rsi:.0f})")
            elif rsi < 70:
                score += 0.0; signals.append(f"🟠 RSI高め({rsi:.0f})")
            else:
                score -= 1.0; signals.append(f"🔴 RSI過熱({rsi:.0f})")

        # 4. 20日モメンタム
        if ret_20d:
            if ret_20d > 10:
                score += 1.5; signals.append(f"🚀 20日強騰({ret_20d:+.1f}%)")
            elif ret_20d > 3:
                score += 0.5; signals.append(f"↗ 20日{ret_20d:+.1f}%")
            elif ret_20d < -10:
                score -= 1.5; signals.append(f"⚠️ 20日急落({ret_20d:+.1f}%)")
            elif ret_20d < -3:
                score -= 0.5; signals.append(f"↘ 20日{ret_20d:+.1f}%")

        # 5. セクターRRG
        sec_sc, sec_sig = _sector_score_and_label(sector)
        score += sec_sc
        if sec_sig:
            signals.append(sec_sig)

        # F&G乗数適用
        adjusted = score * fg_mult

        cur_mval  = float(pos.get("market_value") or 0)
        cur_alloc = cur_mval / total_mkt * 100 if total_mkt > 0 else 0

        result[ticker] = {
            "score":          round(score, 2),
            "adjusted_score": round(adjusted, 2),
            "signals":        signals,
            "current_alloc":  round(cur_alloc, 1),
            "sector":         sector,
        }
        raw_weights[ticker] = max(adjusted + 6.0, 0.5)  # +6でスコア最低値でも正値保証

    # ── アロケーション正規化（フロア付き）────────────────────────
    n        = len(positions)
    floor_pp = max(100 / n * 0.4, 3.0)   # 等分配の40%を最低フロア（最低3%）
    total_w  = sum(raw_weights.values())
    allocs   = {t: raw_weights[t] / total_w * 100 for t in raw_weights}

    # フロア未満の銘柄をフロアにクランプし、残りを再配分
    floored = {t for t, a in allocs.items() if a < floor_pp}
    if floored:
        floor_sum = len(floored) * floor_pp
        remaining = 100.0 - floor_sum
        non_floored_w = sum(raw_weights[t] for t in raw_weights if t not in floored)
        for t in allocs:
            if t in floored:
                allocs[t] = floor_pp
            else:
                allocs[t] = (raw_weights[t] / non_floored_w * remaining
                             if non_floored_w > 0 else floor_pp)

    # ── AIスコアブレンド（ai_scores が渡された場合） ─────────────
    has_ai = bool(ai_scores and any(t in ai_scores for t in positions))
    if has_ai and 0 < ai_blend <= 1.0:
        # AIスコア(-5〜+5) → ルールスコアと同スケールに正規化（÷5×7 で ±7 相当に）
        ai_norm_factor = 7.0 / 5.0
        combined_weights: dict = {}
        for ticker in result:
            rule_adj = result[ticker]["adjusted_score"]
            ai_entry = (ai_scores or {}).get(ticker, {})
            ai_raw   = float(ai_entry.get("score", 0)) if isinstance(ai_entry, dict) else 0.0
            ai_norm  = ai_raw * ai_norm_factor
            combined = rule_adj * (1 - ai_blend) + ai_norm * ai_blend
            result[ticker]["ai_score"]      = round(ai_raw, 1)
            result[ticker]["ai_reasoning"]  = ai_entry.get("reasoning", "") if isinstance(ai_entry, dict) else ""
            result[ticker]["ai_factors"]    = ai_entry.get("key_factors", []) if isinstance(ai_entry, dict) else []
            result[ticker]["combined_score"]= round(combined, 2)
            combined_weights[ticker]        = max(combined + 6.0, 0.5)

        # ブレンドスコアでアロケーション再計算
        total_cw = sum(combined_weights.values())
        new_allocs = {t: combined_weights[t] / total_cw * 100 for t in combined_weights}
        floored2 = {t for t, a in new_allocs.items() if a < floor_pp}
        if floored2:
            fl_sum2 = len(floored2) * floor_pp
            rem2    = 100.0 - fl_sum2
            nf_w2   = sum(combined_weights[t] for t in combined_weights if t not in floored2)
            for t in new_allocs:
                if t in floored2:
                    new_allocs[t] = floor_pp
                else:
                    new_allocs[t] = combined_weights[t] / nf_w2 * rem2 if nf_w2 > 0 else floor_pp
        for ticker in result:
            rec = round(new_allocs.get(ticker, 0), 1)
            result[ticker]["rec_alloc"] = rec
            result[ticker]["delta"]     = round(rec - result[ticker]["current_alloc"], 1)
    else:
        # AIなし → ルールベースのアロケーションをそのまま使用
        for ticker in result:
            rec = round(allocs.get(ticker, 0), 1)
            result[ticker]["rec_alloc"] = rec
            result[ticker]["delta"]     = round(rec - result[ticker]["current_alloc"], 1)
            result[ticker]["ai_score"]     = None
            result[ticker]["ai_reasoning"] = ""
            result[ticker]["ai_factors"]   = []
            result[ticker]["combined_score"] = result[ticker]["adjusted_score"]

    # ── ベンチマーク比較 (QQQ/NDX vs ^N225) ────────────────────────
    _qqq_sc, _n225_sc = 0.0, 0.0
    _bm_loaded = False
    try:
        _bm_data = _fetch_benchmark_tech_data()
        if "NDX" in _bm_data:
            _qqq_sc  = _score_single_rule(_bm_data["NDX"],  market_ctx)
        if "N225" in _bm_data:
            _n225_sc = _score_single_rule(_bm_data["N225"], market_ctx)
        _bm_loaded = bool(_bm_data)
    except Exception as _be:
        logger.warning(f"[trading] benchmark score error: {_be}")
    for ticker in result:
        if ticker == "_meta":
            continue
        _is_jp  = ticker.endswith(".T")
        _csc    = result[ticker].get("combined_score", result[ticker].get("adjusted_score", 0))
        _bm_ref = _n225_sc if _is_jp else _qqq_sc
        result[ticker]["bm_diff"]    = round(_csc - _bm_ref, 2)
        result[ticker]["bm_label"]   = "N225" if _is_jp else "NDX"
        result[ticker]["switch_flag"] = _bm_loaded and (_csc - _bm_ref) < -1.5

    result["_meta"] = {
        "fg_score":    fg_sc,
        "fg_label":    fg_lbl,
        "fg_mult":     fg_mult,
        "fg_desc":     fg_desc,
        "total_mkt":   total_mkt,
        "has_ai":      has_ai,
        "ai_blend":    ai_blend,
        "bm_qqq_score":  round(_qqq_sc, 2),
        "bm_n225_score": round(_n225_sc, 2),
    }
    return result


def _generate_switch_recommendations(
    alloc_result: dict,
    stock_data_map: dict,
    market_ctx: dict,
    model_pref: str = "auto",
) -> tuple:
    """ベンチマーク(NDX/N225)を下回る銘柄の乗り換え候補をAIが提案。
    Returns: (list[dict], model_used_str)
    """
    import json as _json, re as _re
    _meta      = alloc_result.get("_meta", {})
    _qqq_sc    = _meta.get("bm_qqq_score", 0.0)
    _n225_sc   = _meta.get("bm_n225_score", 0.0)

    candidates = []
    for t, ar in alloc_result.items():
        if t == "_meta" or not ar.get("switch_flag"):
            continue
        _is_jp  = t.endswith(".T")
        _bm_nm  = "日経225(^N225)" if _is_jp else "Nasdaq100(QQQ)"
        _bm_sc  = _n225_sc if _is_jp else _qqq_sc
        _sdata  = stock_data_map.get(t, {})
        candidates.append({
            "ticker":    t,
            "sector":    ar.get("sector", ""),
            "score":     ar.get("combined_score", ar.get("adjusted_score", 0)),
            "bm_name":   _bm_nm,
            "bm_score":  _bm_sc,
            "bm_diff":   ar.get("bm_diff", 0),
            "alloc":     ar.get("current_alloc", 0),
            "rsi":       _sdata.get("rsi", 50),
            "ret_20d":   _sdata.get("ret_20d", 0),
            "is_jp":     _is_jp,
        })

    if not candidates:
        return [], ""

    lines = []
    for c in candidates:
        lines.append(
            f"- {c['ticker']} (セクター:{c['sector'][:24]}, 保有{c['alloc']:.1f}%): "
            f"スコア{c['score']:+.2f} vs {c['bm_name']}={c['bm_score']:+.2f} "
            f"(差{c['bm_diff']:+.2f}) | RSI={c['rsi']:.0f} | 20日リターン={c['ret_20d']:+.1f}%"
        )
    cand_text = "\n".join(lines)

    prompt = f"""あなたは株式ポートフォリオ最適化の専門家です。
以下の保有銘柄はベンチマーク指数のルールスコアを大幅に下回っています。

【アンダーパフォーム銘柄】
{cand_text}

【市場環境】
Fear&Greed: {market_ctx.get('fg_score', 50):.0f}
日経予測: {market_ctx.get('nikkei_pred_label', 'N/A')}
米国予測: {market_ctx.get('us_pred_label', 'N/A')}
QQQスコア: {_qqq_sc:+.2f} / 日経225スコア: {_n225_sc:+.2f}

各銘柄について分析し、乗り換え候補を提案してください：
1. 同セクター/テーマ内でより好調な具体的銘柄（ティッカー）を2〜3銘柄
2. 対応するETF（QQQ, SPY, VGT, 1321など）への乗り換えも候補に含める
3. 乗り換えタイミングの注意点（例：RSI調整待ち、決算前後など）

以下のJSON形式のみで回答（前後のテキスト不要）:
{{"switches": [{{"from": "TICKER", "sector": "セクター", "to_stocks": ["TK1","TK2"], "to_etf": "ETF", "rationale": "乗り換え根拠（50字以内）", "timing": "タイミング注意点（30字以内）"}}]}}"""

    try:
        text, model_used = _call_ai_for_trading(
            prompt, model_pref=model_pref, max_output_tokens=700, temperature=0.3
        )
        m = _re.search(r'\{[\s\S]*\}', text)
        if m:
            parsed = _json.loads(m.group())
            return parsed.get("switches", []), model_used
    except Exception as _e:
        logger.warning(f"[trading] switch recommendations failed: {_e}")
    return [], ""


def _load_stock_data_cache(ticker: str, date: str) -> dict | None:
    """Google Sheetsから当日の銘柄データキャッシュを取得。
    Returns: _fetch_trading_stock_data() と同形式の dict、なければ None
    """
    import json as _json
    try:
        ws = _trading_ws("stock_data_cache", _STOCK_CACHE_HEADERS)
        if not ws:
            return None
        rows = ws.get_all_records()
        for r in reversed(rows):
            if r.get("date") == date and r.get("ticker", "").upper() == ticker.upper():
                raw = r.get("data_json", "")
                if raw:
                    return _json.loads(raw)
    except Exception as e:
        logger.warning(f"[trading] stock_cache読込失敗 {ticker}: {e}")
    return None


def _save_stock_data_cache(ticker: str, date: str, data: dict) -> bool:
    """銘柄データをGoogle Sheetsにキャッシュ保存。
    close_series/close_dates は除外してJSONサイズを削減。
    """
    import json as _json
    try:
        ws = _trading_ws("stock_data_cache", _STOCK_CACHE_HEADERS)
        if not ws:
            return False
        # チャート用の長いリストは除外（AIには不要）
        slim = {k: v for k, v in data.items() if k not in ("close_series", "close_dates")}
        data_json = _json.dumps(slim, ensure_ascii=False, default=str)
        if len(data_json) > 49000:
            data_json = data_json[:49000]  # Sheetsセル上限50,000文字
        ws.append_row([
            date,
            ticker.upper(),
            data_json,
            datetime.now(JST).strftime("%Y-%m-%d %H:%M"),
        ])
        return True
    except Exception as e:
        logger.warning(f"[trading] stock_cache保存失敗 {ticker}: {e}")
        return False


def _get_stock_cache_status(tickers: list) -> dict:
    """保有銘柄のキャッシュ状態を一括取得。
    Returns: {ticker: {"cached": bool, "updated_at": str}}
    """
    try:
        ws = _trading_ws("stock_data_cache", _STOCK_CACHE_HEADERS)
        if not ws:
            return {}
        rows = ws.get_all_records()
        today = datetime.now(JST).strftime("%Y-%m-%d")
        status: dict = {t.upper(): {"cached": False, "updated_at": ""} for t in tickers}
        for r in rows:
            t = r.get("ticker", "").upper()
            if t in status and r.get("date") == today:
                status[t] = {"cached": True, "updated_at": r.get("updated_at", "")[:16]}
        return status
    except Exception as e:
        logger.warning(f"[trading] stock_cache状態取得失敗: {e}")
        return {}


def _fetch_trading_stock_data_with_cache(ticker: str, is_jp: bool) -> dict:
    """キャッシュ対応版 _fetch_trading_stock_data。
    今日のキャッシュがSheetsにあれば即返却、なければフェッチしてSheets保存。
    """
    today = datetime.now(JST).strftime("%Y-%m-%d")
    cached = _load_stock_data_cache(ticker, today)
    if cached:
        return cached
    data = _fetch_trading_stock_data(ticker, is_jp)
    if data:
        _save_stock_data_cache(ticker, today, data)
    return data


_EDINET_DOC_LABELS = {
    "020": "大量保有報告書",
    "030": "変更報告書（大量保有）",
    "070": "公開買付届出書",
    "072": "公開買付（訂正）",
    "076": "意見表明報告書",
    "120": "有価証券報告書",
    "130": "訂正有価証券報告書",
    "140": "四半期報告書",
    "150": "訂正四半期報告書",
    "160": "臨時報告書",
    "161": "訂正臨時報告書",
    "180": "自己株券買付状況報告書",
}
# 投資家にとって重要度の高い書類種別コード
_EDINET_PRIORITY_CODES = {"160", "161", "140", "150", "120", "070", "072", "020", "030"}


@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_edinet_docs_for_date(date_str: str) -> list:
    """EDINET API v2 から指定日の全書類メタデータを取得（TTL=1h）。
    date_str: "YYYY-MM-DD"
    Returns: [{"secCode", "filerName", "docID", "docTypeCode", "docDescription",
               "submitDateTime", "currentReportReason"}]
    """
    try:
        resp = requests.get(
            "https://disclosure.edinet-fsa.go.jp/api/v2/documents.json",
            params={"date": date_str, "type": "2"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=12,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        results = data.get("results") or []
        return [
            {
                "secCode":             r.get("secCode", ""),
                "filerName":           r.get("filerName", ""),
                "docID":               r.get("docID", ""),
                "docTypeCode":         r.get("docTypeCode", ""),
                "docDescription":      r.get("docDescription", ""),
                "submitDateTime":      r.get("submitDateTime", ""),
                "currentReportReason": r.get("currentReportReason") or "",
            }
            for r in results
            if r.get("secCode") and r.get("docTypeCode") in _EDINET_PRIORITY_CODES
        ]
    except Exception as e:
        logger.warning(f"[trading] EDINET {date_str} fetch失敗: {e}")
        return []


def _fetch_edinet_news(code: str, days: int = 30, max_items: int = 5) -> list:
    """EDINET APIから指定銘柄の直近書類を取得。
    code: .T を除いたコード（例: "7203"）→ EDINETのsecCodeは末尾に"0"付加
    Returns: [{headline, headline_ja, url, date, source}]
    """
    # EDINETのsecCodeは4桁コード+"0"（例: "72030"）。英数混在コード(285A)も同様
    sec_code_edinet = code + "0"
    results = []
    seen: set = set()

    for days_ago in range(0, days):
        if len(results) >= max_items:
            break
        date_str = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")
        docs = _fetch_edinet_docs_for_date(date_str)
        for doc in docs:
            if doc["secCode"] != sec_code_edinet:
                continue
            doc_id    = doc["docID"]
            type_code = doc["docTypeCode"]
            type_lbl  = _EDINET_DOC_LABELS.get(type_code, f"書類({type_code})")
            desc      = doc.get("docDescription") or type_lbl
            reason    = doc.get("currentReportReason") or ""
            title     = f"【{type_lbl}】{desc}" + (f"（{reason}）" if reason else "")
            if title in seen:
                continue
            seen.add(title)
            # EDINETの書類閲覧URL
            view_url = f"https://disclosure2.edinet-fsa.go.jp/WZEK0040.aspx?{doc_id},,"
            dt_str   = doc.get("submitDateTime", "")[:10]  # "YYYY-MM-DD HH:MM" → 日付のみ
            results.append({
                "headline":    title,
                "headline_ja": title,
                "url":         view_url,
                "date":        dt_str,
                "source":      "EDINET",
            })
            if len(results) >= max_items:
                break

    return results


@st.cache_data(ttl=1800, show_spinner=False)
def _fetch_minkabu_jp_news(code: str, max_items: int = 6) -> list:
    """みんかぶの銘柄ニュースページから適時開示・ニュースを取得（TTL=30分）。
    code: .T を除いた銘柄コード（例: "7203", "8306", "285A"）
    Returns: [{headline, headline_ja, url, date, source}] | []（失敗時）
    """
    import re as _re
    try:
        from bs4 import BeautifulSoup as _BS
    except ImportError:
        return []
    try:
        url = f"https://minkabu.jp/stock/{code}/news"
        resp = requests.get(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                    "Version/17.0 Mobile/15E148 Safari/604.1"
                ),
                "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ja,en-US;q=0.7,en;q=0.3",
                "Referer":         "https://minkabu.jp/",
            },
            timeout=10,
        )
        if resp.status_code != 200:
            logger.warning(f"[trading] minkabu {code} → HTTP {resp.status_code}")
            return []

        soup = _BS(resp.text, "html.parser")
        results = []
        seen_titles: set = set()

        def _norm_date(raw: str) -> str:
            """日付テキストを YYYY-MM-DD 形式へ正規化"""
            m = _re.search(r"(\d{4})[/\-年](\d{1,2})[/\-月](\d{1,2})", raw)
            if m:
                return f"{m.group(1)}-{m.group(2).zfill(2)}-{m.group(3).zfill(2)}"
            # MM/DD 形式（年なし）→ 当年補完
            m2 = _re.search(r"(\d{1,2})[/月](\d{1,2})", raw)
            if m2:
                from datetime import datetime as _dt
                yr = _dt.now().year
                return f"{yr}-{m2.group(1).zfill(2)}-{m2.group(2).zfill(2)}"
            return raw.strip()[:10]

        def _is_valid_title(t: str) -> bool:
            if not t or len(t) < 5:
                return False
            # 株価数字・記号のみの要素を除外
            if _re.fullmatch(r"[\d,.\s円$%+\-()]+", t):
                return False
            return True

        # ── 戦略1: <time> 要素から逆引きで記事リンクを探す ──────────
        for time_el in soup.find_all("time"):
            raw_date = time_el.get("datetime") or time_el.get_text(strip=True)
            date_str = _norm_date(raw_date)

            # 親要素を最大5階層辿って <a> を探す
            node = time_el.parent
            a_el  = None
            for _ in range(6):
                if not node:
                    break
                a_el = node.find("a", href=True)
                if a_el and _is_valid_title(a_el.get_text(strip=True)):
                    break
                a_el = None
                node = node.parent

            if not a_el:
                continue
            title = a_el.get_text(strip=True)
            if not _is_valid_title(title) or title in seen_titles:
                continue

            href = a_el["href"]
            full_url = href if href.startswith("http") else f"https://minkabu.jp{href}"

            # カテゴリラベル（適時開示, 決算 など）を探す
            node2   = time_el.parent
            cat_txt = ""
            for _ in range(4):
                if not node2:
                    break
                for cls_kw in ["category", "tag", "label", "badge", "type"]:
                    cat_el = node2.find(class_=lambda c: c and cls_kw in " ".join(c).lower() if isinstance(c, list) else cls_kw in (c or "").lower())
                    if cat_el:
                        cat_txt = cat_el.get_text(strip=True)
                        break
                if cat_txt:
                    break
                node2 = node2.parent

            source = f"みんかぶ{'｜' + cat_txt if cat_txt else ''}"
            seen_titles.add(title)
            results.append({
                "headline":    title,
                "headline_ja": title,
                "url":         full_url,
                "date":        date_str,
                "source":      source,
            })
            if len(results) >= max_items:
                break

        # ── 戦略2: articleList / news-list 系クラスのリスト要素を探す ─
        if len(results) < 2:
            for container in soup.find_all(
                class_=lambda c: c and any(
                    kw in " ".join(c).lower() if isinstance(c, list) else kw in (c or "").lower()
                    for kw in ["articlelist", "newslist", "news_list", "article-list", "ly_article"]
                )
            ):
                for item in container.find_all(["li", "article", "div"], recursive=False):
                    a_el = item.find("a", href=True)
                    if not a_el:
                        continue
                    title = a_el.get_text(strip=True)
                    if not _is_valid_title(title) or title in seen_titles:
                        continue
                    href     = a_el["href"]
                    full_url = href if href.startswith("http") else f"https://minkabu.jp{href}"
                    time_el2 = item.find("time")
                    date_str = _norm_date(
                        (time_el2.get("datetime") or time_el2.get_text(strip=True)) if time_el2 else ""
                    )
                    seen_titles.add(title)
                    results.append({
                        "headline":    title,
                        "headline_ja": title,
                        "url":         full_url,
                        "date":        date_str,
                        "source":      "みんかぶ",
                    })
                    if len(results) >= max_items:
                        break
                if len(results) >= max_items:
                    break

        return results[:max_items]

    except Exception as e:
        logger.warning(f"[trading] minkabu fetch失敗 {code}: {e}")
        return []


@st.cache_data(ttl=1800, show_spinner=False)
def _fetch_trading_stock_data(ticker: str, is_jp: bool) -> dict:
    """個別銘柄の分析データを取得（テクニカル + ニュース）"""
    result = {}
    try:
        t_ticker = ticker if not is_jp else ticker
        raw = yf.download(t_ticker, period="6mo", auto_adjust=True, progress=False)
        if raw.empty:
            return result
        close = raw["Close"].dropna()
        if len(close) < 5:
            return result

        price   = float(close.iloc[-1])
        ma25    = float(close.rolling(25).mean().iloc[-1]) if len(close) >= 25 else None
        ma75    = float(close.rolling(75).mean().iloc[-1]) if len(close) >= 75 else None
        ret_1d  = float(close.iloc[-1] / close.iloc[-2] - 1) * 100 if len(close) >= 2 else None
        ret_5d  = float(close.iloc[-1] / close.iloc[-6] - 1) * 100 if len(close) >= 6 else None
        ret_20d = float(close.iloc[-1] / close.iloc[-21] - 1) * 100 if len(close) >= 21 else None

        # RSI
        delta = close.diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        rs    = gain / loss.replace(0, float("nan"))
        rsi   = float(100 - 100 / (1 + rs.iloc[-1])) if not rs.iloc[-1] != rs.iloc[-1] else None

        # 出来高（存在する場合）
        vol_ratio = None
        if "Volume" in raw.columns:
            vol = raw["Volume"].dropna()
            if len(vol) >= 20:
                vol_ratio = float(vol.iloc[-1] / vol.rolling(20).mean().iloc[-1])

        result["price"]     = round(price, 2)
        result["ma25"]      = round(ma25, 2) if ma25 else None
        result["ma75"]      = round(ma75, 2) if ma75 else None
        result["ret_1d"]    = round(ret_1d, 2) if ret_1d is not None else None
        result["ret_5d"]    = round(ret_5d, 2) if ret_5d is not None else None
        result["ret_20d"]   = round(ret_20d, 2) if ret_20d is not None else None
        result["rsi"]       = round(rsi, 1) if rsi else None
        result["vol_ratio"] = round(vol_ratio, 2) if vol_ratio else None
        result["close_series"] = close.tail(60).tolist()
        result["close_dates"]  = [d.strftime("%Y-%m-%d") for d in close.tail(60).index]

    except Exception as e:
        logger.warning(f"[trading] price fetch失敗 {ticker}: {e}")

    # ── ニュース取得（リンク + 日本語訳付き） ──────────────────────
    news_items = []  # [{headline, headline_ja, url, date, source}]
    try:
        if not is_jp:
            # 米国株: Finnhub
            api_key = FINNHUB_API_KEY
            if api_key:
                from_dt = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
                to_dt   = datetime.now().strftime("%Y-%m-%d")
                resp = requests.get(
                    "https://finnhub.io/api/v1/company-news",
                    params={"symbol": ticker, "from": from_dt, "to": to_dt, "token": api_key},
                    timeout=8,
                )
                if resp.status_code == 200:
                    for item in resp.json()[:6]:
                        headline = item.get("headline", "")
                        if not headline:
                            continue
                        ts = item.get("datetime", 0)
                        date_s = datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts else ""
                        news_items.append({
                            "headline":    headline,
                            "headline_ja": "",
                            "url":         item.get("url", ""),
                            "date":        date_s,
                            "source":      item.get("source", ""),
                        })
            # 一括翻訳（Groq, キャッシュ済みなら即返却）
            if news_items:
                en_headlines = tuple(n["headline"] for n in news_items)
                ja_list = _translate_headlines_to_ja(en_headlines)
                for i, n in enumerate(news_items):
                    n["headline_ja"] = ja_list[i] if i < len(ja_list) else n["headline"]
        else:
            # 日本株: みんかぶ + EDINET + TDnet を並列取得してマージ
            import concurrent.futures as _cf_news
            code = ticker.replace(".T", "")

            def _get_minkabu():
                return _fetch_minkabu_jp_news(code, max_items=6)

            def _get_edinet():
                return _fetch_edinet_news(code, days=30, max_items=4)

            def _get_tdnet():
                items = []
                for days_ago in range(0, 30):
                    if len(items) >= 4:
                        break
                    date_str = (datetime.now() - timedelta(days=days_ago)).strftime("%Y%m%d")
                    try:
                        tdnet_items = fetch_tdnet_items_for_date(date_str)
                        matched = [it for it in tdnet_items if code in (it.get("code") or "")]
                        for it in matched[:2]:
                            if len(items) >= 4:
                                break
                            title = it.get("title", "")
                            if title:
                                ymd = date_str
                                items.append({
                                    "headline":    title,
                                    "headline_ja": title,
                                    "url":         it.get("pdf_url", ""),
                                    "date":        f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}",
                                    "source":      "TDnet",
                                })
                    except Exception:
                        pass
                return items

            # 3ソースを並列取得（最大10秒待機）
            with _cf_news.ThreadPoolExecutor(max_workers=3) as _ex_news:
                _f_mk  = _ex_news.submit(_get_minkabu)
                _f_ed  = _ex_news.submit(_get_edinet)
                _f_td  = _ex_news.submit(_get_tdnet)
                mk_items = _f_mk.result(timeout=12) or []
                ed_items = _f_ed.result(timeout=12) or []
                td_items = _f_td.result(timeout=12) or []

            # マージ: みんかぶ優先（重複タイトルを除外）、次にEDINET、TDnet
            seen_titles: set = set()
            for _src_list in [mk_items, ed_items, td_items]:
                for _ni in _src_list:
                    _t = _ni.get("headline", "")
                    if _t and _t not in seen_titles:
                        seen_titles.add(_t)
                        news_items.append(_ni)

            # ④ Yahoo Finance Japan RSS（日本株専用フォールバック）
            if len(news_items) < 4:
                try:
                    _yj_url = f"https://finance.yahoo.co.jp/rss/news?code={code}"
                    _yj_resp = requests.get(_yj_url, timeout=6, headers={"User-Agent": "Mozilla/5.0"})
                    if _yj_resp.status_code == 200:
                        from xml.etree import ElementTree as _ET
                        _yj_root = _ET.fromstring(_yj_resp.text)
                        _yj_ns   = {"atom": "http://www.w3.org/2005/Atom"}
                        # RSS 2.0
                        for _item in _yj_root.iter("item"):
                            _ttl = (_item.findtext("title") or "").strip()
                            _lnk = (_item.findtext("link") or "").strip()
                            if _ttl and _ttl not in seen_titles and len(news_items) < 5:
                                seen_titles.add(_ttl)
                                news_items.append({
                                    "headline":    _ttl,
                                    "headline_ja": _ttl,
                                    "url":         _lnk,
                                    "date":        (_item.findtext("pubDate") or "")[:10],
                                    "source":      "Yahoo!ファイナンス",
                                })
                except Exception:
                    pass

            # ⑤ 全ソース合わせて3件未満なら yfinance
            if len(news_items) < 3:
                try:
                    yf_news = yf.Ticker(ticker).news or []
                    for article in yf_news[:max(0, 5 - len(news_items))]:
                        title = article.get("title", "")
                        link  = article.get("link", "")
                        if title and title not in seen_titles:
                            seen_titles.add(title)
                            news_items.append({
                                "headline":    title,
                                "headline_ja": title,
                                "url":         link,
                                "date":        "",
                                "source":      "yfinance",
                            })
                except Exception:
                    pass
    except Exception as e:
        logger.warning(f"[trading] news fetch失敗 {ticker}: {e}")

    result["news_items"] = news_items
    # AI プロンプト用に日本語優先のテキストリストも保持（後方互換）
    result["news"] = [
        n.get("headline_ja") or n.get("headline") for n in news_items
    ]

    # ── ファンダメンタルズ: PER推移・決算 ─────────────────────
    try:
        tk = yf.Ticker(ticker)
        info = tk.info or {}

        trailing_pe = info.get("trailingPE")
        forward_pe  = info.get("forwardPE")
        peg         = info.get("pegRatio")
        pbr         = info.get("priceToBook")
        eps_ttm     = info.get("trailingEps")
        eps_fwd     = info.get("forwardEps")
        rev_growth  = info.get("revenueGrowth")   # YoY
        earn_growth = info.get("earningsGrowth")  # YoY

        result["trailing_pe"]   = round(trailing_pe, 1) if trailing_pe else None
        result["forward_pe"]    = round(forward_pe, 1) if forward_pe else None
        result["peg"]           = round(peg, 2) if peg else None
        result["pbr"]           = round(pbr, 2) if pbr else None
        result["eps_ttm"]       = round(eps_ttm, 2) if eps_ttm else None
        result["eps_fwd"]       = round(eps_fwd, 2) if eps_fwd else None
        result["rev_growth"]    = round(rev_growth * 100, 1) if rev_growth else None
        result["earn_growth"]   = round(earn_growth * 100, 1) if earn_growth else None
        result["sector"]        = info.get("sector") or info.get("industry")
        result["name"]          = (info.get("shortName") or info.get("longName") or "").strip()

        # 次回決算日
        try:
            cal = tk.calendar
            if cal is not None:
                if isinstance(cal, dict):
                    ed = cal.get("Earnings Date", [])
                    result["next_earnings"] = str(ed[0])[:10] if ed else None
                elif isinstance(cal, pd.DataFrame) and "Value" in cal.columns:
                    ed = cal.loc["Earnings Date", "Value"] if "Earnings Date" in cal.index else None
                    result["next_earnings"] = str(ed)[:10] if ed else None
        except Exception:
            pass

        # 直近4四半期 EPS 実績 vs 予想
        try:
            eh = tk.earnings_history
            if eh is not None and not eh.empty:
                recent = eh.tail(4)[["epsEstimate", "epsActual", "surprisePercent"]].copy()
                recent.index = [str(i)[:7] for i in recent.index]
                eps_rows = []
                for quarter, row in recent.iterrows():
                    est  = row.get("epsEstimate")
                    act  = row.get("epsActual")
                    surp = row.get("surprisePercent")
                    if pd.notna(act):
                        eps_rows.append(
                            f"{quarter}: 実績 {act:.2f} / 予想 {est:.2f}"
                            + (f" (サプライズ {surp:+.1f}%)" if pd.notna(surp) else "")
                        )
                result["eps_history"] = eps_rows
        except Exception:
            pass

        # 過去5年の年次PER推移（近似：年次EPS + 各年末株価）
        try:
            fin = tk.financials  # 年次 income statement
            if fin is not None and not fin.empty and "Net Income" in fin.index:
                shares = info.get("sharesOutstanding", 0)
                if shares and shares > 0:
                    hist_annual = yf.download(ticker, period="5y", interval="1mo",
                                              auto_adjust=True, progress=False)
                    pe_history = []
                    for col in fin.columns[:4]:  # 直近4年
                        year = str(col)[:4]
                        ni   = fin.loc["Net Income", col]
                        if pd.isna(ni) or ni <= 0:
                            continue
                        eps_yr = ni / shares
                        try:
                            yr_prices = hist_annual["Close"].loc[year]
                            if isinstance(yr_prices, pd.DataFrame):
                                yr_prices = yr_prices.iloc[:, 0]
                            yr_close = float(yr_prices.dropna().iloc[-1])
                            pe = yr_close / eps_yr
                            if 0 < pe < 500:
                                pe_history.append(f"{year}年末: PER {pe:.1f}倍")
                        except Exception:
                            pass
                    if pe_history:
                        result["pe_history"] = pe_history
        except Exception:
            pass

    except Exception as e:
        logger.warning(f"[trading] fundamentals fetch失敗 {ticker}: {e}")

    return result


@st.cache_data(ttl=1800, show_spinner=False)
def _compute_portfolio_history() -> pd.DataFrame:
    """取引記録 × 日次終値でポートフォリオ資産推移を計算する。
    Returns: DataFrame(index=date, columns=[portfolio_value, invested_cost, pnl, pnl_pct])
    """
    df_trades, err = _load_trades()
    if err or df_trades.empty:
        return pd.DataFrame()

    df = df_trades.copy()
    df["date"]     = pd.to_datetime(df["date"])
    df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce").fillna(0)
    df["price"]    = pd.to_numeric(df["price"],    errors="coerce").fillna(0)
    df["fee"]      = pd.to_numeric(df["fee"],      errors="coerce").fillna(0)
    df = df.sort_values("date").reset_index(drop=True)

    tickers     = df["ticker"].unique().tolist()
    start_date  = df["date"].min() - timedelta(days=5)

    # 各ティッカーの日次終値を取得
    price_dict = {}
    for tk in tickers:
        try:
            raw = yf.download(tk, start=start_date, auto_adjust=True, progress=False)
            if raw.empty:
                continue
            close = raw["Close"]
            if isinstance(close, pd.DataFrame):
                close = close.iloc[:, 0]
            price_dict[tk] = close.rename(tk)
        except Exception:
            pass

    if not price_dict:
        return pd.DataFrame()

    price_df = pd.concat(price_dict.values(), axis=1).ffill()

    # 各営業日のポートフォリオ評価額・投資元本を計算
    rows = []
    for date in price_df.index:
        trades_so_far = df[df["date"] <= date]
        positions = {}
        invested  = 0.0
        for _, row in trades_so_far.iterrows():
            tk  = row["ticker"]
            qty = float(row["quantity"])
            prc = float(row["price"])
            fee = float(row["fee"])
            if row["action"] == "BUY":
                positions[tk] = positions.get(tk, 0.0) + qty
                invested += qty * prc + fee
            elif row["action"] == "SELL":
                avg_cost = invested / sum(positions.values()) if sum(positions.values()) > 0 else prc
                positions[tk] = positions.get(tk, 0.0) - qty
                invested -= qty * avg_cost  # 売却した分の取得コストを差し引く

        port_value = 0.0
        for tk, qty in positions.items():
            if qty > 0 and tk in price_df.columns:
                p = price_df.loc[date, tk]
                if not pd.isna(p):
                    port_value += qty * float(p)

        if port_value > 0:
            rows.append({
                "date":            date,
                "portfolio_value": round(port_value, 2),
                "invested_cost":   round(max(invested, 0), 2),
            })

    if not rows:
        return pd.DataFrame()

    result = pd.DataFrame(rows).set_index("date")
    result["pnl"]     = result["portfolio_value"] - result["invested_cost"]
    result["pnl_pct"] = (result["pnl"] / result["invested_cost"] * 100).where(result["invested_cost"] > 0)
    return result


def _delete_trade_row(sheet_row_num: int) -> bool:
    """Google Sheetsの指定行（1始まり、ヘッダー=1）を削除する。"""
    try:
        ws = _trading_ws("claude_trades", _TRADES_HEADERS)
        if not ws:
            return False
        ws.delete_rows(sheet_row_num)
        return True
    except Exception as e:
        logger.warning(f"[trading] trade削除失敗 row={sheet_row_num}: {e}")
        return False


def _calc_positions_from_df(df: "pd.DataFrame") -> dict:
    """取引記録 DataFrame から保有ポジションを計算する（平均取得単価法）。
    SELL時は平均取得コストを比例削減し avg_cost が正確になるよう修正済み。
    Returns: {ticker: {name, qty, cost, avg_cost}}
    """
    positions: dict = {}
    for _, row in df.iterrows():
        t     = str(row.get("ticker", "")).strip()
        qty   = float(row.get("quantity", 0) or 0)
        price = float(row.get("price", 0) or 0)
        fee   = float(row.get("fee", 0) or 0)
        if not t or qty <= 0:
            continue
        if row["action"] == "BUY":
            if t not in positions:
                positions[t] = {"name": str(row.get("name", t)), "qty": 0.0, "cost": 0.0}
            positions[t]["qty"]  += qty
            positions[t]["cost"] += qty * price + fee
        elif row["action"] == "SELL" and t in positions and positions[t]["qty"] > 0:
            held = positions[t]["qty"]
            sell_qty = min(qty, held)
            # 平均取得単価分だけコストを減らす（avg_cost が変わらないように）
            cost_per_share = positions[t]["cost"] / held
            positions[t]["cost"] -= cost_per_share * sell_qty
            positions[t]["qty"]  -= sell_qty
    return {
        t: {**v, "avg_cost": v["cost"] / v["qty"] if v["qty"] > 0 else 0}
        for t, v in positions.items() if v["qty"] > 0
    }


def _get_open_positions() -> dict:
    """保有中ポジションを計算して返す {ticker: {name, qty, avg_cost, cost}}"""
    df, _ = _load_trades()
    if df.empty:
        return {}
    return _calc_positions_from_df(df)


def _fmt_past_signals(past_signals: list | None) -> str:
    """過去シグナルリストをプロンプト用テキストに整形"""
    if not past_signals:
        return "  （分析履歴なし — 今回が初回分析です）"
    lines = []
    for s in past_signals[:5]:   # 直近5件まで
        date    = s.get("date", "")[:10]
        jdg     = s.get("judgment", "?")
        tgt     = s.get("target_price", "")
        sl      = s.get("stoploss", "")
        pe      = s.get("trailing_pe", "")
        summary = s.get("summary", "")[:80]
        lines.append(
            f"  {date}: 判断「{jdg}」 目標={tgt} 損切={sl} PER={pe}倍\n"
            f"    理由要約: {summary}"
        )
    return "\n".join(lines)


def _generate_ai_trade_signal(
    ticker: str, name: str, is_jp: bool, data: dict,
    position_ctx: dict | None = None,
    past_signals: list | None = None,
    mode: str = "growth",
    model_pref: str = "auto",
    market_ctx: dict | None = None,
) -> dict:
    """AIによる売買シグナルを生成。
    mode: "growth" | "momentum" | "autonomous"
    model_pref: "auto" | "gemini" | "groq" | "openrouter"
    market_ctx: _fetch_market_context_for_trading() の戻り値（任意）。
    """
    price    = data.get("price", "不明")
    ma25     = data.get("ma25", "不明")
    ma75     = data.get("ma75", "不明")
    rsi      = data.get("rsi", "不明")
    ret_1d   = data.get("ret_1d", "不明")
    ret_5d   = data.get("ret_5d", "不明")
    ret_20d  = data.get("ret_20d", "不明")
    vol_r    = data.get("vol_ratio", "不明")
    news     = data.get("news", [])
    currency = "円" if is_jp else "USD"
    news_str = "\n".join(f"・{n}" for n in news) if news else "直近ニュースなし"

    # ファンダメンタルズ情報をプロンプト用に整形
    trailing_pe  = data.get("trailing_pe")
    forward_pe   = data.get("forward_pe")
    peg          = data.get("peg")
    pbr          = data.get("pbr")
    eps_ttm      = data.get("eps_ttm")
    eps_fwd      = data.get("eps_fwd")
    rev_growth   = data.get("rev_growth")
    earn_growth  = data.get("earn_growth")
    next_earn    = data.get("next_earnings")
    eps_hist     = data.get("eps_history", [])
    pe_hist      = data.get("pe_history", [])

    funda_lines = []
    if trailing_pe:  funda_lines.append(f"- 実績PER: {trailing_pe}倍")
    if forward_pe:   funda_lines.append(f"- 予想PER: {forward_pe}倍")
    if peg:          funda_lines.append(f"- PEGレシオ: {peg}")
    if pbr:          funda_lines.append(f"- PBR: {pbr}倍")
    if eps_ttm:      funda_lines.append(f"- EPS（直近12ヶ月）: {eps_ttm} {currency}")
    if eps_fwd:      funda_lines.append(f"- EPS（予想）: {eps_fwd} {currency}")
    if rev_growth is not None:   funda_lines.append(f"- 売上高成長率（YoY）: {rev_growth:+.1f}%")
    if earn_growth is not None:  funda_lines.append(f"- 純利益成長率（YoY）: {earn_growth:+.1f}%")
    if next_earn:    funda_lines.append(f"- 次回決算発表予定: {next_earn}")

    funda_section = "\n".join(funda_lines) if funda_lines else "取得できませんでした"
    eps_hist_str  = "\n".join(f"  {e}" for e in eps_hist) if eps_hist else "  データなし"
    pe_hist_str   = "\n".join(f"  {p}" for p in pe_hist)  if pe_hist  else "  データなし"

    # ── ポジション情報 ───────────────────────────────────────
    if position_ctx:
        avg_cost      = position_ctx.get("avg_cost", 0)
        qty           = position_ctx.get("qty", 0)
        cur_price_val = data.get("price_raw") or (
            float(price) if str(price).replace(".", "").isdigit() else None
        )
        pnl     = (cur_price_val - avg_cost) * qty if cur_price_val else None
        pnl_pct = (cur_price_val / avg_cost - 1) * 100 if cur_price_val and avg_cost > 0 else None
        pnl_str = f"{pnl:+,.0f} {currency}（{pnl_pct:+.1f}%）" if pnl is not None else "取得中"
        position_section = f"""
【現在の保有ポジション】
- 保有株数: {int(qty)}株
- 平均取得単価: {avg_cost:.2f} {currency}
- 現在株価: {price} {currency}
- 含み損益: {pnl_str}
"""
        base_scenario = "保有ポジションをどう扱うべきか"
        judgment      = "**判断**: [追加買い / 保有継続 / 一部利確（半分売り） / 全株売却]"
    else:
        position_section = ""
        base_scenario = "新規エントリーを検討すべきか"
        judgment      = "**判断**: [強気買い / 買い / 様子見 / 見送り / 売り]"

    # ── モード別プロンプト分岐 ────────────────────────────────
    if mode == "momentum":
        mode_header  = "⚡ モメンタム追跡モード（タイムフレーム: 日足 + 週足）"
        analyst_role = "プロの短期トレーダー・モメンタムアナリスト"
        scenario     = f"{base_scenario}（モメンタム戦略：1〜4週間の価格上昇モメンタムを捉える）"
        analysis_focus = """
【分析の優先順位（モメンタムモード）】
1. 価格モメンタム: RSI・20日リターン・52週高値からの乖離・直近ブレイクアウト有無
2. 出来高確認: 出来高急増（対20日平均2倍以上）は強いシグナル。低出来高の上昇は信頼性低
3. 移動平均: 25日MA・75日MAとの位置関係。MAからの乖離率に注目
4. 相対強度: 市場全体 vs この銘柄の強弱
5. ニュース触媒: 決算サプライズ・製品発表・アップグレードなど即時反応を起こすイベント
※ PER・PBR等のバリュエーションは補助情報として参照する程度でよい"""
        target_line  = f"**利確目標（モメンタム）**: X{currency}（+Y%）｜直近レジスタンス or 目標水準"
        stop_line    = f"**損切りライン（タイト）**: X{currency}（-5〜8%以内）｜サポート割れで即撤退"
        period_line  = "**保有期間目安**: 1〜4週間（モメンタム喪失で即撤退）"
        extra_section = """
**モメンタム強度**: RSI・出来高・価格位置から「強い / 中程度 / 弱い」を判定
**エントリータイミング**: 今すぐ / 押し目待ち（何円台まで待つか） / ブレイクアウト確認後
**モメンタム終了シグナル**: どういう状態になったら撤退すべきか（出来高急減・RSI過熱 など）"""
        reason_note  = "テクニカル（モメンタム・出来高・価格位置）中心に200字以内で"
        output_fmt   = f"""🏷️ モード: {mode_header}

{judgment}

**理由**: （{reason_note}）

{extra_section}

{target_line}

{stop_line}

{period_line}

**決算リスク**: 次回決算（{next_earn or "日程不明"}）に向けた注意点"""

    elif mode == "autonomous":
        mode_header  = "🤖 AI自律モード（分析軸・フレームワークはAIが自律決定）"
        analyst_role = "完全自律型の株式アナリスト"
        scenario     = f"{base_scenario}"
        analysis_focus = """
【あなたへの指示】
提供されたデータを全て俯瞰し、この銘柄を「今どのような視点で見るべきか」を自分で判断してください。
- 長期成長株として保有すべきか
- 短期モメンタムで乗るべきか
- バリュー株・ターンアラウンドとして評価すべきか
- 決算プレイとして短期勝負すべきか
- または今は見送りが最善か

最初にあなたが選んだ「分析フレームワーク」を明示し、そのフレームで判断してください。
自由な視点で、あなたが最も投資家の意思決定に役立つと考える観点を優先してください。"""
        output_fmt = f"""🏷️ モード: {mode_header}

**あなたが選んだ分析フレームワーク**: （成長株/モメンタム/バリュー/決算プレイ/その他）とその理由を1文で

{judgment}

**核心的な根拠**: （あなたが最も重要と判断した視点から150〜300字で）

**テクニカル評価**: RSI={rsi}（過熱/適正/売られ過ぎの判定）| MA25={ma25}{currency}に対する株価位置 | トレンド方向

**最も注目すべき指標/データ**: このデータセットの中でこの銘柄の評価に決定的な情報を2〜3点

**目標株価**: X{currency}（+Y%）｜あなた独自の根拠

**損切りライン**: X{currency}（-Y%）｜このフレームが崩れる水準

**推奨保有期間**: （あなたが選んだフレームに応じて自由に設定）

**AIとしての独自見解**: 人間のアナリストが見落としがちな、データから読み取れる非線形なシグナルや反直感的な観点を1点

**決算リスク**: 次回決算（{next_earn or "日程不明"}）に向けた注意点"""
        analysis_focus = analysis_focus  # already set above
        # autonomous は output_fmt 直接使用するので他の変数は不要
        extra_section = reason_note = target_line = stop_line = period_line = ""

    else:  # growth モード（デフォルト）
        mode_header  = "🌱 長期育成モード（タイムフレーム: 月足 + 週足）"
        analyst_role = "プロの長期成長株アナリスト"
        scenario     = f"{base_scenario}（長期育成戦略：6ヶ月〜2年かけてファンダメンタル価値に収束させる）"
        analysis_focus = """
【分析の優先順位（長期育成モード）】
1. 成長性: EPS成長率・売上成長率・次回決算の予想サプライズ
2. バリュエーション: 現在PERは過去レンジ・同業他社比で割安/適正/割高か
3. 競合優位性・ビジネスモデル: 参入障壁・シェア拡大余地
4. 財務健全性: PEG・自己資本比率・フリーキャッシュフロー
5. テクニカル: 押し目での仕込み機会・トレンドの方向性（補助として使用）
※ 短期の株価変動よりも「2年後の企業価値」で判断する"""
        target_line  = f"**目標株価（長期）**: X{currency}（+Y%）｜予想EPS × 適正PER倍率で算出"
        stop_line    = f"**損切りライン（広め）**: X{currency}（-15〜20%）｜ファンダが崩れる水準"
        period_line  = "**保有期間目安**: 6ヶ月〜2年（決算ごとに再評価）"
        extra_section = (
            f"**テクニカル評価**: RSI={rsi}（70↑過熱/30↓売られ過ぎ）"
            f" | MA25={ma25}{currency}に対し株価は上/下"
            f" | MA75={ma75}{currency}との位置関係とトレンド方向をコメント\n"
            f"**バリュエーション評価**: 現在PER {trailing_pe or '不明'}倍は割安/適正/割高か。過去PER推移と比較した見解\n"
            "**成長ドライバー**: 今後2年で業績を牽引する要因を1〜2点\n"
            "**リスク要因**: ビジネスモデルを脅かす競合・規制・マクロ要因"
        )
        reason_note  = "ファンダメンタルズ・バリュエーション・成長性を中心に250字以内で"
        output_fmt   = f"""🏷️ モード: {mode_header}

{judgment}

**理由**: （{reason_note}）

{extra_section}

{target_line}

{stop_line}

{period_line}

**決算リスク**: 次回決算（{next_earn or "日程不明"}）に向けた注意点"""

    past_change_line = (
        "\n**前回との変化**: 前回分析からPER・株価・業績予想がどう変わったか1〜2文で"
        if past_signals else ""
    )

    # ── 売却後アクション共通セクション（全モード共通で末尾に追加）──
    post_sell_section = """

---

**💰 売却後の資金配分（売却・一部利確を推奨する場合のみ回答）**
**推奨**: [別銘柄・セクターに乗り換え / キャッシュ保有]
- **乗り換え推奨の場合**: 上記の市場コンテキスト（Fear&Greed指数・セクターRRGのLeading/Improving・予測モデルシグナル）を踏まえ、今資金を移すべきセクターや銘柄の特性を1〜2文で具体的に。恐怖圏なら逆張り対象も言及
- **キャッシュ保有推奨の場合**: その根拠（市場全体の転換点・VIX上昇・Fear&Greed過熱等）と、再エントリーを検討する具体的なトリガー（何がどうなったら動くか）"""
    output_fmt += post_sell_section

    prompt = f"""あなたは{analyst_role}です。以下のデータを元に「{name}（{ticker}）」について{scenario}を判断してください。

{analysis_focus}

【テクニカルデータ】
- 現在株価: {price} {currency}
- 25日移動平均: {ma25} {currency}
- 75日移動平均: {ma75} {currency}
- RSI(14): {rsi}
- 直近1日リターン: {ret_1d}%
- 直近5日リターン: {ret_5d}%
- 直近20日リターン: {ret_20d}%
- 出来高比率（対20日平均）: {vol_r}倍
{position_section}
【バリュエーション・決算】
{funda_section}

【直近4四半期 EPS（実績 vs 予想）】
{eps_hist_str}

【過去PER推移（年末株価 ÷ 年次EPS）】
{pe_hist_str}

【直近ニュース・開示】
{news_str}

【市場コンテキスト（windexモデル）】
{_format_market_ctx_for_prompt(market_ctx or {}, ticker_sector=data.get("sector"))}

【過去のAI分析履歴（同銘柄・新しい順）】
{_fmt_past_signals(past_signals)}

【出力形式】必ず以下の形式で回答してください：

{output_fmt}
{past_change_line}
"""
    try:
        text, model_used = _call_ai_for_trading(
            prompt, model_pref=model_pref, max_output_tokens=950, temperature=0.3
        )
        return {"text": text, "model": model_used, "error": None}
    except Exception as e:
        return {"text": "", "model": "", "error": str(e)}


@st.cache_data(ttl=300, show_spinner=False)
def _fetch_daily_changes_for_tickers(tickers_tuple: tuple) -> dict:
    """各ティッカーの本日騰落率・騰落額を返す。
    Returns: {ticker: {cur_price, prev_close, day_change, day_change_pct}}
    """
    result = {}
    for ticker in tickers_tuple:
        try:
            raw = yf.download(ticker, period="5d", auto_adjust=True, progress=False)
            if raw.empty:
                continue
            close = raw["Close"]
            if isinstance(close, pd.DataFrame):
                close = close.iloc[:, 0]
            close = close.dropna()
            if len(close) < 2:
                continue
            prev = float(close.iloc[-2])
            cur  = float(close.iloc[-1])
            result[ticker] = {
                "cur_price":      cur,
                "prev_close":     prev,
                "day_change":     cur - prev,
                "day_change_pct": (cur - prev) / prev * 100 if prev > 0 else 0.0,
            }
        except Exception:
            pass
    return result


@st.cache_data(ttl=1800, show_spinner=False)
def _fetch_market_context_for_trading() -> dict:
    """windex予測モデルの主要指標を集約してトレーディングAI用コンテキストを返す（TTL=30分）。
    Returns: {fear_greed, naaim, sector_quad, nikkei_pred, us_pred}
    """
    import concurrent.futures as _cf

    def _fg():
        try:
            fg = fetch_fear_greed_index()
            if fg:
                return {"score": fg.get("score"), "rating": fg.get("rating")}
        except Exception:
            pass
        return None

    def _naaim():
        try:
            df = fetch_naaim_data()
            if df is not None and not df.empty:
                latest = df.iloc[-1]
                return {"date": str(latest["Date"])[:10], "exposure": float(latest["NAAIM"])}
        except Exception:
            pass
        return None

    def _sector():
        try:
            sr = compute_sector_rotation()
            if sr.get("ok"):
                quadrants = {"Leading": [], "Weakening": [], "Improving": [], "Lagging": []}
                for _sym, s in sr["sectors"].items():
                    q = s.get("quadrant", "")
                    if q in quadrants:
                        quadrants[q].append(s["name"])
                return quadrants
        except Exception:
            pass
        return None

    def _nikkei():
        try:
            nr = compute_nikkei_prediction()
            if nr.get("ok"):
                snap = nr.get("snapshot", {})
                return {
                    "composite":        nr.get("composite"),
                    "prob_up_tomorrow": nr.get("prob_up_tomorrow"),
                    "prob_up_week":     nr.get("prob_up_week"),
                    "nikkei":           snap.get("日経平均", "?"),
                    "usdjpy":           snap.get("USD/JPY", "?"),
                    "vix":              snap.get("VIX", "?"),
                }
        except Exception:
            pass
        return None

    def _us():
        try:
            ur = compute_us_prediction("SP500")
            if ur.get("ok"):
                snap = ur.get("snapshot", {})
                return {
                    "composite":        ur.get("composite"),
                    "prob_up_tomorrow": ur.get("prob_up_tomorrow"),
                    "prob_up_week":     ur.get("prob_up_week"),
                    "sp500":            snap.get("S&P500", "?"),
                    "nasdaq":           snap.get("NASDAQ", "?"),
                    "vix":              snap.get("VIX", "?"),
                    "us10y":            snap.get("米10年金利", "?"),
                }
        except Exception:
            pass
        return None

    with _cf.ThreadPoolExecutor(max_workers=5) as ex:
        fg_f    = ex.submit(_fg)
        naaim_f = ex.submit(_naaim)
        sec_f   = ex.submit(_sector)
        nik_f   = ex.submit(_nikkei)
        us_f    = ex.submit(_us)
        return {
            "fear_greed":  fg_f.result(),
            "naaim":       naaim_f.result(),
            "sector_quad": sec_f.result(),
            "nikkei_pred": nik_f.result(),
            "us_pred":     us_f.result(),
        }


def _format_market_ctx_for_prompt(ctx: dict, ticker_sector: str | None = None) -> str:
    """market context dict → プロンプト埋め込み用テキスト。
    ticker_sector: 個別銘柄のセクター名（日本語）。指定時はRRGで該当セクターを強調。
    """
    if not ctx:
        return "  取得できませんでした"
    lines = []

    us  = ctx.get("us_pred")
    nik = ctx.get("nikkei_pred")
    if us:
        lines.append(
            f"  S&P500: {us.get('sp500','?')}  NASDAQ: {us.get('nasdaq','?')}"
            f"  VIX: {us.get('vix','?')}  米10年金利: {us.get('us10y','?')}"
        )
        comp = us.get("composite") or 0
        p_up = us.get("prob_up_tomorrow")
        p_wk = us.get("prob_up_week")
        if p_up is not None and p_wk is not None:
            lines.append(
                f"  米国株予測モデル合成シグナル: {comp:+.2f}"
                f"  明日↑確率: {p_up:.0f}%  来週↑確率: {p_wk:.0f}%"
            )
    if nik:
        lines.append(f"  日経平均: {nik.get('nikkei','?')}  USD/JPY: {nik.get('usdjpy','?')}")
        comp = nik.get("composite") or 0
        p_up = nik.get("prob_up_tomorrow")
        p_wk = nik.get("prob_up_week")
        if p_up is not None and p_wk is not None:
            lines.append(
                f"  日経予測モデル合成シグナル: {comp:+.2f}"
                f"  明日↑確率: {p_up:.0f}%  来週↑確率: {p_wk:.0f}%"
            )

    fg = ctx.get("fear_greed")
    if fg:
        score = fg.get("score")
        score_str = f"{score:.0f}" if score is not None else "?"
        lines.append(f"  Fear & Greed指数: {score_str}/100（{fg.get('rating','?')}）")

    naaim = ctx.get("naaim")
    if naaim:
        exp = naaim.get("exposure")
        exp_str = f"{exp:.1f}" if exp is not None else "?"
        lines.append(f"  NAAIM機関投資家エクスポージャー: {exp_str}%（{naaim.get('date','')}時点）")

    sq = ctx.get("sector_quad")
    if sq:
        lines.append("  セクターローテーション（RRGモデル）:")
        for label_jp, key in [
            ("Leading（強い）",       "Leading"),
            ("Weakening（勢い衰退）", "Weakening"),
            ("Improving（回復中）",   "Improving"),
            ("Lagging（弱い）",       "Lagging"),
        ]:
            names = sq.get(key, [])
            if names:
                marker = ""
                if ticker_sector:
                    if any(ticker_sector in n or n in ticker_sector for n in names):
                        marker = f"  ← ★{ticker_sector}"
                lines.append(f"    {label_jp}: {', '.join(names[:4])}{marker}")

    return "\n".join(lines) if lines else "  取得できませんでした"


def _generate_portfolio_wide_analysis(
    positions: dict,
    daily_changes: dict,
    mode: str = "growth",
    model_pref: str = "auto",
    market_ctx: dict | None = None,
) -> dict:
    """全保有銘柄を一括でAIに渡してポートフォリオ全体分析を生成する。
    market_ctx: _fetch_market_context_for_trading() の戻り値（任意）。
    Returns: {"text": str, "model": str, "error": str|None}
    """
    if not positions:
        return {"text": "", "model": "", "error": "保有銘柄がありません"}

    # ── 銘柄サマリーを作成 ───────────────────────────────────
    total_cost = sum(p["cost"] for p in positions.values())
    total_mkt  = sum(p["market_value"] for p in positions.values() if p.get("market_value"))
    total_gain = total_mkt - total_cost
    total_gp   = total_gain / total_cost * 100 if total_cost > 0 else 0

    # セクター集計
    sector_vals: dict[str, float] = {}
    for p in positions.values():
        sec = p.get("sector", "その他")
        val = p.get("market_value") or 0
        sector_vals[sec] = sector_vals.get(sec, 0.0) + val

    sector_lines = []
    for sec, val in sorted(sector_vals.items(), key=lambda x: -x[1]):
        pct = val / total_mkt * 100 if total_mkt > 0 else 0
        sector_lines.append(f"  {sec}: {pct:.1f}% ({val:,.0f})")

    # 銘柄別詳細
    pos_lines = []
    for ticker, p in positions.items():
        cur   = p.get("market_value") or 0
        cost  = p.get("cost") or 0
        gain  = cur - cost
        gp    = gain / cost * 100 if cost > 0 else 0
        cur_price = p.get("cur_price") or 0
        flag  = "🇯🇵" if p.get("is_jp") else "🇺🇸"
        sec   = p.get("sector", "その他")
        alloc = cur / total_mkt * 100 if total_mkt > 0 else 0
        day_chg = daily_changes.get(ticker, {}).get("day_change_pct", None)
        day_str = f"本日{day_chg:+.2f}%" if day_chg is not None else ""
        pos_lines.append(
            f"  {flag} {ticker} ({p['name']}) | "
            f"現在値: {cur_price:,.1f} | "
            f"評価額: {cur:,.0f} ({alloc:.1f}%) | "
            f"含み損益: {gain:+,.0f} ({gp:+.1f}%) | "
            f"セクター: {sec} {day_str}"
        )

    mode_desc = {
        "growth":     "長期育成（ファンダ重視・6ヶ月〜2年保有）",
        "momentum":   "モメンタム（テクニカル重視・1〜4週間保有）",
        "autonomous": "AI自律（分析軸をAIが自律決定）",
    }.get(mode, "長期育成")

    prompt = f"""あなたはプロのポートフォリオマネージャーです。以下の保有ポートフォリオ全体を分析し、具体的なアクション提言を行ってください。

投資戦略モード: {mode_desc}

【保有ポジション一覧】
{chr(10).join(pos_lines)}

【ポートフォリオ全体サマリー】
- 合計評価額: {total_mkt:,.0f}
- 合計投資元本: {total_cost:,.0f}
- 含み損益合計: {total_gain:+,.0f} ({total_gp:+.1f}%)
- 保有銘柄数: {len(positions)}銘柄

【セクター配分】
{chr(10).join(sector_lines)}

【市場コンテキスト（windexモデル）】
{_format_market_ctx_for_prompt(market_ctx or {})}

【出力形式】必ず以下の構成で回答してください：

## 🔍 リスク診断

**集中リスク**: セクター・銘柄・地域の偏りとそのリスク水準（高/中/低）

**相関リスク**: 保有銘柄間の値動きの相関（同じ方向に動くリスクがあるか）

**バランス評価**: 現在のポートフォリオ構成は{mode_desc}の戦略に対して適切か

**総合リスクスコア**: X/10（10が最高リスク）とその根拠

---

## ⚡ アクション優先順位 TOP3

今すぐ動くべき銘柄を優先順位をつけて3つ挙げてください（保有銘柄内で最も重要なアクションから順に）。

### 1位: [銘柄名 (ティッカー)]
- **推奨アクション**: [全株売却 / 一部利確（半分） / 追加買い / 保有継続]
- **理由**: 100字以内
- **具体的な水準**: 〇〇円/ドルを超えたら / 下回ったら実行

### 2位: [銘柄名 (ティッカー)]
- **推奨アクション**: ...
- **理由**: ...
- **具体的な水準**: ...

### 3位: [銘柄名 (ティッカー)]
- **推奨アクション**: ...
- **理由**: ...
- **具体的な水準**: ...

---

## 📋 ポートフォリオ改善提案

**最優先で改善すべき点**: 1〜2文
**追加検討すべき銘柄の方向性**: 現在のポートフォリオの弱点を補う銘柄の特性（具体的な銘柄名は不要）
**次回見直しタイミング**: いつ、何をトリガーにポートフォリオ全体を再評価すべきか
"""

    try:
        text, model_used = _call_ai_for_trading(
            prompt, model_pref=model_pref, max_output_tokens=1200, temperature=0.3
        )
        return {"text": text, "model": model_used, "error": None}
    except Exception as e:
        return {"text": "", "model": "", "error": str(e)}


@st.cache_data(ttl=3600, show_spinner=False)
def _generate_portfolio_ai_comment(positions_key: str, changes_json: str) -> dict:
    """AI によるポートフォリオ日次コメントを生成（JSON形式）。
    Args:
        positions_key: キャッシュキー用のティッカー文字列
        changes_json: JSON文字列 {ticker: {day_change_pct, cur_price, ...}}
    Returns:
        {"narrative": str, "bullets": [str, ...], "error": str|None}
    """
    import json as _json
    try:
        changes = _json.loads(changes_json)
    except Exception:
        return {"narrative": "", "bullets": [], "error": "データ解析失敗"}

    lines = []
    for ticker, chg in sorted(changes.items(), key=lambda x: x[1].get("day_change_pct", 0)):
        pct = chg.get("day_change_pct", 0)
        lines.append(f"  {ticker}: {pct:+.2f}%")

    if not lines:
        return {"narrative": "", "bullets": [], "error": "騰落データなし"}

    prompt = (
        "あなたはプロのポートフォリオアナリストです。\n"
        "以下の保有銘柄の本日騰落データを分析し、投資家向けの日本語コメントを生成してください。\n\n"
        "本日の各銘柄騰落:\n"
        + "\n".join(lines)
        + "\n\n"
        "以下のJSON形式のみ返してください（コードブロック・余分なテキスト不要）:\n"
        '{\n'
        '  "narrative": "ポートフォリオ全体の動きを3〜4文で説明。どのセクター・銘柄が牽引/足を引っ張ったか具体的に。",\n'
        '  "bullets": [\n'
        '    "一目でわかるポイント1（銘柄名と数字を含む）",\n'
        '    "一目でわかるポイント2",\n'
        '    "一目でわかるポイント3"\n'
        '  ]\n'
        '}'
    )

    res = call_ai_with_fallback(prompt, max_tokens=700, temperature=0.3)
    if res.get("error"):
        return {"narrative": "", "bullets": [], "error": res["error"]}

    import re as _re
    text = res.get("text", "")
    m = _re.search(r'\{[\s\S]*\}', text)
    if m:
        try:
            return _json.loads(m.group())
        except Exception:
            pass
    return {"narrative": text, "bullets": [], "error": None}


@st.cache_data(ttl=300, show_spinner=False)
def _fetch_usd_jpy() -> float:
    """USD/JPY レートを yfinance から取得。失敗時は 150.0 を返す。"""
    try:
        raw = yf.download("USDJPY=X", period="2d", auto_adjust=True, progress=False)
        if not raw.empty:
            close = raw["Close"]
            if isinstance(close, pd.DataFrame):
                close = close.iloc[:, 0]
            vals = close.dropna()
            if len(vals) > 0:
                return float(vals.iloc[-1])
    except Exception:
        pass
    return 150.0


@st.cache_data(ttl=900, show_spinner=False)
def _compute_portfolio_summary() -> dict:
    """ポートフォリオサマリー計算。Yahoo Finance スタイルの4カードに必要なデータを返す。
    Returns: {
        "positions": {ticker: {name, qty, avg_cost, cost, cur_price, market_value, gain, gain_pct, is_jp, sector}},
        "dividends": {ticker: {is_jp, divs_by_month: {YYYY-MM: amount_in_native_currency}}},
        "usd_jpy": float,
        "error": str | None,
    }
    """
    df_trades, err = _load_trades()
    if err:
        return {"positions": {}, "dividends": {}, "usd_jpy": 150.0, "error": err}
    if df_trades.empty:
        return {"positions": {}, "dividends": {}, "usd_jpy": 150.0, "error": None}

    usd_jpy = _fetch_usd_jpy()
    open_pos = _get_open_positions()
    if not open_pos:
        return {"positions": {}, "dividends": {}, "usd_jpy": usd_jpy, "error": None}

    positions = {}
    dividends = {}

    for ticker, pos in open_pos.items():
        is_jp = ticker.endswith(".T")
        cur_price = None

        # 現在値取得
        try:
            fi = yf.Ticker(ticker).fast_info
            cur_price = float(fi.get("lastPrice") or fi.get("last_price") or 0) or None
        except Exception:
            pass
        if not cur_price:
            try:
                raw = yf.download(ticker, period="2d", auto_adjust=True, progress=False)
                if not raw.empty:
                    close = raw["Close"]
                    if isinstance(close, pd.DataFrame):
                        close = close.iloc[:, 0]
                    vals = close.dropna()
                    if len(vals) > 0:
                        cur_price = float(vals.iloc[-1])
            except Exception:
                pass

        # セクター取得（失敗時は "その他"）
        sector = "その他"
        try:
            info = yf.Ticker(ticker).info
            raw_sector = info.get("sector", "") or ""
            sector_map = {
                "Technology": "テクノロジー",
                "Communication Services": "通信サービス",
                "Consumer Cyclical": "一般消費財",
                "Consumer Defensive": "生活必需品",
                "Healthcare": "ヘルスケア",
                "Financial Services": "金融",
                "Industrials": "資本財",
                "Basic Materials": "素材",
                "Energy": "エネルギー",
                "Utilities": "公益",
                "Real Estate": "不動産",
            }
            sector = sector_map.get(raw_sector, raw_sector or "その他")
        except Exception:
            pass

        # 配当履歴（過去6ヶ月）
        try:
            tk_obj = yf.Ticker(ticker)
            div_hist = tk_obj.dividends
            if div_hist is not None and len(div_hist) > 0:
                six_months_ago = pd.Timestamp.now(tz="UTC") - pd.DateOffset(months=6)
                recent_divs = div_hist[div_hist.index >= six_months_ago]
                divs_by_month = {}
                divs_detail = []
                for _dt, _amount in recent_divs.items():
                    _per_share = float(_amount)
                    _total     = _per_share * pos["qty"]
                    month_key  = _dt.strftime("%Y-%m")
                    divs_by_month[month_key] = divs_by_month.get(month_key, 0.0) + _total
                    divs_detail.append({
                        "date":      _dt.strftime("%Y-%m-%d"),
                        "per_share": _per_share,
                        "total":     _total,
                        "qty":       pos["qty"],
                    })
                dividends[ticker] = {
                    "is_jp":       is_jp,
                    "name":        pos["name"],
                    "divs_by_month": divs_by_month,
                    "divs_detail": divs_detail,
                }
        except Exception:
            pass

        qty        = pos["qty"]
        avg_cost   = pos["avg_cost"]
        cost_total = pos["cost"]
        mkt_val    = qty * cur_price if cur_price else None
        gain       = (mkt_val - cost_total) if mkt_val is not None else None
        gain_pct   = (gain / cost_total * 100) if (gain is not None and cost_total > 0) else None

        positions[ticker] = {
            "name":         pos["name"],
            "qty":          qty,
            "avg_cost":     avg_cost,
            "cost":         cost_total,
            "cur_price":    cur_price,
            "market_value": mkt_val,
            "gain":         gain,
            "gain_pct":     gain_pct,
            "is_jp":        is_jp,
            "sector":       sector,
        }

    return {
        "positions": positions,
        "dividends": dividends,
        "usd_jpy":   usd_jpy,
        "error":     None,
    }


_REC_CACHE_HEADERS = ["date", "mode", "tickers_key", "news_key", "text", "model", "created_at"]


def _make_rec_cache_key(tickers: list, stock_data_map: dict) -> tuple:
    """銘柄リスト＋各銘柄の最新ニュース見出しからキャッシュキーを生成。
    Returns: (tickers_key: str, news_key: str)
    """
    import hashlib as _hl
    tickers_key = ",".join(sorted(tickers))
    news_parts = []
    for t in sorted(tickers):
        data = stock_data_map.get(t, {})
        news_list = data.get("news") or []
        first = news_list[0][:60] if news_list else ""
        news_parts.append(f"{t}:{first}")
    news_key = _hl.md5("|".join(news_parts).encode()).hexdigest()[:12]
    return tickers_key, news_key


def _load_rec_cache(date: str, mode: str, tickers_key: str, news_key: str) -> dict | None:
    """Google Sheetsから推奨分析キャッシュを検索。ヒットすれば {"text", "model"} を返す。"""
    try:
        ws = _trading_ws("rec_cache", _REC_CACHE_HEADERS)
        if not ws:
            return None
        rows = ws.get_all_records()
        for r in reversed(rows):
            if (r.get("date") == date and r.get("mode") == mode and
                    r.get("tickers_key") == tickers_key and r.get("news_key") == news_key):
                return {"text": r.get("text", ""), "model": r.get("model", "")}
    except Exception as e:
        logger.warning(f"[trading] rec_cache読込失敗: {e}")
    return None


def _save_rec_cache(date: str, mode: str, tickers_key: str, news_key: str,
                    text: str, model: str) -> bool:
    """推奨分析結果をGoogle Sheetsにキャッシュ保存"""
    try:
        ws = _trading_ws("rec_cache", _REC_CACHE_HEADERS)
        if not ws:
            return False
        ws.append_row([
            date, mode, tickers_key, news_key,
            text, model,
            datetime.now(JST).strftime("%Y-%m-%d %H:%M"),
        ])
        return True
    except Exception as e:
        logger.warning(f"[trading] rec_cache保存失敗: {e}")
        return False


_INVEST_REC_CACHE_HEADERS = ["date", "budget", "model_type", "risk_type", "result_json", "ai_model", "created_at"]


def _load_invest_rec_cache(date: str, budget: int, model_type: str, risk_type: str) -> dict | None:
    import json as _json
    try:
        ws = _trading_ws("invest_rec_cache", _INVEST_REC_CACHE_HEADERS)
        if not ws:
            return None
        for r in reversed(ws.get_all_records()):
            if (r.get("date") == date and str(r.get("budget")) == str(budget)
                    and r.get("model_type") == model_type and r.get("risk_type") == risk_type):
                raw = r.get("result_json", "")
                if raw:
                    try:
                        return _json.loads(raw)
                    except ValueError:
                        logger.warning("[trading] invest_rec_cache JSON破損、スキップ")
                        continue
    except Exception as e:
        logger.warning(f"[trading] invest_rec_cache読込失敗: {e}")
    return None


def _save_invest_rec_cache(date: str, budget: int, model_type: str, risk_type: str,
                           result: dict, ai_model: str) -> bool:
    import json as _json
    try:
        ws = _trading_ws("invest_rec_cache", _INVEST_REC_CACHE_HEADERS)
        if not ws:
            return False
        js = _json.dumps(result, ensure_ascii=False, default=str)
        ws.append_row([date, budget, model_type, risk_type, js[:49000], ai_model,
                       datetime.now(JST).strftime("%Y-%m-%d %H:%M")])
        return True
    except Exception as e:
        logger.warning(f"[trading] invest_rec_cache保存失敗: {e}")
        return False


def _generate_investment_portfolio_rec(
    budget: int,
    model_type: str,       # "etf" | "individual"
    risk_type: str,        # "aggressive" | "balanced"
    market_ctx: dict,
    existing_holdings: list | None = None,
    model_pref: str = "auto",
    trading_mode: str = "growth",  # "growth" | "momentum" | "autonomous"
) -> dict:
    """予算・モデル・リスクプロファイル・トレーディングモードに応じた新規投資推奨ポートフォリオをAIが生成。
    Returns: {"portfolio": [...], "metrics": {...}, "model": str, "error": str|None}
    """
    fg_sc   = market_ctx.get("fg_score", 50) or 50
    fg_lbl  = market_ctx.get("fg_label", "")
    nk_pred = market_ctx.get("nikkei_pred_label", "")
    us_pred = market_ctx.get("us_pred_label", "")
    leading = market_ctx.get("sector_quad", {}).get("Leading", [])
    improving = market_ctx.get("sector_quad", {}).get("Improving", [])
    leading_str = " / ".join(leading[:4]) if leading else "不明"
    improving_str = " / ".join(improving[:3]) if improving else "不明"

    budget_str = f"{budget:,}円"
    model_desc = {
        "etf":        "ETF混合モデル（ETF＋個別株、分散重視）",
        "individual": "個別株モデル（ETF不使用、5〜8銘柄の個別株のみ・日米問わず）",
    }.get(model_type, model_type)
    risk_desc = {
        "aggressive": "リスク先行型（高成長・高ボラティリティ許容、期待リターン最大化）",
        "balanced":   "リスクリターン考慮型（シャープレシオ重視、最大ドローダウン抑制）",
    }.get(risk_type, risk_type)

    _mode_info = {
        "growth": (
            "🌱 長期育成モード",
            "ファンダメンタルズ（PER/EPS成長率・配当・財務健全性）を最優先。"
            "保有期間6ヶ月〜2年を想定した銘柄を選ぶ。短期ノイズに強い大型・中型株中心。"
            "損切ライン-15〜20%・目標=適正PER×予想EPSの水準。",
        ),
        "momentum": (
            "⚡ モメンタムモード",
            "テクニカル（RSI・移動平均上抜け・出来高急増・ブレイクアウト）を最優先。"
            "保有期間1〜4週間の中短期トレードを想定。上昇トレンド継続中の銘柄を優先。"
            "損切-5〜8%・目標=直近レジスタンス突破後の次の節目。",
        ),
        "autonomous": (
            "🤖 AI自律モード",
            "投資スタイルをAIが自律決定。成長株・モメンタム・バリュー・決算プレイ等を"
            "市場環境に応じて最適に組み合わせ。保有期間・損切ラインもAIが柔軟に判断。"
            "独自の視点で通常は注目されにくい銘柄も積極的に提案してよい。",
        ),
    }
    _mode_label, _mode_guide = _mode_info.get(trading_mode, _mode_info["growth"])

    holdings_str = "なし（新規投資）"
    if existing_holdings:
        holdings_str = " / ".join(existing_holdings[:8])

    etf_note = ""
    if model_type == "etf":
        etf_note = """
ETF候補例: QQQ(NDX100), SPY/VOO(S&P500), VGT(テクノロジー), XLF(金融),
  SOXX(半導体), SMH(半導体), IWM(小型株), EWJ/1321.T(日経), 2558.T(S&P500 JPY),
  2244.T(NASDAQ100 JPY), 1476.T(iSharesコアJリート)"""
    else:
        etf_note = "\n個別株のみ選定。ETF・投資信託は不可。日本株(.T)・米国株の両方を検討。"

    prompt = f"""あなたは日米株式ポートフォリオ設計の専門家です。
以下の条件で最適な投資ポートフォリオを提案してください。

【投資条件】
・投資予算: {budget_str}
・モデル: {model_desc}
・リスクプロファイル: {risk_desc}
・既存保有銘柄（重複避けること推奨）: {holdings_str}
{etf_note}

【投資スタンス（トレーディングモード）】
・モード: {_mode_label}
・{_mode_guide}

【現在の市場環境】
・Fear&Greed: {fg_sc:.0f} ({fg_lbl})
・日経225予測: {nk_pred}
・米国市場予測: {us_pred}
・RRGリーディングセクター: {leading_str}
・RRGインプルービングセクター: {improving_str}

【提案ルール】
・銘柄数: {5 if model_type == "individual" else 4}〜8銘柄/ETF
・日本株ティッカーは末尾に.T（例: 8306.T）
・各銘柄の比率合計は100%
・投資金額 = 予算 × 比率
・根拠は20字以内で端的に（モードの投資スタンスに沿った理由を記載）

【リスク指標の推計方法】
期待年間リターン: セクター・過去実績・市場環境から推定（%）
リスク(ボラティリティ): 年率標準偏差の推定（%）
シャープレシオ: (期待リターン - 1.5%) / ボラティリティ
最大ドローダウン推定: 過去の市場危機時の想定下落率（%、マイナス値）

以下のJSONのみで回答（前後のテキスト不要）:
{{
  "portfolio": [
    {{"ticker": "NVDA", "name": "NVIDIA", "flag": "🇺🇸", "allocation": 25, "amount": {int(budget*0.25)}, "rationale": "AI半導体独占・成長最大"}},
    {{"ticker": "8306.T", "name": "三菱UFJ", "flag": "🇯🇵", "allocation": 15, "amount": {int(budget*0.15)}, "rationale": "金利上昇恩恵・配当安定"}}
  ],
  "metrics": {{
    "expected_return": 18.0,
    "risk_volatility": 22.0,
    "sharpe_ratio": 0.75,
    "max_drawdown_estimate": -28.0,
    "comment": "ポートフォリオの特徴と注意点を50字以内で"
  }}
}}"""

    import json as _json_ip, re as _re  # ローカルスコープで明示的にインポート
    _err = "生成失敗（原因不明）"
    _model_used = ""
    try:
        text, _model_used = _call_ai_for_trading(
            prompt, model_pref=model_pref, max_output_tokens=1400, temperature=0.3
        )
        # JSONブロックを抽出（前後のテキストを除去）
        m = _re.search(r'\{[\s\S]*\}', text)
        if not m:
            _err = f"AIがJSON形式で返答しませんでした: {text[:300]}"
            logger.warning(f"[trading] invest_portfolio JSON未検出: {text[:300]}")
        else:
            try:
                parsed = _json_ip.loads(m.group())
            except ValueError:
                # JSON が途中で切れている場合、末尾を修復して再試行
                _raw = m.group().rstrip().rstrip(",")
                if _raw.count("[") > _raw.count("]"):
                    _raw += "]"
                if _raw.count("{") > _raw.count("}"):
                    _raw += "}"
                parsed = _json_ip.loads(_raw)
            parsed["model"] = _model_used
            parsed["error"] = None
            return parsed
    except ValueError as e:
        _err = f"JSON解析失敗: {str(e)[:120]}"
        logger.warning(f"[trading] invest_portfolio JSON解析失敗: {e}")
    except Exception as e:
        _err = f"AI呼び出し失敗: {str(e)[:120]}"
        logger.warning(f"[trading] invest_portfolio生成失敗: {e}")
    return {"portfolio": [], "metrics": {}, "model": _model_used, "error": _err}


def _generate_full_portfolio_recommendation(
    positions: dict,
    stock_data_map: dict,
    market_ctx: dict,
    mode: str = "growth",
    model_pref: str = "auto",
) -> dict:
    """windex市場コンテキスト＋各銘柄のIR・テクニカルを合わせた総合推奨分析。
    stock_data_map: {ticker: _fetch_trading_stock_data() の戻り値}
    Returns: {"text": str, "model": str, "error": str|None}
    """
    if not positions:
        return {"text": "", "model": "", "error": "保有銘柄がありません"}

    mode_desc = {
        "growth":     "長期育成（ファンダ重視・6ヶ月〜2年保有）",
        "momentum":   "モメンタム（テクニカル重視・1〜4週間保有）",
        "autonomous": "AI自律（分析軸をAIが自律決定）",
    }.get(mode, "長期育成")

    total_cost = sum(p.get("cost", 0) for p in positions.values())
    total_mkt  = sum(p.get("market_value") or 0 for p in positions.values())

    stock_blocks = []
    for ticker, p in positions.items():
        data   = stock_data_map.get(ticker, {})
        is_jp  = ticker.endswith(".T")
        cur    = "円" if is_jp else "USD"
        flag   = "🇯🇵" if is_jp else "🇺🇸"
        cost   = p.get("cost", 0)
        mval   = p.get("market_value") or 0
        gain   = mval - cost
        gp     = gain / cost * 100 if cost > 0 else 0
        alloc  = mval / total_mkt * 100 if total_mkt > 0 else 0
        price   = data.get("price", "-")
        rsi     = data.get("rsi", "-")
        ma25    = data.get("ma25", "-")
        ma75    = data.get("ma75", "-")
        ret_1d  = data.get("ret_1d")
        ret_5d  = data.get("ret_5d")
        vol_r   = data.get("vol_ratio")
        sec     = data.get("sector") or p.get("sector", "その他")
        ret_str_parts = []
        if ret_1d is not None: ret_str_parts.append(f"1日:{ret_1d:+.1f}%")
        if ret_5d is not None: ret_str_parts.append(f"5日:{ret_5d:+.1f}%")
        if vol_r  is not None: ret_str_parts.append(f"出来高比:{vol_r:.1f}倍")
        ret_str = " | ".join(ret_str_parts) or "-"
        news_lines = [f"    ・{n}" for n in (data.get("news") or [])[:4] if n]
        block = (
            f"【{flag} {ticker} | {p['name']}】\n"
            f"  現在値: {price}{cur} | RSI: {rsi} | MA25: {ma25}{cur} | MA75: {ma75}{cur}\n"
            f"  リターン: {ret_str}\n"
            f"  評価額: {mval:,.0f}{cur}({alloc:.1f}%) | 含み: {gain:+,.0f}{cur}({gp:+.1f}%)\n"
            f"  取得単価: {p.get('avg_cost',0):.1f}{cur} | 保有: {int(p.get('qty',0))}株\n"
            f"  セクター: {sec}\n"
        )
        if news_lines:
            block += "  最新IR・ニュース:\n" + "\n".join(news_lines) + "\n"
        stock_blocks.append(block)

    total_gain = total_mkt - total_cost
    total_gp   = total_gain / total_cost * 100 if total_cost > 0 else 0

    prompt = f"""あなたはプロの株式アナリストです。以下の市場モデルデータと各企業のIR・テクニカル情報を総合分析し、具体的な推奨アクションを提示してください。
投資戦略モード: {mode_desc}

━━━ 市場環境（windexモデル）━━━
{_format_market_ctx_for_prompt(market_ctx)}

━━━ 保有銘柄別 詳細情報 ━━━
{"".join(stock_blocks)}
ポートフォリオ合計: 評価額 {total_mkt:,.0f} | 元本 {total_cost:,.0f} | 損益 {total_gain:+,.0f}({total_gp:+.1f}%)

━━━ 出力形式（必ずこの構成で日本語で回答）━━━

## 📊 市場環境サマリー
Fear&Greed指数・NAAIM・セクターRRG・Nikkei/US予測モデルの具体的な数値を引用しながら、現在の相場環境（リスクオン/中立/リスクオフ）を3〜4文で説明すること。

---

## 🏆 銘柄別推奨アクション

各保有銘柄について必ず以下のフォーマットで記載（全銘柄を網羅すること）：

### [銘柄名（ティッカー）]
- **推奨**: [🔴 売却 / 🟡 一部利確 / 🟢 保有継続 / 💙 追加買い]
- **テクニカル評価**: RSI=XX（70↑過熱/30↓売られ過ぎ）| MA25に対して株価は上/下 | 5日リターンとモメンタム方向
- **IR・ニュース評価**: 最新の開示・ニュースが株価にとってポジティブ/ネガティブかを1〜2文
- **市場環境との整合性**: Fear&Greed・RRGセクター位置・予測モデルシグナルと当銘柄の方向性が一致しているか
- **アクション水準**: 具体的な価格や条件（○○円/ドルを超えたら / 下回ったら実行）

---

## 🎯 総合アクションプラン
1. **今週中にやること**: 最優先アクション（銘柄・金額・条件）
2. **来月までに検討すること**: 中期的なポートフォリオ調整方向
3. **市場環境が変わったらやること**: Fear&Greed/NAAIM/VIXが逆転したときのトリガーと対応
"""
    try:
        text, model_used = _call_ai_for_trading(
            prompt, model_pref=model_pref, max_output_tokens=2000, temperature=0.25
        )
        return {"text": text, "model": model_used, "error": None}
    except Exception as e:
        return {"text": "", "model": "", "error": str(e)}


def render_claude_trading_project():
    """🤖 Claude 個別株トレーディングプロジェクト"""
    st.markdown('<a id="claude-trading"></a>', unsafe_allow_html=True)
    st.markdown(
        '<div style="background:linear-gradient(135deg,#0a0f1e,#0d1b2a,#0f2744);'
        'border-radius:12px;padding:14px 20px;margin-bottom:12px;">'
        '<div style="font-size:20px;font-weight:800;color:#60a5fa">🤖 Claude 個別株トレーディングプロジェクト</div>'
        '<div style="font-size:12px;color:#94a3b8;margin-top:2px">'
        'Claudeが中期売買シグナルを生成 → 手動執行 → 約定記録 → 損益追跡</div>'
        '</div>',
        unsafe_allow_html=True,
    )

    # ── 投資戦略モード切替 ─────────────────────────────────────
    _cur_mode = st.session_state.get("trading_mode", "growth")

    _MODE_DEFS = [
        {
            "key":    "growth",
            "emoji":  "🌱",
            "label":  "長期育成モード",
            "sub":    "ファンダメンタルズ重視 · 保有期間 6ヶ月〜2年",
            "detail": "PER/EPS成長/競合優位性 · 損切 -15〜20% · 目標=適正PER×予想EPS",
            "color":  "#4ade80", "sub_color": "#86efac",
            "border": "#22c55e", "bg": "#052e16",
        },
        {
            "key":    "momentum",
            "emoji":  "⚡",
            "label":  "モメンタムモード",
            "sub":    "テクニカル重視 · 保有期間 1〜4週間",
            "detail": "RSI/出来高/ブレイクアウト · 損切 -5〜8% · 目標=直近レジスタンス",
            "color":  "#fbbf24", "sub_color": "#fcd34d",
            "border": "#f59e0b", "bg": "#1c1400",
        },
        {
            "key":    "autonomous",
            "emoji":  "🤖",
            "label":  "AI自律モード",
            "sub":    "AIが分析軸を自律決定 · 保有期間は状況次第",
            "detail": "成長/モメンタム/バリュー/決算プレイ etc. を最適選択 · 独自見解あり",
            "color":  "#a78bfa", "sub_color": "#c4b5fd",
            "border": "#8b5cf6", "bg": "#1e0040",
        },
    ]

    mode_cols = st.columns(3)
    for _md, _col in zip(_MODE_DEFS, mode_cols):
        _is_sel = _cur_mode == _md["key"]
        _border = _md["border"] if _is_sel else "#334155"
        _bg     = _md["bg"]     if _is_sel else "#0f172a"
        _check  = (f'<div style="margin-top:6px;font-size:11px;color:{_md["color"]};font-weight:600">✓ 選択中</div>'
                   if _is_sel else "")
        _col.markdown(
            f'<div style="background:{_bg};border:2px solid {_border};border-radius:10px;'
            f'padding:12px 14px">'
            f'<div style="font-size:14px;font-weight:700;color:{_md["color"]}">'
            f'{_md["emoji"]} {_md["label"]}</div>'
            f'<div style="font-size:11px;color:{_md["sub_color"]};margin-top:3px">{_md["sub"]}</div>'
            f'<div style="font-size:10px;color:#64748b;margin-top:3px">{_md["detail"]}</div>'
            f'{_check}</div>',
            unsafe_allow_html=True,
        )
        if _col.button(
            f'{_md["emoji"]} {"選択中" if _is_sel else "切替"}',
            key=f'btn_mode_{_md["key"]}',
            type="primary" if _is_sel else "secondary",
            use_container_width=True,
        ):
            st.session_state["trading_mode"] = _md["key"]
            st.rerun()

    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    tab_signal, tab_trade, tab_pnl, tab_hist, tab_summary = st.tabs([
        "🤖 AI分析・シグナル", "✏️ 取引記録入力",
        "💰 損益・ポートフォリオ", "📈 資産推移", "💹 サマリー",
    ])

    # ── タブ①: AI分析・シグナル ────────────────────────────────
    with tab_signal:
        open_pos = _get_open_positions()            # 保有中ポジション

        # 選択肢: 保有銘柄のみ
        all_options = {}
        if open_pos:
            for ticker, pos in open_pos.items():
                is_jp_pos = ticker.endswith(".T")
                cur = "円" if is_jp_pos else "USD"
                avg = pos.get("avg_cost", 0)
                # pos["name"] がティッカーと同じ（未入力）なら正式名称を取得
                _disp_name = pos.get("name") or ticker
                if _disp_name == ticker or not _disp_name:
                    _disp_name = _get_stock_display_name(ticker)
                lbl = f"📂 {ticker} — {_disp_name}（取得単価 {avg:.1f}{cur}）"
                all_options[lbl] = {
                    "ticker": ticker, "name": _disp_name,
                    "market": "JP" if is_jp_pos else "US",
                    "position_ctx": pos,
                }

        if not all_options:
            st.info("保有銘柄がありません。「取引記録入力」タブから取引を登録してください。")
        else:
            # ── 銘柄データキャッシュ状態バー ────────────────────────────────
            _today_str  = datetime.now(JST).strftime("%Y-%m-%d")
            _cache_stat = _get_stock_cache_status(list(open_pos.keys()))
            _all_cached = all(v["cached"] for v in _cache_stat.values()) if _cache_stat else False
            _n_cached   = sum(1 for v in _cache_stat.values() if v["cached"])
            _n_total    = len(open_pos)
            _last_upd   = max(
                (v["updated_at"] for v in _cache_stat.values() if v["cached"]),
                default="",
            )
            _cache_color = "#4ade80" if _all_cached else ("#fbbf24" if _n_cached > 0 else "#ef4444")
            st.markdown(
                f'<div style="background:#0f172a;border:1px solid {_cache_color}33;'
                f'border-radius:8px;padding:8px 14px;margin-bottom:8px;'
                f'display:flex;align-items:center;gap:10px;">'
                f'<span style="font-size:12px;color:{_cache_color};font-weight:700">'
                f'📦 キャッシュ: {_n_cached}/{_n_total}銘柄</span>'
                + (f'<span style="font-size:11px;color:#64748b">最終更新: {_last_upd}</span>' if _last_upd else '')
                + f'<span style="font-size:11px;color:#475569">（本日のデータをSheets保存・AI分析時に即利用）</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

            _cache_col1, _cache_col2 = st.columns([2, 1])
            if _cache_col1.button(
                "📡 全保有銘柄データをキャッシュ更新",
                key="btn_cache_update",
                help="みんかぶ・EDINET・TDnetから全銘柄を並列取得してGoogle Sheetsに保存",
                type="secondary" if _all_cached else "primary",
            ):
                import concurrent.futures as _cf_cache
                _prog = st.progress(0, text="銘柄データ取得中...")
                _results_cache = {}

                def _fetch_and_cache(t_item):
                    _t, _p = t_item
                    _d = _fetch_trading_stock_data(_t, _t.endswith(".T"))
                    if _d:
                        _save_stock_data_cache(_t, _today_str, _d)
                    return _t, _d

                with _cf_cache.ThreadPoolExecutor(max_workers=5) as _ex_c:
                    _futs_c = {_ex_c.submit(_fetch_and_cache, item): item[0]
                               for item in open_pos.items()}
                    _done_c = 0
                    for _fut_c in _cf_cache.as_completed(_futs_c):
                        try:
                            _t2, _d2 = _fut_c.result()
                            _results_cache[_t2] = _d2
                        except Exception:
                            pass
                        _done_c += 1
                        _prog.progress(_done_c / _n_total,
                                       text=f"取得完了: {_done_c}/{_n_total}銘柄")
                _prog.empty()
                st.success(f"✅ {len(_results_cache)}銘柄のデータをキャッシュしました")
                st.session_state["_prefetched_stock_data"] = _results_cache
                st.rerun()

            # 銘柄別キャッシュ状態をチップで表示
            _chip_html = ""
            for _tk in open_pos:
                _cs = _cache_stat.get(_tk.upper(), {})
                _c_ok = _cs.get("cached", False)
                _chip_color  = "#166534" if _c_ok else "#7f1d1d"
                _chip_border = "#4ade80" if _c_ok else "#ef4444"
                _chip_icon   = "✓" if _c_ok else "✗"
                _chip_html += (
                    f'<span style="background:{_chip_color};border:1px solid {_chip_border};'
                    f'border-radius:4px;padding:2px 7px;font-size:11px;color:#f1f5f9;'
                    f'margin-right:4px">{_chip_icon} {_tk}</span>'
                )
            if _chip_html:
                st.markdown(
                    f'<div style="margin-bottom:10px">{_chip_html}</div>',
                    unsafe_allow_html=True,
                )

            st.markdown(
                '<div style="border-top:1px solid #1e293b;margin:6px 0 10px"></div>',
                unsafe_allow_html=True,
            )

            # ── アロケーション試算 ───────────────────────────────────────
            st.markdown(
                '<div style="font-size:13px;font-weight:600;color:#38bdf8;'
                'margin:4px 0 8px">── 推奨アロケーション試算 ──</div>',
                unsafe_allow_html=True,
            )
            st.markdown(
                '<div style="background:#0c1a2e;border:1px solid #0ea5e9;border-radius:8px;'
                'padding:8px 14px;margin-bottom:8px;font-size:12px;color:#7dd3fc">'
                '📊 株価位置（MA25/75）・モメンタム・RSI・セクターRRGの強さ・Fear&Greed を数値スコア化して'
                '推奨配分比率を自動計算します。あくまで参考値です。'
                '</div>',
                unsafe_allow_html=True,
            )

            _ab1, _ab2 = st.columns([2, 1])
            if _ab1.button("📊 アロケーション試算を実行", key="btn_alloc", type="secondary"):
                with st.spinner("市場データ・銘柄スコア計算中..."):
                    _alloc_mktctx = _fetch_market_context_for_trading()
                    _pf_alloc = st.session_state.get("_prefetched_stock_data", {})

                    def _alloc_fetch_one(t_item):
                        _t, _p = t_item
                        try:
                            if _t in _pf_alloc:
                                return _t, _pf_alloc[_t]
                            return _t, _fetch_trading_stock_data_with_cache(_t, _t.endswith(".T"))
                        except Exception:
                            return _t, {}

                    import concurrent.futures as _cf_alloc
                    _alloc_data_map = {}
                    with _cf_alloc.ThreadPoolExecutor(max_workers=6) as _ex_alloc:
                        _futs_alloc = {_ex_alloc.submit(_alloc_fetch_one, item): item[0]
                                       for item in open_pos.items()}
                        for _fut_alloc in _cf_alloc.as_completed(_futs_alloc):
                            try:
                                _at, _ad = _fut_alloc.result()
                                _alloc_data_map[_at] = _ad
                            except Exception:
                                pass

                # AIスコアなしでまず試算
                _alloc_result = _calc_allocation_recommendations(
                    open_pos, _alloc_data_map, _alloc_mktctx
                )
                st.session_state["_alloc_result"]   = _alloc_result
                st.session_state["_alloc_data_map"] = _alloc_data_map
                st.session_state["_alloc_mktctx"]   = _alloc_mktctx
                # AIスコアは別途リセット
                st.session_state.pop("_ai_scores", None)

            # 「🤖 AIスコアを統合」ボタン（試算後にのみ表示）
            _alloc_res = st.session_state.get("_alloc_result")
            if _alloc_res:
                _ai_sc_existing = st.session_state.get("_ai_scores")
                _ai_btn_label = (
                    "🔄 AIスコアを再生成" if _ai_sc_existing else "🤖 AIスコアを統合（ニュース・ファンダも評価）"
                )
                _ai_model_sel = st.session_state.get("rec_model_sel", "🔄 自動（Gemini→Groq→OpenRouter）")
                _ai_mp = {
                    "🔄 自動（Gemini→Groq→OpenRouter）": "auto",
                    "🟡 Gemini（Google）":               "gemini",
                    "⚡ Groq（Llama-3.3 70B・高速）":    "groq",
                    "🌐 OpenRouter（DeepSeek/Qwen等）":  "openrouter",
                }.get(_ai_mp if isinstance((_ai_mp := st.session_state.get("rec_model_sel","🔄 自動（Gemini→Groq→OpenRouter）")), str) else "", "auto")

                _ai_btn_col1, _ai_btn_col2 = st.columns([2, 1])
                _ai_blend_val = _ai_btn_col2.slider(
                    "AIスコアの重み", 0.0, 1.0, 0.5, 0.1,
                    key="ai_blend_slider",
                    help="0=ルールのみ / 0.5=均等ブレンド / 1.0=AIのみ",
                )
                if _ai_btn_col1.button(_ai_btn_label, key="btn_ai_score", type="primary"):
                    _cur_mode_alloc = st.session_state.get("trading_mode", "growth")
                    _tk_key_alloc   = ",".join(sorted(open_pos.keys()))
                    _today_alloc    = datetime.now(JST).strftime("%Y-%m-%d")

                    # キャッシュ確認
                    _cached_ai = _load_ai_score_cache(_today_alloc, _tk_key_alloc, _cur_mode_alloc)
                    if _cached_ai:
                        _ai_scores_raw = _cached_ai["scores"]
                        _ai_model_name = _cached_ai["model"] + " 📋"
                        st.info("📋 本日のAIスコアキャッシュを使用")
                    else:
                        with st.spinner("AI が各銘柄を評価中..."):
                            _adm = st.session_state.get("_alloc_data_map", {})
                            _amc = st.session_state.get("_alloc_mktctx", {})
                            _ai_scores_raw = _generate_ai_allocation_scores(
                                open_pos, _adm, _amc,
                                mode=_cur_mode_alloc,
                                model_pref=_ai_mp,
                            )
                            _ai_model_name = _ai_scores_raw.pop("_model", "")
                            # キャッシュ保存
                            _save_ai_score_cache(
                                _today_alloc, _tk_key_alloc, _cur_mode_alloc,
                                _ai_scores_raw, _ai_model_name,
                            )

                    st.session_state["_ai_scores"]      = _ai_scores_raw
                    st.session_state["_ai_model_name"]  = _ai_model_name
                    # AIスコアをブレンドして再計算
                    _adm2 = st.session_state.get("_alloc_data_map", {})
                    _amc2 = st.session_state.get("_alloc_mktctx", {})
                    _new_alloc = _calc_allocation_recommendations(
                        open_pos, _adm2, _amc2,
                        ai_scores=_ai_scores_raw,
                        ai_blend=_ai_blend_val,
                    )
                    st.session_state["_alloc_result"] = _new_alloc

            # ─── アロケーション結果表示 ────────────────────────────────
            _alloc_res = st.session_state.get("_alloc_result")
            if _alloc_res:
                _meta      = _alloc_res.get("_meta", {})
                fg_sc      = _meta.get("fg_score", 50)
                fg_desc    = _meta.get("fg_desc", "")
                fg_mult    = _meta.get("fg_mult", 1.0)
                has_ai     = _meta.get("has_ai", False)
                ai_blend   = _meta.get("ai_blend", 0.5)
                _bm_qqq    = _meta.get("bm_qqq_score", 0.0)
                _bm_n225   = _meta.get("bm_n225_score", 0.0)

                # F&G バー
                fg_color = ("#ef4444" if fg_sc < 25 else
                            "#f97316" if fg_sc < 45 else
                            "#eab308" if fg_sc < 55 else
                            "#22c55e" if fg_sc < 75 else "#a3e635")
                _ai_badge = (
                    f'<span style="font-size:11px;color:#a78bfa;margin-left:10px">'
                    f'🤖 AI統合済（重み{ai_blend:.0%}）'
                    f' — {st.session_state.get("_ai_model_name","")}</span>'
                    if has_ai else ""
                )
                st.markdown(
                    f'<div style="background:#0f172a;border:1px solid {fg_color}44;'
                    f'border-radius:8px;padding:8px 14px;margin-bottom:10px">'
                    f'<span style="font-size:12px;color:{fg_color};font-weight:700">'
                    f'😰 Fear&Greed: {fg_sc:.0f}</span>'
                    f'<span style="font-size:11px;color:#94a3b8;margin-left:8px">{fg_desc}</span>'
                    f'<span style="font-size:11px;color:#64748b;margin-left:8px">×{fg_mult:.2f}</span>'
                    f'{_ai_badge}'
                    f'</div>',
                    unsafe_allow_html=True,
                )

                # 銘柄テーブル
                _alloc_tickers = [t for t in _alloc_res if t != "_meta"]
                _sort_key = "combined_score" if has_ai else "adjusted_score"
                _alloc_sorted = sorted(
                    _alloc_tickers,
                    key=lambda t: _alloc_res[t].get(_sort_key, 0),
                    reverse=True,
                )

                # ヘッダー（AIあり: 8列 / なし: 7列）
                _COL_AI  = [1.8, 0.85, 0.85, 0.85, 1.1, 1.1, 1.0, 2.4]
                _COL_NO  = [2.0, 0.9,  0.9,  0.9,  1.0, 1.0, 3.0]
                _HDR_AI  = ["銘柄", "現在%", "推奨%", "pp差", "ルール", "AI", "vs BM", "シグナル"]
                _HDR_NO  = ["銘柄", "現在%", "推奨%", "pp差", "スコア", "vs BM", "シグナル"]
                _hdr_cols   = st.columns(_COL_AI if has_ai else _COL_NO)
                _hdr_labels = _HDR_AI if has_ai else _HDR_NO
                for _h, _lbl in zip(_hdr_cols, _hdr_labels):
                    _h.markdown(
                        f'<div style="font-size:11px;color:#64748b;font-weight:600">{_lbl}</div>',
                        unsafe_allow_html=True,
                    )

                _switch_flags = []  # 乗り換え候補を後でまとめて表示するために収集
                for _atk in _alloc_sorted:
                    _ar      = _alloc_res[_atk]
                    _delta   = _ar["delta"]
                    _dc      = "#4ade80" if _delta > 1 else "#ef4444" if _delta < -1 else "#94a3b8"
                    _di      = "▲" if _delta > 1 else "▼" if _delta < -1 else "─"
                    _rule_sc = _ar.get("adjusted_score", 0)
                    _ai_sc   = _ar.get("ai_score")
                    _comb_sc = _ar.get("combined_score", _rule_sc)
                    _sc_color= ("#4ade80" if _comb_sc > 2 else
                                "#fbbf24" if _comb_sc > 0 else "#ef4444")
                    _bar_w   = min(max(int((_comb_sc + 7) / 14 * 100), 5), 100)
                    _bm_diff  = _ar.get("bm_diff", 0.0)
                    _bm_lbl   = _ar.get("bm_label", "NDX")
                    _sw_flag  = _ar.get("switch_flag", False)
                    if _sw_flag:
                        _switch_flags.append(_atk)

                    _cols = st.columns(_COL_AI if has_ai else _COL_NO)

                    # 銘柄名 + switch警告バッジ
                    _sw_badge = (' <span style="font-size:9px;background:#7f1d1d;color:#fca5a5;'
                                 'border-radius:3px;padding:1px 4px">⚠️乗換検討</span>'
                                 if _sw_flag else "")
                    _cols[0].markdown(
                        f'<div style="font-size:12px;font-weight:700;color:#e2e8f0">'
                        f'{_atk}{_sw_badge}</div>'
                        f'<div style="font-size:10px;color:#64748b">{_ar.get("sector","")[:16]}</div>',
                        unsafe_allow_html=True,
                    )
                    _cols[1].markdown(
                        f'<div style="font-size:13px;color:#94a3b8">{_ar["current_alloc"]:.1f}%</div>',
                        unsafe_allow_html=True,
                    )
                    _cols[2].markdown(
                        f'<div style="font-size:13px;font-weight:700;color:#38bdf8">{_ar["rec_alloc"]:.1f}%</div>',
                        unsafe_allow_html=True,
                    )
                    _cols[3].markdown(
                        f'<div style="font-size:13px;font-weight:700;color:{_dc}">'
                        f'{_di}{abs(_delta):.1f}pp</div>',
                        unsafe_allow_html=True,
                    )
                    # ルールスコア（バー付き）
                    _cols[4].markdown(
                        f'<div style="font-size:11px;color:{_sc_color};font-weight:700">'
                        f'{_rule_sc:+.1f}</div>'
                        f'<div style="background:#1e293b;border-radius:3px;height:4px;margin-top:3px">'
                        f'<div style="background:{_sc_color};width:{_bar_w}%;height:4px;border-radius:3px"></div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                    if has_ai:
                        # AIスコア
                        _ai_color = ("#4ade80" if (_ai_sc or 0) > 1
                                     else "#ef4444" if (_ai_sc or 0) < -1 else "#94a3b8")
                        _ai_txt = f'{_ai_sc:+.1f}' if _ai_sc is not None else "─"
                        _ai_reason = _ar.get("ai_reasoning", "")
                        _cols[5].markdown(
                            f'<div style="font-size:11px;color:{_ai_color};font-weight:700" '
                            f'title="{_ai_reason}">{_ai_txt}</div>',
                            unsafe_allow_html=True,
                        )
                        # vs BM (col 6)
                        _bm_c = "#4ade80" if _bm_diff > 0 else "#ef4444" if _bm_diff < -1 else "#f97316"
                        _cols[6].markdown(
                            f'<div style="font-size:11px;color:{_bm_c};font-weight:700"'
                            f' title="vs {_bm_lbl} ({_bm_diff:+.2f})">'
                            f'{_bm_diff:+.1f}<br>'
                            f'<span style="font-size:9px;color:#64748b">{_bm_lbl}</span></div>',
                            unsafe_allow_html=True,
                        )
                        # シグナル + AI根拠
                        _sig_str  = "  ".join(_ar["signals"][:3])
                        _aif_str  = " / ".join(_ar.get("ai_factors", [])[:2])
                        _cols[7].markdown(
                            f'<div style="font-size:10px;color:#94a3b8;line-height:1.5">{_sig_str}</div>'
                            + (f'<div style="font-size:10px;color:#a78bfa;line-height:1.5">🤖 {_aif_str}</div>'
                               if _aif_str else ""),
                            unsafe_allow_html=True,
                        )
                    else:
                        # vs BM (col 5)
                        _bm_c = "#4ade80" if _bm_diff > 0 else "#ef4444" if _bm_diff < -1 else "#f97316"
                        _cols[5].markdown(
                            f'<div style="font-size:11px;color:{_bm_c};font-weight:700"'
                            f' title="vs {_bm_lbl} ({_bm_diff:+.2f})">'
                            f'{_bm_diff:+.1f}<br>'
                            f'<span style="font-size:9px;color:#64748b">{_bm_lbl}</span></div>',
                            unsafe_allow_html=True,
                        )
                        _chip_str = "  ".join(_ar["signals"][:3])
                        _cols[6].markdown(
                            f'<div style="font-size:10px;color:#94a3b8;line-height:1.6">{_chip_str}</div>',
                            unsafe_allow_html=True,
                        )

                # リバランシングサマリー
                _inc = [(t, _alloc_res[t]["delta"]) for t in _alloc_tickers if _alloc_res[t]["delta"] > 1]
                _dec = [(t, _alloc_res[t]["delta"]) for t in _alloc_tickers if _alloc_res[t]["delta"] < -1]
                _inc_str = " / ".join(f"{t}(+{d:.1f}pp)" for t, d in sorted(_inc, key=lambda x:-x[1]))
                _dec_str = " / ".join(f"{t}({d:.1f}pp)" for t, d in sorted(_dec, key=lambda x:x[1]))
                # ベンチマークスコア表示
                _bm_info = (
                    f'<div style="color:#64748b;font-size:11px;margin-bottom:6px">'
                    f'📊 ベンチマーク参照スコア → '
                    f'<span style="color:#38bdf8">NDX(QQQ): {_bm_qqq:+.2f}</span>'
                    f' &nbsp;|&nbsp; '
                    f'<span style="color:#fb923c">日経225: {_bm_n225:+.2f}</span>'
                    f'&nbsp;（vs BM列：各銘柄スコア − ベンチマークスコア）</div>'
                )
                st.markdown(
                    f'<div style="background:#0f172a;border-radius:8px;padding:10px 14px;margin-top:10px;'
                    f'border:1px solid #1e293b;font-size:12px">'
                    + _bm_info
                    + (f'<div style="color:#4ade80;margin-bottom:4px">▲ 増やす推奨: {_inc_str}</div>' if _inc_str else '')
                    + (f'<div style="color:#ef4444">▼ 減らす推奨: {_dec_str}</div>' if _dec_str else '')
                    + '</div>',
                    unsafe_allow_html=True,
                )

                # ── 乗り換え候補 ────────────────────────────────────────
                if _switch_flags:
                    st.markdown(
                        f'<div style="font-size:12px;color:#fca5a5;margin:10px 0 6px">'
                        f'⚠️ ベンチマークを大幅に下回る銘柄: '
                        f'<b>{" / ".join(_switch_flags)}</b> — 乗り換えを検討してください</div>',
                        unsafe_allow_html=True,
                    )
                    _sw_col1, _sw_col2 = st.columns([3, 1])
                    with _sw_col1:
                        _sw_model_opts = {
                            "🔄 自動": "auto", "🟡 Gemini": "gemini",
                            "⚡ Groq": "groq",  "🌐 OpenRouter": "openrouter",
                        }
                        _sw_model_sel = st.selectbox(
                            "モデル（乗り換え分析）",
                            list(_sw_model_opts.keys()),
                            key="sw_model_sel",
                            label_visibility="collapsed",
                        )
                    with _sw_col2:
                        _do_switch = st.button("🔄 乗り換え候補を生成", key="btn_switch_rec")
                    if _do_switch:
                        _sw_mctx = st.session_state.get("_alloc_mktctx", {})
                        _sw_dmap = st.session_state.get("_alloc_data_map", {})
                        with st.spinner("乗り換え候補をAIが分析中…"):
                            _sw_res, _sw_model = _generate_switch_recommendations(
                                _alloc_res, _sw_dmap, _sw_mctx,
                                model_pref=_sw_model_opts[_sw_model_sel],
                            )
                        st.session_state["_switch_result"] = _sw_res
                        st.session_state["_switch_model"]  = _sw_model
                    _sw_result = st.session_state.get("_switch_result", [])
                    if _sw_result:
                        _sw_model_name = st.session_state.get("_switch_model", "")
                        st.markdown(
                            f'<div style="font-size:11px;color:#64748b;margin:4px 0 2px">'
                            f'🤖 {_sw_model_name} による乗り換え分析</div>',
                            unsafe_allow_html=True,
                        )
                        for _sw in _sw_result:
                            _sw_from = _sw.get("from", "?")
                            _sw_to_s = ", ".join(_sw.get("to_stocks", []))
                            _sw_to_e = _sw.get("to_etf", "")
                            _sw_rat  = _sw.get("rationale", "")
                            _sw_tim  = _sw.get("timing", "")
                            _sw_sec  = _sw.get("sector", "")
                            _to_all  = " / ".join(filter(None, [_sw_to_s, _sw_to_e]))
                            st.markdown(
                                f'<div style="background:#1c0a0a;border:1px solid #7f1d1d;'
                                f'border-radius:8px;padding:10px 14px;margin-bottom:6px">'
                                f'<div style="font-size:13px;font-weight:700;color:#fca5a5">'
                                f'📤 {_sw_from} → 候補: <span style="color:#4ade80">{_to_all}</span></div>'
                                f'<div style="font-size:11px;color:#94a3b8;margin-top:4px">💡 {_sw_rat}</div>'
                                f'<div style="font-size:11px;color:#fbbf24;margin-top:2px">⏰ {_sw_tim}</div>'
                                f'</div>',
                                unsafe_allow_html=True,
                            )

            st.markdown(
                '<div style="border-top:1px solid #1e293b;margin:14px 0 10px"></div>',
                unsafe_allow_html=True,
            )

            # ── 総合AI推奨分析（Market Dashboard + 全銘柄IR）─────────────────
            st.markdown(
                '<div style="font-size:13px;font-weight:600;color:#a78bfa;'
                'margin:4px 0 8px">── 市場×IR 総合AI推奨分析 ──</div>',
                unsafe_allow_html=True,
            )
            st.markdown(
                '<div style="background:#1a0a2e;border:1px solid #4c1d95;border-radius:8px;'
                'padding:10px 14px;margin-bottom:10px;font-size:12px;color:#c4b5fd">'
                '📡 windexの市場予測モデル（Fear&Greed / NAAIM / セクターRRG / 日経・米国予測）と'
                '各保有銘柄の最新IR・テクニカルを組み合わせて、AI が総合的な推奨アクションを生成します。'
                '</div>',
                unsafe_allow_html=True,
            )
            _rec_model_opts = {
                "🔄 自動（Gemini→Groq→OpenRouter）": "auto",
                "🟡 Gemini（Google）":               "gemini",
                "⚡ Groq（Llama-3.3 70B・高速）":    "groq",
                "🌐 OpenRouter（DeepSeek/Qwen等）":  "openrouter",
            }
            _rec_model_label = st.selectbox(
                "使用するAIモデル（総合分析）",
                list(_rec_model_opts.keys()),
                key="rec_model_sel",
            )
            _rec_model_pref = _rec_model_opts[_rec_model_label]

            _rec_btn_col, _rec_force_col = st.columns([3, 2])
            _run_rec    = _rec_btn_col.button("🔍 市場×IR 総合AI推奨を生成", type="primary", key="btn_full_rec")
            _force_regen = _rec_force_col.button(
                "🔄 キャッシュ無視で再生成",
                key="btn_rec_force_regen",
                help="銘柄名修正・プロンプト変更後などに使用。Sheetsキャッシュを無視してAIを再呼び出しします",
            )

            if _run_rec or _force_regen:
                _rec_mode = st.session_state.get("trading_mode", "growth")
                _n_stocks = len(open_pos)
                _today    = datetime.now(JST).strftime("%Y-%m-%d")

                # Step1: 全銘柄データ取得（キャッシュキー算出に必要）
                with st.spinner(f"全{_n_stocks}銘柄のデータ取得＋市場モデル取得中... （1〜2分）"):
                    import concurrent.futures as _cf_rec
                    _rec_mktctx = _fetch_market_context_for_trading()

                    # キャッシュ済みデータを優先使用（session_state → Sheets → 新規フェッチ）
                    _prefetched = st.session_state.get("_prefetched_stock_data", {})

                    def _fetch_one(ticker_item):
                        _t, _p = ticker_item
                        try:
                            if _t in _prefetched:
                                return _t, _prefetched[_t]
                            return _t, _fetch_trading_stock_data_with_cache(_t, _t.endswith(".T"))
                        except Exception:
                            return _t, {}

                    _stock_data_map = {}
                    with _cf_rec.ThreadPoolExecutor(max_workers=6) as _ex_rec:
                        _futs = {_ex_rec.submit(_fetch_one, item): item[0]
                                 for item in open_pos.items()}
                        for _fut in _cf_rec.as_completed(_futs):
                            try:
                                _tk, _d = _fut.result()
                                _stock_data_map[_tk] = _d
                            except Exception:
                                pass

                # Step2: キャッシュキー算出 → Sheets照合（force_regenでスキップ）
                _tickers_key, _news_key = _make_rec_cache_key(
                    list(open_pos.keys()), _stock_data_map
                )
                _cached_rec = None
                if not _force_regen:
                    with st.spinner("📋 キャッシュ確認中..."):
                        _cached_rec = _load_rec_cache(
                            _today, _rec_mode, _tickers_key, _news_key
                        )

                # Step3: キャッシュヒットならそのまま使用、なければAI呼び出し
                _from_cache = False
                if _cached_rec:
                    _rec_result = {
                        "text":  _cached_rec["text"],
                        "model": _cached_rec["model"],
                        "error": None,
                    }
                    _from_cache = True
                else:
                    with st.spinner("AI 分析中..."):
                        _rec_result = _generate_full_portfolio_recommendation(
                            open_pos, _stock_data_map, _rec_mktctx,
                            mode=_rec_mode, model_pref=_rec_model_pref,
                        )
                    # Step4: 成功したらSheetsに保存
                    if not _rec_result.get("error"):
                        _save_rec_cache(
                            _today, _rec_mode, _tickers_key, _news_key,
                            _rec_result["text"], _rec_result["model"],
                        )

                if _rec_result.get("error"):
                    st.error(f"AI分析エラー: {_rec_result['error']}")
                else:
                    if _from_cache:
                        st.info(
                            "📋 本日のキャッシュから取得（同銘柄・同ニュース構成）　"
                            "最新AIで再取得する場合は「キャッシュを無視して再生成する」にチェックしてください。"
                        )
                    st.markdown(
                        '<div style="background:#0f172a;border:1px solid #4c1d95;border-radius:10px;'
                        'padding:14px 18px;margin-bottom:8px">'
                        '<div style="font-size:14px;font-weight:700;color:#a78bfa;margin-bottom:8px">'
                        '🔍 市場×IR 総合AI推奨分析</div>',
                        unsafe_allow_html=True,
                    )
                    st.markdown(_rec_result["text"])
                    st.markdown("</div>", unsafe_allow_html=True)
                    _cache_badge = " 📋キャッシュ" if _from_cache else ""
                    st.caption(
                        f"🤖 {_rec_result['model']}{_cache_badge} ｜ "
                        f"{_today} ｜ "
                        f"対象{_n_stocks}銘柄 + windexモデル"
                    )
                    # IRニュース詳細をexpanderで表示
                    with st.expander("📰 参照したIR・ニュース一覧（全銘柄）"):
                        for _tk, _sd in _stock_data_map.items():
                            _ni_list = _sd.get("news_items") or []
                            if not _ni_list:
                                _ni_list = [{"headline": n} for n in (_sd.get("news") or [])]
                            _tk_disp = _get_stock_display_name(_tk)
                            _src_tags = list({_ni.get("source","") for _ni in _ni_list if _ni.get("source")})
                            _src_str  = " / ".join(_src_tags) if _src_tags else ""
                            st.markdown(
                                f'<div style="font-size:12px;font-weight:700;color:#94a3b8;'
                                f'margin:10px 0 4px">■ {_tk} — {_tk_disp}'
                                + (f' <span style="font-size:10px;color:#475569">({_src_str})</span>' if _src_str else '')
                                + f'</div>',
                                unsafe_allow_html=True,
                            )
                            if _ni_list:
                                for _ni in _ni_list[:5]:
                                    _hja = _ni.get("headline_ja", "") or _ni.get("headline", "")
                                    _url = _ni.get("url", "")
                                    _dt  = _ni.get("date", "")[:10]
                                    _dt_str = f'<span style="color:#475569;margin-right:4px">{_dt}</span>' if _dt else ""
                                    if _url:
                                        st.markdown(
                                            f'<div style="font-size:11px;padding:2px 0">'
                                            f'• {_dt_str}<a href="{_url}" target="_blank" '
                                            f'style="color:#60a5fa;text-decoration:none">{_hja}</a></div>',
                                            unsafe_allow_html=True,
                                        )
                                    else:
                                        st.markdown(
                                            f'<div style="font-size:11px;color:#cbd5e1;padding:2px 0">• {_dt_str}{_hja}</div>',
                                            unsafe_allow_html=True,
                                        )
                            else:
                                st.markdown(
                                    '<div style="font-size:11px;color:#475569;padding:2px 0 6px">'
                                    '📭 ニュース・IR未取得（みんかぶ/EDINET/TDnet/Yahoo!F未ヒット）</div>',
                                    unsafe_allow_html=True,
                                )

            st.markdown(
                '<div style="border-top:1px solid #1e293b;margin:14px 0 10px"></div>',
                unsafe_allow_html=True,
            )

            # ── 新規投資推奨ポートフォリオ ──────────────────────────
            st.markdown(
                '<div style="font-size:13px;font-weight:600;color:#34d399;'
                'margin:4px 0 8px">── 💼 新規投資推奨ポートフォリオ（AI） ──</div>',
                unsafe_allow_html=True,
            )
            st.markdown(
                '<div style="background:#0a1f18;border:1px solid #065f46;border-radius:8px;'
                'padding:8px 14px;margin-bottom:10px;font-size:12px;color:#6ee7b7">'
                '既存保有銘柄を参考に、AI が日米株・ETFから最適な新規投資先を提案します。'
                'ETF混合モデルと個別株モデル、リスクプロファイル別に生成します。'
                '</div>',
                unsafe_allow_html=True,
            )

            # コントロール行
            _ip_c1, _ip_c2, _ip_c3, _ip_c4 = st.columns([1.5, 1.5, 1.5, 1.5])
            _ip_budget = _ip_c1.radio(
                "投資予算", ["100万円", "500万円"],
                key="ip_budget", horizontal=True,
            )
            _ip_risk = _ip_c2.radio(
                "リスクプロファイル", ["リスク先行型", "リスクリターン考慮型"],
                key="ip_risk", horizontal=False,
            )
            _ip_model_opts = {
                "🔄 自動": "auto", "🟡 Gemini": "gemini",
                "⚡ Groq": "groq",  "🌐 OpenRouter": "openrouter",
            }
            _ip_model_sel = _ip_c3.selectbox(
                "AIモデル", list(_ip_model_opts.keys()),
                key="ip_model_sel", label_visibility="visible",
            )
            _ip_force = _ip_c4.checkbox(
                "🔄 再生成（キャッシュ無視）", key="ip_force_regen",
            )

            if st.button("💼 推奨ポートフォリオを生成（ETF混合 & 個別株）", type="primary", key="btn_invest_portfolio"):
                _ip_budget_val  = 1_000_000 if "100" in _ip_budget else 5_000_000
                _ip_risk_key    = "aggressive" if "先行" in _ip_risk else "balanced"
                _ip_model_pref  = _ip_model_opts[_ip_model_sel]
                _ip_today       = datetime.now(JST).strftime("%Y-%m-%d")
                _ip_holdings    = list(open_pos.keys()) if open_pos else []
                _ip_mktctx      = st.session_state.get("_alloc_mktctx") or _fetch_market_context_for_trading()
                _ip_trade_mode  = st.session_state.get("trading_mode", "growth")
                # キャッシュキーにモードを含めて、モード違いのキャッシュが混在しないようにする
                _ip_cache_risk  = f"{_ip_risk_key}_{_ip_trade_mode}"

                _ip_results = {}
                for _ip_mt in ["etf", "individual"]:
                    _cached = None
                    if not _ip_force:
                        _cached = _load_invest_rec_cache(_ip_today, _ip_budget_val, _ip_mt, _ip_cache_risk)
                    if _cached:
                        _ip_results[_ip_mt] = _cached
                    else:
                        with st.spinner(f"{'ETF混合' if _ip_mt=='etf' else '個別株'}モデルをAIが生成中…"):
                            _r = _generate_investment_portfolio_rec(
                                _ip_budget_val, _ip_mt, _ip_risk_key,
                                _ip_mktctx, _ip_holdings, _ip_model_pref,
                                trading_mode=_ip_trade_mode,
                            )
                        if not _r.get("error") and _r.get("portfolio"):
                            _save_invest_rec_cache(_ip_today, _ip_budget_val, _ip_mt, _ip_cache_risk,
                                                   _r, _r.get("model", ""))
                        _ip_results[_ip_mt] = _r

                st.session_state["_ip_results"]     = _ip_results
                st.session_state["_ip_budget_val"]  = _ip_budget_val
                st.session_state["_ip_risk_key"]    = _ip_risk_key

            # 結果表示
            _ip_disp = st.session_state.get("_ip_results", {})
            if _ip_disp:
                _ip_bv  = st.session_state.get("_ip_budget_val", 1_000_000)
                _ip_rk  = st.session_state.get("_ip_risk_key", "balanced")
                _ip_rlbl = "🔥 リスク先行型" if _ip_rk == "aggressive" else "⚖️ リスクリターン考慮型"

                _tab_etf, _tab_ind = st.tabs(["📊 ETF混合モデル", "📈 個別株モデル"])
                for _tab_obj, _ip_mt in [(_tab_etf, "etf"), (_tab_ind, "individual")]:
                    with _tab_obj:
                        _ip_r = _ip_disp.get(_ip_mt, {})
                        if _ip_r.get("error") or not _ip_r.get("portfolio"):
                            st.error(f"⚠️ {_ip_r.get('error','生成失敗')}")
                            if _ip_r.get("model"):
                                st.caption(f"モデル: {_ip_r['model']}")
                            st.info("「🔄 再生成（キャッシュ無視）」にチェックして再度ボタンを押してください。")
                            continue

                        _ip_pf  = _ip_r.get("portfolio", [])
                        _ip_met = _ip_r.get("metrics", {})
                        _ai_mdl = _ip_r.get("model", "")

                        # リスク指標カード
                        _er   = _ip_met.get("expected_return", 0)
                        _rv   = _ip_met.get("risk_volatility", 0)
                        _sr   = _ip_met.get("sharpe_ratio", 0)
                        _mdd  = _ip_met.get("max_drawdown_estimate", 0)
                        _cmt  = _ip_met.get("comment", "")
                        _sr_c = "#4ade80" if _sr > 1.0 else "#fbbf24" if _sr > 0.5 else "#ef4444"
                        _er_c = "#4ade80" if _er > 12 else "#fbbf24" if _er > 6 else "#94a3b8"
                        st.markdown(
                            f'<div style="background:#0f172a;border:1px solid #334155;border-radius:10px;'
                            f'padding:12px 16px;margin-bottom:10px">'
                            f'<div style="font-size:12px;color:#64748b;margin-bottom:8px">'
                            f'{_ip_rlbl} ｜ 予算 {_ip_bv:,}円 ｜ 🤖 {_ai_mdl}</div>'
                            f'<div style="display:flex;gap:16px;flex-wrap:wrap">'
                            f'<div style="text-align:center">'
                            f'<div style="font-size:11px;color:#64748b">期待リターン</div>'
                            f'<div style="font-size:20px;font-weight:800;color:{_er_c}">{_er:+.1f}%</div></div>'
                            f'<div style="text-align:center">'
                            f'<div style="font-size:11px;color:#64748b">ボラティリティ</div>'
                            f'<div style="font-size:20px;font-weight:800;color:#f97316">{_rv:.1f}%</div></div>'
                            f'<div style="text-align:center">'
                            f'<div style="font-size:11px;color:#64748b">シャープレシオ</div>'
                            f'<div style="font-size:20px;font-weight:800;color:{_sr_c}">{_sr:.2f}</div></div>'
                            f'<div style="text-align:center">'
                            f'<div style="font-size:11px;color:#64748b">最大DD推定</div>'
                            f'<div style="font-size:20px;font-weight:800;color:#ef4444">{_mdd:.0f}%</div></div>'
                            f'</div>'
                            + (f'<div style="font-size:11px;color:#94a3b8;margin-top:8px;border-top:1px solid #1e293b;'
                               f'padding-top:6px">💬 {_cmt}</div>' if _cmt else '')
                            + f'</div>',
                            unsafe_allow_html=True,
                        )

                        # ポートフォリオテーブル
                        _hdr = st.columns([0.6, 2.0, 1.2, 1.3, 3.0])
                        for _h, _lbl in zip(_hdr, ["", "銘柄", "比率", "金額(円)", "根拠"]):
                            _h.markdown(f'<div style="font-size:11px;color:#1e3a5f;font-weight:700">{_lbl}</div>',
                                        unsafe_allow_html=True)

                        for _item in _ip_pf:
                            _flag   = _item.get("flag", "🌐")
                            _tk     = _item.get("ticker", "")
                            _nm     = _item.get("name", _tk)
                            _alloc  = float(_item.get("allocation", 0))
                            _amt    = int(_item.get("amount", 0))
                            _rat    = _item.get("rationale", "")
                            _bar_w  = min(int(_alloc), 100)
                            _a_c    = ("#1d4ed8" if _alloc >= 20 else "#4f46e5" if _alloc >= 10 else "#1e3a5f")
                            _row = st.columns([0.6, 2.0, 1.2, 1.3, 3.0])
                            _row[0].markdown(f'<div style="font-size:16px">{_flag}</div>',
                                             unsafe_allow_html=True)
                            _row[1].markdown(
                                f'<div style="font-size:12px;font-weight:700;color:#0f172a">{_tk}</div>'
                                f'<div style="font-size:10px;color:#334155">{_nm}</div>',
                                unsafe_allow_html=True,
                            )
                            _row[2].markdown(
                                f'<div style="font-size:13px;font-weight:700;color:{_a_c}">{_alloc:.0f}%</div>'
                                f'<div style="background:#cbd5e1;border-radius:3px;height:4px;margin-top:3px">'
                                f'<div style="background:{_a_c};width:{_bar_w}%;height:4px;border-radius:3px"></div></div>',
                                unsafe_allow_html=True,
                            )
                            _row[3].markdown(
                                f'<div style="font-size:12px;color:#1e293b;font-weight:600">{_amt:,}</div>',
                                unsafe_allow_html=True,
                            )
                            _row[4].markdown(
                                f'<div style="font-size:11px;color:#1e3a5f">{_rat}</div>',
                                unsafe_allow_html=True,
                            )

            st.markdown(
                '<div style="border-top:1px solid #1e293b;margin:14px 0 10px"></div>',
                unsafe_allow_html=True,
            )

            # ── 個別銘柄分析 ────────────────────────────────────────
            st.markdown(
                '<div style="font-size:13px;font-weight:600;color:#94a3b8;'
                'margin:4px 0 6px">── 個別銘柄分析 ──</div>',
                unsafe_allow_html=True,
            )
            if open_pos:
                st.caption("📂 保有中の銘柄を選択。追加買い / 保有継続 / 利確 / 損切りの観点でAIが判断します。")

            sel_label = st.selectbox("分析する銘柄を選択", list(all_options.keys()))
            sel = all_options[sel_label]
            is_jp = sel["market"] == "JP"
            pos_ctx = sel.get("position_ctx")

            current_mode = st.session_state.get("trading_mode", "growth")
            _mode_meta = {
                "growth":     ("🌱 長期育成", "#4ade80"),
                "momentum":   ("⚡ モメンタム", "#fbbf24"),
                "autonomous": ("🤖 AI自律", "#a78bfa"),
            }
            mode_badge, mode_color = _mode_meta.get(current_mode, ("🌱 長期育成", "#4ade80"))
            st.markdown(
                f'<div style="font-size:12px;color:{mode_color};margin-bottom:6px">'
                f'現在のモード: <b>{mode_badge}</b></div>',
                unsafe_allow_html=True,
            )

            # モデル選択
            _model_opts = {
                "🔄 自動（Gemini→Groq→OpenRouter）": "auto",
                "🟡 Gemini（Google）":               "gemini",
                "⚡ Groq（Llama-3.3 70B・高速）":    "groq",
                "🌐 OpenRouter（DeepSeek/Qwen等）":  "openrouter",
            }
            sel_model_label = st.selectbox(
                "使用するAIモデル",
                list(_model_opts.keys()),
                key="signal_model_sel",
                help="Groqは最速。自動はGeminiを最優先で試行し失敗時にフォールバック。",
            )
            current_model_pref = _model_opts[sel_model_label]

            # キャッシュ済みかどうか表示
            _sel_ticker = sel["ticker"]
            _sel_cs = _cache_stat.get(_sel_ticker.upper(), {})
            if _sel_cs.get("cached"):
                st.caption(f"📦 キャッシュ済み（{_sel_cs['updated_at']}）— データ取得をスキップしてAIに渡します")
            else:
                st.caption("⬜ 未キャッシュ — ボタン押下時にデータ取得します")

            if st.button("🤖 AIに分析させる", type="primary"):
                _spin_msg = (
                    f"{sel['ticker']} AI分析中...（キャッシュ利用）"
                    if _sel_cs.get("cached")
                    else f"{sel['ticker']} データ取得・AI分析中...（30秒程度）"
                )
                with st.spinner(_spin_msg):
                    past   = _load_signals(sel["ticker"])   # 過去の分析履歴
                    # キャッシュ対応フェッチ（session_state → Sheets → 新規取得）
                    _pf = st.session_state.get("_prefetched_stock_data", {})
                    data = (
                        _pf.get(sel["ticker"])
                        or _fetch_trading_stock_data_with_cache(sel["ticker"], is_jp)
                    )
                    # windex市場コンテキスト（キャッシュ済みなら即返却）
                    _sig_mktctx = _fetch_market_context_for_trading()
                    _sig_name = (data.get("name") or sel["name"] or sel["ticker"])
                    if _sig_name == sel["ticker"]:
                        _sig_name = _get_stock_display_name(sel["ticker"])
                    signal = _generate_ai_trade_signal(
                        sel["ticker"], _sig_name, is_jp, data,
                        position_ctx=pos_ctx, past_signals=past,
                        mode=current_mode, model_pref=current_model_pref,
                        market_ctx=_sig_mktctx,
                    )

                if signal.get("error"):
                    st.error(f"AI分析エラー: {signal['error']}")
                else:
                    cur   = "円" if is_jp else "USD"
                    price = data.get("price", "-")
                    rsi   = data.get("rsi", "-")
                    ma25  = data.get("ma25", "-")
                    ma75  = data.get("ma75", "-")

                    mode_label = "ポジション管理" if pos_ctx else "新規エントリー検討"
                    hdr_color  = "#f59e0b" if pos_ctx else "#60a5fa"

                    st.markdown(
                        f'<div style="background:#0f2744;border-radius:10px;padding:14px 18px;'
                        f'margin-bottom:10px;border:1px solid #1e3a5f;">'
                        f'<div style="font-size:15px;font-weight:700;color:{hdr_color};margin-bottom:6px">'
                        f'{"📂" if pos_ctx else "👀"} {sel["name"]}（{sel["ticker"]}）'
                        f' — {mode_label}</div>'
                        f'<div style="font-size:12px;color:#94a3b8">'
                        f'株価: <b style="color:#f1f5f9">{price}{cur}</b> ｜ '
                        f'RSI: <b style="color:#f1f5f9">{rsi}</b> ｜ '
                        f'MA25: <b style="color:#f1f5f9">{ma25}{cur}</b> ｜ '
                        f'MA75: <b style="color:#f1f5f9">{ma75}{cur}</b>'
                        + (f' ｜ 保有 <b style="color:#f1f5f9">{int(pos_ctx["qty"])}株</b>'
                           f' 取得単価 <b style="color:#f1f5f9">{pos_ctx["avg_cost"]:.1f}{cur}</b>'
                           if pos_ctx else "")
                        + f'</div></div>',
                        unsafe_allow_html=True,
                    )
                    st.markdown(signal["text"])
                    st.caption(f"🤖 {signal['model']} ｜ {datetime.now(JST).strftime('%Y-%m-%d %H:%M JST')}")

                    # 分析結果を Google Sheets に自動保存
                    _save_signal(sel["ticker"], sel["name"], signal, data)

                    if data.get("news_items"):
                        with st.expander(f"📰 参照したニュース・開示（{len(data['news_items'])}件）"):
                            for _ni in data["news_items"]:
                                _hja  = _ni.get("headline_ja", "") or _ni.get("headline", "")
                                _hen  = _ni.get("headline", "")
                                _url  = _ni.get("url", "")
                                _date = _ni.get("date", "")
                                _src  = _ni.get("source", "")
                                # タイトル行（リンク付き）
                                if _url:
                                    _title_html = (
                                        f'<a href="{_url}" target="_blank" rel="noopener noreferrer" '
                                        f'style="color:#60a5fa;text-decoration:none;font-size:13px;font-weight:600">'
                                        f'{_hja}</a>'
                                    )
                                else:
                                    _title_html = (
                                        f'<span style="color:#e2e8f0;font-size:13px;font-weight:600">'
                                        f'{_hja}</span>'
                                    )
                                # 原文（英語の場合のみ）
                                _en_html = ""
                                if _hen and _hja and _hen != _hja:
                                    _en_html = (
                                        f'<div style="font-size:11px;color:#64748b;margin-top:2px">'
                                        f'{_hen}</div>'
                                    )
                                # 日付・ソース
                                _meta = " ｜ ".join(x for x in [_date, _src] if x)
                                _meta_html = (
                                    f'<div style="font-size:10px;color:#475569;margin-top:2px">{_meta}</div>'
                                    if _meta else ""
                                )
                                st.markdown(
                                    f'<div style="padding:8px 2px;border-bottom:1px solid #1e293b">'
                                    f'• {_title_html}{_en_html}{_meta_html}</div>',
                                    unsafe_allow_html=True,
                                )
                    elif data.get("news"):
                        with st.expander("📰 参照したニュース・開示"):
                            for n in data["news"]:
                                st.markdown(f"・{n}")

                    # 過去の分析履歴を表示
                    if past:
                        with st.expander(f"📋 {sel['ticker']} の過去分析履歴（{len(past)}件）"):
                            for s in past[:5]:
                                jdg   = s.get("judgment", "?")
                                jdg_c = "#22c55e" if "買" in jdg else "#ef4444" if "売" in jdg else "#94a3b8"
                                st.markdown(
                                    f'<div style="border-left:3px solid {jdg_c};padding:6px 10px;'
                                    f'margin-bottom:6px;background:#1e293b;border-radius:0 6px 6px 0;">'
                                    f'<span style="font-size:11px;color:#64748b">{s.get("date","")[:10]}</span>'
                                    f' <span style="color:{jdg_c};font-weight:700">{jdg}</span>'
                                    f' <span style="font-size:11px;color:#94a3b8">PER {s.get("trailing_pe","-")}倍'
                                    f' ｜ 目標 {s.get("target_price","-")}</span><br>'
                                    f'<span style="font-size:11px;color:#94a3b8">{s.get("summary","")[:100]}</span>'
                                    f'</div>',
                                    unsafe_allow_html=True,
                                )

    # ── タブ③: 取引記録入力 ────────────────────────────────────
    with tab_trade:
        st.markdown("#### 約定後の取引記録入力")

        # エラー/成功メッセージを session_state で永続化
        _tr_status = st.session_state.pop("_trade_status", None)
        _tr_msg    = st.session_state.pop("_trade_msg", None)
        if _tr_status == "ok":
            st.success(f"✅ {_tr_msg}")
        elif _tr_status == "error":
            st.error(f"❌ 保存失敗: {_tr_msg}")
        elif _tr_status == "warn":
            st.warning(_tr_msg)

        st.session_state.setdefault("_trade_ticker", "")
        st.session_state.setdefault("_trade_name", "")

        with st.form("trade_form", clear_on_submit=True):
            ci1, ci2 = st.columns(2)
            t_ticker = ci1.text_input("ティッカー", value=st.session_state.get("_trade_ticker", ""),
                                      placeholder="例: NVDA / 7203.T")
            t_name   = ci2.text_input("銘柄名", value=st.session_state.get("_trade_name", ""),
                                      placeholder="例: NVIDIA / トヨタ自動車")
            c1, c2, c3 = st.columns(3)
            t_date   = c1.date_input("約定日", value=datetime.now(JST).date())
            t_action = c2.selectbox("売買区分", ["BUY（買い）", "SELL（売り）"])
            t_qty    = c3.number_input("数量（株）", min_value=1, step=1, value=100)
            c4, c5 = st.columns(2)
            t_price  = c4.number_input("約定価格", min_value=0.0, step=0.1, format="%.2f")
            t_fee    = c5.number_input("手数料", min_value=0.0, step=1.0, value=0.0)
            c7, c8 = st.columns(2)
            t_target = c7.number_input("AIの目標株価（参考）", min_value=0.0, step=0.1, format="%.2f")
            t_stop   = c8.number_input("AIの損切りライン（参考）", min_value=0.0, step=0.1, format="%.2f")
            t_memo   = st.text_input("メモ（任意）")

            if st.form_submit_button("💾 記録を保存", type="primary"):
                trade_ticker = t_ticker.strip().upper()
                trade_name   = t_name.strip() or trade_ticker
                if trade_ticker and t_price > 0:
                    action = "BUY" if "BUY" in t_action else "SELL"
                    ok, err = _save_trade(
                        str(t_date), trade_ticker, trade_name,
                        action, int(t_qty), float(t_price), float(t_fee),
                        t_memo, float(t_target), float(t_stop)
                    )
                    if ok:
                        st.session_state["_trade_ticker"] = ""
                        st.session_state["_trade_name"]   = ""
                        st.session_state["_trade_status"] = "ok"
                        st.session_state["_trade_msg"]    = f"{trade_ticker} {action} {t_qty}株 @ {t_price} を記録しました"
                    else:
                        st.session_state["_trade_status"] = "error"
                        st.session_state["_trade_msg"]    = err
                else:
                    st.session_state["_trade_status"] = "warn"
                    st.session_state["_trade_msg"]    = "ティッカーと約定価格を入力してください"

    # ── タブ④: 損益・ポートフォリオ ────────────────────────────
    with tab_pnl:
        st.markdown("#### ポートフォリオ損益")
        df_trades, trades_err = _load_trades()

        if trades_err:
            _err_c1, _err_c2 = st.columns([4, 1])
            _err_c1.error(f"⚠️ {trades_err}")
            if _err_c2.button("🔄 再試行", key="retry_load_trades"):
                st.rerun()
            st.caption("Google Sheets への接続が一時的に失敗しました。「再試行」を押すか、しばらく待ってから再読み込みしてください。")
        elif df_trades.empty:
            st.info("取引記録がまだありません。")
        else:
            # ── 取引履歴（削除ボタン付き）──────────────────────────
            st.markdown("**取引履歴**　<span style='font-size:11px;color:#64748b'>🗑️ ボタンで誤記録を削除できます</span>",
                        unsafe_allow_html=True)
            _hdr = st.columns([1.5, 1.2, 1.8, 0.8, 0.8, 1.2, 0.8, 2, 0.5])
            for _lbl, _c in zip(["日付","ティッカー","銘柄名","売買","数量","単価","手数料","メモ",""], _hdr):
                _c.markdown(f"<span style='font-size:11px;color:#64748b;font-weight:700'>{_lbl}</span>",
                            unsafe_allow_html=True)
            st.markdown('<hr style="margin:4px 0;border-color:#334155">', unsafe_allow_html=True)

            _del_requested = None
            for _seq, (_, _row) in enumerate(df_trades.iterrows()):
                _c = st.columns([1.5, 1.2, 1.8, 0.8, 0.8, 1.2, 0.8, 2, 0.5])
                _c[0].markdown(f"<span style='font-size:12px'>{str(_row.get('date',''))[:10]}</span>",
                               unsafe_allow_html=True)
                _c[1].markdown(f"<span style='font-size:12px;font-weight:700'>{_row.get('ticker','')}</span>",
                               unsafe_allow_html=True)
                _c[2].markdown(f"<span style='font-size:12px'>{_row.get('name','')}</span>",
                               unsafe_allow_html=True)
                _act = str(_row.get("action",""))
                _act_color = "#22c55e" if _act == "BUY" else "#ef4444"
                _c[3].markdown(f"<span style='font-size:12px;color:{_act_color};font-weight:700'>{_act}</span>",
                               unsafe_allow_html=True)
                _c[4].markdown(f"<span style='font-size:12px'>{int(_row.get('quantity',0)):,}</span>",
                               unsafe_allow_html=True)
                _c[5].markdown(f"<span style='font-size:12px'>{float(_row.get('price',0)):,.1f}</span>",
                               unsafe_allow_html=True)
                _c[6].markdown(f"<span style='font-size:12px'>{int(_row.get('fee',0)):,}</span>",
                               unsafe_allow_html=True)
                _c[7].markdown(f"<span style='font-size:12px;color:#94a3b8'>{str(_row.get('memo',''))[:20]}</span>",
                               unsafe_allow_html=True)
                if _c[8].button("🗑️", key=f"del_tr_{_seq}", help="この記録を削除"):
                    _del_requested = _seq + 2  # Sheetsの行番号（ヘッダー=1、データ=2〜）

            if _del_requested is not None:
                with st.spinner("削除中..."):
                    if _delete_trade_row(_del_requested):
                        st.success("削除しました")
                        st.cache_data.clear()
                        st.rerun()
                    else:
                        st.error("削除に失敗しました。再試行してください。")

            st.markdown("<br>", unsafe_allow_html=True)

            # 保有ポジションを計算（平均取得単価法：SELL時にコスト比例削減）
            open_pos = _calc_positions_from_df(df_trades)

            if open_pos:
                st.markdown("**保有中ポジション（現在値・含み損益）**")
                pnl_rows = []
                for ticker, pos in open_pos.items():
                    cur_price = None
                    try:
                        info = yf.Ticker(ticker).fast_info
                        cur_price = float(info.get("lastPrice") or info.get("last_price") or 0) or None
                    except Exception:
                        pass
                    if not cur_price:
                        try:
                            cur_raw = yf.download(ticker, period="2d", auto_adjust=True, progress=False)
                            if not cur_raw.empty:
                                close_col = cur_raw["Close"]
                                if isinstance(close_col, pd.DataFrame):
                                    close_col = close_col.iloc[:, 0]
                                vals = close_col.dropna()
                                if len(vals) > 0:
                                    cur_price = float(vals.iloc[-1])
                        except Exception:
                            pass

                    avg_cost = pos["cost"] / pos["qty"] if pos["qty"] > 0 else 0
                    pnl      = (cur_price - avg_cost) * pos["qty"] if cur_price else None
                    pnl_pct  = (cur_price / avg_cost - 1) * 100 if cur_price and avg_cost > 0 else None

                    _pnl_name = pos.get("name") or ticker
                    if _pnl_name == ticker:
                        _pnl_name = _get_stock_display_name(ticker)
                    pnl_rows.append({
                        "銘柄":    _pnl_name,
                        "コード":  ticker,
                        "保有株数": int(pos["qty"]),
                        "平均取得単価": round(avg_cost, 2),
                        "現在株価": round(cur_price, 2) if cur_price else "取得失敗",
                        "含み損益": f"{pnl:+,.0f}" if pnl is not None else "-",
                        "損益率":   f"{pnl_pct:+.2f}%" if pnl_pct is not None else "-",
                    })

                st.dataframe(pd.DataFrame(pnl_rows), hide_index=True, use_container_width=True)

                # ── ポートフォリオ全体AI分析 ──────────────────────
                st.markdown("<br>", unsafe_allow_html=True)
                st.markdown(
                    '<div style="background:linear-gradient(135deg,#0a1628,#0d1f3c);'
                    'border:1px solid #1e3a5f;border-radius:10px;padding:14px 18px;margin-bottom:14px">'
                    '<div style="font-size:14px;font-weight:700;color:#60a5fa;margin-bottom:4px">'
                    '📊 ポートフォリオ全体AI分析</div>'
                    '<div style="font-size:12px;color:#94a3b8">'
                    '全保有銘柄を一括分析 → リスク診断 + アクション優先順位TOP3 + 売却後の資金配分</div>'
                    '</div>',
                    unsafe_allow_html=True,
                )
                _pnl_model_opts = {
                    "🔄 自動（Gemini→Groq→OpenRouter）": "auto",
                    "🟡 Gemini（Google）":              "gemini",
                    "⚡ Groq（高速）":                  "groq",
                    "🌐 OpenRouter":                    "openrouter",
                }
                _pnl_col1, _pnl_col2 = st.columns([3, 1])
                _pnl_model_label = _pnl_col1.selectbox(
                    "全体分析用AIモデル", list(_pnl_model_opts.keys()),
                    key="pnl_wide_model_sel", label_visibility="collapsed",
                )
                _pnl_model_pref = _pnl_model_opts[_pnl_model_label]

                if _pnl_col2.button("🔍 全体分析", type="primary", use_container_width=True, key="pnl_wide_btn"):
                    _pnl_mode = st.session_state.get("trading_mode", "growth")
                    with st.spinner("全保有銘柄のデータ取得・AI分析中...（30〜60秒）"):
                        _pnl_summary   = _compute_portfolio_summary()
                        _pnl_positions = _pnl_summary.get("positions", {})
                        _pnl_tickers   = tuple(sorted(_pnl_positions.keys()))
                        _pnl_changes   = _fetch_daily_changes_for_tickers(_pnl_tickers) if _pnl_tickers else {}
                        _pnl_mktctx    = _fetch_market_context_for_trading()
                        _pnl_result    = _generate_portfolio_wide_analysis(
                            _pnl_positions, _pnl_changes,
                            mode=_pnl_mode, model_pref=_pnl_model_pref,
                            market_ctx=_pnl_mktctx,
                        )
                    st.session_state["_wide_analysis"] = _pnl_result

                _pnl_wide_res = st.session_state.get("_wide_analysis")
                if _pnl_wide_res:
                    if _pnl_wide_res.get("error"):
                        st.error(f"分析エラー: {_pnl_wide_res['error']}")
                    else:
                        st.markdown(
                            f'<div style="font-size:11px;color:#64748b;margin-bottom:6px">'
                            f'🤖 {_pnl_wide_res.get("model", "")} ｜ '
                            f'{datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")}</div>',
                            unsafe_allow_html=True,
                        )
                        st.markdown(_pnl_wide_res["text"])

            else:
                st.info("現在の保有ポジションはありません（全て決済済み）。")

    # ── タブ⑤: 資産推移 ────────────────────────────────────────
    with tab_hist:
        st.markdown("#### 📈 ポートフォリオ資産推移")

        with st.spinner("資産推移を計算中..."):
            hist_df = _compute_portfolio_history()

        if hist_df.empty:
            st.info("取引記録がないか、株価データを取得できませんでした。")
        else:
            import plotly.graph_objects as go

            # ── 全期間メトリクス（常に全期間ベース）─────────────
            latest      = hist_df.iloc[-1]
            port_now    = latest["portfolio_value"]
            cost_now    = latest["invested_cost"]
            pnl_now     = latest["pnl"]
            pnl_pct_now = latest["pnl_pct"]
            peak        = hist_df["portfolio_value"].max()
            drawdown    = (port_now - peak) / peak * 100 if peak > 0 else 0

            # 1日騰落（hist_df が2行以上あれば計算）
            if len(hist_df) >= 2:
                prev_val    = hist_df.iloc[-2]["portfolio_value"]
                day_chg     = port_now - prev_val
                day_chg_pct = day_chg / prev_val * 100 if prev_val > 0 else 0
            else:
                day_chg = day_chg_pct = 0.0

            def _metric_card(col, label, value, sub="", color="#e2e8f0"):
                col.markdown(
                    f'<div style="background:#1e293b;border-radius:8px;padding:12px 14px;text-align:center">'
                    f'<div style="font-size:11px;color:#94a3b8">{label}</div>'
                    f'<div style="font-size:20px;font-weight:700;color:{color};margin-top:4px">{value}</div>'
                    f'<div style="font-size:11px;color:#64748b;margin-top:2px">{sub}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

            m1, m2, m3, m4 = st.columns(4)
            _metric_card(m1, "現在評価額", f"{port_now:,.0f}", "（USD/円混在時は参考値）")
            dc_color = "#22c55e" if day_chg >= 0 else "#ef4444"
            _metric_card(m2, "本日の騰落",
                         f"{day_chg:+,.0f}", f"{day_chg_pct:+.2f}%", color=dc_color)
            pnl_color = "#22c55e" if pnl_now >= 0 else "#ef4444"
            _metric_card(m3, "含み損益合計",
                         f"{pnl_now:+,.0f}", f"{pnl_pct_now:+.2f}%", color=pnl_color)
            dd_color = "#ef4444" if drawdown < -5 else "#94a3b8"
            _metric_card(m4, "最大からの下落",
                         f"{drawdown:.1f}%", f"最高値 {peak:,.0f}", color=dd_color)

            st.markdown("<br>", unsafe_allow_html=True)

            # ── 期間セレクタ ────────────────────────────────────
            _PERIODS = ["1D", "5D", "1M", "6M", "YTD", "1Y", "全期間"]
            sel_period = st.radio(
                "表示期間", _PERIODS, index=5,
                horizontal=True, key="hist_period",
                label_visibility="collapsed",
            )

            today = pd.Timestamp.today().normalize()
            if sel_period == "1D":
                chart_df = hist_df.iloc[-2:] if len(hist_df) >= 2 else hist_df
            elif sel_period == "5D":
                chart_df = hist_df.iloc[-5:] if len(hist_df) >= 5 else hist_df
            elif sel_period == "1M":
                chart_df = hist_df[hist_df.index >= today - pd.DateOffset(months=1)]
            elif sel_period == "6M":
                chart_df = hist_df[hist_df.index >= today - pd.DateOffset(months=6)]
            elif sel_period == "YTD":
                chart_df = hist_df[hist_df.index >= pd.Timestamp(today.year, 1, 1)]
            elif sel_period == "1Y":
                chart_df = hist_df[hist_df.index >= today - pd.DateOffset(years=1)]
            else:
                chart_df = hist_df
            if chart_df.empty:
                chart_df = hist_df

            # ── 資産推移チャート ────────────────────────────────
            # 損益の塗りつぶし色（期間内で全体的にプラスかマイナスか）
            period_gain = chart_df["pnl"].mean() if "pnl" in chart_df.columns else 0
            fill_color  = "rgba(34,197,94,0.12)" if period_gain >= 0 else "rgba(239,68,68,0.10)"

            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=chart_df.index, y=chart_df["portfolio_value"],
                name="ポートフォリオ時価",
                line=dict(color="#60a5fa", width=2),
                hovertemplate="%{x|%Y-%m-%d}<br>評価額: %{y:,.0f}<extra></extra>",
            ))
            fig.add_trace(go.Scatter(
                x=chart_df.index, y=chart_df["invested_cost"],
                name="投資元本",
                line=dict(color="#94a3b8", width=1.5, dash="dot"),
                hovertemplate="%{x|%Y-%m-%d}<br>元本: %{y:,.0f}<extra></extra>",
            ))
            fig.add_trace(go.Scatter(
                x=list(chart_df.index) + list(chart_df.index[::-1]),
                y=list(chart_df["portfolio_value"]) + list(chart_df["invested_cost"][::-1]),
                fill="toself",
                fillcolor=fill_color,
                line=dict(width=0),
                showlegend=False,
                hoverinfo="skip",
            ))
            fig.update_layout(
                paper_bgcolor="#0f172a", plot_bgcolor="#0f172a",
                font=dict(color="#e2e8f0"),
                legend=dict(font=dict(color="#e2e8f0"), bgcolor="#1e293b",
                            bordercolor="#334155", borderwidth=1),
                xaxis=dict(tickfont=dict(color="#94a3b8"), gridcolor="#1e293b",
                           title=dict(font=dict(color="#94a3b8"))),
                yaxis=dict(tickfont=dict(color="#94a3b8"), gridcolor="#1e293b",
                           title=dict(text="評価額", font=dict(color="#94a3b8"))),
                hoverlabel=dict(bgcolor="#1e293b", font=dict(color="#e2e8f0")),
                margin=dict(l=10, r=10, t=10, b=10),
                height=320,
            )
            st.plotly_chart(fig, use_container_width=True)

            # ── 損益率推移チャート ──────────────────────────────
            with st.expander("📊 損益率推移（%）"):
                fig2 = go.Figure()
                colors2 = ["#22c55e" if v >= 0 else "#ef4444"
                           for v in chart_df["pnl_pct"].fillna(0)]
                fig2.add_trace(go.Bar(
                    x=chart_df.index, y=chart_df["pnl_pct"].fillna(0),
                    marker_color=colors2,
                    hovertemplate="%{x|%Y-%m-%d}<br>損益率: %{y:+.2f}%<extra></extra>",
                ))
                fig2.add_hline(y=0, line_color="#475569", line_width=1)
                fig2.update_layout(
                    paper_bgcolor="#0f172a", plot_bgcolor="#0f172a",
                    font=dict(color="#e2e8f0"),
                    xaxis=dict(tickfont=dict(color="#94a3b8"), gridcolor="#1e293b"),
                    yaxis=dict(tickfont=dict(color="#94a3b8"), gridcolor="#1e293b",
                               ticksuffix="%"),
                    hoverlabel=dict(bgcolor="#1e293b", font=dict(color="#e2e8f0")),
                    margin=dict(l=10, r=10, t=10, b=10),
                    height=220, showlegend=False,
                )
                st.plotly_chart(fig2, use_container_width=True)

            st.caption("※ USD建て・円建て銘柄が混在する場合は為替換算なしの合算値です。")

            st.markdown("<br>", unsafe_allow_html=True)

            # ── AI ポートフォリオ コメント ──────────────────────
            st.markdown(
                '<div style="background:#1e293b;border:1px solid #334155;border-radius:12px;'
                'padding:20px 24px;margin-top:8px">'
                '<div style="display:flex;align-items:center;gap:8px;margin-bottom:14px">'
                '<span style="font-size:20px">🤖</span>'
                '<div>'
                '<div style="font-size:14px;font-weight:700;color:#e2e8f0">'
                '今日のポートフォリオはどうですか？</div>'
                '<div style="font-size:11px;color:#64748b">AI分析 · 自動更新（1時間キャッシュ）</div>'
                '</div>'
                '</div>',
                unsafe_allow_html=True,
            )

            # 保有銘柄の日次騰落を取得
            open_pos_for_ai = _get_open_positions()
            ai_comment_data = {}
            ai_tickers_gainers = []
            ai_tickers_losers  = []

            if open_pos_for_ai:
                tickers_tuple = tuple(sorted(open_pos_for_ai.keys()))
                with st.spinner("銘柄騰落を取得中..."):
                    daily_chg = _fetch_daily_changes_for_tickers(tickers_tuple)

                if daily_chg:
                    import json as _json
                    changes_json = _json.dumps(daily_chg)
                    positions_key = ",".join(tickers_tuple)

                    with st.spinner("AIコメント生成中..."):
                        ai_comment_data = _generate_portfolio_ai_comment(
                            positions_key, changes_json
                        )

                    # 上昇・下落ランキング
                    sorted_chg = sorted(
                        daily_chg.items(),
                        key=lambda x: x[1].get("day_change_pct", 0),
                        reverse=True,
                    )
                    ai_tickers_gainers = [(t, d) for t, d in sorted_chg if d.get("day_change_pct", 0) > 0]
                    ai_tickers_losers  = [(t, d) for t, d in sorted_chg if d.get("day_change_pct", 0) < 0]

            narrative = ai_comment_data.get("narrative", "")
            bullets   = ai_comment_data.get("bullets", [])
            ai_err    = ai_comment_data.get("error")

            if ai_err and not narrative:
                st.warning(f"AIコメント取得失敗: {ai_err}")
            else:
                col_narr, col_bullets = st.columns([3, 2])
                with col_narr:
                    if narrative:
                        st.markdown(
                            f'<p style="color:#cbd5e1;font-size:13.5px;line-height:1.7;'
                            f'margin:0">{narrative}</p>',
                            unsafe_allow_html=True,
                        )
                    else:
                        st.markdown(
                            '<p style="color:#64748b;font-size:13px">取引記録を入力するとAI分析が表示されます。</p>',
                            unsafe_allow_html=True,
                        )

                with col_bullets:
                    if bullets:
                        st.markdown(
                            '<div style="font-size:12px;font-weight:700;color:#94a3b8;'
                            'margin-bottom:8px">一目でわかる</div>',
                            unsafe_allow_html=True,
                        )
                        for b in bullets:
                            st.markdown(
                                f'<div style="display:flex;gap:8px;margin-bottom:6px">'
                                f'<span style="color:#3b82f6;flex-shrink:0">•</span>'
                                f'<span style="color:#cbd5e1;font-size:13px">{b}</span>'
                                f'</div>',
                                unsafe_allow_html=True,
                            )

            # 上昇・下落チップ
            if ai_tickers_gainers or ai_tickers_losers:
                st.markdown("<div style='margin-top:14px'>", unsafe_allow_html=True)
                chip_html = '<div style="font-size:12px;font-weight:600;color:#94a3b8;margin-bottom:8px">上昇・下落銘柄</div><div style="display:flex;flex-wrap:wrap;gap:8px">'
                # 上昇（緑）
                for ticker, d in ai_tickers_gainers[:3]:
                    pct = d.get("day_change_pct", 0)
                    chip_html += (
                        f'<span style="background:#052e16;border:1px solid #166534;'
                        f'color:#4ade80;padding:4px 10px;border-radius:20px;font-size:12px;font-weight:600">'
                        f'{ticker} +{pct:.2f}%</span>'
                    )
                # 下落（赤）
                for ticker, d in reversed(ai_tickers_losers[-3:]):
                    pct = d.get("day_change_pct", 0)
                    chip_html += (
                        f'<span style="background:#450a0a;border:1px solid #991b1b;'
                        f'color:#f87171;padding:4px 10px;border-radius:20px;font-size:12px;font-weight:600">'
                        f'{ticker} {pct:.2f}%</span>'
                    )
                chip_html += '</div></div>'
                st.markdown(chip_html, unsafe_allow_html=True)

            st.markdown("</div>", unsafe_allow_html=True)

    # ── タブ⑥: サマリーダッシュボード ─────────────────────────────
    with tab_summary:
        st.markdown("#### 💹 ポートフォリオ サマリー")

        # 通貨選択
        currency_choice = st.radio(
            "表示通貨", ["🇺🇸 USD", "🇯🇵 JPY"],
            horizontal=True, key="summary_currency",
        )
        use_jpy = "JPY" in currency_choice
        cur_label = "円" if use_jpy else "USD"

        with st.spinner("ポートフォリオデータを取得中..."):
            summary = _compute_portfolio_summary()

        if summary["error"]:
            st.error(f"⚠️ {summary['error']}")
            st.caption("ページを再読み込みしてください。")
        elif not summary["positions"]:
            st.info("保有銘柄がまだありません。取引記録を入力してください。")
        else:
            positions = summary["positions"]
            dividends = summary["dividends"]
            usd_jpy   = summary["usd_jpy"]

            def _to_display(value_native, is_jp: bool) -> float:
                """ネイティブ通貨の値を表示通貨に変換"""
                if value_native is None:
                    return 0.0
                if use_jpy:
                    return float(value_native) if is_jp else float(value_native) * usd_jpy
                else:
                    return float(value_native) / usd_jpy if is_jp else float(value_native)

            import plotly.graph_objects as go

            # ── カード1: 含み損益（コスト vs 現在評価額）──────────────
            st.markdown(
                '<div style="background:#1e293b;border:1px solid #334155;border-radius:10px;'
                'padding:18px 20px;margin-bottom:18px">'
                '<div style="font-size:13px;color:#94a3b8;margin-bottom:12px;font-weight:600">'
                '📊 保有株式 損益サマリー</div>',
                unsafe_allow_html=True,
            )

            total_cost = sum(
                _to_display(p["cost"], p["is_jp"]) for p in positions.values()
            )
            total_mkt = sum(
                _to_display(p["market_value"], p["is_jp"])
                for p in positions.values() if p["market_value"] is not None
            )
            total_gain  = total_mkt - total_cost
            total_gp    = total_gain / total_cost * 100 if total_cost > 0 else 0
            gain_color  = "#22c55e" if total_gain >= 0 else "#ef4444"
            gain_sign   = "+" if total_gain >= 0 else ""

            c_left, c_right = st.columns([3, 1])
            with c_left:
                # 水平バーチャート（元本 vs 評価額）
                fig_gl = go.Figure()
                fig_gl.add_trace(go.Bar(
                    y=["コスト", "評価額"],
                    x=[total_cost, total_mkt],
                    orientation="h",
                    marker_color=["#475569", "#3b82f6"],
                    text=[f"{cur_label} {total_cost:,.0f}", f"{cur_label} {total_mkt:,.0f}"],
                    textposition="outside",
                    textfont=dict(color="#e2e8f0", size=12),
                    hovertemplate="%{y}: %{x:,.0f}<extra></extra>",
                ))
                fig_gl.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#e2e8f0"),
                    xaxis=dict(tickfont=dict(color="#94a3b8"), gridcolor="#334155",
                               tickformat=",.0f"),
                    yaxis=dict(tickfont=dict(color="#e2e8f0")),
                    margin=dict(l=10, r=60, t=5, b=5),
                    height=100,
                    showlegend=False,
                )
                st.plotly_chart(fig_gl, use_container_width=True)

            with c_right:
                st.markdown(
                    f'<div style="text-align:center;padding-top:10px">'
                    f'<div style="font-size:11px;color:#94a3b8">含み損益</div>'
                    f'<div style="font-size:22px;font-weight:700;color:{gain_color}">'
                    f'{gain_sign}{cur_label} {total_gain:,.0f}</div>'
                    f'<div style="font-size:14px;color:{gain_color}">'
                    f'{gain_sign}{total_gp:.2f}%</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

            st.markdown("</div>", unsafe_allow_html=True)

            # ── カード2: 配当金 ────────────────────────────────────
            st.markdown(
                '<div style="background:#1e293b;border:1px solid #334155;border-radius:10px;'
                'padding:18px 20px;margin-bottom:18px">'
                '<div style="font-size:13px;color:#94a3b8;margin-bottom:12px;font-weight:600">'
                '💴 配当金（過去6ヶ月）</div>',
                unsafe_allow_html=True,
            )

            # 月別配当を集計
            months_6 = []
            now = datetime.now()
            for i in range(5, -1, -1):
                m = (now.month - i - 1) % 12 + 1
                y = now.year - ((now.month - i - 1) // 12 + (1 if (now.month - i - 1) < 0 else 0))
                months_6.append(f"{y:04d}-{m:02d}")
            months_6 = sorted(set(months_6))[-6:]

            # ティッカー別に月次配当を集計（スタック棒グラフ用）
            ticker_month_div: dict[str, dict[str, float]] = {}
            detail_rows = []  # 明細テーブル用
            has_any_div = False

            _ticker_colors = [
                "#f59e0b", "#60a5fa", "#34d399", "#f87171",
                "#a78bfa", "#fb923c", "#38bdf8", "#4ade80",
            ]
            for _ci, (_tk, _dd) in enumerate(dividends.items()):
                _is_jp = _dd["is_jp"]
                _name  = _dd.get("name", _tk)
                _label = f"{_tk}（{_name}）" if _name != _tk else _tk
                ticker_month_div[_label] = {m: 0.0 for m in months_6}
                for month_key, amount in _dd["divs_by_month"].items():
                    if month_key in months_6:
                        ticker_month_div[_label][month_key] += _to_display(amount, _is_jp)
                        has_any_div = True
                # 明細行
                for ev in _dd.get("divs_detail", []):
                    _disp = _to_display(ev["total"], _is_jp)
                    _ps   = _to_display(ev["per_share"], _is_jp)
                    detail_rows.append({
                        "銘柄":     _label,
                        "権利落日": ev["date"],
                        "1株配当":  f"{_ps:,.4f} {cur_label}",
                        "保有株数": int(ev["qty"]),
                        "受取配当": f"{_disp:,.2f} {cur_label}",
                    })

            if has_any_div:
                import plotly.graph_objects as go
                fig_div = go.Figure()
                for _ci, (_label, _mvals) in enumerate(ticker_month_div.items()):
                    _color = _ticker_colors[_ci % len(_ticker_colors)]
                    fig_div.add_trace(go.Bar(
                        name=_label,
                        x=list(_mvals.keys()),
                        y=list(_mvals.values()),
                        marker_color=_color,
                        hovertemplate=f"{_label}<br>%{{x}}<br>配当: %{{y:,.2f}}<extra></extra>",
                    ))
                fig_div.update_layout(
                    barmode="stack",
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#e2e8f0"),
                    xaxis=dict(tickfont=dict(color="#94a3b8"), gridcolor="#1e293b"),
                    yaxis=dict(tickfont=dict(color="#94a3b8"), gridcolor="#1e293b",
                               tickprefix=f"{cur_label} "),
                    hoverlabel=dict(bgcolor="#1e293b", font=dict(color="#e2e8f0")),
                    legend=dict(font=dict(color="#e2e8f0", size=11),
                                orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
                    margin=dict(l=10, r=10, t=40, b=10),
                    height=220,
                )
                st.plotly_chart(fig_div, use_container_width=True)

                # 明細テーブル
                if detail_rows:
                    detail_rows_sorted = sorted(detail_rows, key=lambda r: r["権利落日"], reverse=True)
                    total_div = sum(
                        float(r["受取配当"].split()[0].replace(",", ""))
                        for r in detail_rows_sorted
                    )
                    for dr in detail_rows_sorted:
                        st.markdown(
                            f'<div style="display:flex;justify-content:space-between;'
                            f'padding:5px 0;border-bottom:1px solid #1e293b;font-size:12px">'
                            f'<span style="color:#94a3b8">{dr["権利落日"]}</span>'
                            f'<span style="color:#e2e8f0;font-weight:600">{dr["銘柄"]}</span>'
                            f'<span style="color:#64748b">{dr["1株配当"]}</span>'
                            f'<span style="color:#64748b">{dr["保有株数"]}株</span>'
                            f'<span style="color:#f59e0b;font-weight:700">{dr["受取配当"]}</span>'
                            f'</div>',
                            unsafe_allow_html=True,
                        )
                    st.markdown(
                        f'<div style="text-align:right;font-size:12px;color:#94a3b8;margin-top:8px">'
                        f'過去6ヶ月合計: <b style="color:#f59e0b">{cur_label} {total_div:,.2f}</b></div>',
                        unsafe_allow_html=True,
                    )
            else:
                st.info("配当履歴がありません（過去6ヶ月）。")

            st.markdown("</div>", unsafe_allow_html=True)

            # ── カード3 & 4 を横並び ─────────────────────────────
            col3, col4 = st.columns(2)

            # ── カード3: アセットアロケーション ─────────────────
            with col3:
                st.markdown(
                    '<div style="background:#1e293b;border:1px solid #334155;border-radius:10px;'
                    'padding:18px 20px;height:100%">'
                    '<div style="font-size:13px;color:#94a3b8;margin-bottom:12px;font-weight:600">'
                    '🌏 アセットアロケーション</div>',
                    unsafe_allow_html=True,
                )

                jp_val = sum(
                    _to_display(p["market_value"], True)
                    for p in positions.values() if p["is_jp"] and p["market_value"] is not None
                )
                us_val = sum(
                    _to_display(p["market_value"], False)
                    for p in positions.values() if not p["is_jp"] and p["market_value"] is not None
                )
                total_alloc = jp_val + us_val

                if total_alloc > 0:
                    jp_pct = jp_val / total_alloc * 100
                    us_pct = us_val / total_alloc * 100
                    fig_alloc = go.Figure(go.Pie(
                        labels=["🇯🇵 日本株", "🇺🇸 米国株"],
                        values=[jp_val, us_val],
                        marker_colors=["#f97316", "#3b82f6"],
                        textinfo="label+percent",
                        textfont=dict(color="#e2e8f0", size=12),
                        hole=0.5,
                        hovertemplate="%{label}<br>%{value:,.0f} (%{percent})<extra></extra>",
                    ))
                    fig_alloc.update_layout(
                        paper_bgcolor="rgba(0,0,0,0)",
                        font=dict(color="#e2e8f0"),
                        legend=dict(font=dict(color="#e2e8f0"), bgcolor="rgba(0,0,0,0)"),
                        hoverlabel=dict(bgcolor="#1e293b", font=dict(color="#e2e8f0")),
                        margin=dict(l=10, r=10, t=10, b=10),
                        height=220,
                    )
                    st.plotly_chart(fig_alloc, use_container_width=True)
                    st.markdown(
                        f'<div style="display:flex;gap:16px;justify-content:center;font-size:12px">'
                        f'<span style="color:#f97316">🇯🇵 {jp_pct:.1f}% ({cur_label} {jp_val:,.0f})</span>'
                        f'<span style="color:#3b82f6">🇺🇸 {us_pct:.1f}% ({cur_label} {us_val:,.0f})</span>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                else:
                    st.info("データなし")

                st.markdown("</div>", unsafe_allow_html=True)

            # ── カード4: セクターアロケーション ─────────────────
            with col4:
                st.markdown(
                    '<div style="background:#1e293b;border:1px solid #334155;border-radius:10px;'
                    'padding:18px 20px;height:100%">'
                    '<div style="font-size:13px;color:#94a3b8;margin-bottom:12px;font-weight:600">'
                    '🏭 セクター別配分</div>',
                    unsafe_allow_html=True,
                )

                sector_vals: dict[str, float] = {}
                for p in positions.values():
                    if p["market_value"] is None:
                        continue
                    sec = p["sector"]
                    val = _to_display(p["market_value"], p["is_jp"])
                    sector_vals[sec] = sector_vals.get(sec, 0.0) + val

                if sector_vals:
                    sorted_sectors = sorted(sector_vals.items(), key=lambda x: x[1], reverse=True)
                    sec_labels = [s[0] for s in sorted_sectors]
                    sec_values = [s[1] for s in sorted_sectors]

                    sector_colors = [
                        "#3b82f6", "#8b5cf6", "#06b6d4", "#22c55e",
                        "#f59e0b", "#f97316", "#ef4444", "#ec4899",
                        "#64748b", "#14b8a6", "#a855f7",
                    ]

                    fig_sec = go.Figure(go.Bar(
                        x=sec_values,
                        y=sec_labels,
                        orientation="h",
                        marker_color=sector_colors[:len(sec_labels)],
                        text=[f"{v / sum(sec_values) * 100:.1f}%" for v in sec_values],
                        textposition="outside",
                        textfont=dict(color="#94a3b8", size=11),
                        hovertemplate="%{y}<br>%{x:,.0f}<extra></extra>",
                    ))
                    fig_sec.update_layout(
                        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                        font=dict(color="#e2e8f0"),
                        xaxis=dict(tickfont=dict(color="#94a3b8"), gridcolor="#334155",
                                   tickformat=",.0f"),
                        yaxis=dict(tickfont=dict(color="#e2e8f0"), autorange="reversed"),
                        hoverlabel=dict(bgcolor="#1e293b", font=dict(color="#e2e8f0")),
                        margin=dict(l=10, r=60, t=5, b=5),
                        height=max(160, len(sec_labels) * 36),
                        showlegend=False,
                    )
                    st.plotly_chart(fig_sec, use_container_width=True)
                else:
                    st.info("データなし")

                st.markdown("</div>", unsafe_allow_html=True)

            # ── 個別銘柄テーブル ─────────────────────────────────
            st.markdown("<br>", unsafe_allow_html=True)
            st.markdown("##### 保有銘柄一覧")

            rows_tbl = []
            for ticker, p in positions.items():
                mkt_disp  = _to_display(p["market_value"], p["is_jp"])
                gain_disp = _to_display(p["gain"], p["is_jp"])
                gp = p["gain_pct"] or 0.0
                pos_sign  = "+" if gain_disp >= 0 else ""
                gain_str = f'{pos_sign}{cur_label} {abs(gain_disp):,.0f} ({gp:+.2f}%)' if p["gain"] is not None else "—"
                flag = "🇯🇵" if p["is_jp"] else "🇺🇸"
                rows_tbl.append({
                    "": flag,
                    "ティッカー": ticker,
                    "銘柄名": p["name"],
                    "セクター": p["sector"],
                    f"評価額 ({cur_label})": f"{mkt_disp:,.0f}" if p["market_value"] else "—",
                    "含み損益": gain_str,
                })
            if rows_tbl:
                st.dataframe(
                    rows_tbl,
                    use_container_width=True,
                    hide_index=True,
                )

            st.caption(f"USD/JPY レート: {usd_jpy:.2f}  ※ 配当はyfinanceの配当履歴より。")


# =====================================================
# 🔐 管理者用チェンジログ（パスワード保護）
# =====================================================

_CHANGELOG = [
    {
        "date": "2026-07-25",
        "title": "📅 NASDAQ100 構成銘柄入れ替え履歴を追加",
        "items": [
            "モメンタムランキングのNASDAQ100タブに入れ替え銘柄一覧（追加・除外）を表示",
            "2023〜2024年の年次・四半期入れ替え履歴を収録",
        ],
        "tag": "新機能",
        "color": "#06b6d4",
    },
    {
        "date": "2026-07-25",
        "title": "💰 損益タブの現在株価取得を改善",
        "items": [
            "yf.Ticker.fast_infoを最優先で使用し取得成功率を向上",
            "MultiIndexのフォールバック処理を追加",
        ],
        "tag": "バグ修正",
        "color": "#ef4444",
    },
    {
        "date": "2026-07-25",
        "title": "🤖 Claude 個別株トレーディングプロジェクト",
        "items": [
            "AI売買シグナル生成（テクニカル＋TDnet決算＋Finnhubニュース）",
            "取引記録入力フォーム（約定後に手動入力）",
            "損益・ポートフォリオ追跡（含み損益リアルタイム計算）",
            "ポートフォリオ全体AI分析（リスク診断・アクション優先順位TOP3）",
        ],
        "tag": "新機能",
        "color": "#3b82f6",
    },
    {
        "date": "2026-07-25",
        "title": "🚀 モメンタムランキングにNASDAQ100・S&P500を追加",
        "items": [
            "S&P500構成銘柄30銘柄のリストを新規追加",
            "ラジオボタン切り替え → タブ（日経225 / NASDAQ100 / S&P500）に変更",
        ],
        "tag": "機能改善",
        "color": "#8b5cf6",
    },
    {
        "date": "2026-07-25",
        "title": "📡 光通信バスケットのグラフ急変を修正",
        "items": [
            "廃止・合併済み銘柄（IIVI/FNSR/SNDK/ATXI）をバスケットから削除",
            "ffill()を正規化前に適用し東証休場日等の欠損によるスパイクを解消",
        ],
        "tag": "バグ修正",
        "color": "#ef4444",
    },
    {
        "date": "2026-07-25",
        "title": "🌐 PCE/Core PCEの取得元をBLS API → FRED CSVに変更",
        "items": [
            "api.bls.govのタイムアウト頻発を解消",
            "FRED CSVエンドポイント（APIキー不要）でPCEPI・PCEPILFEを取得",
        ],
        "tag": "バグ修正",
        "color": "#ef4444",
    },
    {
        "date": "2026-07-25",
        "title": "🔧 5項目のバグ修正",
        "items": [
            "gemini-pro（廃止モデル）→ gemini-1.5-flash-8bに変更",
            "OECD CLI URL を廃止済みstats.oecd.org → 新sdmx.oecd.orgに更新",
            "BLS API endyearに来年を指定していたバグを修正",
            "@st.cache_dataの重複デコレータを削除",
            "JPX空売りURLデッドコードを削除",
        ],
        "tag": "バグ修正",
        "color": "#ef4444",
    },
]


def render_admin_changelog():
    """パスワード保護付き管理者チェンジログ"""
    admin_pw = st.secrets.get("ADMIN_PASSWORD", "")
    if not admin_pw:
        return  # secretsに未設定の場合はパネル自体を表示しない

    if not st.session_state.get("_admin_authed"):
        with st.expander("🔐 管理者メニュー", expanded=False):
            pw_input = st.text_input("パスワード", type="password",
                                     key="admin_pw_input", label_visibility="collapsed",
                                     placeholder="パスワードを入力")
            if st.button("ログイン", key="admin_login_btn"):
                if pw_input == admin_pw:
                    st.session_state["_admin_authed"] = True
                    st.rerun()
                else:
                    st.error("パスワードが違います")
        return

    with st.expander("🔐 管理者メニュー — 更新履歴", expanded=True):
        if st.button("🔒 ログアウト", key="admin_logout_btn"):
            st.session_state["_admin_authed"] = False
            st.rerun()

        st.markdown("### 📋 最近の更新履歴")
        for entry in _CHANGELOG:
            tag_color = entry["color"]
            items_html = "".join(
                f'<li style="color:#cbd5e1;font-size:13px;margin:3px 0">{it}</li>'
                for it in entry["items"]
            )
            st.markdown(
                f'<div style="background:#1e293b;border-left:4px solid {tag_color};'
                f'border-radius:0 8px 8px 0;padding:12px 16px;margin-bottom:10px;">'
                f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">'
                f'<span style="background:{tag_color};color:#fff;font-size:11px;'
                f'font-weight:700;padding:2px 8px;border-radius:4px">{entry["tag"]}</span>'
                f'<span style="color:#94a3b8;font-size:12px">{entry["date"]}</span>'
                f'</div>'
                f'<div style="font-size:14px;font-weight:700;color:#f1f5f9;margin-bottom:6px">'
                f'{entry["title"]}</div>'
                f'<ul style="margin:0;padding-left:18px">{items_html}</ul>'
                f'</div>',
                unsafe_allow_html=True,
            )


# ===========================
def _render_trading_page():
    """?page=trading でアクセスされた際の専用ページ描画。
    メインダッシュボードのデータフェッチを一切実行しないため高速に開く。
    """
    st.markdown(
        '<a href="/" style="display:inline-flex;align-items:center;gap:6px;'
        'color:#94a3b8;font-size:13px;text-decoration:none;'
        'padding:6px 12px;background:#1e293b;border-radius:7px;'
        'border:1px solid #334155;margin-bottom:12px;">'
        '← windex ダッシュボードに戻る'
        '</a>',
        unsafe_allow_html=True,
    )
    render_claude_trading_project()


def main():
    now_jst = datetime.now(JST)

    # ── ① 国ブロック（最優先チェック）──────────────────────────
    if check_country_block():
        st.error("🚫 Access Denied — このサービスはご利用いただけない地域からのアクセスが検出されました。")
        st.stop()
        return

    # ── トレーディング専用ページ（?page=trading）────────────────
    if st.query_params.get("page") == "trading":
        _render_trading_page()
        return

    # ── ② 言語自動検出（セッション初回のみ）────────────────────
    if "lang" not in st.session_state:
        st.session_state["lang"] = _detect_lang()

    # ── ③ SEO / OGP メタタグ注入 ────────────────────────────────
    inject_seo_meta()

    st.title("📊 Market Dashboard")
    st.caption(
        t(f"最終更新: {now_jst:%Y-%m-%d %H:%M:%S} JST | フォント: {FONT_NAME}",
          f"Last updated: {now_jst:%Y-%m-%d %H:%M:%S} JST | Font: {FONT_NAME}")
    )

    # ── トレーディングプロジェクトへのショートカット ───────────────
    st.markdown(
        '<a href="?page=trading" style="'
        'display:inline-flex;align-items:center;gap:8px;'
        'background:linear-gradient(135deg,#1e3a5f,#1e40af);'
        'color:#93c5fd !important;text-decoration:none !important;'
        'padding:7px 16px;border-radius:8px;border:1px solid #2563eb;'
        'font-size:13px;font-weight:700;margin-bottom:4px;'
        'box-shadow:0 2px 8px rgba(37,99,235,0.35);">'
        '💹 Claude 個別株トレーディングプロジェクトへ →'
        '</a>',
        unsafe_allow_html=True,
    )

    render_admin_changelog()

    # ── Google翻訳ウィジェット ──────────────────────────────────
    # components.html だと Streamlit 再描画のたびに iframe がリセットされて消える。
    # st.markdown で <script> をページ本体に直接注入することで永続表示を実現。
    st.markdown(
        """
        <div id="google_translate_element"
             style="margin:4px 0 10px 0; display:inline-block;"></div>

        <script type="text/javascript">
        // 二重初期化防止
        if (!window._gtransInitialized) {
            window._gtransInitialized = true;

            function googleTranslateElementInit() {
                new google.translate.TranslateElement({
                    pageLanguage: 'ja',
                    includedLanguages: 'ja,en,zh-TW',
                    layout: google.translate.TranslateElement.InlineLayout.SIMPLE,
                    autoDisplay: false,
                    multilanguagePage: true
                }, 'google_translate_element');
            }

            // スクリプトをページ本体に動的追加（iframe外で実行）
            var s = document.createElement('script');
            s.type = 'text/javascript';
            s.src  = '//translate.google.com/translate_a/element.js?cb=googleTranslateElementInit';
            document.head.appendChild(s);
        } else {
            // 再描画時: 要素が消えていたら再マウント
            (function reMount() {
                var el = document.getElementById('google_translate_element');
                if (el && el.children.length === 0) {
                    try {
                        new google.translate.TranslateElement({
                            pageLanguage: 'ja',
                            includedLanguages: 'ja,en,zh-TW',
                            layout: google.translate.TranslateElement.InlineLayout.SIMPLE,
                            autoDisplay: false
                        }, 'google_translate_element');
                    } catch(e) {}
                }
            })();
        }
        </script>

        <style>
        /* ウィジェットの見た目 */
        .goog-te-gadget-simple {
            border: 1px solid #d0d7de !important;
            border-radius: 6px !important;
            padding: 4px 10px !important;
            font-size: 13px !important;
            background: #f6f8fa !important;
            cursor: pointer !important;
        }
        .goog-te-gadget-simple span { color: #24292f !important; }
        /* 上部バナーを非表示 */
        .goog-te-banner-frame { display: none !important; }
        .goog-te-menu-frame  { z-index: 99999 !important; }
        body { top: 0 !important; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # ── アクセス計測（1セッション1回）────────────────────────
    if is_ipad_or_ios_safari():
        # iPad/iOS Safari: JSリロードを使わずサーバー側でIP取得
        if not st.session_state.get("_anl_client_collected"):
            info = fetch_ip_info_server_side()
            st.session_state["_anl_country"] = info["country"]
            st.session_state["_anl_city"]    = info["city"]
            try:
                ua = str(st.context.headers.get("user-agent", ""))
            except Exception:
                ua = ""
            st.session_state["_anl_ua"]      = ua
            st.session_state["_user_agent"]  = ua
            st.session_state["_anl_client_collected"] = True
            logger.info(f"[analytics] iPad/iOS: {info['country']}/{info['city']}")
        track_pageview()
    else:
        # PC/Android: JS経由でIP・UAを取得（URLリダイレクト方式）
        inject_client_info_collector()
        track_pageview()

    # ── streamlit-analytics2 計測開始 ────────────────────────
    if ANALYTICS2_AVAILABLE:
        streamlit_analytics.start_tracking()
    # (disabled)

    st.markdown(_build_nav_html(st.session_state.get("lang", "ja")), unsafe_allow_html=True)

    inject_global_css()
    responsive_cols = get_responsive_cols()

    with st.sidebar:
        # ── 言語切替トグル ───────────────────────────────────────
        _lang_ja, _lang_en = st.columns(2)
        with _lang_ja:
            if st.button("🇯🇵 日本語", key="lang_ja_btn", width="stretch",
                         type="primary" if st.session_state.get("lang") == "ja" else "secondary"):
                st.session_state["lang"] = "ja"
                st.rerun()
        with _lang_en:
            if st.button("🇺🇸 English", key="lang_en_btn", width="stretch",
                         type="primary" if st.session_state.get("lang") == "en" else "secondary"):
                st.session_state["lang"] = "en"
                st.rerun()
        st.divider()

        st.subheader(t("⚙️ 操作", "⚙️ Controls"))
        if st.button(
            t("🔄 マーケットデータ更新", "🔄 Refresh Market Data"),
            type="primary", width="stretch",
        ):
            # 市場データのみクリア（AI生成コンテンツは保持）
            for fn in [
                fetch_macro_indicators,
                _fetch_summary_prices,
                compute_bear_market_risk,
                compute_composite_sentiment,
                fetch_fear_greed_index,
                fetch_fg_components,
                fetch_japan_fear_greed_index,
                _fetch_momentum_ranking,
                _fetch_optical_vs_semi,
                _fetch_eco_actuals_bls,
                _fetch_eco_actuals_fmp,
                _fetch_eco_actuals_fred,
                _fetch_event_market_reactions,
                compute_nikkei_prediction,
                compute_sector_rotation,
                fetch_naaim_data,
            ]:
                try:
                    fn.clear()
                except Exception:
                    pass
            st.success(t("✅ マーケットデータを更新しました", "✅ Market data refreshed"))
            st.rerun()
        st.divider()
        st.subheader(t("🔑 API設定", "🔑 API Status"))
        api_status = {
            "Tiingo": "✅" if TIINGO_API_KEY else "❌",
            "Google Analytics": "✅" if GA_MEASUREMENT_ID else "❌",
            "DeepL": "✅" if DEEPL_API_KEY else "❌",
            "Gemini": "✅" if GEMINI_API_KEY else "❌",
            "Groq": "✅" if GROQ_API_KEY else "❌",
            "OpenRouter": "✅" if OPENROUTER_API_KEY else "❌",
            t("FMP (経済指標実績)", "FMP (Eco. actuals)"): "✅" if FMP_API_KEY else t("❌ 未設定（無料登録可）", "❌ Not set (free signup)"),
        }
        for name, status in api_status.items():
            st.write(f"**{name}:** {status}")
        active_ai = []
        if GEMINI_API_KEY: active_ai.append("Gemini")
        if GROQ_API_KEY: active_ai.append("Groq")
        if OPENROUTER_API_KEY: active_ai.append("OpenRouter")
        chain_str = " → ".join(active_ai) if active_ai else t("未設定", "Not configured")
        st.caption(f"🤖 AI chain: {chain_str}")
        with st.expander(t("📝 設定方法", "📝 How to configure")):
            st.code("""
# .streamlit/secrets.toml
TIINGO_API_KEY = "your_key"
GA_MEASUREMENT_ID = "G-XXXXXXXXXX"
DEEPL_API_KEY = "your_key:fx"
GEMINI_API_KEY = "your_key"
GROQ_API_KEY = "gsk_..."
OPENROUTER_API_KEY = "sk-or-..."
            """, language="toml")
        st.divider()
        st.subheader(t("🌐 ニュース設定", "🌐 News"))
        translate_mode = st.toggle(t("📰 ニュース翻訳", "📰 Translate news"), value=True)
        st.divider()
        st.subheader(t("📱 表示設定", "📱 Display"))
        st.caption(t("CSSで自動切替: PC→4列 / タブレット横→3列 / iPad mini縦→2列",
                     "Auto layout: PC→4col / Tablet→3col / iPad mini→2col"))
        col_manual = st.selectbox(
            t("カラム数を手動指定（自動の上書き）", "Override column count"),
            options=(["自動（CSS制御）", "2列", "3列", "4列"] if st.session_state.get("lang") != "en"
                     else ["Auto (CSS)", "2 col", "3 col", "4 col"]),
            index=0,
            key="col_select",
        )
        manual_map = ({"自動（CSS制御）": None, "2列": 2, "3列": 3, "4列": 4}
                      if st.session_state.get("lang") != "en"
                      else {"Auto (CSS)": None, "2 col": 2, "3 col": 3, "4 col": 4})
        manual_val = manual_map.get(col_manual)
        if manual_val is not None:
            st.session_state["detected_cols"] = manual_val
            responsive_cols = manual_val
        else:
            st.session_state.pop("detected_cols", None)
            responsive_cols = 4
        st.caption(t(f"Python列数: {responsive_cols}（表示はCSS自動調整）",
                     f"Columns: {responsive_cols} (CSS auto-adjusts display)"))
        st.divider()
        st.subheader(t("ℹ️ キャッシュ設定", "ℹ️ Cache TTLs"))
        st.write(f"**{'日足' if st.session_state.get('lang')!='en' else 'Daily'}:** {TTL_DAILY}s")
        st.write(f"**{'イントラ' if st.session_state.get('lang')!='en' else 'Intraday'}:** {TTL_INTRADAY}s")
        st.write(f"**RSS:** {TTL_RSS}s")
        st.write(f"**{'チャート' if st.session_state.get('lang')!='en' else 'Chart'}:** {TTL_CHART}s")
        st.divider()
        st.subheader(t("🤖 AI設定", "🤖 AI Settings"))
        st.caption(t("Gemini優先 → quota超過(429)時のみGroqへ自動切替",
                     "Gemini first → auto-fallback to Groq on quota (429)"))
        st.divider()
        st.subheader(t("🔧 デバッグ情報", "🔧 Debug"))
        if st.checkbox(t("利用可能なGeminiモデルを表示", "Show available Gemini models"), value=False):
            if GENAI_AVAILABLE and GEMINI_API_KEY:
                try:
                    genai.configure(api_key=GEMINI_API_KEY)
                    models_list = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
                    st.write(models_list[:10])
                except Exception as e:
                    st.error(f"{'モデル一覧取得エラー' if st.session_state.get('lang')!='en' else 'Model list error'}: {e}")
            else:
                st.warning(t("Gemini APIキーが設定されていません", "Gemini API key not configured"))

    # ===================================================
    # ★ Today's Market Snapshot（全体概要 — 最初に表示）
    # ===================================================
    render_market_summary()

    # ===================================================
    # ★ 米国経済イベントカレンダー（バックグラウンド並列取得）
    # カレンダーデータを4スレッドで並列フェッチしながら
    # 後続セクションを先にレンダリングして表示速度を改善する
    # ===================================================
    _cal_executor = ThreadPoolExecutor(max_workers=4)
    _f_reactions  = _cal_executor.submit(_fetch_event_market_reactions)
    _f_fmp        = _cal_executor.submit(_fetch_eco_actuals_fmp)
    _f_bls        = _cal_executor.submit(_fetch_eco_actuals_bls)
    _f_fred       = _cal_executor.submit(_fetch_eco_actuals_fred)
    # プレースホルダーを配置（後で埋める）
    _calendar_placeholder = st.empty()

    # ===================================================
    # ★ マクロ経済指標（CAPE / LEI / PCE）
    # ===================================================
    render_macro_indicators()

    # ===================================================
    # ★ 弱気相場リスク判定
    # ===================================================
    render_bear_market_checker()

    # ===================================================
    # ★ モメンタムランキング（日経225 / ナスダック）
    # ===================================================
    render_momentum_ranking()
    st.divider()

    # ===================================================
    # ★ Claude 個別株トレーディングプロジェクト（専用ページ）
    # ===================================================
    st.markdown('<a id="claude-trading"></a>', unsafe_allow_html=True)
    st.markdown(
        '<div style="background:#0f172a;border:1px solid #334155;border-radius:12px;'
        'padding:18px 22px;margin:8px 0 16px;">'
        '<div style="font-size:17px;font-weight:700;color:#e2e8f0;margin-bottom:6px;">'
        '💹 Claude 個別株トレーディングプロジェクト</div>'
        '<div style="font-size:13px;color:#94a3b8;margin-bottom:14px;">'
        'AI分析シグナル・取引記録・損益管理・ポートフォリオ追跡を専用ページで提供しています。'
        'メインダッシュボードとは分離しているため、高速に開きます。'
        '</div>'
        '<a href="?page=trading" style="display:inline-flex;align-items:center;gap:8px;'
        'background:linear-gradient(135deg,#1e40af,#2563eb);color:#fff !important;'
        'padding:10px 22px;border-radius:8px;text-decoration:none !important;'
        'font-size:14px;font-weight:700;box-shadow:0 3px 12px rgba(37,99,235,0.4);">'
        '💹 売買プロジェクトを開く →'
        '</a>'
        '</div>',
        unsafe_allow_html=True,
    )
    st.divider()

    # ===================================================
    # ★ 光通信・AIインフラ vs 半導体 パフォーマンス比較
    # ===================================================
    render_optical_vs_semi()

    # ===================================================
    # ★ 決算ベース指数予測（SOX・光通信バスケット）
    # ===================================================
    render_earnings_index_forecast()

    # ===================================================
    # ★ Fear & Greed Index（米国・日本）
    # ===================================================
    st.markdown('<a id="fear-greed"></a>', unsafe_allow_html=True)
    st.header("😱 Fear & Greed Index")
    tab_us, tab_jp = st.tabs([
        t("🇺🇸 米国版", "🇺🇸 US"),
        t("🇯🇵 日本版", "🇯🇵 Japan"),
    ])

    with tab_us:
        st.subheader(t("米国市場の投資家心理（CNN Fear & Greed Index）",
                       "US Market Sentiment (CNN Fear & Greed Index)"))
        with st.spinner(t("Fear & Greed Indexを取得中...", "Loading Fear & Greed Index...")):
            fg_data = fetch_fear_greed_index()

        if fg_data:
            score = fg_data["score"]
            rating = fg_data["rating"]
            prev_close = fg_data.get("previous_close")
            hist_df = fg_data.get("historical")
            score_change = (score - prev_close) if prev_close is not None else None
            label, color = get_fear_greed_label(score)

            col1, col2, col3 = st.columns([2, 1, 1])
            with col1:
                png_bytes = make_fear_greed_gauge(score)
                if isinstance(png_bytes, bytes):
                    st.image(png_bytes)
                else:
                    st.pyplot(png_bytes, clear_figure=True)
            with col2:
                st.metric(t("現在スコア", "Current Score"), f"{score:.0f}/100",
                          delta=f"{score_change:+.1f}" if score_change else None,
                          delta_color="inverse")
                st.markdown(f"**{t('状態', 'Status')}:** <span style='color:{color};font-weight:bold'>{label}</span>", unsafe_allow_html=True)
                st.caption(f"{t('レーティング', 'Rating')}: {rating}")
            with col3:
                st.markdown(f"**📊 {t('指標の見方', 'Scale')}**")
                st.caption("0-25: Extreme Fear")
                st.caption("25-45: Fear")
                st.caption("45-55: Neutral")
                st.caption("55-75: Greed")
                st.caption("75-100: Extreme Greed")

            if hist_df is not None and not hist_df.empty:
                with st.expander(t("📈 履歴トレンド", "📈 Historical Trend"), expanded=True):
                    draw_trend_chart(
                        hist_df,
                        title=t("米国 Fear & Greed Index 推移", "US Fear & Greed Index History"),
                        ylabel="Fear & Greed Score",
                        y_min=0, y_max=100,
                        add_fg_bands=True,
                        color=LINE_NEUTRAL,
                    )
                    st.markdown(DASHBOARD_LINKS_HTML, unsafe_allow_html=True)
        else:
            st.error(t("⚠️ Fear & Greed Indexを取得できませんでした",
                       "⚠️ Failed to load Fear & Greed Index"))

        st.divider()
        st.caption(t("🤖 CNN Fear & Greed Indexとは別手法で、市場全体を総合評価した参考スコアです。",
                     "🤖 An independent composite score evaluating overall market sentiment."))

        # ── AI Sentiment Index を先に描画（重いNAAIMを待たない） ──
        render_composite_sentiment()
        st.divider()

        # ── 4指標相関分析 + AIコメント ────────────────────────
        render_4indicator_correlation()
        st.divider()

        # ── NAAIMはプレースホルダーで後から描画 ──────────────────
        with st.container():
            render_naaim_section()

        # ─── 7指標詳細 ─────────────────────────────
        st.divider()
        st.markdown("#### 🔬 構成7指標の詳細")
        st.caption("CNNと同じ7指標をYahoo Financeデータから独自算出しています。")

        # 最初から開いた状態で表示
        if "show_fg_components" not in st.session_state:
            st.session_state["show_fg_components"] = True
        if False:  # ボタンは使用しない
            pass
        else:
            with st.spinner("7指標を計算中（初回30秒ほどかかります）..."):
                comps = fetch_fg_components()
            # 指標定義
            COMP_META = [
            {
                "key":   "momentum",
                "title": "① Market Momentum",
                "sub":   "S&P 500 vs 125-Day Moving Average",
                "icon":  "📈",
                "color": "#1565c0",
                "desc":  (
                    "S&P500の現値が過去125営業日の移動平均より**上**にあれば強気モメンタム（Greed）、"
                    "**下**にあれば投資家が慎重になっているサイン（Fear）。"
                    "CNNはこの移動平均乖離を主要シグナルとして使用。"
                ),
                "metric_fn": lambda c: (
                    f"S&P500: {c['sp500']:,.0f}",
                    f"MA125: {c['ma125']:,.0f}",
                    f"乖離: {c['pct_diff']:+.2f}%",
                ),
            },
            {
                "key":   "strength",
                "title": "② Stock Price Strength",
                "sub":   "Net New 52-Week Highs and Lows on the NYSE",
                "icon":  "💪",
                "color": "#2e7d32",
                "desc":  (
                    "NYSE銘柄の52週高値更新数と52週安値更新数の差。"
                    "高値更新>安値更新が多いほど強気（Greed）。"
                    "一部の大型株だけでなく市場全体の方向性を確認する指標。"
                ),
                "metric_fn": lambda c: (
                    f"高値圏ETF: {c['new_highs']}銘柄",
                    f"安値圏ETF: {c['new_lows']}銘柄",
                    f"净差: {c['net']:+d}",
                ),
            },
            {
                "key":   "breadth",
                "title": "③ Stock Price Breadth",
                "sub":   "McClellan Volume Summation Index",
                "icon":  "📊",
                "color": "#6a1b9a",
                "desc":  (
                    "NYSE上昇銘柄出来高と下落銘柄出来高の差から算出するMcClellan Volume Summation Index。"
                    "正（プラス）なら市場全体が強く（Greed）、負（マイナス）なら弱い（Fear）。"
                ),
                "metric_fn": lambda c: (
                    f"MSI: {c['msi']:+.1f}",
                    "正=市場強", "負=市場弱",
                ),
            },
            {
                "key":   "put_call",
                "title": "④ Put and Call Options",
                "sub":   "CBOE Put/Call Ratio",
                "icon":  "📋",
                "color": "#e65100",
                "desc":  (
                    "プット（売る権利）とコール（買う権利）の比率。"
                    "P/C比が上昇している＝投資家が守りに入っている＝Fear。"
                    "**P/C > 1.0** は弱気（Bearish）シグナル。"
                ),
                "metric_fn": lambda c: (
                    f"Ratio: {c['ratio']:.3f}",
                    f"Source: {c.get('source','CBOE')}",
                    "High=Fear / Low=Greed",
                ),
            },
            {
                "key":   "volatility",
                "title": "⑤ Market Volatility",
                "sub":   "CBOE Volatility Index (VIX)",
                "icon":  "😰",
                "color": "#b71c1c",
                "desc":  (
                    "VIXは今後30日間のS&P500オプションの予想変動率。"
                    "上昇相場ではVIXが低下し、急落時に急騰する。"
                    "**VIX > 25** はFear警戒ゾーン。長期平均との比較も重要。"
                ),
                "metric_fn": lambda c: (
                    f"VIX: {c['vix']:.2f}",
                    f"MA50: {c['ma50']:.2f}",
                    "25超=Fear圏",
                ),
            },
            {
                "key":   "safe_haven",
                "title": "⑥ Safe Haven Demand",
                "sub":   "Treasury Bond vs Stock Returns (20 days)",
                "icon":  "🏦",
                "color": "#004d40",
                "desc":  (
                    "過去20営業日の米国債(TLT)と株式(SPY)のリターン差。"
                    "投資家が怖がっているとき、安全資産の国債を買い株を売るため"
                    "TLTがSPYをアウトパフォームする＝**Safe Haven Demand増加＝Fear**。"
                ),
                "metric_fn": lambda c: (
                    f"TLT 20日: {c['tlt_ret20']:+.2f}%",
                    f"SPY 20日: {c['spy_ret20']:+.2f}%",
                    f"差: {c['spread']:+.2f}%pt",
                ),
            },
            {
                "key":   "junk_bond",
                "title": "⑦ Junk Bond Demand",
                "sub":   "High Yield (HYG) vs Investment Grade (LQD)",
                "icon":  "💰",
                "color": "#37474f",
                "desc":  (
                    "ジャンク債（高利回り）は高リスク。投資家がリスクを取りたいとき"
                    "ジャンク債を買うため利回りが低下しIGとのスプレッドが縮まる＝**Greed**。"
                    "スプレッド拡大は慎重ムード＝Fear。HYG/LQD比率で追跡。"
                ),
                "metric_fn": lambda c: (
                    f"HYG/LQD: {c['ratio']:.3f}",
                    f"MA20比: {c['spread']:+.2f}%",
                    "上昇=Greed",
                ),
            },
        ]

            # 2列グリッドで表示
            # 最後の1件（例: Junk Bond）も横幅が広がりすぎないよう、常に2列を確保する
            for i in range(0, len(COMP_META), 2):
                row_metas = COMP_META[i:i+2]
                cols = st.columns(2)
                for col, meta in zip(cols, row_metas):
                    with col:
                        comp = comps.get(meta["key"], {})
                        comp_score = comp.get("score")
                        comp_label = comp.get("label", "N/A")
                        comp_color = meta["color"]

                        if comp_score is not None:
                            fg_label, fg_color = get_fear_greed_label(comp_score)
                            score_text = f"{comp_score:.0f}/100"
                        else:
                            fg_label, fg_color = "N/A", "#999"
                            score_text = "N/A"

                        # カードHTML
                        st.markdown(
                            f'<div style="border:1px solid #ddd;border-radius:10px;'
                            f'padding:12px 14px;margin-bottom:4px;'
                            f'border-left:4px solid {comp_color};">'
                            f'<div style="font-size:15px;font-weight:700;color:{comp_color};">'
                            f'{meta["icon"]} {meta["title"]}</div>'
                            f'<div style="font-size:11px;color:#888;margin-bottom:6px;">'
                            f'{meta["sub"]}</div>'
                            f'<div style="display:flex;align-items:center;gap:12px;margin-bottom:6px;">'
                            f'<span style="font-size:22px;font-weight:900;color:{fg_color};">'
                            f'{score_text}</span>'
                            f'<span style="font-size:13px;color:{fg_color};font-weight:700;">'
                            f'{fg_label}</span>'
                            f'</div>'
                            f'<div style="font-size:11px;color:#555;margin-bottom:8px;">'
                            f'{comp_label}</div>'
                            f'</div>',
                            unsafe_allow_html=True
                        )

                        # サブメトリクス
                        if comp and meta.get("metric_fn"):
                            try:
                                m1, m2, m3 = meta["metric_fn"](comp)
                                mc1, mc2, mc3 = st.columns(3)
                                mc1.caption(m1)
                                mc2.caption(m2)
                                mc3.caption(m3)
                            except Exception:
                                pass

                        # 説明（折りたたみ）
                        with st.expander("📖 指標説明", expanded=False):
                            st.markdown(meta["desc"])

                        # ミニチャート
                        if comp.get("hist_df") is not None and not comp["hist_df"].empty:
                            _draw_component_chart(comp, meta["key"], comp_color)

            # 末尾ソースリンク
            st.caption("📌 スコアはYahoo Financeデータから独自計算。CNN公式値と若干差異があります。")
            st.markdown(DASHBOARD_LINKS_HTML, unsafe_allow_html=True)

    with tab_jp:
        st.subheader("日本市場の投資家心理（複合指標ベース）")
        st.caption("日経平均のボラティリティ、モメンタム、移動平均からスコアを算出")
        with st.spinner("日本市場データを取得中..."):
            jp_fg_data = fetch_japan_fear_greed_index()

        if jp_fg_data:
            score = jp_fg_data["score"]
            prev_close = jp_fg_data.get("previous_close")
            hist_df = jp_fg_data.get("historical")
            volatility = jp_fg_data.get("volatility")
            momentum = jp_fg_data.get("momentum")
            ma_diff = jp_fg_data.get("ma_diff")
            score_change = (score - prev_close) if prev_close is not None else None
            label, color = get_fear_greed_label(score)

            col1, col2, col3 = st.columns([2, 1, 1])
            with col1:
                png_bytes = make_fear_greed_gauge(score)
                if isinstance(png_bytes, bytes):
                    st.image(png_bytes)
                else:
                    st.pyplot(png_bytes, clear_figure=True)
            with col2:
                st.metric("現在スコア", f"{score:.0f}/100",
                          delta=f"{score_change:+.1f}" if score_change else None,
                          delta_color="inverse")
                st.markdown(f"**状態:** <span style='color:{color};font-weight:bold'>{label}</span>", unsafe_allow_html=True)
            with col3:
                st.markdown("**📊 構成指標**")
                st.caption(f"ボラティリティ: {volatility:.2f}%")
                st.caption(f"モメンタム(30日): {momentum:+.2f}%")
                st.caption(f"MA50乖離率: {ma_diff:+.2f}%")

            with st.expander("ℹ️ 指標の算出方法", expanded=False):
                st.markdown("""
**日本版Fear & Greedスコアの計算方法:**
1. **ボラティリティ（40%）**: 過去30日の日次変動率の標準偏差
2. **モメンタム（40%）**: 過去30日間のリターン
3. **移動平均乖離率（20%）**: 50日移動平均との差
                """)

            # ★ 日本版も4期間タブで表示
            if hist_df is not None and not hist_df.empty:
                with st.expander("📈 履歴トレンド", expanded=True):
                    draw_trend_chart(
                        hist_df,
                        title="日本版 Fear & Greed Index 推移",
                        ylabel="Fear & Greed Score",
                        y_min=0, y_max=100,
                        add_fg_bands=True,
                        color="#e91e63",  # ピンク系で米国版と区別
                    )
        else:
            st.error("⚠️ 日本市場データを取得できませんでした")

    st.divider()

    # ===================================================
    # ★ 日経VI（日本版恐怖指数）セクション
    # ===================================================
    st.header("🌊 日経VI（ボラティリティ指数）")
    st.caption("日本版恐怖指数。高いほど市場の不安・変動が大きい。^JNIVが取得できない場合は日経平均の実現ボラティリティで代替表示。")

    with st.spinner("日経VIデータを取得中..."):
        nk_vi_df = fetch_nikkei_vi_history(years=4)

    if not nk_vi_df.empty:
        source_label = nk_vi_df.attrs.get("source", "")
        current_vi = float(nk_vi_df["score"].iloc[-1])
        prev_vi = float(nk_vi_df["score"].iloc[-2]) if len(nk_vi_df) >= 2 else None
        vi_change = (current_vi - prev_vi) if prev_vi is not None else None

        col_vi1, col_vi2, col_vi3 = st.columns([1, 1, 2])
        with col_vi1:
            st.metric(
                "日経VI 現在値",
                f"{current_vi:.2f}",
                delta=f"{vi_change:+.2f}" if vi_change is not None else None,
                delta_color="inverse",  # 上昇=危険=赤
            )
        with col_vi2:
            vi_level = "🔴 高（恐怖）" if current_vi > 30 else ("🟡 中（注意）" if current_vi > 20 else "🟢 低（安定）")
            st.metric("水準", vi_level)
        with col_vi3:
            st.markdown("**📊 日経VIの目安**")
            st.caption("20以下: 安定・楽観的な市場")
            st.caption("20〜30: やや不安定・注意")
            st.caption("30以上: 高ボラ・恐怖状態")
            st.caption("40以上: 極度の恐怖（リーマン級）")
        if source_label:
            st.caption(f"データソース: {source_label}")

        with st.expander("📈 日経VI 履歴トレンド", expanded=True):
            y_max_vi = max(60.0, float(nk_vi_df["score"].max()) * 1.1)
            draw_trend_chart(
                nk_vi_df,
                title="日経VI 推移",
                ylabel="日経VI（ボラティリティ）",
                y_min=0, y_max=y_max_vi,
                add_fg_bands=False,
                color="#ff6b35",  # オレンジ系
            )
    else:
        st.error("⚠️ 日経VIデータを取得できませんでした")

    st.divider()

    # ===================================================
    # ★ VIX（米国恐怖指数）履歴チャートセクション
    # ===================================================
    st.header("😰 VIX（米国恐怖指数）履歴")
    st.caption("S&P500オプションの impliedボラティリティから算出。20以上で警戒、30以上で恐怖状態。")

    with st.spinner("VIXデータを取得中..."):
        vix_hist_df = fetch_long_history("^VIX", years=4)

    if not vix_hist_df.empty:
        current_vix = float(vix_hist_df["score"].iloc[-1])
        prev_vix = float(vix_hist_df["score"].iloc[-2]) if len(vix_hist_df) >= 2 else None
        vix_change = (current_vix - prev_vix) if prev_vix is not None else None

        col_vix1, col_vix2, col_vix3 = st.columns([1, 1, 2])
        with col_vix1:
            st.metric(
                "VIX 現在値",
                f"{current_vix:.2f}",
                delta=f"{vix_change:+.2f}" if vix_change is not None else None,
                delta_color="inverse",
            )
        with col_vix2:
            vix_level = "🔴 恐怖（30+）" if current_vix >= 30 else ("🟡 警戒（20-30）" if current_vix >= 20 else "🟢 安定（20以下）")
            st.metric("水準", vix_level)
        with col_vix3:
            st.markdown("**📊 VIXの目安**")
            st.caption("15以下: 非常に安定・楽観")
            st.caption("15〜20: 通常範囲")
            st.caption("20〜30: 警戒域")
            st.caption("30以上: 恐怖状態")
            st.caption("40以上: 極度の恐怖（危機レベル）")

        with st.expander("📈 VIX 履歴トレンド", expanded=True):
            # ★ tz-naive正規化
            vix_plot_df = vix_hist_df.copy()
            vix_plot_df['date'] = pd.to_datetime(vix_plot_df['date'])
            if vix_plot_df['date'].dt.tz is not None:
                vix_plot_df['date'] = vix_plot_df['date'].dt.tz_localize(None)
            vix_plot_df = vix_plot_df.sort_values('date').reset_index(drop=True)

            tab_3y, tab_1y, tab_3m, tab_all = st.tabs(["3年（デフォルト）", "1年", "3ヶ月", "全期間"])

            def _draw_vix(df_slice: pd.DataFrame, date_fmt: str, locator):
                if df_slice.empty:
                    st.info("この期間のデータがありません")
                    return
                fig, ax = plt.subplots(figsize=(12, 4))
                ax.plot(df_slice['date'], df_slice['score'],
                        linewidth=1.8, color="#9c27b0", marker='o', markersize=1.5)
                # VIX水準バンド（凡例ラベルは英語でmojibake回避）
                ax.axhspan(0,  15, alpha=0.08, color='green',  label='Calm(<15)')
                ax.axhspan(15, 20, alpha=0.08, color='yellow', label='Normal(15-20)')
                ax.axhspan(20, 30, alpha=0.10, color='orange', label='Caution(20-30)')
                ax.axhspan(30, 100, alpha=0.10, color='red',   label='Fear(30+)')
                ax.axhline(20, linewidth=1.0, color='orange', linestyle='--', alpha=0.7)
                ax.axhline(30, linewidth=1.0, color='red',    linestyle='--', alpha=0.7)
                # 最新値アノテーション
                last_val = df_slice['score'].iloc[-1]
                last_dt = df_slice['date'].iloc[-1]
                ax.annotate(f"{last_val:.1f}", xy=(last_dt, last_val),
                            xytext=(8, 0), textcoords="offset points",
                            fontsize=10, fontweight="bold", color="#9c27b0", va="center")
                ax.set_ylabel("VIX", fontsize=11, fontfamily="DejaVu Sans")
                y_max_plot = max(45.0, float(df_slice['score'].max()) * 1.1)
                ax.set_ylim(0, y_max_plot)
                ax.legend(loc='upper right', fontsize=8, framealpha=0.7)
                ax.grid(True, alpha=0.3)
                ax.xaxis.set_major_formatter(mdates.DateFormatter(date_fmt))
                ax.xaxis.set_major_locator(locator)
                plt.xticks(rotation=45)
                plt.tight_layout()
                st.pyplot(fig, clear_figure=True)
                st.caption(
                    f"データ点数: {len(df_slice)}日 | "
                    f"平均: {df_slice['score'].mean():.1f} | "
                    f"最高: {df_slice['score'].max():.1f} | "
                    f"最低: {df_slice['score'].min():.1f}"
                )

            cutoff_3y = pd.Timestamp.now() - pd.DateOffset(years=3)
            cutoff_1y = pd.Timestamp.now() - pd.DateOffset(years=1)
            cutoff_3m = pd.Timestamp.now() - pd.DateOffset(months=3)

            with tab_3m:
                _draw_vix(vix_plot_df[vix_plot_df['date'] >= cutoff_3m], '%m/%d', mdates.WeekdayLocator(interval=2))
            with tab_1y:
                _draw_vix(vix_plot_df[vix_plot_df['date'] >= cutoff_1y], '%Y/%m', mdates.MonthLocator(interval=1))
            with tab_3y:
                _draw_vix(vix_plot_df[vix_plot_df['date'] >= cutoff_3y], '%Y/%m', mdates.MonthLocator(interval=3))
            with tab_all:
                _draw_vix(vix_plot_df, '%Y/%m', mdates.YearLocator())
                if not vix_plot_df.empty:
                    first_date = vix_plot_df['date'].iloc[0].strftime('%Y-%m-%d')
                    last_date  = vix_plot_df['date'].iloc[-1].strftime('%Y-%m-%d')
                    st.caption(f"📅 取得期間: {first_date} 〜 {last_date}（計{len(vix_plot_df)}日分）")
    else:
        st.error("⚠️ VIXデータを取得できませんでした")

    st.divider()

    # ===================================================
    # ★ 方向性予測スコア（日本株・米国株）
    # ===================================================
    tab_jp_pred, tab_us_pred = st.tabs(["🇯🇵 日本株（日経平均）", "🇺🇸 米国株（S&P500/NASDAQ/ダウ）"])
    with tab_jp_pred:
        render_nikkei_prediction()
    with tab_us_pred:
        render_us_prediction()
    st.divider()

    # ★ マーケットリサーチAI
    render_market_research_ai()
    st.divider()

    # ★ 米議員株トレード監視
    render_congress_tracker()
    st.divider()

    # ★ 日米業種リードラグシグナル
    render_leadlag_section()
    st.divider()

    # ★ 高度市場解析
    render_advanced_analytics()
    st.divider()

    # 市場データ表示
    for title, items in MARKETS.items():
        st.subheader(f"📈 {title}")
        render_market_row(items, cols=responsive_cols)
        st.divider()

    # Market News Board
    render_market_news_board(translate_mode=translate_mode)
    st.divider()

    # 光通信・AIインフラバスケット
    render_optical_basket()
    st.divider()

    # AIインフラ投資マネーフロー
    render_ai_infra_moneyflow()
    st.divider()

    # 銘柄需給・バリュエーション評価
    render_stock_screener()
    st.divider()

    # RSSニュース（調査報道・経済メディア）
    st.header("📰 経済ニュース・調査報道")
    st.caption("東洋経済・文春オンライン・Bloomberg・Reuters等。タブで媒体を切り替えてください。")
    render_rss_news(translate_mode=translate_mode)

    # TDnet自動解析
    st.header("📄 TDnet自動解析（決算・適時開示）")

    if not PDFPLUMBER_AVAILABLE:
        st.warning("⚠️ pdfplumberがインストールされていません。")
    if not GENAI_AVAILABLE or not GEMINI_API_KEY:
        if not GROQ_API_KEY:
            st.warning("⚠️ GEMINI_API_KEY も GROQ_API_KEY も設定されていません。")
        else:
            st.info("ℹ️ Gemini APIキー未設定のためGroqを使用します")

    tdnet_enabled = PDFPLUMBER_AVAILABLE and ((GENAI_AVAILABLE and GEMINI_API_KEY) or GROQ_API_KEY)

    with st.expander("⚙️ TDnet設定", expanded=False):
        col1, col2, col3 = st.columns(3)
        with col1:
            today_jst = datetime.now(JST).date()
            tdnet_date = st.date_input("対象日", value=today_jst, key="tdnet_date")
        with col2:
            tdnet_keyword = st.text_input("フィルタ", value="", placeholder="銘柄コード/会社名", key="tdnet_keyword")
        with col3:
            tdnet_max_items = st.slider("表示件数", 5, 50, 20, key="tdnet_max")

    KESSAN_KEYWORDS = [
        "決算", "業績", "配当", "増益", "減益", "黒字", "赤字",
        "上方修正", "下方修正", "業績修正", "予想修正", "経常利益",
        "営業利益", "純利益", "売上", "収益", "financial results",
        "earnings", "dividend", "forecast", "通期", "四半期",
        "第1四半期", "第2四半期", "第3四半期", "中間", "年度",
    ]

    def is_kessan(item: dict) -> bool:
        text = ((item.get("title") or "") + " " + (item.get("around") or "")).lower()
        return any(kw.lower() in text for kw in KESSAN_KEYWORDS)

    if tdnet_enabled:
        yyyymmdd = tdnet_date.strftime("%Y%m%d")
        try:
            with st.spinner(f"TDnet一覧を取得中...（{yyyymmdd}）"):
                tdnet_items = fetch_tdnet_items_for_date(yyyymmdd)

            total_before = len(tdnet_items)
            tdnet_items = [it for it in tdnet_items if is_kessan(it)]
            st.caption(f"🔍 決算フィルタ適用: 全{total_before}件 → 決算関連 {len(tdnet_items)}件")

            if tdnet_keyword.strip():
                kw = tdnet_keyword.strip().lower()
                tdnet_items = [
                    it for it in tdnet_items
                    if (kw in (it.get("title") or "").lower())
                    or (kw in (it.get("pdf_url") or "").lower())
                    or (kw in (it.get("around") or "").lower())
                    or (kw == (it.get("code") or "").lower())
                ]
                st.caption(f"🔎 キーワード「{tdnet_keyword}」: {len(tdnet_items)}件")

            tdnet_items = tdnet_items[:tdnet_max_items]
            st.caption(f"📊 表示: {len(tdnet_items)}件の決算開示資料")

            if "tdnet_summaries" not in st.session_state:
                st.session_state.tdnet_summaries = {}

            col_batch1, col_batch2 = st.columns([3, 1])
            with col_batch1:
                batch_n = st.number_input("まとめて解析する件数", min_value=1,
                                          max_value=max(1, len(tdnet_items)),
                                          value=min(5, len(tdnet_items)), key="batch_n")
            with col_batch2:
                run_batch = st.button("🚀 まとめて解析", type="primary", disabled=(len(tdnet_items) == 0))

            if run_batch:
                try:
                    target_batch = tdnet_items[:int(batch_n)]
                    progress = st.progress(0)
                    for i, it in enumerate(target_batch, start=1):
                        pdf_url = it["pdf_url"]
                        title = it["title"]
                        with st.spinner(f"[{i}/{len(target_batch)}] 解析中: {title[:30]}..."):
                            try:
                                pdf_bytes = download_pdf_bytes(pdf_url)
                                pdf_text = extract_text_from_pdf(pdf_bytes)
                                if not pdf_text:
                                    summary = "（PDFからテキストを抽出できませんでした）"
                                    used_api = ""
                                else:
                                    summary, used_api = gemini_summarize_tdnet(pdf_text, title)
                                st.session_state.tdnet_summaries[pdf_url] = (summary, used_api)
                            except Exception as e:
                                st.session_state.tdnet_summaries[pdf_url] = (f"（エラー: {str(e)[:100]}）", "")
                        progress.progress(i / len(target_batch))
                        time.sleep(0.1)
                    st.success("✅ まとめて解析が完了しました")
                except Exception as e:
                    st.error(f"❌ バッチ解析エラー: {e}")

            st.divider()

            for idx, it in enumerate(tdnet_items, start=1):
                title = it["title"]
                pdf_url = it["pdf_url"]
                code = it.get("code") or "----"
                time_str = it.get("time") or "--:--"

                with st.container(border=True):
                    col1, col2, col3, col4 = st.columns([0.5, 5, 1, 1])
                    col1.markdown(f"**{idx}**")
                    col2.markdown(f"**{sanitize_html(title)}**  \n`{code}` {time_str}")
                    with col3:
                        st.markdown(
                            f'<a href="{sanitize_html(pdf_url)}" target="_blank" '
                            f'style="display:inline-block;padding:0.25rem 0.75rem;'
                            f'background-color:#f0f2f6;border-radius:0.25rem;'
                            f'text-decoration:none;color:#262730;font-size:0.875rem;">📄 PDF</a>',
                            unsafe_allow_html=True
                        )
                    run_one = col4.button("🔍", key=f"tdnet_{idx}")

                    if pdf_url in st.session_state.tdnet_summaries:
                        stored = st.session_state.tdnet_summaries[pdf_url]
                        summary_text, used_api_tdnet = stored if isinstance(stored, tuple) else (stored, "")
                        st.markdown("**✅ 解析結果:**")
                        st.write(summary_text)
                        if used_api_tdnet:
                            st.caption(f"🤖 使用AI: {used_api_tdnet}")

                    if run_one:
                        try:
                            with st.spinner("解析中..."):
                                pdf_bytes = download_pdf_bytes(pdf_url)
                                pdf_text = extract_text_from_pdf(pdf_bytes)
                                if not pdf_text:
                                    summary = "（PDFからテキストを抽出できませんでした）"
                                    used_api_tdnet = ""
                                else:
                                    summary, used_api_tdnet = gemini_summarize_tdnet(pdf_text, title)
                                st.session_state.tdnet_summaries[pdf_url] = (summary, used_api_tdnet)
                                st.success("✅ 解析完了")
                                st.write(summary)
                                if used_api_tdnet:
                                    st.caption(f"🤖 使用AI: {used_api_tdnet}")
                        except Exception as e:
                            st.error(f"❌ 解析エラー: {e}")

        except Exception as e:
            st.error(f"❌ TDnet一覧の取得に失敗: {e}")

    st.divider()
    st.caption("データソース: Yahoo Finance / Tiingo / CNN / TDnet | 投資判断は自己責任で")

    # ── アクセス解析は ?analytics=on のときのみ表示 ──────────
    if st.query_params.get("analytics") == "on":
        st.divider()
        render_analytics_dashboard()

    # ── streamlit-analytics2 計測終了・結果表示 ──────────────
    if ANALYTICS2_AVAILABLE:
        streamlit_analytics.stop_tracking(
            unsafe_password=None,
        )

    # ── カレンダーセクションをプレースホルダーに埋め込む ──────
    # 後続セクションの描画が終わった時点でスレッドの結果を回収する。
    # キャッシュ済みなら即時返却、初回でも並列フェッチ中に他のセクションが描画済み。
    _preloaded = {
        "reactions": _f_reactions.result(),
        "fmp":       _f_fmp.result(),
        "bls":       _f_bls.result(),
        "fred":      _f_fred.result(),
    }
    _cal_executor.shutdown(wait=False)
    with _calendar_placeholder.container():
        render_economic_events_section(preloaded=_preloaded)


if __name__ == "__main__":
    # ── ③ サイトマップ / robots.txt をクエリパラメータで配信 ────
    # ?sitemap=1 → sitemap.xml を表示
    # ?robots=1  → robots.txt を表示
    _qp = st.query_params
    if _qp.get("sitemap") == "1":
        _sitemap_xml = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://market-dashboard.streamlit.app/</loc>
    <lastmod>{today}</lastmod>
    <changefreq>daily</changefreq>
    <priority>1.0</priority>
  </url>
</urlset>""".format(today=datetime.now().strftime("%Y-%m-%d"))
        st.markdown(f"```xml\n{_sitemap_xml}\n```")
        st.caption("sitemap.xml — クローラー向けサイトマップ")
        st.stop()

    elif _qp.get("robots") == "1":
        _robots_txt = """User-agent: *
Allow: /

# 主要クローラーに明示的に許可
User-agent: Googlebot
Allow: /

User-agent: Bingbot
Allow: /

# ブロック対象クローラー（悪意のあるスクレイパー）
User-agent: SemrushBot
Disallow: /
User-agent: AhrefsBot
Disallow: /
User-agent: MJ12bot
Disallow: /
User-agent: DotBot
Disallow: /

Sitemap: https://market-dashboard.streamlit.app/?sitemap=1"""
        st.code(_robots_txt, language="text")
        st.caption("robots.txt — 検索エンジン向けクロール制御")
        st.stop()

    else:
        main()