#!/usr/bin/env python3
"""
毎朝、AI推奨ポートフォリオ（新規投資先提案）を生成してSlackに送信するスクリプト。

GitHub Actionsのcronから `python scripts/daily_portfolio_slack.py` として直接実行される
想定で、Streamlitの実行環境（`streamlit run`）は不要。app.py側の各fetch/AI関数はもともと
st.*を呼ばない設計（fetch_*/compute_*レイヤー）なので、モジュールとしてimportして再利用する。

必要な環境変数（GitHub Secretsから渡す想定）:
    SLACK_WEBHOOK_URL   必須。SlackのIncoming Webhook URL。
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

MODE_LABELS = {
    "growth": "🌱長期育成", "momentum": "⚡モメンタム", "autonomous": "🤖AI自律",
    "ai_mix": "✨AIミックス", "optical_mix": "💡光銘柄ミックス", "dividend_stable": "💰配当安定",
}


def _fmt_yen(n) -> str:
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return str(n)


def build_slack_message(result: dict, budget: int, mode_label: str, today: str) -> dict:
    portfolio = result.get("portfolio") or []
    metrics = result.get("metrics") or {}
    error = result.get("error")

    if error or not portfolio:
        text = (
            f"*📊 本日のAI推奨ポートフォリオ（{today}）*\n"
            f"⚠️ 生成に失敗しました: {error or '不明なエラー'}"
        )
        return {"text": text}

    lines = [f"*📊 本日のAI推奨ポートフォリオ（{today}・{mode_label}・予算{_fmt_yen(budget)}円）*", ""]

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
        lines.append(f"• *{flag} {ticker}* {name} — {alloc}%（{_fmt_yen(amount)}円）")
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
            lines.append(f"_{metrics['comment']}_")

    lines.append("")
    lines.append("⚠️ 情報提供目的のみ・投資助言ではありません。")

    return {"text": "\n".join(lines)}


def main() -> None:
    slack_url = os.environ.get("SLACK_WEBHOOK_URL", "")
    if not slack_url:
        print("SLACK_WEBHOOK_URL is not set. Aborting.", file=sys.stderr)
        sys.exit(1)

    budget     = int(os.environ.get("PORTFOLIO_BUDGET", "1000000"))
    mode       = os.environ.get("PORTFOLIO_MODE", "growth")
    model_type = os.environ.get("PORTFOLIO_MODEL_TYPE", "etf")
    mode_label = MODE_LABELS.get(mode, mode)

    today = datetime.now(JST).strftime("%Y-%m-%d")

    print("Fetching market context...")
    market_ctx = app._fetch_market_context_for_trading()

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
    result = app._generate_investment_portfolio_rec(
        budget, model_type, "balanced", market_ctx, [], "auto",
        trading_mode=mode, candidate_perf=cand_perf, agent_a=agent_a, agent_b=agent_b,
    )

    message = build_slack_message(result, budget, mode_label, today)
    resp = requests.post(slack_url, json=message, timeout=15)
    resp.raise_for_status()
    print(f"Posted to Slack. status={resp.status_code}")


if __name__ == "__main__":
    main()
