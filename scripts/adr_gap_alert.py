#!/usr/bin/env python3
"""
日本株の夜間価格（米国ADRの円換算）が、東証の終値から大きくずれた銘柄と、日経225先物の夜間の大きな動きを、LINE・Slackへ知らせる（売買はしない）。

・対象は、公開の主要銘柄（app.py の _JP_ADR_PAIRS と同じ。保有銘柄ではない）。ADR1枚あたりの株数は、直近の価格から自動で推定する。
・通知条件（どれか）: ADR円換算と東証終値の差が ±ADR_GAP_PCT%（既定3）以上 / 日経先物の夜間の動きが ±FUT_GAP_PCT%（既定1.5）以上。
  当てはまらない日は何も送らない。
環境変数: LINE_CHANNEL_ACCESS_TOKEN / SLACK_WEBHOOK_URL（無ければ標準出力に表示）、ADR_GAP_PCT、FUT_GAP_PCT。
"""
import os
import sys

import numpy as np
import pandas as pd
import requests
import yfinance as yf

PAIRS = {
    "5803.T": ("フジクラ", "FJIKY"), "5801.T": ("古河電工", "FUWAY"), "5802.T": ("住友電工", "SMTOY"),
    "9984.T": ("ソフトバンクG", "SFTBY"), "8035.T": ("東京エレクトロン", "TOELF"), "6857.T": ("アドバンテスト", "ATEYY"),
    "285A.T": ("キオクシア", "KXIAY"), "7203.T": ("トヨタ", "TM"), "6758.T": ("ソニーG", "SONY"), "6501.T": ("日立", "HTHIY"),
    "9983.T": ("ファストリ", "FRCOY"), "7974.T": ("任天堂", "NTDOY"), "8306.T": ("三菱UFJ", "MUFG"), "6861.T": ("キーエンス", "KYCCF"),
}
SNAP = (0.05, 0.1, 0.2, 0.25, 0.5, 1, 2, 4, 5, 10, 20, 50)


def _post(text: str) -> None:
    line, slack = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", ""), os.environ.get("SLACK_WEBHOOK_URL", "")
    if not line and not slack:
        print("[通知先なし。内容を表示]\n" + text)
        return
    if line:
        try:
            requests.post("https://api.line.me/v2/bot/message/broadcast", headers={"Authorization": f"Bearer {line}", "Content-Type": "application/json"},
                          json={"messages": [{"type": "text", "text": text[:4900]}]}, timeout=15).raise_for_status()
        except Exception as e:
            print(f"LINE failed: {e}", file=sys.stderr)
    if slack:
        try:
            requests.post(slack, json={"text": text}, timeout=15).raise_for_status()
        except Exception as e:
            print(f"Slack failed: {e}", file=sys.stderr)


def gaps(close: pd.DataFrame) -> list:
    fx = close["JPY=X"].dropna()
    rows = []
    for code, (name, adr) in PAIRS.items():
        if code not in close.columns or adr not in close.columns:
            continue
        t, a = close[code].dropna(), close[adr].dropna()
        j = pd.concat([t.rename("t"), a.rename("a"), fx.rename("fx")], axis=1, sort=True).dropna()
        if len(j) < 15 or t.empty or a.empty:
            continue
        ratio = float((j["t"] / (j["a"] * j["fx"])).median())
        snap = min(SNAP, key=lambda x: abs(np.log(x / ratio)))
        if abs(np.log(snap / ratio)) > 0.12:
            continue
        adr_yen = float(a.iloc[-1]) * float(fx.iloc[-1]) * snap
        tse = float(t.iloc[-1])
        rows.append((name, code, tse, adr_yen, (adr_yen / tse - 1) * 100, str(a.index[-1].date())))
    return rows


def fut_move() -> tuple | None:
    f = yf.download("NIY=F", period="5d", interval="15m", auto_adjust=False, progress=False)
    fc = f["Close"]
    fc = (fc["NIY=F"] if isinstance(fc, pd.DataFrame) else fc).dropna()
    fc.index = fc.index.tz_convert("Asia/Tokyo")
    cand = fc[(fc.index.hour == 15) & (fc.index.minute >= 15) & (fc.index.minute <= 30) & (fc.index < fc.index[-1])]
    if not len(cand):
        return None
    ref, last = float(cand.iloc[-1]), float(fc.iloc[-1])
    return last, ref, (last / ref - 1) * 100, fc.index[-1].strftime("%m/%d %H:%M")


def main() -> None:
    gap_th, fut_th = float(os.environ.get("ADR_GAP_PCT", 3)), float(os.environ.get("FUT_GAP_PCT", 1.5))
    syms = list(PAIRS) + [v[1] for v in PAIRS.values()] + ["JPY=X"]
    raw = yf.download(syms, period="3mo", interval="1d", auto_adjust=False, progress=False, group_by="column")
    close = raw["Close"].dropna(how="all")
    rows = gaps(close)
    big = sorted([r for r in rows if abs(r[4]) >= gap_th], key=lambda r: -abs(r[4]))
    try:
        fm = fut_move()
    except Exception as e:
        fm = None
        print(f"futures failed: {type(e).__name__}")
    print("rows:", len(rows), "big:", len(big), "fut:", fm)
    lines = []
    if big:
        lines.append(f"🌙 日本株ADRの円換算が、東証終値から{gap_th:.0f}%以上ずれた銘柄")
        for name, code, tse, adr_yen, d, dt in big[:8]:
            lines.append(f"・{name}（{code}） 東証{tse:,.0f}円 → ADR換算{adr_yen:,.0f}円（{d:+.1f}%、米国{dt}）")
    if fm and abs(fm[2]) >= fut_th:
        lines.append(f"📊 日経225先物（夜間）: {fm[0]:,.0f}円、東証引け({fm[1]:,.0f}円)から{fm[2]:+.2f}%（{fm[3]}）")
    if lines:
        lines.append("※ ADRは取引が薄い銘柄もあり、為替と時差でずれます。翌朝の東証の動きの目安です。売買の指示ではありません。")
        _post("\n".join(lines))
    else:
        print("no alert")


if __name__ == "__main__":
    main()
