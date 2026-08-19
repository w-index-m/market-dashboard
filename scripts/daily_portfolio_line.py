#!/usr/bin/env python3
"""
毎朝、最大7種類のメッセージをLINE（Messaging API・ブロードキャスト配信）とSlack
（Incoming Webhook）の両方に送信するスクリプト:
  1. 市況サマリー（Fear&Greed・VIX・NAAIM・日経/US予測）
  2. 保有銘柄アクション判定（買い増し/保有継続/一部利確/売却をAIが銘柄ごとに判定）
  2b. 保有銘柄 関連ニュースまとめ（2のためにfetch済みのデータを再利用・AI1回で銘柄ごと1文要約）
  2c. 保有銘柄 決算ハイライト（2のためにfetch済みのEPS実績vs予想・売上/純利益成長率・次回決算日を
      AIを介さずそのまま表示。追加API呼び出しなし）
  3. 経済指標チェック（直近発表結果の前回比、本日の発表予定。FMP→BLS→FREDの既存データを再利用）
  4. AI/半導体株の異常検知（相対弱さ・急落→急回復パターン）
  5. AI推奨ポートフォリオ（新規投資先提案）

新規投資の提案だけだと「ポートフォリオの入れ替え」ができない（売る判断が無い）ため、
既存保有銘柄の売買判定（2）を組み込んでいる。既存のインタラクティブUIが使っている
_generate_full_portfolio_recommendation()をそのまま再利用する。保有銘柄が無い/取引記録が
無い場合、2・2b・2cは送信されない（正常系として静かにスキップ）。

GitHub Actionsのcronから `python scripts/daily_portfolio_line.py` として直接実行される想定で、
Streamlitの実行環境（`streamlit run`）は不要。app.py側の各fetch/AI関数はもともとst.*を呼ばない
設計（fetch_*/compute_*レイヤー）なので、モジュールとしてimportして再利用する。
各メッセージは独立して送信され、どれか1つが失敗しても他は送られる。

配信先は環境変数で設定されている方だけ動く（両方設定すれば両方に届く。片方だけでもOK）。
LINE Notify は2025年3月末にサービス終了したため、後継のLINE公式アカウント + Messaging API
（ブロードキャスト配信）を使う。ブロードキャストはその公式アカウントを友だち追加している
全員に届くので、個人利用（自分だけが友だち）を想定している。LINEはこの性質上ユーザーごとの
個別配信ができないため、TRADING_USERNAME（既定アカウント）のみに送る。

アカウントごとのSlack個別配信: アプリの「取引記録入力」タブ→「🔔 通知設定」で、各アカウントが
自分のSlack Incoming Webhook URLを登録できる（usersシートのslack_webhook_url列に保存）。
登録済みの追加アカウント（TRADING_USERNAME以外）は、市況サマリー・経済指標・異常検知・
AI推奨ポートフォリオは共通のまま、保有銘柄アクション判定だけそのアカウント自身の取引記録を
もとに個別生成し、登録したWebhookへ配信する。

必要な環境変数（GitHub Secretsから渡す想定）:
    LINE_CHANNEL_ACCESS_TOKEN  任意。LINE Developersで発行するMessaging APIチャネルアクセストークン。
    SLACK_WEBHOOK_URL          任意。SlackのIncoming Webhook URL。
    ※ LINE_CHANNEL_ACCESS_TOKEN / SLACK_WEBHOOK_URL の少なくとも1つは必須。
    GEMINI_API_KEY / GROQ_API_KEY / OPENROUTER_API_KEY  いずれか（AIフォールバックチェーン）
    FMP_API_KEY, FINNHUB_API_KEY, ALPHA_VANTAGE_KEY, TIINGO_API_KEY  任意（市場データ取得に使用）
    GOOGLE_SHEETS_ID, GOOGLE_SERVICE_ACCOUNT_JSON  取引記録読込・推奨履歴記録に必要
    TRADING_USERNAME      任意。取引記録シートのユーザー名（"admin"ならclaude_tradesタブ）。デフォルト admin
    PORTFOLIO_BUDGET      任意。予算（円）。デフォルト 1000000
    PORTFOLIO_MODE        任意。growth/momentum/ai_mix/optical_mix/dividend_stable/stable_growth。デフォルト growth
    PORTFOLIO_MODEL_TYPE  任意。etf/individual。デフォルト etf
"""
import os
import re
import sys
import time
from datetime import datetime

import pytz
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402  (app.py側でst.set_page_config()をimport時に実行しないようガード済み)

JST = pytz.timezone("Asia/Tokyo")

