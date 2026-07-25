# -*- coding: utf-8 -*-
"""
analytics.py — Market Dashboard アクセス解析
================================================
機能:
  - ページビュー記録（日時・IP・UA・地域・デバイス）
  - スキャンログ記録（スキャン日時・CVE件数・製品）
  - Google Sheets への永続保存（未設定時はセッションメモリ）
  - アクセス解析ダッシュボード表示

Secrets 設定（Streamlit Cloud）:
  GOOGLE_SHEETS_ID = "スプレッドシートID"
  GOOGLE_SERVICE_ACCOUNT_JSON = "{...サービスアカウントJSON...}"
  IPINFO_TOKEN = "ipinfoトークン（任意）"
================================================
"""

import os
import json
import logging
import datetime
import re
from datetime import timedelta
from typing import Tuple, List

import pytz
import pandas as pd
import streamlit as st
import requests

logger = logging.getLogger(__name__)
try:
    JST = pytz.timezone("Asia/Tokyo")
except Exception:
    import datetime as _dt
    JST = _dt.timezone(_dt.timedelta(hours=9))

_SESSION_KEY = "_anl_session_id"
_TRACKED_KEY = "_anl_tracked"
_PV_LOG_KEY  = "_anl_pv_log"
_SC_LOG_KEY  = "_anl_scan_log"
REALTIME_MIN = 5


# ══════════════════════════════════════
# ユーティリティ
# ══════════════════════════════════════
def _secret(key: str, default: str = "") -> str:
    try:
        return str(st.secrets[key])
    except Exception:
        return os.getenv(key, default)


def _detect_backend() -> str:
    if _secret("GOOGLE_SHEETS_ID") and _secret("GOOGLE_SERVICE_ACCOUNT_JSON"):
        return "sheets"
    return "session"


def _session_id() -> str:
    if _SESSION_KEY not in st.session_state:
        import uuid
        st.session_state[_SESSION_KEY] = str(uuid.uuid4())[:12]
    return st.session_state[_SESSION_KEY]


# ══════════════════════════════════════
# IP / UA 取得（サーバーサイド）
# ══════════════════════════════════════
def _get_client_ua() -> str:
    """User-Agent を複数の方法で取得する"""
    try:
        ua = st.context.headers.get("user-agent", "")
        if ua:
            return str(ua)[:300]
    except Exception:
        pass
    try:
        headers = dict(st.context.headers)
        for key in headers:
            if key.lower() == "user-agent":
                return str(headers[key])[:300]
    except Exception:
        pass
    return ""


def _is_private_ip(ip: str) -> bool:
    """プライベートIP・ループバックIPかどうかを判定する"""
    if not ip:
        return True
    if ip.startswith("127.") or ip.startswith("10.") or ip.startswith("192.168.") or ip.startswith("::1"):
        return True
    parts = ip.split(".")
    if len(parts) == 4:
        try:
            if parts[0] == "172" and 16 <= int(parts[1]) <= 31:
                return True
        except ValueError:
            pass
    return False


def _is_google_proxy_ip(ip: str) -> bool:
    """Google Cloud / Streamlit Cloud のプロキシIPかどうか判定"""
    if not ip:
        return False
    # GCPのパブリックIP帯（主要レンジ）
    gcp_prefixes = ("34.", "35.", "130.211.", "104.154.", "104.196.")
    return any(ip.startswith(p) for p in gcp_prefixes)


def _country_from_accept_language(headers) -> str:
    """
    Accept-Language ヘッダーから国コードを推定する。
    IPが取れない場合のフォールバック。
    """
    try:
        lang = headers.get("accept-language", "").lower()
        if not lang:
            return ""
        first = lang.split(",")[0].split(";")[0].strip()
        lang_map = {
            "ja": "JP", "ko": "KR",
            "zh-cn": "CN", "zh-tw": "TW", "zh-hk": "HK",
            "en-us": "US", "en-gb": "GB", "en-au": "AU", "en-ca": "CA",
            "de": "DE", "de-de": "DE", "de-at": "AT",
            "fr": "FR", "fr-fr": "FR",
            "es": "ES", "es-mx": "MX", "pt-br": "BR",
            "it": "IT", "ru": "RU", "ar": "AR",
            "hi": "IN", "th": "TH", "vi": "VN", "id": "ID",
        }
        return lang_map.get(first, lang_map.get(first.split("-")[0], ""))
    except Exception:
        return ""


