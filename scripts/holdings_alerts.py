#!/usr/bin/env python3
"""
保有銘柄のアラート通知（売買はしない。条件に当てはまった時だけLINE/Slackへ知らせる）。

取引記録（Google Sheets）から現在の保有銘柄を計算し、直近の終値データで次を判定する:
  1. 当日の急落/急騰   : 前日比が ALERT_DAY_DOWN% 以下 / ALERT_DAY_UP% 以上
  2. 損切りライン      : 平均取得単価に対する損益率が ALERT_STOP_LOSS% を「新たに」下回った
  3. 利益確認ライン    : 同 ALERT_TAKE_PROFIT% を「新たに」上回った
  4. 高値からの下落    : 直近60営業日の高値からの下落率が ALERT_DRAWDOWN% を「新たに」下回った
  5. 市場の警戒（USの回のみ）: VIX が ALERT_VIX 以上に「新たに」上昇した
「新たに」= 前営業日の終値では未達で、最新終値で達した場合のみ。状態を保存せずに、
毎日同じ警告を繰り返さないための判定（前日終値でも達していれば通知しない）。
条件に当てはまるものが無い日は何も送らない（Slack通知の回数を増やさないため）。

実行は市場ごとに分ける（日本株は引け後、米国株は引け後）:
    python scripts/holdings_alerts.py jp|us|all
投資信託は日次の終値が取れないため対象外。保有データはリポジトリに保存せず、実行時に
Google Sheetsから読むだけ（公開リポジトリのため）。

環境変数: LINE_CHANNEL_ACCESS_TOKEN / SLACK_WEBHOOK_URL（少なくとも1つ）、
GOOGLE_SHEETS_ID / GOOGLE_SERVICE_ACCOUNT_JSON、TRADING_USERNAME（既定 admin）、
ALERT_DAY_DOWN(-8) ALERT_DAY_UP(12) ALERT_STOP_LOSS(-15) ALERT_TAKE_PROFIT(100)
ALERT_DRAWDOWN(-15) ALERT_VIX(30)、ALERT_DRY_RUN=1 で送信せずに標準出力へ表示。
"""
import os
import sys
from datetime import datetime

import pandas as pd
import pytz
import requests

JST = pytz.timezone("Asia/Tokyo")
LINE_BROADCAST_URL = "https://api.line.me/v2/bot/message/broadcast"
LINE_TEXT_MAX_LEN = 4900


def _cfg() -> dict:
    def f(name, default):
        try:
            return float(os.environ.get(name, default))
        except ValueError:
            return float(default)
    return {
        "day_down": f("ALERT_DAY_DOWN", -8), "day_up": f("ALERT_DAY_UP", 12),
        "stop_loss": f("ALERT_STOP_LOSS", -15), "take_profit": f("ALERT_TAKE_PROFIT", 100),
        "drawdown": f("ALERT_DRAWDOWN", -15), "vix": f("ALERT_VIX", 30),
    }


def evaluate(positions: dict, closes: dict, names: dict, cfg: dict) -> list:
    """保有銘柄ごとにアラート文（日本語1行）を返す。closes: {ticker: 終値のpd.Series}。"""
    out = []
    for tk, pos in positions.items():
        s = closes.get(tk)
        if s is None:
            continue
        s = s.dropna()
        if len(s) < 3:
            continue
        cur, prev = float(s.iloc[-1]), float(s.iloc[-2])
        if prev <= 0 or cur <= 0:
            continue
        nm = f"{names.get(tk, tk)}（{tk}）"
        day = (cur / prev - 1) * 100
        if day <= cfg["day_down"]:
            out.append(f"📉 {nm} 本日 {day:+.1f}%（急落）")
        elif day >= cfg["day_up"]:
            out.append(f"📈 {nm} 本日 {day:+.1f}%（急騰）")

        avg = float(pos.get("avg_cost") or 0)
        if avg > 0:
            pnl_now, pnl_prev = (cur / avg - 1) * 100, (prev / avg - 1) * 100
            if pnl_now <= cfg["stop_loss"] < pnl_prev:
                out.append(f"🛑 {nm} 取得単価比 {pnl_now:+.1f}%（損切りライン {cfg['stop_loss']:.0f}% を下回りました）")
            if pnl_now >= cfg["take_profit"] > pnl_prev:
                out.append(f"💰 {nm} 取得単価比 {pnl_now:+.1f}%（利益確認ライン +{cfg['take_profit']:.0f}% に到達）")

        win = s.tail(61)
        if len(win) >= 20:
            dd_now = (cur / float(win.max()) - 1) * 100
            win_prev = win.iloc[:-1]
            dd_prev = (prev / float(win_prev.max()) - 1) * 100
            if dd_now <= cfg["drawdown"] < dd_prev:
                out.append(f"⚠️ {nm} 直近60日高値から {dd_now:.1f}%（{cfg['drawdown']:.0f}% を下回りました）")
    return out