LINE_BROADCAST_URL = "https://api.line.me/v2/bot/message/broadcast"
LINE_TEXT_MAX_LEN = 4900  # LINEのtextメッセージ上限は5000文字。安全マージンを取る。

MODE_LABELS = {
    "growth": "🌱長期育成", "momentum": "⚡モメンタム",
    "ai_mix": "✨AIミックス", "optical_mix": "💡光銘柄ミックス", "dividend_stable": "💰配当安定",
    "stable_growth": "🪨安定成長",
}


def _fmt_yen(n) -> str:
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return str(n)


def _post_to_line(token: str, text: str, label: str) -> None:
    if len(text) > LINE_TEXT_MAX_LEN:
        text = text[:LINE_TEXT_MAX_LEN - 3] + "..."
    try:
        resp = requests.post(
            LINE_BROADCAST_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"messages": [{"type": "text", "text": text}]},
            timeout=15,
        )
        resp.raise_for_status()
        print(f"Posted {label} to LINE. status={resp.status_code}")
    except Exception as e:
        print(f"Failed to post {label} to LINE: {e}", file=sys.stderr)


def _post_to_slack(webhook_url: str, text: str, label: str) -> None:
    try:
        resp = requests.post(webhook_url, json={"text": text}, timeout=15)
        resp.raise_for_status()
        print(f"Posted {label} to Slack. status={resp.status_code}")
    except Exception as e:
        print(f"Failed to post {label} to Slack: {e}", file=sys.stderr)


def _post_to_channels(line_token: str, slack_webhook: str, text: str, label: str) -> None:
    """設定されている配信先（LINE/Slack、両方でも片方でも可）すべてに同じ内容を送る。"""
    if line_token:
        _post_to_line(line_token, text, label)
    if slack_webhook:
        _post_to_slack(slack_webhook, text, label)


# ─────────────────────────────────────────────
# 1) 市況サマリー
# ─────────────────────────────────────────────
def build_market_summary_message(market_ctx: dict, today: str) -> str:
    fg_score    = market_ctx.get("fg_score")
    fg_label    = market_ctx.get("fg_label", "")
    fg_c7       = market_ctx.get("fg_change_7d")
    fg_c30      = market_ctx.get("fg_change_30d")
    vix         = market_ctx.get("vix_val")
    naaim       = market_ctx.get("naaim_exp")
    crash_score = market_ctx.get("crash_risk_score")
    crash_label = market_ctx.get("crash_risk_label", "")
    nikkei      = market_ctx.get("nikkei_pred") or {}
    us          = market_ctx.get("us_pred") or {}

    lines = [f"🌐 本日の市況サマリー（{today}）", ""]

    if fg_score is not None:
        chg_parts = []
        if fg_c7 is not None:
            chg_parts.append(f"7日{fg_c7:+.0f}pt")
        if fg_c30 is not None:
            chg_parts.append(f"30日{fg_c30:+.0f}pt")
        chg_str = f"（{' / '.join(chg_parts)}）" if chg_parts else ""
        lines.append(f"😨 Fear&Greed: {fg_score:.0f} {fg_label} {chg_str}")
    if vix is not None:
        lines.append(f"📉 VIX: {vix:.1f}")
    if naaim is not None:
        lines.append(f"📊 NAAIM機関投資家エクスポージャー: {naaim:.0f}%")
    if crash_score is not None:
        lines.append(f"⚠️ クラッシュリスクスコア: {crash_score}/10 {crash_label}")

    lines.append("")

    def _fmt_pred(label: str, flag: str, pred: dict, price_key: str) -> str:
        comp = pred.get("composite")
        pu   = pred.get("prob_up_tomorrow")
        comp_str = f"{comp:+.2f}" if isinstance(comp, (int, float)) else "-"
        pu_str   = f"{pu:.0f}%" if isinstance(pu, (int, float)) else "-"
        return (
            f"{flag} {label}予測: 総合スコア{comp_str} | "
            f"翌日上昇確率{pu_str} | 現値{pred.get(price_key, '?')}"
        )

    if nikkei:
        lines.append(_fmt_pred("日経平均", "🇯🇵", nikkei, "nikkei"))
    if us:
        lines.append(_fmt_pred("S&P500", "🇺🇸", us, "sp500"))

    lines.append("")
    lines.append("🔗 ダッシュボード: https://windex.streamlit.app")

    return "\n".join(lines)