def _get_client_ip() -> Tuple[str, str]:
    """
    クライアントIPを取得する。
    Streamlit Cloud (GCP) では x-forwarded-for の先頭IPが実クライアントIP。
    ipify等のサーバー側フォールバックはサーバー自身のIPを返すため使用しない。
    戻り値: (global_ip, local_ip)
    """
    global_ip = ""
    local_ip  = ""
    try:
        headers = st.context.headers

        # ① 単一IP系ヘッダーを優先（最も信頼性が高い）
        for h in ("cf-connecting-ip", "x-real-ip", "true-client-ip", "x-client-ip"):
            val = headers.get(h, "").strip()
            if val and not _is_private_ip(val) and not _is_google_proxy_ip(val):
                global_ip = val
                break

        # ② x-forwarded-for: GCP LB形式は「実クライアントIP, GCP_LB_IP」
        #    先頭のIPが実クライアントIP
        if not global_ip:
            xff = headers.get("x-forwarded-for", "").strip()
            if xff:
                for ip in xff.split(","):
                    ip = ip.strip()
                    if not ip:
                        continue
                    if _is_private_ip(ip):
                        if not local_ip:
                            local_ip = ip
                    elif _is_google_proxy_ip(ip):
                        # GCPプロキシIPはスキップ（実クライアントIPではない）
                        continue
                    else:
                        global_ip = ip
                        break

        # ③ フォールバックなし: GCPプロキシIPを global_ip として記録しない
        #    （Streamlit Cloud のアーキテクチャ上、実クライアントIPが取れない場合は空欄とする）

    except Exception:
        pass

    return global_ip, local_ip


@st.cache_data(ttl=3600, show_spinner=False)
def _get_geo(ip: str) -> Tuple[str, str, str]:
    """ipinfo.io でIPから国・都市・組織を取得。戻り値: (country, city, org)"""
    if not ip or _is_private_ip(ip):
        return "Local", "Local", "Local"
    try:
        token  = _secret("IPINFO_TOKEN", "")
        params = {"token": token} if token else {}
        # ipinfo.io をまず試みる
        r = requests.get(
            f"https://ipinfo.io/{ip}/json",
            params=params, timeout=5,
        )
        if r.status_code == 200:
            d = r.json()
            country = d.get("country", "??")
            city    = d.get("city",    "??")
            org     = d.get("org",     "")
            if country and country != "??":
                return country, city, org
    except Exception:
        pass

    # フォールバック: ip-api.com（無料・トークン不要）
    try:
        r2 = requests.get(
            f"http://ip-api.com/json/{ip}?fields=status,country,countryCode,city,isp",
            timeout=5,
        )
        if r2.status_code == 200:
            d2 = r2.json()
            if d2.get("status") == "success":
                return (
                    d2.get("countryCode", "??"),
                    d2.get("city",        "??"),
                    d2.get("isp",         ""),
                )
    except Exception:
        pass

    return "??", "??", ""


def _parse_ua(ua: str) -> Tuple[str, str]:
    """後方互換用。内部で _parse_ua_rich を呼ぶ。"""
    r = _parse_ua_rich(ua)
    return r["device_type"], r["browser_full"]


def _parse_ua_rich(ua: str) -> dict:
    """User-Agent から OS・ブラウザ・デバイス・ボットを詳細抽出"""
    empty = {"device_type": "Unknown", "os": "Unknown",
             "browser": "Unknown", "browser_ver": "", "browser_full": "Unknown",
             "is_bot": False, "bot_name": ""}
    if not ua:
        return empty

    # ── Bot 検出 ─────────────────────────────────────────
    BOT_PATTERNS = [
        (r"Googlebot",             "Googlebot"),
        (r"bingbot",               "Bingbot"),
        (r"Twitterbot",            "Twitterbot"),
        (r"facebookexternalhit",   "Facebook Bot"),
        (r"Slackbot",              "Slackbot"),
        (r"DuckDuckBot",           "DuckDuckBot"),
        (r"YandexBot",             "YandexBot"),
        (r"Baiduspider",           "Baidu Spider"),
        (r"AhrefsBot",             "AhrefsBot"),
        (r"SemrushBot",            "SemrushBot"),
        (r"\bbot\b|\bspider\b|\bcrawler\b|\bscraper\b", "Bot"),
    ]
    for pat, name in BOT_PATTERNS:
        if re.search(pat, ua, re.IGNORECASE):
            return {**empty, "device_type": "Bot", "os": "Server",
                    "browser": name, "browser_full": name,
                    "is_bot": True, "bot_name": name}

    ua_l = ua.lower()

    # ── デバイスタイプ ────────────────────────────────────
    if re.search(r"iphone|android.*mobile|windows phone|blackberry", ua_l):
        device_type = "Mobile"
    elif re.search(r"ipad|tablet|kindle|silk", ua_l):
        device_type = "Tablet"
    else:
        device_type = "PC"

    # ── OS 判定 ───────────────────────────────────────────
    MACOS_NICK = {
        "10.15": "Catalina", "11": "Big Sur",  "12": "Monterey",
        "13": "Ventura",     "14": "Sonoma",   "15": "Sequoia",
    }
    os_name = "Unknown"

    m = re.search(r"iPhone OS (\d+)[._](\d+)", ua)
    if m:
        os_name = f"iOS {m.group(1)}.{m.group(2)}"

    if os_name == "Unknown":
        m = re.search(r"iPad.*OS (\d+)[._](\d+)", ua)
        if m:
            os_name = f"iPadOS {m.group(1)}.{m.group(2)}"

    if os_name == "Unknown":
        m = re.search(r"Android (\d+\.?\d*)", ua)
        if m:
            os_name = f"Android {m.group(1)}"

    if os_name == "Unknown" and re.search(r"Windows NT", ua):
        os_name = "Windows 10/11"

    if os_name == "Unknown":
        m = re.search(r"Mac OS X (\d+)[._](\d+)", ua)
        if m:
            ver = f"{m.group(1)}.{m.group(2)}"
            nick = MACOS_NICK.get(ver, MACOS_NICK.get(m.group(1), ""))
            os_name = f"macOS {nick}" if nick else f"macOS {ver}"

    if os_name == "Unknown" and "cros" in ua_l:
        os_name = "ChromeOS"

    if os_name == "Unknown" and "linux" in ua_l:
        os_name = "Linux"

    # ── ブラウザ判定（順序重要） ──────────────────────────
    browser_name = "Other"
    browser_ver  = ""
    BROWSER_PATTERNS = [
        (r"SamsungBrowser/(\d+)", "Samsung Browser"),
        (r"Edg/(\d+)",            "Edge"),
        (r"OPR/(\d+)",            "Opera"),
        (r"Whale/(\d+)",          "Whale"),
        (r"YaBrowser/(\d+)",      "Yandex"),
        (r"Chrome/(\d+)",         "Chrome"),
        (r"Firefox/(\d+)",        "Firefox"),
        (r"Version/(\d+).*Safari","Safari"),
        (r"Safari/(\d+)",         "Safari"),
    ]
    for pat, name in BROWSER_PATTERNS:
        m = re.search(pat, ua)
        if m:
            browser_name = name
            browser_ver  = m.group(1)
            break

    browser_full = f"{browser_name} {browser_ver}" if browser_ver else browser_name

    return {
        "device_type":  device_type,
        "os":           os_name,
        "browser":      browser_name,
        "browser_ver":  browser_ver,
        "browser_full": browser_full,
        "is_bot":       False,
        "bot_name":     "",
    }


