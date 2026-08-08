#!/usr/bin/env python3
"""
Slack Events API 用の常時稼働Webサーバー。Slackで @market-dashboard へメンションして
株価・決算等を質問すると、app.pyの株価取得・AI分析関数を使って回答する。

GitHub Actionsの日次配信スクリプト（scripts/daily_portfolio_line.py）とは別物・別デプロイ。
あちらは「決まった時刻に一方通行で送るだけ」なのに対し、こちらは「Slackから来た質問に
その場で答える」双方向のやり取りを担当する。GitHub Actionsは常時起動できないため、
Render/Cloud Run/Railway等の常時稼働サービスにこのファイルをデプロイする想定。

セットアップ手順の概要:
  1. Slack Appの「OAuth & Permissions」で Bot Token Scopes に
     app_mentions:read・chat:write を追加してインストール → Bot User OAuth Token(xoxb-...)を取得
  2. Slack Appの「Basic Information」から Signing Secret を取得
  3. このファイルをRender等にデプロイ（例: `gunicorn slack_bot:flask_app`）
  4. デプロイ後のURL（例: https://xxx.onrender.com/slack/events）を、
     Slack Appの「Event Subscriptions」→ Request URL に設定し、app_mention イベントを購読

必要な環境変数:
    SLACK_BOT_TOKEN        必須。xoxb-から始まるBot User OAuth Token
    SLACK_SIGNING_SECRET   必須。Slackからのリクエストであることを検証するための署名鍵
    GEMINI_API_KEY / GROQ_API_KEY / OPENROUTER_API_KEY  いずれか（AIフォールバックチェーン）
    FMP_API_KEY, FINNHUB_API_KEY, ALPHA_VANTAGE_KEY, TIINGO_API_KEY  任意（市場データ取得に使用）
"""
import hashlib
import hmac
import os
import re
import sys
import threading
import time

import requests
from flask import Flask, jsonify, request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app  # noqa: E402  (app.py側でst.set_page_config()をimport時に実行しないようガード済み)

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_SIGNING_SECRET = os.environ.get("SLACK_SIGNING_SECRET", "")

flask_app = Flask(__name__)

# メッセージ本文からティッカーらしき文字列を探す一次候補（英字1〜5字+.T任意、または4桁+.T）
_TICKER_RE = re.compile(r'\b([A-Za-z]{1,5}(?:\.T)?|\d{4}\.T)\b')
_MENTION_RE = re.compile(r'<@[^>]+>')


def _verify_slack_signature(req) -> bool:
    """SlackのSigning Secretを使ってリクエストが正当なものか検証する。
    タイムスタンプが5分以上ずれている場合はリプレイ攻撃防止のため拒否する。
    """
    if not SLACK_SIGNING_SECRET:
        return False
    timestamp = req.headers.get("X-Slack-Request-Timestamp", "")
    try:
        if abs(time.time() - int(timestamp)) > 60 * 5:
            return False
    except ValueError:
        return False
    sig_basestring = f"v0:{timestamp}:{req.get_data(as_text=True)}"
    my_sig = "v0=" + hmac.new(
        SLACK_SIGNING_SECRET.encode(), sig_basestring.encode(), hashlib.sha256
    ).hexdigest()
    slack_sig = req.headers.get("X-Slack-Signature", "")
    return hmac.compare_digest(my_sig, slack_sig)


def _post_to_slack_channel(channel: str, text: str, thread_ts: str | None = None) -> None:
    try:
        payload = {"channel": channel, "text": text}
        if thread_ts:
            payload["thread_ts"] = thread_ts
        resp = requests.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-Type": "application/json"},
            json=payload, timeout=15,
        )
        data = resp.json()
        if not data.get("ok"):
            print(f"Slack post failed: {data}", file=sys.stderr)
    except Exception as e:
        print(f"Slack post error: {e}", file=sys.stderr)


def _extract_ticker(text: str) -> str | None:
    """質問文からティッカーを推定する。まず英字/4桁+.Tのパターンを直接探し、
    見つからなければ全文を_resolve_ticker_query（銘柄名・証券コード解決）に渡す。
    """
    text_clean = _MENTION_RE.sub("", text).strip()
    if not text_clean:
        return None

    m = _TICKER_RE.search(text_clean)
    if m:
        try:
            ticker, _name = app._resolve_ticker_query(m.group(1))
            if ticker:
                return ticker
        except Exception:
            pass

    try:
        ticker, _name = app._resolve_ticker_query(text_clean)
        return ticker or None
    except Exception:
        return None