# ─────────────────────────────────────────────
# 2) 保有銘柄アクション判定（買い増し/保有継続/一部利確/売却）
# ─────────────────────────────────────────────
def generate_holdings_action(market_ctx: dict, mode: str, model_pref: str, username: str) -> tuple:
    """
    Google Sheetsの取引記録から現在の保有銘柄を計算し、既存のマルチエージェント分析
    （_generate_full_portfolio_recommendation）で銘柄ごとの推奨アクションを判定する。
    stock_data_mapも返す（build_holdings_news_messageでニュース見出しを再利用するため、
    ここで一度だけ取得すれば二重にAPIを叩かずに済む）。ticker_names（ticker→銘柄名）も
    返す（build_holdings_action_messageが、AIが見出しに銘柄名を書き忘れた場合の保険に使う）。
    Returns: ({"text": str, "model": str, "error": str|None}, stock_data_map, ticker_names)
    """
    trades_df, err = app._load_trades(username)
    if trades_df.empty:
        return {"text": "", "model": "", "error": err or "取引記録がありません"}, {}, {}

    positions = app._calc_positions_from_df(trades_df)
    if not positions:
        return {"text": "", "model": "", "error": "保有銘柄がありません"}, {}, {}

    # 既知の日本語正式社名マスタ（app._KNOWN_NAMES）を優先し、無ければ取引記録の名前を使う
    # （例: 285A.T → 取引記録の名前ではなく「キオクシアHD」を優先表示）
    ticker_names = {
        _t: app._KNOWN_NAMES.get(_t) or _p.get("name", _t) or _t
        for _t, _p in positions.items()
    }

    # _calc_positions_from_df は market_value を持たないため、時価を取得して付与する
    # （付与しないと評価額・含み損益・配分%が常に0/フル損扱いになってしまう）
    prices = app._fetch_portfolio_prices(tuple(positions.keys()))
    positions = {
        _t: {**_p, "market_value": _p.get("qty", 0) * prices.get(_t, {}).get("price", 0)}
        for _t, _p in positions.items()
    }

    # 銘柄ごとに連続でyf.downloadすると1回のスクリプト実行内でYahooのレート制限に
    # かかりやすいため、銘柄間に小さく間隔を空ける（_fetch_trading_stock_data自体も
    # 個別に3回までリトライするが、それとは別にバーストを避けるための間隔）。
    # このスクリプトは手動実行（workflow_dispatch）だと1日に複数回動くことがあるため、
    # 当日分の技術データがすでにGoogle Sheetsのstock_data_cacheに保存されていれば
    # まずそれを使う。無ければライブ取得し、成功したら次回以降の実行のためにキャッシュへ
    # 保存する（レート制限に当たった実行があっても、その日の別の実行で取得済みなら
    # 使い回せる）。
    today_str = datetime.now(JST).strftime("%Y-%m-%d")

    def _has_technical(_d: dict) -> bool:
        return bool(_d) and any(_d.get(k) is not None for k in ("rsi", "ma25", "ret_5d"))

    stock_data_map = {}
    for i, ticker in enumerate(positions):
        cached = app._load_stock_data_cache(ticker, today_str)
        if _has_technical(cached):
            stock_data_map[ticker] = cached
            continue

        if i > 0:
            time.sleep(1.0)
        try:
            data = app._fetch_trading_stock_data(ticker, ticker.endswith(".T"))
        except Exception as e:
            print(f"stock data fetch failed for {ticker}: {e}", file=sys.stderr)
            data = {}

        # テクニカル指標が全部欠けている場合、レート制限の一時的な失敗の可能性が
        # あるため少し間隔を空けて1回だけ再取得を試みる
        if not _has_technical(data):
            time.sleep(3.0)
            try:
                retry_data = app._fetch_trading_stock_data(ticker, ticker.endswith(".T"))
                if _has_technical(retry_data):
                    data = retry_data
            except Exception as e:
                print(f"stock data retry failed for {ticker}: {e}", file=sys.stderr)

        if _has_technical(data):
            try:
                app._save_stock_data_cache(ticker, today_str, data)
            except Exception as e:
                print(f"stock data cache save failed for {ticker}: {e}", file=sys.stderr)

        stock_data_map[ticker] = data

    # 保有銘柄アクション判定も1日1回で足りるため、同じ理由でAI呼び出し自体を
    # キャッシュする（銘柄データの取得・整形は上のループで毎回そのまま行う）
    cache_key = f"holdings_action_{username}_{mode}"
    ai_result = app._load_ai_digest_cache(cache_key, today_str)
    if ai_result is None:
        ai_result = app._generate_full_portfolio_recommendation(
            positions, stock_data_map, market_ctx, mode=mode, model_pref=model_pref,
        )
        if not ai_result.get("error"):
            app._save_ai_digest_cache(cache_key, today_str, ai_result)
    return ai_result, stock_data_map, ticker_names