def _get_js_geo() -> dict:
    """
    ブラウザ側 JS で実クライアントIPと地域情報を取得。
    IPINFO_TOKEN が設定されている場合はトークン付きで呼び出す。
    1セッションに1回だけ実行し session_state にキャッシュ。
    """
    _KEY = "_anl_js_geo"
    if st.session_state.get(_KEY) is not None:
        return st.session_state[_KEY]

    try:
        from streamlit_javascript import st_javascript
        import json as _json

        token       = _secret("IPINFO_TOKEN", "")
        token_param = f"?token={token}" if token else ""

        result = st_javascript(f"""
            await fetch('https://ipinfo.io/json{token_param}', {{cache: 'no-store'}})
                .then(r => r.json())
                .then(d => JSON.stringify({{
                    ip:      d.ip       || '',
                    city:    d.city     || '',
                    region:  d.region   || '',
                    country: d.country  || '',
                    org:     d.org      || '',
                    tz:      d.timezone || ''
                }}))
                .catch(() => '{{}}')
        """)

        if result and isinstance(result, str) and result.startswith("{"):
            data = _json.loads(result)
            if data.get("ip"):
                st.session_state[_KEY] = data
                return data
    except Exception as _e:
        logger.debug(f"_get_js_geo error: {_e}")

    return {}



# ══════════════════════════════════════
# Google Sheets クライアント
# ══════════════════════════════════════
@st.cache_resource
def _sheets_client():
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        sa_json = _secret("GOOGLE_SERVICE_ACCOUNT_JSON")
        if not sa_json:
            return None
        sa_info = json.loads(sa_json)
        scopes  = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds  = Credentials.from_service_account_info(sa_info, scopes=scopes)
        return gspread.authorize(creds)
    except Exception as e:
        logger.warning(f"[analytics] Sheets初期化失敗: {e}")
        return None


def _sheets_ws(tab: str, headers: List[str]):
    try:
        client = _sheets_client()
        if not client:
            return None
        sp = client.open_by_key(_secret("GOOGLE_SHEETS_ID"))
        try:
            ws = sp.worksheet(tab)
        except Exception:
            ws = sp.add_worksheet(title=tab, rows=10000, cols=len(headers))
            ws.append_row(headers)
            return ws
        row1 = ws.row_values(1)
        if not row1:
            ws.append_row(headers)
        elif row1 != headers:
            existing = set(row1)
            new_cols = [h for h in headers if h not in existing]
            if new_cols:
                for col_name in new_cols:
                    ws.update_cell(1, len(row1) + new_cols.index(col_name) + 1, col_name)
        return ws
    except Exception as e:
        logger.warning(f"[analytics] sheets_ws({tab}) 失敗: {e}")
        return None


# ══════════════════════════════════════
# クライアント情報コレクター（JS補助）
# ══════════════════════════════════════
def inject_client_info_collector():
    """
    PC/Android向け: JavaScriptでブラウザ情報を補助取得。
    session_stateにUA・タイムゾーンなどを書き込む。
    """
    # サーバーサイドで取得できない情報をJSで補完
    ua = _get_client_ua()
    if ua and not st.session_state.get("_anl_ua"):
        st.session_state["_anl_ua"] = ua

    global_ip, local_ip = _get_client_ip()
    if not st.session_state.get("_anl_global_ip"):
        st.session_state["_anl_global_ip"] = global_ip
    if not st.session_state.get("_anl_local_ip"):
        st.session_state["_anl_local_ip"] = local_ip

    # 国・都市情報（GCPプロキシIPはgeo lookup対象外）
    if not st.session_state.get("_anl_country"):
        primary_ip = global_ip if (global_ip and not _is_google_proxy_ip(global_ip)) else ""
        if primary_ip:
            country, city, _ = _get_geo(primary_ip)
        else:
            country, city = "??", "??"
        # Accept-Language フォールバック
        if country in ("??", "", "Local"):
            try:
                headers = st.context.headers
                lang_country = _country_from_accept_language(headers)
                if lang_country:
                    country = lang_country
            except Exception:
                pass
        st.session_state["_anl_country"] = country
        st.session_state["_anl_city"]    = city