def _answer_question(channel: str, thread_ts: str, question: str) -> None:
    """バックグラウンドスレッドで実行。Slackの3秒タイムアウトの外で処理してよいのはこのため。"""
    ticker = _extract_ticker(question)
    if not ticker:
        _post_to_slack_channel(
            channel,
            "銘柄が特定できませんでした。ティッカーや証券コードを含めて聞いてください（例: NVDA、7203.T）。",
            thread_ts,
        )
        return

    is_jp = ticker.endswith(".T")
    try:
        data = app._fetch_trading_stock_data(ticker, is_jp)
    except Exception as e:
        _post_to_slack_channel(channel, f"{ticker}のデータ取得中にエラーが発生しました: {e}", thread_ts)
        return

    if not data or data.get("price") is None:
        _post_to_slack_channel(channel, f"{ticker}のデータを取得できませんでした。", thread_ts)
        return

    cur = "円" if is_jp else "USD"
    news_lines = "\n".join(f"- {n}" for n in (data.get("news") or [])[:5]) or "(直近ニュースなし)"
    prompt = f"""あなたは株式アナリストです。以下のデータだけを根拠に、質問に日本語で簡潔に答えてください（200字程度）。
データに無い情報は「データなし」と答え、憶測で数値を作らないこと。

【{ticker}のデータ】
現在値: {data.get('price')}{cur}
RSI(14): {data.get('rsi')}
MA25: {data.get('ma25')}{cur}
1日リターン: {data.get('ret_1d')}%　5日リターン: {data.get('ret_5d')}%　20日リターン: {data.get('ret_20d')}%
出来高比率(20日平均比): {data.get('vol_ratio')}
直近ニュース:
{news_lines}

【質問】
{question}"""
    try:
        answer, _model = app._call_ai_for_trading(prompt, max_output_tokens=400, temperature=0.3)
    except Exception as e:
        answer = f"AI応答生成に失敗しました: {e}"
    _post_to_slack_channel(channel, answer, thread_ts)


_processed_event_ids: set = set()  # 二重応答防止用（プロセス内メモリのみ。再起動でリセットされる）
_MAX_PROCESSED_EVENTS = 500


@flask_app.route("/slack/events", methods=["POST"])
def slack_events():
    if not _verify_slack_signature(request):
        return jsonify({"error": "invalid signature"}), 401

    payload = request.get_json(silent=True) or {}

    # Slackの初回URL検証チャレンジ（Request URL登録時に一度だけ来る）
    if payload.get("type") == "url_verification":
        return jsonify({"challenge": payload.get("challenge", "")})

    event    = payload.get("event", {})
    event_id = payload.get("event_id", "")

    # Slackは3秒以内に200が返らないと再送してくる。Renderの無料プランはアイドル後スリープし、
    # 初回リクエストがスリープからの復帰待ちでタイムアウトすることがある。その場合、再送が
    # 「このイベントが実際に処理される最初の機会」になるため、単純に再送を無視すると
    # メンションが永久に無応答になる。event_id単位で重複排除することで、初回が成功していた
    # 場合の二重応答は防ぎつつ、初回が失敗していた場合は再送で正しく処理する。
    if event_id:
        if event_id in _processed_event_ids:
            return "", 200
        _processed_event_ids.add(event_id)
        if len(_processed_event_ids) > _MAX_PROCESSED_EVENTS:
            _processed_event_ids.pop()

    if event.get("type") == "app_mention" and not event.get("bot_id"):
        channel = event.get("channel", "")
        thread_ts = event.get("thread_ts") or event.get("ts", "")
        text = event.get("text", "")
        # 実際の処理は3秒制限の外（バックグラウンドスレッド）で行い、先に200を返す
        threading.Thread(
            target=_answer_question, args=(channel, thread_ts, text), daemon=True,
        ).start()

    return "", 200


@flask_app.route("/", methods=["GET"])
def health_check():
    return "ok", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host="0.0.0.0", port=port)