def build_holdings_action_message(result: dict, today: str, ticker_names: dict | None = None) -> str | None:
    error = result.get("error")
    text = result.get("text", "")

    if error:
        # 保有銘柄が無い/取引記録が無いのは正常系なので、エラーとして送らずスキップする
        if "保有銘柄がありません" in error or "取引記録がありません" in error:
            return None
        return f"📋 保有銘柄アクション判定（{today}）\n⚠️ 取得に失敗しました: {error}"

    # AIが見出しに銘柄名を書かない（またはティッカーをそのまま名前欄にも書く）ことがあるため、
    # 取引記録上の名前で確実に上書きする。AIの見出し形式は "### TICKER"・"### 🇺🇸 TICKER | TICKER"
    # など揺れがあるため、そのティッカーを含む見出し行を丸ごと決め打ちの形に置き換える
    # （プロンプトでは「銘柄名（ティッカー）」を指示しているが、従わないことがある保険）
    if ticker_names and text:
        for ticker, name in ticker_names.items():
            if not name:
                continue
            flag = "🇯🇵" if ticker.endswith(".T") else "🇺🇸"
            text = re.sub(
                rf'(?m)^#{{1,6}}[^\n]*\b{re.escape(ticker)}\b[^\n]*$',
                f'### {flag} {name}（{ticker}）',
                text,
            )
    if not text:
        return None

    # Streamlit(markdown)向けの見出し・強調記法をLINEのプレーンテキスト向けに簡易変換
    plain = re.sub(r'^#{1,6}\s*', '', text, flags=re.MULTILINE)
    plain = re.sub(r'\*\*(.+?)\*\*', r'\1', plain)
    plain = re.sub(r'^-{3,}\s*$', '───────', plain, flags=re.MULTILINE)
    plain = re.sub(r'\n{3,}', '\n\n', plain).strip()

    return f"📋 保有銘柄アクション判定（{today}）\n\n{plain}"


def build_holdings_news_message(stock_data_map: dict, today: str, username: str = "") -> str | None:
    """
    保有銘柄ごとの直近ニュース見出し一覧＋AIによる1銘柄1文の軽い要約。
    generate_holdings_actionで既に取得済みのstock_data_map（_fetch_trading_stock_dataの
    戻り値）を再利用するので、要約用のAI呼び出し1回以外に追加のAPI呼び出しは発生しない。
    ポジティブ/ネガティブの判定はここでは行わない（見出しの機械的な感情分析は誤判定の
    リスクがあるため）— そちらは「📋 保有銘柄アクション判定」内のAIによるIR・ニュース
    評価を参照する前提。
    この要約も1日1回で足りるため、他のAI呼び出しと同じ理由でキャッシュする。
    """
    cache_key = f"holdings_news_{username}"
    summaries = app._load_ai_digest_cache(cache_key, today)
    if summaries is None:
        try:
            summaries = app._summarize_holdings_news(stock_data_map)
            app._save_ai_digest_cache(cache_key, today, summaries)
        except Exception as e:
            print(f"holdings news summarize failed: {e}", file=sys.stderr)
            summaries = {}

    lines = [f"📰 保有銘柄 関連ニュースまとめ（{today}）", ""]
    has_content = False
    for ticker, data in stock_data_map.items():
        items = (data or {}).get("news_items") or []
        if not items:
            continue
        has_content = True
        lines.append(f"◆ {ticker}")
        if summaries.get(ticker):
            lines.append(f"　要約: {summaries[ticker]}")
        for item in items[:3]:
            headline = item.get("headline_ja") or item.get("headline") or ""
            if not headline:
                continue
            date   = item.get("date", "")
            source = item.get("source", "")
            meta   = " ".join(p for p in (date, source) if p)
            lines.append(f"・{headline}" + (f"（{meta}）" if meta else ""))
        lines.append("")

    if not has_content:
        return None

    lines.append("※ 要約はAIによる見出しの機械的な要約です。ポジティブ/ネガティブの評価は「📋 保有銘柄アクション判定」内のIR・ニュース評価を参照してください。")
    return "\n".join(lines).strip()