def _post(line_token: str, slack_webhook: str, text: str) -> None:
    if line_token:
        try:
            r = requests.post(LINE_BROADCAST_URL,
                              headers={"Authorization": f"Bearer {line_token}", "Content-Type": "application/json"},
                              json={"messages": [{"type": "text", "text": text[:LINE_TEXT_MAX_LEN]}]}, timeout=15)
            r.raise_for_status()
            print("Posted to LINE")
        except Exception as e:
            print(f"Failed to post to LINE: {e}", file=sys.stderr)
    if slack_webhook:
        try:
            r = requests.post(slack_webhook, json={"text": text}, timeout=15)
            r.raise_for_status()
            print("Posted to Slack")
        except Exception as e:
            print(f"Failed to post to Slack: {e}", file=sys.stderr)


def main() -> None:
    import yfinance as yf

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import app  # noqa: E402

    market = (sys.argv[1] if len(sys.argv) > 1 else "all").lower()
    username = os.environ.get("TRADING_USERNAME", "admin")
    cfg = _cfg()
    dry = os.environ.get("ALERT_DRY_RUN") == "1"
    line_token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
    slack_webhook = os.environ.get("SLACK_WEBHOOK_URL", "")
    if not dry and not (line_token or slack_webhook):
        print("LINE_CHANNEL_ACCESS_TOKEN / SLACK_WEBHOOK_URL のどちらも未設定", file=sys.stderr)
        sys.exit(1)

    trades_df, err = app._load_trades(username)
    if trades_df.empty:
        print(f"取引記録なし: {err}")
        return
    positions = app._calc_positions_from_df(trades_df)
    positions = {t: p for t, p in positions.items() if t not in app._JP_FUND_MAP}
    if market == "jp":
        positions = {t: p for t, p in positions.items() if t.endswith(".T")}
    elif market == "us":
        positions = {t: p for t, p in positions.items() if not t.endswith(".T")}
    names = {t: app._KNOWN_NAMES.get(t) or p.get("name", t) or t for t, p in positions.items()}

    closes: dict = {}
    tickers = list(positions)
    if market in ("us", "all"):
        tickers.append("^VIX")
    if tickers:
        raw = yf.download(tickers, period="6mo", auto_adjust=True, progress=False, group_by="column")
        close = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw
        for t in tickers:
            if isinstance(close, pd.Series):
                closes[t] = close
            elif t in close.columns:
                closes[t] = close[t]
    alerts = evaluate(positions, closes, names, cfg)

    if market in ("us", "all") and "^VIX" in closes:
        v = closes["^VIX"].dropna()
        if len(v) >= 2 and float(v.iloc[-1]) >= cfg["vix"] > float(v.iloc[-2]):
            alerts.append(f"🌪️ VIX {float(v.iloc[-1]):.1f}（{cfg['vix']:.0f} を超えました。市場が警戒モードです）")

    if not alerts:
        print(f"アラートなし（{market}、保有{len(positions)}銘柄を確認）")
        return
    label = {"jp": "日本株", "us": "米国株"}.get(market, "全保有")
    text = f"🔔 保有銘柄アラート（{label}・{datetime.now(JST).strftime('%Y-%m-%d %H:%M')} JST）\n\n" + "\n".join(alerts)
    text += "\n\n※ 売買は自動では行いません。判断の材料としてお使いください。"
    if dry:
        print(text)
        return
    _post(line_token, slack_webhook, text)


if __name__ == "__main__":
    main()