# ══════════════════════════════════════
# PV 記録
# ══════════════════════════════════════
def track_pageview(page: str = "market_dashboard"):
    """アプリ起動時に呼び出す。セッション内で1回だけ記録。"""
    if st.session_state.get(_TRACKED_KEY):
        return
    st.session_state[_TRACKED_KEY] = True

    now = datetime.datetime.now(JST)
    sid = _session_id()
    ua  = _get_client_ua()

    # ── UA 詳細解析 ──────────────────────────────────────
    ua_info  = _parse_ua_rich(ua)
    device   = ua_info["device_type"]
    browser  = ua_info["browser_full"]
    os_name  = ua_info["os"]

    # ── Accept-Language ───────────────────────────────────
    lang_code = ""
    try:
        _h = st.context.headers
        lang_code = _h.get("accept-language", "").split(",")[0].split(";")[0].strip()
    except Exception:
        pass

    # ── IP / 地域解決（サーバーサイド） ─────────────────
    global_ip, local_ip = _get_client_ip()
    primary_ip = global_ip if (global_ip and not _is_google_proxy_ip(global_ip)) else ""
    if primary_ip:
        country, city, org = _get_geo(primary_ip)
    else:
        country, city, org = "??", "??", ""

    # Accept-Language フォールバック（IPから国が取れない場合）
    if country in ("??", "", "Local"):
        try:
            ac = _country_from_accept_language(st.context.headers)
            if ac:
                country = ac
        except Exception:
            pass

    city_disp = city if city not in ("??", "") else "N/A"

    st.session_state["_anl_country"]   = country
    st.session_state["_anl_city"]      = city_disp
    st.session_state["_anl_ua"]        = ua
    st.session_state["_anl_global_ip"] = global_ip
    st.session_state["_anl_local_ip"]  = local_ip
    st.session_state["_anl_os"]        = os_name
    st.session_state["_anl_lang"]      = lang_code

    row = {
        "ts":         now.isoformat(),
        "date":       now.strftime("%Y-%m-%d"),
        "hour":       now.hour,
        "session_id": sid,
        "page":       page,
        "country":    country,
        "city":       city_disp,
        "org":        org[:60] if org else "",
        "global_ip":  global_ip[:40] if global_ip else "",
        "local_ip":   local_ip[:40] if local_ip else "",
        "device":     device,
        "browser":    browser,
        "os":         os_name,
        "lang":       lang_code[:20] if lang_code else "",
    }

    if _PV_LOG_KEY not in st.session_state:
        st.session_state[_PV_LOG_KEY] = []
    st.session_state[_PV_LOG_KEY].append(row)

    backend = _detect_backend()
    if backend == "sheets":
        try:
            _write_pv_sheets(row, sid)
            st.session_state["_anl_sheets_status"] = "✅ 書き込み成功"
        except Exception as e:
            st.session_state["_anl_sheets_status"] = f"❌ 書き込みエラー: {e}"
            logger.warning(f"[analytics] PV書き込みエラー: {e}")

    # ── 指定シートにも必ず書き込む（設定に依存しない） ──
    try:
        ok = _write_access_log_to_target(row, sid)
        st.session_state["_anl_target_status"] = (
            "✅ access_log シート書き込み成功" if ok
            else "❌ access_log 書き込み失敗（ログ確認）"
        )
    except Exception as e:
        st.session_state["_anl_target_status"] = f"❌ access_log エラー: {e}"
        logger.warning(f"[analytics] target sheets エラー: {e}")

    if backend != "sheets":
        sheets_id   = _secret("GOOGLE_SHEETS_ID")
        sheets_json = _secret("GOOGLE_SERVICE_ACCOUNT_JSON")
        if not sheets_id:
            st.session_state["_anl_sheets_status"] = "⚠️ GOOGLE_SHEETS_ID 未設定"
        elif not sheets_json:
            st.session_state["_anl_sheets_status"] = "⚠️ GOOGLE_SERVICE_ACCOUNT_JSON 未設定"
        else:
            st.session_state["_anl_sheets_status"] = "⚠️ セッションのみ記録"


# ── 指定スプレッドシートへの直接書き込み ─────────────────
TARGET_SHEET_ID = "1h58Fg8Ks7QrqH_LdXTwGs-E8raIiQZdldSRxjwgxlhY"
ACCESS_LOG_TAB  = "access_log"   # 新しいシートタブ名