def build_earnings_highlight_message(
    stock_data_map: dict, ticker_names: dict, today: str
) -> str | None:
    """
    保有銘柄の決算ハイライト（直近四半期のEPS実績vs予想・サプライズ%、売上高/純利益成長率、
    次回決算日）。AIによる要約や解釈は行わず、_fetch_trading_stock_dataが既に取得済みの
    数値をそのまま機械的にリスト表示する（数値の伝達は確実性を優先し、AIの言い換えを介さない）。
    決算・EPSデータが1件も無ければNoneを返す（正常系として静かにスキップ）。
    """
    lines = [f"📈 保有銘柄 決算ハイライト（{today}）", ""]
    has_content = False
    for ticker, data in stock_data_map.items():
        data = data or {}
        eps_hist     = data.get("eps_history") or []
        rev_growth   = data.get("rev_growth")
        earn_growth  = data.get("earn_growth")
        next_earn    = data.get("next_earnings")
        if not eps_hist and rev_growth is None and earn_growth is None and not next_earn:
            continue
        has_content = True
        name = ticker_names.get(ticker, ticker) if ticker_names else ticker
        lines.append(f"◆ {ticker} {name}")
        if eps_hist:
            lines.append(f"   直近四半期EPS: {eps_hist[-1]}")
        growth_parts = []
        if rev_growth is not None:
            growth_parts.append(f"売上高成長率(YoY) {rev_growth:+.1f}%")
        if earn_growth is not None:
            growth_parts.append(f"純利益成長率(YoY) {earn_growth:+.1f}%")
        if growth_parts:
            lines.append("   " + " | ".join(growth_parts))
        if next_earn:
            lines.append(f"   次回決算日: {next_earn}")
        lines.append("")

    if not has_content:
        return None

    lines.append("※ AIによる解釈を介さない、決算データのそのままの表示です。")
    return "\n".join(lines).strip()


# ─────────────────────────────────────────────
# 3) 経済指標チェック（直近の発表結果・前回比、本日の発表予定）
# ─────────────────────────────────────────────
def build_eco_calendar_message(today: str) -> str | None:
    """
    既存の経済カレンダー機能（render_economic_events_section）が使っているのと同じ
    データ取得関数（FMP→BLS→FREDの順、無ければ空辞書）を再利用する。yfinance/Finnhubは
    経済指標カレンダーを持っていないため対象外 — FMP_API_KEYが主な情報源（無料のBLS/FREDが補完）。
    """
    events = app._get_upcoming_us_eco_events(days_back=10, days_ahead=1)
    if not events:
        return None

    eco_actuals = dict(app._fetch_eco_actuals_fmp())
    has_fmp = any(not k.startswith("_") for k in eco_actuals)
    if not has_fmp:
        for k, v in app._fetch_eco_actuals_bls().items():
            eco_actuals.setdefault(k, v)
    for k, v in app._fetch_eco_actuals_fred().items():
        eco_actuals.setdefault(k, v)

    lines = [f"📅 経済指標チェック（{today}）", ""]
    has_content = False

    # 直近発表済みイベントの実績・前回比（最大5件）
    past_events = [e for e in events if e["is_past"]]
    result_lines = []
    for ev in past_events:
        date_str = ev["date_str"]
        name = ev["name"]
        data = eco_actuals.get(f"{date_str}|{name}") or eco_actuals.get(date_str)
        if not data or not isinstance(data, dict):
            continue
        actual = data.get("actual")
        if actual is None:
            continue
        previous = data.get("previous")
        unit = data.get("unit", "")
        beat = data.get("beat")
        chg_str = f"（前回{previous}{unit}から{actual - previous:+.1f}{unit}）" if previous is not None else ""
        beat_str = " 📈予想上回り" if beat is True else " 📉予想下回り" if beat is False else ""
        result_lines.append(f"・{ev['icon']} {name}: {actual}{unit}{chg_str}{beat_str}")
    if result_lines:
        has_content = True
        lines.append("【直近の発表結果】")
        lines.extend(result_lines[-5:])
        lines.append("")

    # 本日発表予定のイベント
    today_events = [e for e in events if not e["is_past"] and e["jst_dt"].strftime("%Y-%m-%d") == today]
    if today_events:
        has_content = True
        impact_labels = {"high": "重要度:高", "medium": "重要度:中", "low": "重要度:低"}
        lines.append("【本日の発表予定】")
        for ev in today_events:
            lines.append(
                f"・{ev['icon']} {ev['jst_dt'].strftime('%H:%M')} {ev['name']}"
                f"（{impact_labels.get(ev['impact'], '')}）"
            )
        lines.append("")

    if not has_content:
        return None

    lines.append("⚠️ 情報提供目的のみ・投資助言ではありません。")
    return "\n".join(lines).strip()


