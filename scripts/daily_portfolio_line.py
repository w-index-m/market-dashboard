#!/usr/bin/env python3
"""
毎朝、最大6種類のメッセージをLINE（Messaging API・ブロードキャスト配信）とSlack
（Incoming Webhook）の両方に送信するスクリプト:
  1. 市況サマリー（Fear&Greed・VIX・NAAIM・日経/US予測）
  2. 保有銘柄アクション判定（買い増し/保有継続/一部利確/売却をAIが銘柄ごとに判定）
  2b. 保有銘柄 関連ニュース見出し（2のためにfetch済みのデータを再利用・追加API呼び出しなし）
  3. 経済指標チェック（直近発表結果の前回比、本日の発表予定。FMP→BLS→FREDの既存データを再利用）
  4. AI/半導体株の異常検知（相対弱さ・急落→急回復パターン）
  5. AI推奨ポートフォリオ（新規投資先提案）

新規投資の提案だけだと「ポートフォリオの入れ替え」ができない（売る判断が無い）ため、
既存保有銘柄の売買判定（2）を組み込んでいる。既存のインタラクティブUIが使っている
_generate_full_portfolio_recommendation()をそのまま再利用する。保有銘柄が無い/取引記録が
無い場合、2と2bは送信されない（正常系として静かにスキップ）。

GitHub Actionsのcronから `python scripts/daily_portfolio_line.py` として直接実行される想定で、
Streamlitの実行環境（`streamlit run`）は不要。app.py側の各fetch/AI関数はもともとst.*を呼ばない
設計（fetch_*/compute_*レイヤー）なので、モジュールとしてimportして再利用する。
各メッセージは独立して送信され、どれか1つが失敗しても他は送られる。

配信先は環境変数で設定されている方だけ動く（両方設定すれば両方に届く。片方だけでもOK）。
LINE Notify は2025年3月末にサービス終了したため、後継のLINE公式アカウント + Messaging API
（ブロードキャスト配信）を使う。ブロードキャストはその公式アカウントを友だち追加している
全員に届くので、個人利用（自分だけが友だち）を想定している。

必要な環境変数（GitHub Secretsから渡す想定）:
    LINE_CHANNEL_ACCESS_TOKEN  任意。LINE Developersで発行するMessaging APIチャネルアクセストークン。
    SLACK_WEBHOOK_URL          任意。SlackのIncoming Webhook URL。
    ※ LINE_CHANNEL_ACCESS_TOKEN / SLACK_WEBHOOK_URL の少なくとも1つは必須。
    GEMINI_API_KEY / GROQ_API_KEY / OPENROUTER_API_KEY  いずれか（AIフォールバックチェーン）
    FMP_API_KEY, FINNHUB_API_KEY, ALPHA_VANTAGE_KEY, TIINGO_API_KEY  任意（市場データ取得に使用）
    GOOGLE_SHEETS_ID, GOOGLE_SERVICE_ACCOUNT_JSON  取引記録読込・推奨履歴記録に必要
    TRADING_USERNAME      任意。取引記録シートのユーザー名（"admin"ならclaude_tradesタブ）。デフォルト admin
    PORTFOLIO_BUDGET      任意。予算（円）。デフォルト 1000000
    PORTFOLIO_MODE        任意。growth/momentum/autonomous/ai_mix/optical_mix/dividend_stable/stable_growth。デフォルト growth
    PORTFOLIO_MODEL_TYPE  任意。etf/individual。デフォルト etf
"""
import os
import re
import sys
from datetime import datetime

import pytz
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402  (app.py側でst.set_page_config()をimport時に実行しないようガード済み)

JST = pytz.timezone("Asia/Tokyo")

LINE_BROADCAST_URL = "https://api.line.me/v2/bot/message/broadcast"
LINE_TEXT_MAX_LEN = 4900  # LINEのtextメッセージ上限は5000文字。安全マージンを取る。