def _write_access_log_to_target(row: dict, sid: str) -> bool:
    """
    指定のスプレッドシートの「access_log」タブにアクセスログを書き込む。
    GOOGLE_SHEETS_IDの設定に依存せず、TARGET_SHEET_IDに直接書き込む。
    """
    try:
        import gspread
        from google.oauth2.service_account import Credentials

        sa_json = _secret("GOOGLE_SERVICE_ACCOUNT_JSON")
        if not sa_json:
            logger.warning("[access_log] GOOGLE_SERVICE_ACCOUNT_JSON 未設定")
            return False

        sa_info = json.loads(sa_json)
        scopes  = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds  = Credentials.from_service_account_info(sa_info, scopes=scopes)
        client = gspread.authorize(creds)

        sp = client.open_by_key(TARGET_SHEET_ID)

        # タブが存在しなければ作成
        headers = [
            "ts", "date", "hour", "weekday", "session_id", "page",
            "country", "city", "org", "global_ip", "device", "browser",
            "os", "lang", "referrer",
        ]
        try:
            ws = sp.worksheet(ACCESS_LOG_TAB)
            # ヘッダー行がなければ追加
            row1 = ws.row_values(1)
            if not row1:
                ws.append_row(headers)
        except gspread.exceptions.WorksheetNotFound:
            ws = sp.add_worksheet(
                title=ACCESS_LOG_TAB, rows=50000, cols=len(headers)
            )
            ws.append_row(headers)

        # 最新行を先頭(2行目)に挿入
        now_jst = datetime.datetime.now(JST)
        data_row = [
            row.get("ts",        ""),
            row.get("date",      ""),
            row.get("hour",      ""),
            now_jst.strftime("%A"),
            sid,
            row.get("page",      "market_dashboard"),
            row.get("country",   ""),
            row.get("city",      ""),
            row.get("org",       ""),
            row.get("global_ip", ""),
            row.get("device",    ""),
            row.get("browser",   ""),
            row.get("os",        ""),
            row.get("lang",      ""),
            "",                                # referrer（将来用）
        ]
        ws.insert_row(data_row, index=2, value_input_option="USER_ENTERED")
        logger.info(f"[access_log] 書き込み成功: {row.get('country','')} {row.get('city','')}")
        return True

    except Exception as e:
        logger.warning(f"[access_log] 書き込みエラー: {e}")
        st.session_state["_anl_target_sheets_error"] = str(e)[:200]
        return False


def _write_pv_sheets(row: dict, sid: str):
    headers = ["ts", "date", "hour", "session_id", "page",
               "country", "city", "org", "global_ip", "local_ip", "device", "browser"]
    ws = _sheets_ws("pageviews", headers)
    if not ws:
        return
    if not row.get("device") or row.get("device") == "Unknown":
        row = dict(row)
        row["org"] = (row.get("org", "") + " [UA取得失敗]").strip()
    ws.insert_row([row.get(k, "") for k in headers], index=2,
                  value_input_option="USER_ENTERED")
    try:
        rt_ws = _sheets_ws("realtime", ["session_id", "last_seen"])
        if rt_ws:
            now_str = row["ts"]
            for i, rec in enumerate(rt_ws.get_all_records(), start=2):
                if rec.get("session_id") == sid:
                    rt_ws.update_cell(i, 2, now_str)
                    return
            rt_ws.append_row([sid, now_str])
    except Exception as e:
        logger.debug(f"[analytics] realtime更新エラー: {e}")


# ══════════════════════════════════════
# スキャンログ記録
# ══════════════════════════════════════
def track_scan(devices: list, cve_count: int, critical: int, kev: int):
    """スキャン実行時に呼び出す。"""
    now = datetime.datetime.now(JST)
    sid = _session_id()
    products = list(set(d.get("product", "") for d in devices))
    row = {
        "ts":           now.isoformat(),
        "date":         now.strftime("%Y-%m-%d"),
        "hour":         now.hour,
        "session_id":   sid,
        "device_count": len(devices),
        "products":     ",".join(products),
        "cve_total":    cve_count,
        "critical":     critical,
        "kev":          kev,
    }
    if _SC_LOG_KEY not in st.session_state:
        st.session_state[_SC_LOG_KEY] = []
    st.session_state[_SC_LOG_KEY].append(row)

    if _detect_backend() == "sheets":
        try:
            headers = ["ts", "date", "hour", "session_id", "device_count",
                       "products", "cve_total", "critical", "kev"]
            ws = _sheets_ws("scan_logs", headers)
            if ws:
                ws.insert_row([row.get(k, "") for k in headers], index=2,
                              value_input_option="USER_ENTERED")
        except Exception as e:
            logger.warning(f"[analytics] スキャンログ書き込みエラー: {e}")


# ══════════════════════════════════════
# データ読み込み
# ══════════════════════════════════════
@st.cache_data(ttl=120, show_spinner=False)
def _load_pv_df() -> pd.DataFrame:
    if _detect_backend() == "sheets":
        try:
            c = _sheets_client()
            if c:
                sp   = c.open_by_key(_secret("GOOGLE_SHEETS_ID"))
                ws   = sp.worksheet("pageviews")
                recs = ws.get_all_records()
                if recs:
                    df = pd.DataFrame(recs)
                    df["date"] = pd.to_datetime(df["date"], errors="coerce")
                    return df
        except Exception:
            pass
    log = st.session_state.get(_PV_LOG_KEY, [])
    if log:
        df = pd.DataFrame(log)
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        return df
    return pd.DataFrame()