# ─────────────────────────────────────────────
# 4) AI/半導体株 異常検知（相対弱さ・急落→急回復）
# ─────────────────────────────────────────────
def build_anomaly_alert_message(today: str) -> str | None:
    smh_hist = app._fetch_ticker_history_multi_year("SMH", years=2)
    if smh_hist.empty:
        return None

    lines = [f"⚡ AI/半導体株 異常検知（{today}・SMH基準）", ""]
    has_content = False

    rw = app._detect_relative_weakness("SMH", "^GSPC")
    if rw.get("ok"):
        base_w = rw["base_window"]
        base = rw["windows"][base_w]
        if rw["is_weak"]:
            has_content = True
            if rw["scenario"] == "rotation":
                lines.append(
                    f"🔄 SMHが市場に対し{base_w}営業日で{base['gap']:+.1f}pt劣後（市場は堅調〜横ばい）"
                    " → セクターローテーションの可能性"
                )
            else:
                lines.append(
                    f"⚠️ SMHが市場に対し{base_w}営業日で{base['gap']:+.1f}pt劣後（市場も下落）"
                    " → リスクオフ・マクロ要因の可能性"
                )
        else:
            lines.append(f"✅ 相対弱さ: 特に異常なし（{base_w}営業日乖離 {base['gap']:+.1f}pt）")

    whiplash_eps = app._detect_whiplash_episodes(smh_hist)
    last_date = smh_hist.index.max()
    recent_eps = [
        ep for ep in whiplash_eps
        if ep.get("recovery_date") is not None
        and 0 <= (last_date - ep["recovery_date"]).days <= 3
    ]
    if recent_eps:
        has_content = True
        for ep in recent_eps:
            lines.append(
                f"🌀 急落→急回復パターン検出: {ep['peak_date'].strftime('%Y-%m-%d')} → "
                f"{ep['trough_date'].strftime('%Y-%m-%d')}（{ep['decline_pct']:+.1f}%）→ "
                f"{ep['recovery_date'].strftime('%Y-%m-%d')}に回復"
            )

    if not has_content:
        lines.append("直近で特筆すべき乱高下・相対弱さは検出されていません。")

    lines.append("")
    lines.append("⚠️ 情報提供目的のみ。値動きパターンの検知であり、原因の特定はできません。")
    return "\n".join(lines)


# ─────────────────────────────────────────────
# 5) AI推奨ポートフォリオ
# ─────────────────────────────────────────────
def build_portfolio_message(
    result: dict, budget: int, mode_label: str, today: str, crash_signals: list | None = None,
) -> str:
    # 配分比率（AIの確信度が高いほど比率も高くなる想定）の降順に並べ替えて表示
    portfolio = sorted(result.get("portfolio") or [],
                        key=lambda it: float(it.get("allocation", 0) or 0), reverse=True)
    metrics = result.get("metrics") or {}
    error = result.get("error")

    if error or not portfolio:
        return (
            f"📊 本日のAI推奨ポートフォリオ（{today}）\n"
            f"⚠️ 生成に失敗しました: {error or '不明なエラー'}"
        )

    lines = [f"📊 本日のAI推奨ポートフォリオ（{today}・{mode_label}・予算{_fmt_yen(budget)}円）", ""]

    cash_pct = result.get("cash_reserve_pct", 0)
    crash_label = result.get("crash_risk_label", "")
    if cash_pct:
        # crash_signals: market_ctxの_fetch_market_context_for_trading()が判定した具体的な理由
        # （F&G極度の強欲・F&G7日急騰・NAAIM過剰投資・VIX高水準・米国市場弱気シグナル等）
        reason_str = "、".join(crash_signals) if crash_signals else "複合リスクシグナル"
        lines.append(f"⚠️ クラッシュ前兆シグナル検出（{reason_str}）（{crash_label}） → キャッシュ{cash_pct}%保持を推奨")
        lines.append("")

    for item in portfolio:
        flag   = item.get("flag", "")
        ticker = item.get("ticker", "")
        name   = item.get("name", "")
        alloc  = item.get("allocation", 0)
        amount = item.get("amount", 0)
        entry       = item.get("entry_price")
        entry_note  = item.get("entry_note", "")
        conclusion  = item.get("conclusion", "")
        lines.append(f"◆ {flag} {ticker} {name} — {alloc}%（{_fmt_yen(amount)}円）")
        if entry:
            try:
                is_jp_entry = ticker.endswith(".T")
                entry_str = f"¥{float(entry):,.0f}" if is_jp_entry else f"${float(entry):.2f}"
            except (TypeError, ValueError):
                entry_str = str(entry)
            lines.append(f"   エントリー目安: {entry_str}　{entry_note}")
        if conclusion:
            lines.append(f"   {conclusion}")

    if metrics:
        lines.append("")
        lines.append(
            f"期待リターン {metrics.get('expected_return', '-')}% | "
            f"想定ボラ {metrics.get('risk_volatility', '-')}% | "
            f"シャープレシオ {metrics.get('sharpe_ratio', '-')} | "
            f"想定最大DD {metrics.get('max_drawdown_estimate', '-')}%"
        )
        if metrics.get("comment"):
            lines.append(metrics["comment"])

    lines.append("")
    lines.append("⚠️ 情報提供目的のみ・投資助言ではありません。")

    return "\n".join(lines)