MODE_LABELS = {
    "growth": "🌱長期育成", "momentum": "⚡モメンタム", "autonomous": "🤖AI自律",
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

    ticker_names = {_t: _p.get("name", _t) for _t, _p in positions.items()}

    # _calc_positions_from_df は market_value を持たないため、時価を取得して付与する
    # （付与しないと評価額・含み損益・配分%が常に0/フル損扱いになってしまう）
    prices = app._fetch_portfolio_prices(tuple(positions.keys()))
    positions = {
        _t: {**_p, "market_value": _p.get("qty", 0) * prices.get(_t, {}).get("price", 0)}
        for _t, _p in positions.items()
    }

    stock_data_map = {}
    for ticker in positions:
        try:
            stock_data_map[ticker] = app._fetch_trading_stock_data(ticker, ticker.endswith(".T"))
        except Exception as e:
            print(f"stock data fetch failed for {ticker}: {e}", file=sys.stderr)
            stock_data_map[ticker] = {}

    ai_result = app._generate_full_portfolio_recommendation(
        positions, stock_data_map, market_ctx, mode=mode, model_pref=model_pref,
    )
    return ai_result, stock_data_map, ticker_names


def build_holdings_action_message(result: dict, today: str, ticker_names: dict | None = None) -> str | None:
    error = result.get("error")
    text = result.get("text", "")

    if error:
        # 保有銘柄が無い/取引記録が無いのは正常系なので、エラーとして送らずスキップする
        if "保有銘柄がありません" in error or "取引記録がありません" in error:
            return None
        return f"📋 保有銘柄アクション判定（{today}）\n⚠️ 取得に失敗しました: {error}"

    # AIが見出し（### TICKER）に銘柄名を書き忘れることがあるため、取引記録上の名前を補う
    # （プロンプトでは「銘柄名（ティッカー）」を指示しているが、従わないことがある保険）
    if ticker_names and text:
        for ticker, name in ticker_names.items():
            if not name or name == ticker:
                continue
            text = re.sub(
                rf'(?m)^(#{{1,6}}\s*){re.escape(ticker)}\s*$',
                rf'\g<1>{name}（{ticker}）',
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


def build_holdings_news_message(stock_data_map: dict, today: str) -> str | None:
    """
    保有銘柄ごとの直近ニュース見出し一覧。generate_holdings_actionで既に取得済みの
    stock_data_map（_fetch_trading_stock_dataの戻り値）を再利用するので、追加のAPI
    呼び出しは発生しない。ポジティブ/ネガティブの判定はここでは行わない
    （見出しの機械的な感情分析は誤判定のリスクがあるため）— そちらは「📋 保有銘柄
    アクション判定」内のAIによるIR・ニュース評価を参照する前提。
    """
    lines = [f"📰 保有銘柄 関連ニュース（{today}）", ""]
    has_content = False
    for ticker, data in stock_data_map.items():
        items = (data or {}).get("news_items") or []
        if not items:
            continue
        has_content = True
        lines.append(f"◆ {ticker}")
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

    lines.append("※ 見出しの一覧です。ポジティブ/ネガティブの評価は「📋 保有銘柄アクション判定」内のIR・ニュース評価を参照してください。")
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
def build_portfolio_message(result: dict, budget: int, mode_label: str, today: str) -> str:
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
        lines.append(f"⚠️ クラッシュ前兆シグナル検出（{crash_label}） → キャッシュ{cash_pct}%保持を推奨")
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
            lines.append(f"   エントリー目安: {entry}　{entry_note}")
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
    agent_b = app._run_stock_agents_parallel(
        top_args, today, agent_a.get("stance", "中立"), max_workers=3,
    )

    print("Running Agent C (portfolio assembly)...")
    return app._generate_investment_portfolio_rec(
        budget, model_type, "balanced", market_ctx, [], "auto",
        trading_mode=mode, candidate_perf=cand_perf, agent_a=agent_a, agent_b=agent_b,
    )


def main() -> None:
    line_token    = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
    slack_webhook = os.environ.get("SLACK_WEBHOOK_URL", "")
    if not line_token and not slack_webhook:
        print("Neither LINE_CHANNEL_ACCESS_TOKEN nor SLACK_WEBHOOK_URL is set. Aborting.", file=sys.stderr)
        sys.exit(1)

    def _post(text, label):
        _post_to_channels(line_token, slack_webhook, text, label)

    budget     = int(os.environ.get("PORTFOLIO_BUDGET", "1000000"))
    mode       = os.environ.get("PORTFOLIO_MODE", "growth")
    model_type = os.environ.get("PORTFOLIO_MODEL_TYPE", "etf")
    mode_label = MODE_LABELS.get(mode, mode)
    username   = os.environ.get("TRADING_USERNAME", "admin")

    today = datetime.now(JST).strftime("%Y-%m-%d")

    print("Fetching market context...")
    market_ctx = app._fetch_market_context_for_trading()

    # 1) 市況サマリー
    try:
        _post(build_market_summary_message(market_ctx, today), "market summary")
    except Exception as e:
        print(f"market summary failed: {e}", file=sys.stderr)

    # 2) 保有銘柄アクション判定（買い増し/保有継続/一部利確/売却）＋ 関連ニュース見出し
    try:
        holdings_result, holdings_stock_data, holdings_ticker_names = generate_holdings_action(
            market_ctx, mode, "auto", username
        )
        holdings_msg = build_holdings_action_message(holdings_result, today, holdings_ticker_names)
        if holdings_msg:
            _post(holdings_msg, "holdings action")
        news_msg = build_holdings_news_message(holdings_stock_data, today)
        if news_msg:
            _post(news_msg, "holdings news")
    except Exception as e:
        print(f"holdings action failed: {e}", file=sys.stderr)

    # 3) 経済指標チェック（直近の発表結果・前回比、本日の発表予定）
    try:
        eco_msg = build_eco_calendar_message(today)
        if eco_msg:
            _post(eco_msg, "eco calendar")
    except Exception as e:
        print(f"eco calendar failed: {e}", file=sys.stderr)

    # 4) AI/半導体株 異常検知
    try:
        alert_msg = build_anomaly_alert_message(today)
        if alert_msg:
            _post(alert_msg, "anomaly alert")
    except Exception as e:
        print(f"anomaly alert failed: {e}", file=sys.stderr)

    # 5) AI推奨ポートフォリオ
    try:
        result = generate_portfolio(market_ctx, today, budget, mode, model_type)
        _post(build_portfolio_message(result, budget, mode_label, today), "portfolio")
        # ベンチマーク比較用に、本日の推奨内容を銘柄単位でGoogle Sheetsに記録
        # （手動生成では呼ばない＝1日1回のこの自動配信だけが履歴のソース）
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
        _post(
            f"📊 本日のAI推奨ポートフォリオ（{today}）\n⚠️ 生成に失敗しました: {e}",
            "portfolio (error)",
        )


if __name__ == "__main__":
    main()