@st.cache_data(ttl=120, show_spinner=False)
def _load_scan_df() -> pd.DataFrame:
    if _detect_backend() == "sheets":
        try:
            c = _sheets_client()
            if c:
                sp   = c.open_by_key(_secret("GOOGLE_SHEETS_ID"))
                ws   = sp.worksheet("scan_logs")
                recs = ws.get_all_records()
                if recs:
                    df = pd.DataFrame(recs)
                    df["date"] = pd.to_datetime(df["date"], errors="coerce")
                    return df
        except Exception:
            pass
    log = st.session_state.get(_SC_LOG_KEY, [])
    if log:
        df = pd.DataFrame(log)
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        return df
    return pd.DataFrame()


@st.cache_data(ttl=30, show_spinner=False)
def _load_realtime() -> int:
    now    = datetime.datetime.now(JST)
    cutoff = now - timedelta(minutes=REALTIME_MIN)
    if _detect_backend() == "sheets":
        try:
            c = _sheets_client()
            if c:
                sp  = c.open_by_key(_secret("GOOGLE_SHEETS_ID"))
                ws  = sp.worksheet("realtime")
                cnt = sum(
                    1 for r in ws.get_all_records()
                    if _is_recent(r.get("last_seen", ""), cutoff)
                )
                return max(cnt, 1)
        except Exception:
            pass
    return 1


def _is_recent(ts_str: str, cutoff) -> bool:
    try:
        ls = datetime.datetime.fromisoformat(ts_str)
        if ls.tzinfo is None:
            ls = JST.localize(ls)
        return ls >= cutoff
    except Exception:
        return False


# ══════════════════════════════════════
# バーチャート（altair不使用）
# ══════════════════════════════════════
def _bar_html(series: "pd.Series", color: str = "#1ba0d7", height: int = 20) -> str:
    if series is None or series.empty:
        return ""
    max_val = series.max() or 1
    rows = ""
    for label, val in series.items():
        pct = int(val / max_val * 100)
        rows += (
            f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;'
            f'font-family:monospace;font-size:10px;">'
            f'<div style="width:90px;text-align:right;color:#7a86a8;white-space:nowrap;'
            f'overflow:hidden;text-overflow:ellipsis">{label}</div>'
            f'<div style="flex:1;background:#1a1e2c;border-radius:2px;height:{height}px;">'
            f'<div style="width:{pct}%;height:100%;background:{color};border-radius:2px;'
            f'display:flex;align-items:center;padding-left:6px;font-size:10px;'
            f'font-weight:700;color:#fff">{val}</div>'
            f'</div></div>'
        )
    return f'<div style="padding:8px 0">{rows}</div>'