def generate_portfolio(market_ctx: dict, today: str, budget: int, mode: str, model_type: str) -> dict:
    # 通常のcronは1日1回だが、手動実行（workflow_dispatch）だと同じ日に複数回
    # 動くことがある。AI推奨ポートフォリオは1日1回生成すれば足りるため、同じ日に
    # 何度も作り直してGemini/Groqのクォータを浪費しないよう、当日分がキャッシュに
    # あればAgent A/B/Cを一切呼ばずに再利用する
    cache_key = "ai_portfolio"
    cached = app._load_ai_digest_cache(cache_key, today)
    if cached:
        print("Using cached AI portfolio recommendation for today")
        return cached

    print("Fetching candidate performance...")
    cand_perf = app._fetch_candidate_performance(today, mode)

    print("Running Agent A (macro analysis)...")
    agent_a = app._analyze_macro_agent(
        today,
        float(market_ctx.get("fg_score", 50) or 50),
        str(market_ctx.get("fg_label", "")),
        int(market_ctx.get("crash_risk_score", 0)),
        str(market_ctx.get("crash_risk_label", "🟢 低リスク")),
        float(market_ctx.get("vix_val", 0)),
        float(market_ctx.get("naaim_exp", 0)),
        " / ".join((market_ctx.get("sector_quad") or {}).get("Leading", [])[:4]) or "不明",
        " / ".join((market_ctx.get("sector_quad") or {}).get("Improving", [])[:3]) or "不明",
        str(market_ctx.get("nikkei_pred_label", "")),
        str(market_ctx.get("us_pred_label", "")),
    )

    print("Running Agent B (per-stock analysis, parallel)...")
    top_args = app._get_top_candidate_args(cand_perf, mode, budget, n=15)
    # Gemini分のクォータを使い切った瞬間から全呼び出しがGroqへ雪崩れ込み、
    # Groqの分あたりトークン上限をバーストで超えやすいため並列数を抑える
    agent_b = app._run_stock_agents_parallel(
        top_args, today, agent_a.get("stance", "中立"), max_workers=2,
    )

    print("Running Agent C (portfolio assembly)...")
    result = app._generate_investment_portfolio_rec(
        budget, model_type, "balanced", market_ctx, [], "auto",
        trading_mode=mode, candidate_perf=cand_perf, agent_a=agent_a, agent_b=agent_b,
    )
    if not result.get("error"):
        app._save_ai_digest_cache(cache_key, today, result)
    return result


def send_holdings_messages(username: str, mode: str, market_ctx: dict, today: str, post) -> None:
    """アカウント個別の保有銘柄アクション判定＋関連ニュースを生成して送信する。
    市況・経済指標・異常検知・AI推奨ポートフォリオは全アカウント共通なのでここには含まない。
    """
    try:
        holdings_result, holdings_stock_data, holdings_ticker_names = generate_holdings_action(
            market_ctx, mode, "auto", username
        )
        holdings_msg = build_holdings_action_message(holdings_result, today, holdings_ticker_names)
        if holdings_msg:
            post(holdings_msg, f"holdings action ({username})")
        news_msg = build_holdings_news_message(holdings_stock_data, today, username)
        if news_msg:
            post(news_msg, f"holdings news ({username})")
        earnings_msg = build_earnings_highlight_message(holdings_stock_data, holdings_ticker_names, today)
        if earnings_msg:
            post(earnings_msg, f"earnings highlight ({username})")
    except Exception as e:
        print(f"holdings action failed for {username}: {e}", file=sys.stderr)


