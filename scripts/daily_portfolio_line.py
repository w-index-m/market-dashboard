#!/usr/bin/env python3
"""
毎朝、3種類のメッセージをLINE（Messaging API・ブロードキャスト配信）に送信するスクリプト:
  1. 市況サマリー（Fear&Greed・VIX・NAAIM・日経/US予測）
  2. AI/半導体株の異常検知（相対弱さ・急落→急回復パターン）
  3. AI推奨ポートフォリオ（新規投資先提案）

GitHub Actionsのcronから `python scripts/daily_portfolio_line.py` として直接実行される想定で、
Streamlitの実行環境（`streamlit run`）は不要。app.py側の各fetch/AI関数はもともとst.*を呼ばない
設計（fetch_*/compute_*レイヤー）なので、モジュールとしてimportして再利用する。
3つのメッセージは独立して送信され、どれか1つが失敗しても他は送られる。

LINE Notify は2025年3月末にサービス終了したため、後継のLINE公式アカウント + Messaging API
（ブロードキャスト配信）を使う。ブロードキャストはその公式アカウントを友だち追加している
全員に届くので、個人利用（自分だけが友だち）を想定している。

必要な環境変数（GitHub Secretsから渡す想定）:
    LINE_CHANNEL_ACCESS_TOKEN  必須。LINE Developersで発行するMessaging APIチャネルアクセストークン。
    GEMINI_API_KEY / GROQ_API_KEY / OPENROUTER_API_KEY  いずれか（AIフォールバックチェーン）
    FMP_API_KEY, FINNHUB_API_KEY, ALPHA_VANTAGE_KEY, TIINGO_API_KEY  任意（市場データ取得に使用）
    PORTFOLIO_BUDGET      任意。予算（円）。デフォルト 1000000
    PORTFOLIO_MODE        任意。growth/momentum/autonomous/ai_mix/optical_mix/dividend_stable。デフォルト growth
    PORTFOLIO_MODEL_TYPE  任意。etf/individual。デフォルト etf
"""
import os
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
# 2) AI/半導体株 異常検知（相対弱さ・急落→急回復）
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
# 3) AI推奨ポートフォリオ
# ─────────────────────────────────────────────
def build_portfolio_message(result: dict, budget: int, mode_label: str, today: str) -> str:
    portfolio = result.get("portfolio") or []
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
    cand_perf = app._fetch_candidate_performance(today)

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
    line_token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
    if not line_token:
        print("LINE_CHANNEL_ACCESS_TOKEN is not set. Aborting.", file=sys.stderr)
        sys.exit(1)

    budget     = int(os.environ.get("PORTFOLIO_BUDGET", "1000000"))
    mode       = os.environ.get("PORTFOLIO_MODE", "growth")
    model_type = os.environ.get("PORTFOLIO_MODEL_TYPE", "etf")
    mode_label = MODE_LABELS.get(mode, mode)

    today = datetime.now(JST).strftime("%Y-%m-%d")

    print("Fetching market context...")
    market_ctx = app._fetch_market_context_for_trading()

    # 1) 市況サマリー
    try:
        _post_to_line(line_token, build_market_summary_message(market_ctx, today), "market summary")
    except Exception as e:
        print(f"market summary failed: {e}", file=sys.stderr)

    # 2) AI/半導体株 異常検知
    try:
        alert_msg = build_anomaly_alert_message(today)
        if alert_msg:
            _post_to_line(line_token, alert_msg, "anomaly alert")
    except Exception as e:
        print(f"anomaly alert failed: {e}", file=sys.stderr)

    # 3) AI推奨ポートフォリオ
    try:
        result = generate_portfolio(market_ctx, today, budget, mode, model_type)
        _post_to_line(line_token, build_portfolio_message(result, budget, mode_label, today), "portfolio")
    except Exception as e:
        print(f"portfolio generation failed: {e}", file=sys.stderr)
        _post_to_line(
            line_token,
            f"📊 本日のAI推奨ポートフォリオ（{today}）\n⚠️ 生成に失敗しました: {e}",
            "portfolio (error)",
        )


if __name__ == "__main__":
    main()