# ══════════════════════════════════════
# ダッシュボード描画
# ══════════════════════════════════════
def render_analytics_dashboard():
    """アクセス解析ダッシュボードを描画する。"""
    backend = _detect_backend()

    if backend == "sheets":
        st.markdown(
            '<div style="background:rgba(58,240,128,.08);border:1px solid rgba(58,240,128,.25);'
            'border-radius:4px;padding:6px 12px;font-family:monospace;font-size:10px;'
            'color:#3af080;margin-bottom:12px">🟢 Google Sheets 永続保存中</div>',
            unsafe_allow_html=True,
        )
    else:
        st.warning(
            "⚠️ Google Sheetsが未設定。セッションメモリのみ記録中（再起動でリセット）\n\n"
            "永続保存するには Secrets に以下を設定してください:\n"
            "- `GOOGLE_SHEETS_ID`\n"
            "- `GOOGLE_SERVICE_ACCOUNT_JSON`"
        )

    ua      = st.session_state.get("_anl_ua", "")
    ua_info = _parse_ua_rich(ua)
    device  = ua_info["device_type"]
    browser = ua_info["browser_full"]
    os_name = ua_info["os"]
    lang    = st.session_state.get("_anl_lang", "")
    rt      = _load_realtime()

    # JS geo をダッシュボード表示のタイミングで取得（ログとは別）
    js_geo  = _get_js_geo()
    if js_geo.get("ip"):
        country  = js_geo.get("country", st.session_state.get("_anl_country", "??"))
        city     = js_geo.get("city",    "")
        region   = js_geo.get("region",  "")
        isp      = js_geo.get("org",     "")
        city_str = f"{city}, {region}" if region and region != city else city
        # session_state も更新しておく
        st.session_state["_anl_country"] = country
        st.session_state["_anl_city"]    = city_str
        st.session_state["_anl_isp"]     = isp
    else:
        country  = st.session_state.get("_anl_country", "取得中...")
        city_str = st.session_state.get("_anl_city",    "取得中...")
        isp      = st.session_state.get("_anl_isp",  "")

    info1, info2 = st.columns([3, 1])
    isp_disp  = f" &nbsp;|&nbsp; 📡 {isp}" if isp else ""
    os_disp   = f" / {os_name}" if os_name and os_name != "Unknown" else ""
    lang_disp = f" &nbsp;|&nbsp; 🌐 {lang}" if lang else ""
    info1.markdown(
        f'<div style="background:rgba(27,160,215,.08);border:1px solid rgba(27,160,215,.2);'
        f'border-radius:4px;padding:8px 14px;font-family:monospace;font-size:10px;color:#7a86a8">'
        f'🌍 <b style="color:#dde2f5">{country}</b> / {city_str}'
        f'{isp_disp} &nbsp;|&nbsp; '
        f'💻 {device}{os_disp} / {browser}'
        f'{lang_disp}</div>',
        unsafe_allow_html=True,
    )
    info2.markdown(
        f'<div style="background:rgba(58,240,128,.06);border:1px solid rgba(58,240,128,.2);'
        f'border-radius:4px;padding:8px;text-align:center;font-family:monospace;font-size:10px;">'
        f'<span style="color:#3af080;font-weight:700">● {rt}人</span>'
        f'<span style="color:#404870"> オンライン</span></div>',
        unsafe_allow_html=True,
    )

    sheets_status = st.session_state.get("_anl_sheets_status", "（未記録 - 再読み込みで確認）")
    st.markdown(
        f'<div style="background:rgba(255,255,255,.04);border:1px solid #252d48;'
        f'border-radius:4px;padding:6px 12px;font-family:monospace;font-size:10px;'
        f'color:#7a86a8;margin-bottom:8px">📋 Sheets書き込みステータス: {sheets_status}</div>',
        unsafe_allow_html=True,
    )

    col_r1, col_r2 = st.columns(2)
    if col_r1.button("🔄 データ更新", key="anl_refresh"):
        st.cache_data.clear()
        st.rerun()
    if col_r2.button("🧪 Sheets接続テスト", key="anl_test"):
        with st.spinner("接続テスト中..."):
            try:
                sheets_id   = _secret("GOOGLE_SHEETS_ID")
                sheets_json = _secret("GOOGLE_SERVICE_ACCOUNT_JSON")
                if not sheets_id:
                    st.error("❌ GOOGLE_SHEETS_ID が Secrets に未設定")
                elif not sheets_json:
                    st.error("❌ GOOGLE_SERVICE_ACCOUNT_JSON が Secrets に未設定")
                else:
                    import gspread
                    from google.oauth2.service_account import Credentials
                    sa_info = json.loads(sheets_json)
                    st.info(f"SA email: {sa_info.get('client_email', '不明')}")
                    creds = Credentials.from_service_account_info(sa_info, scopes=[
                        "https://www.googleapis.com/auth/spreadsheets",
                        "https://www.googleapis.com/auth/drive",
                    ])
                    client = gspread.authorize(creds)
                    sp = client.open_by_key(sheets_id)
                    st.success(f"✅ Sheets接続成功！スプレッドシート名: {sp.title}")
                    st.info(f"シート一覧: {[ws.title for ws in sp.worksheets()]}")
            except Exception as e:
                st.error(f"❌ 接続失敗: {e}")

    df_pv   = _load_pv_df()
    df_scan = _load_scan_df()

    today  = pd.Timestamp.now().normalize()
    last7  = today - pd.Timedelta(days=7)
    has_sid = "session_id" in df_pv.columns if not df_pv.empty else False

    m1, m2, m3, m4, m5, m6 = st.columns(6)
    if not df_pv.empty:
        df_pv["date"] = pd.to_datetime(df_pv["date"], errors="coerce")
        m1.metric("本日 PV",      len(df_pv[df_pv["date"] >= today]))
        m2.metric("7日 PV",       len(df_pv[df_pv["date"] >= last7]))
        m3.metric("累計 PV",      len(df_pv))
        m4.metric("累計 Session", df_pv["session_id"].nunique() if has_sid else "-")
    else:
        for m in [m1, m2, m3, m4]:
            m.metric("-", 0)

    if not df_scan.empty:
        m5.metric("累計スキャン", len(df_scan))
        m6.metric("検出CVE合計",
                  int(df_scan["cve_total"].sum()) if "cve_total" in df_scan.columns else 0)
    else:
        m5.metric("累計スキャン", 0)
        m6.metric("検出CVE合計",  0)

    st.markdown("---")

    t_pv, t_scan, t_geo, t_dev, t_raw = st.tabs(
        ["📅 PVトレンド", "🔍 スキャン履歴", "🌍 地域", "💻 デバイス", "📋 生データ"]
    )

    with t_pv:
        if df_pv.empty:
            st.info("まだデータがありません。")
        else:
            period = st.radio("期間", ["7日", "30日", "全期間"],
                              index=1, horizontal=True, key="anl_period")
            cutoff = {"7日": last7,
                      "30日": today - pd.Timedelta(days=30),
                      "全期間": pd.Timestamp("2000-01-01")}[period]
            df_t = df_pv[df_pv["date"] >= cutoff].copy()
            if not df_t.empty:
                daily = df_t.groupby("date").size().rename("PV")
                st.markdown("**📅 日別 PV**")
                st.markdown(_bar_html(daily, "#1ba0d7"), unsafe_allow_html=True)
                st.dataframe(daily.reset_index().rename(columns={"date": "日付"}),
                             use_container_width=True, hide_index=True)
            else:
                st.info("期間内データなし")

            if "hour" in df_pv.columns:
                st.markdown("**⏰ 時間帯別アクセス (JST)**")
                hcnt = df_pv["hour"].value_counts().reindex(range(24), fill_value=0).sort_index()
                st.markdown(_bar_html(hcnt, "#00c8f0", 14), unsafe_allow_html=True)

    with t_scan:
        if df_scan.empty:
            st.info("まだスキャンデータがありません。")
        else:
            sc1, sc2, sc3 = st.columns(3)
            sc1.metric("総スキャン回数", len(df_scan))
            if "cve_total" in df_scan.columns:
                sc2.metric("平均CVE検出数", f'{df_scan["cve_total"].mean():.1f}')
                sc3.metric("最大CVE検出数", int(df_scan["cve_total"].max()))

            if "date" in df_scan.columns:
                st.markdown("**📊 日別スキャン回数**")
                daily_scan = df_scan.groupby("date").size().rename("スキャン数")
                st.markdown(_bar_html(daily_scan, "#3af080"), unsafe_allow_html=True)

            if "products" in df_scan.columns:
                st.markdown("**🖥️ スキャン製品 Top10**")
                all_products = []
                for p in df_scan["products"].dropna():
                    all_products.extend(str(p).split(","))
                prod_counts = pd.Series(all_products).value_counts().head(10)
                st.markdown(_bar_html(prod_counts, "#1ba0d7"), unsafe_allow_html=True)

            st.markdown("**📋 スキャン履歴**")
            show_cols = [c for c in ["ts", "device_count", "products", "cve_total", "critical", "kev"]
                         if c in df_scan.columns]
            st.dataframe(df_scan.sort_values("ts", ascending=False).head(50)[show_cols],
                         use_container_width=True, hide_index=True)

    with t_geo:
        if df_pv.empty or "country" not in df_pv.columns:
            st.info("データなし")
        else:
            df_v = df_pv[~df_pv["country"].isin(["??", "Local", ""])]
            if df_v.empty:
                st.info("地域データ取得中... 次回アクセス時から記録されます。")
            else:
                g1, g2 = st.columns(2)
                with g1:
                    st.markdown("**🌏 国別 Top10**")
                    cnt_c = df_v["country"].value_counts().head(10)
                    st.markdown(_bar_html(cnt_c, "#1ba0d7"), unsafe_allow_html=True)
                    st.dataframe(cnt_c.reset_index(), use_container_width=True, hide_index=True)
                with g2:
                    st.markdown("**🏙️ 都市別 Top10**")
                    if "city" in df_v.columns:
                        df_ci = df_v[~df_v["city"].isin(["??", "Local", ""])]
                        cnt_ci = df_ci["city"].value_counts().head(10)
                        st.markdown(_bar_html(cnt_ci, "#00c8f0"), unsafe_allow_html=True)
                        st.dataframe(cnt_ci.reset_index(), use_container_width=True, hide_index=True)

    with t_dev:
        if df_pv.empty:
            st.info("データなし")
        else:
            d1, d2 = st.columns(2)
            with d1:
                st.markdown("**💻 デバイス別**")
                if "device" in df_pv.columns:
                    cnt_d = df_pv["device"].value_counts()
                    st.markdown(_bar_html(cnt_d, "#fa582d"), unsafe_allow_html=True)
                    st.dataframe(cnt_d.reset_index(), use_container_width=True, hide_index=True)
            with d2:
                st.markdown("**🌐 ブラウザ別**")
                if "browser" in df_pv.columns:
                    cnt_b = df_pv["browser"].value_counts().head(8)
                    st.markdown(_bar_html(cnt_b, "#a855f7"), unsafe_allow_html=True)
                    st.dataframe(cnt_b.reset_index(), use_container_width=True, hide_index=True)

    with t_raw:
        st.markdown("**📋 PVログ**")
        if not df_pv.empty:
            show_cols = [c for c in ["ts", "date", "hour", "session_id", "country",
                                     "city", "device", "browser"] if c in df_pv.columns]
            n = st.slider("表示件数", 10, min(len(df_pv), 500), 50, key="anl_raw_n")
            st.dataframe(df_pv.sort_values("ts", ascending=False).head(n)[show_cols],
                         use_container_width=True, hide_index=True)
            csv = df_pv.to_csv(index=False).encode("utf-8-sig")
            st.download_button(
                "⬇️ PVログ CSV", data=csv,
                file_name=f"pv_log_{datetime.datetime.now(JST).strftime('%Y%m%d')}.csv",
                mime="text/csv", key="anl_dl_pv",
            )

        st.markdown("**🔍 スキャンログ**")
        if not df_scan.empty:
            csv2 = df_scan.to_csv(index=False).encode("utf-8-sig")
            st.dataframe(df_scan.sort_values("ts", ascending=False).head(100),
                         use_container_width=True, hide_index=True)
            st.download_button(
                "⬇️ スキャンログ CSV", data=csv2,
                file_name=f"scan_log_{datetime.datetime.now(JST).strftime('%Y%m%d')}.csv",
                mime="text/csv", key="anl_dl_scan",
            )