def main() -> None:
    line_token    = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
    slack_webhook = os.environ.get("SLACK_WEBHOOK_URL", "")
    if not line_token and not slack_webhook:
        print("Neither LINE_CHANNEL_ACCESS_TOKEN nor SLACK_WEBHOOK_URL is set. Aborting.", file=sys.stderr)
        sys.exit(1)

    budget     = int(os.environ.get("PORTFOLIO_BUDGET", "1000000"))
    mode       = os.environ.get("PORTFOLIO_MODE", "growth")
    model_type = os.environ.get("PORTFOLIO_MODEL_TYPE", "etf")
    mode_label = MODE_LABELS.get(mode, mode)
    username   = os.environ.get("TRADING_USERNAME", "admin")

    today = datetime.now(JST).strftime("%Y-%m-%d")

    print("Fetching market context...")
    market_ctx = app._fetch_market_context_for_trading()

    # アカウントごとに個別のSlack Webhookが設定されていれば（アプリの取引記録入力タブの
    # 「🔔 通知設定」から登録可能）、そのアカウントの保有銘柄アクション判定をそのWebhook
    # 宛てに個別配信する。TRADING_USERNAME（GitHub Secrets側の既定アカウント、通常admin）は
    # 従来通りLINE/SLACK_WEBHOOK_URLの環境変数を使うため、二重配信を避けるためここでは除外する。
    per_user_targets = []
    all_usernames = []
    try:
        all_usernames = list(app._auth_get_users().keys())
        for _u, _info in app._auth_get_users().items():
            _webhook = (_info.get("slack_webhook_url") or "").strip()
            if _webhook and _u != username:
                per_user_targets.append((_u, _webhook))
    except Exception as e:
        print(f"per-account notification settings fetch failed: {e}", file=sys.stderr)

    # 全登録アカウント分、その日時点の資産評価額をasset_historyシートに記録する
    # （取引記録の日付に関係なく、実行日ベースの本物の資産推移を積み上げるため）。
    # Slack Webhook未登録のアカウントも対象（通知の有無とは無関係に記録する）
    for _u in all_usernames:
        try:
            if app._save_daily_asset_snapshot(_u, today):
                print(f"Saved asset snapshot for {_u}.")
        except Exception as e:
            print(f"asset snapshot failed for {_u}: {e}", file=sys.stderr)

    # ── 全アカウント共通のメッセージを1回だけ生成 ──────────────────
    market_summary_msg = None
    try:
        market_summary_msg = build_market_summary_message(market_ctx, today)
    except Exception as e:
        print(f"market summary failed: {e}", file=sys.stderr)

    eco_msg = None
    try:
        eco_msg = build_eco_calendar_message(today)
    except Exception as e:
        print(f"eco calendar failed: {e}", file=sys.stderr)

    alert_msg = None
    try:
        alert_msg = build_anomaly_alert_message(today)
    except Exception as e:
        print(f"anomaly alert failed: {e}", file=sys.stderr)

    portfolio_msg = None
    try:
        result = generate_portfolio(market_ctx, today, budget, mode, model_type)
        portfolio_msg = build_portfolio_message(
            result, budget, mode_label, today, market_ctx.get("crash_signals")
        )
        # ベンチマーク比較用に、本日の推奨内容を銘柄単位でGoogle Sheetsに記録
        # （手動生成では呼ばない＝1日1回のこの自動配信だけが履歴のソース。アカウント数に
        # 関係なく1日1回だけ記録すればよいのでループの外で行う）
        if result.get("portfolio") and not result.get("error"):
            try:
                if app.record_portfolio_track(today, mode, budget, result["portfolio"]):
                    print("Recorded portfolio_track for benchmark comparison.")
                else:
                    print("record_portfolio_track returned False (sheet unavailable?)", file=sys.stderr)
            except Exception as e:
                print(f"record_portfolio_track failed: {e}", file=sys.stderr)
    except Exception as e:
        print(f"portfolio generation failed: {e}", file=sys.stderr)
        portfolio_msg = f"📊 本日のAI推奨ポートフォリオ（{today}）\n⚠️ 生成に失敗しました: {e}"

    def _send_all(post, holdings_username: str, label_suffix: str = "") -> None:
        if market_summary_msg:
            post(market_summary_msg, f"market summary{label_suffix}")
        send_holdings_messages(holdings_username, mode, market_ctx, today, post)
        if eco_msg:
            post(eco_msg, f"eco calendar{label_suffix}")
        if alert_msg:
            post(alert_msg, f"anomaly alert{label_suffix}")
        if portfolio_msg:
            post(portfolio_msg, f"portfolio{label_suffix}")

    # ── 既定アカウント（TRADING_USERNAME）: 環境変数のLINE/Slackへ配信 ──────
    _send_all(
        lambda text, label: _post_to_channels(line_token, slack_webhook, text, label),
        username,
    )

    # ── 個別Webhookを設定した追加アカウント: それぞれのSlackへ配信 ──────
    for _u, _webhook in per_user_targets:
        print(f"Sending per-account digest for {_u}...")
        _send_all(
            lambda text, label, _w=_webhook: _post_to_channels("", _w, text, label),
            _u,
            label_suffix=f" ({_u})",
        )
        time.sleep(1.0)


if __name__ == "__main__":
    main()
