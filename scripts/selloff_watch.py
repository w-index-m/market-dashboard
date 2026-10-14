#!/usr/bin/env python3
"""
急落の広がりを毎営業日に記録し、大きな動きがあった日だけLINE・Slackへ知らせる（売買はしない）。

・グループ別（光通信 / AIインフラ / 半導体大型 / メモリ / AIの資金繰り）の前日比の中央値、S&P500・ナスダック100・SMH、
  米10年債利回りとVIXを data/selloff_log.json に1日1行で追記する（公開データのみ。保有銘柄は一切含めない）。
・判定ルールは app.py の _breadth_verdict と同じ（変更するときは両方を揃えること）。
・通知条件（どれか）: 光通信の中央値 ≤ -3% / SMH ≤ -3% / S&P500 ≤ -1.5% / 値動き±8%以上かつ出来高1.8倍以上の銘柄がある。
  条件に当てはまらない日は通知しない。
環境変数: LINE_CHANNEL_ACCESS_TOKEN / SLACK_WEBHOOK_URL、SELLOFF_DRY_RUN=1 で送信せず標準出力に表示。
"""
import json
import os
import sys
from datetime import datetime, timezone

import pandas as pd
import requests
import yfinance as yf

OUT = os.path.join(os.path.dirname(__file__), "..", "data", "selloff_log.json")
GROUPS = {
    "光通信": ["COHR", "LITE", "GLW", "AAOI", "CIEN", "VIAV"],
    "AIインフラ（電源・接続）": ["VRT", "APH", "ANET", "CRDO", "ALAB"],
    "半導体（大型）": ["NVDA", "AMD", "AVGO", "QCOM", "INTC", "AMAT", "TSM"],
    "メモリ・ストレージ": ["MU", "SNDK", "WDC", "STX"],
    "AIの資金繰り（ネオクラウド等）": ["CRWV", "NBIS", "IREN", "ORCL"],
}
INDEXES = {"SPY": "S&P500", "QQQ": "ナスダック100", "SMH": "半導体ETF"}
MAX_ENTRIES = 600


def verdict(grp: dict, idx: dict) -> str:
    spy, smh = idx.get("SPY"), idx.get("SMH")
    weak = [g for g, v in grp.items() if v is not None and v <= -2.0]
    if spy is not None and spy <= -1.5:
        return "市場全体のリスク回避"
    if smh is not None and smh <= -2.5:
        return "半導体全体に波及"
    if len(weak) >= 2:
        return "AI関連テーマに広く波及"
    if len(weak) == 1:
        return "一部のテーマに限定"
    return "目立った広がりなし"


def _post(text: str) -> None:
    line, slack = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", ""), os.environ.get("SLACK_WEBHOOK_URL", "")
    if os.environ.get("SELLOFF_DRY_RUN"):
        print("[DRY RUN]\n" + text)
        return
    if line:
        try:
            requests.post("https://api.line.me/v2/bot/message/broadcast",
                          headers={"Authorization": f"Bearer {line}", "Content-Type": "application/json"},
                          json={"messages": [{"type": "text", "text": text[:4900]}]}, timeout=15).raise_for_status()
        except Exception as e:
            print(f"LINE failed: {e}", file=sys.stderr)
    if slack:
        try:
            requests.post(slack, json={"text": text}, timeout=15).raise_for_status()
        except Exception as e:
            print(f"Slack failed: {e}", file=sys.stderr)


def main() -> None:
    syms = sorted({s for v in GROUPS.values() for s in v} | set(INDEXES) | {"^TNX", "^VIX"})
    raw = yf.download(syms, period="2mo", interval="1d", auto_adjust=False, progress=False, group_by="column")
    close = raw["Close"].dropna(how="all")
    vol = raw["Volume"]
    if len(close) < 2:
        print("no data")
        return
    date = str(close.index[-1].date())
    rets, vr = {}, {}
    for s in syms:
        if s in close.columns:
            c = close[s].dropna()
            if len(c) >= 2:
                rets[s] = float(c.iloc[-1] / c.iloc[-2] - 1) * 100
        if s in vol.columns:
            v = vol[s].dropna()
            if len(v) >= 21 and float(v.iloc[-21:-1].mean()) > 0:
                vr[s] = float(v.iloc[-1] / v.iloc[-21:-1].mean())
    grp = {}
    for g, ss in GROUPS.items():
        vals = [rets[x] for x in ss if x in rets]
        if vals:
            grp[g] = round(float(pd.Series(vals).median()), 2)
    idx = {k: round(rets[k], 2) for k in INDEXES if k in rets}
    tnx = close["^TNX"].dropna() if "^TNX" in close.columns else pd.Series(dtype=float)
    vix = close["^VIX"].dropna() if "^VIX" in close.columns else pd.Series(dtype=float)
    entry = {
        "date": date, "groups": grp, "indexes": idx,
        "tnx": round(float(tnx.iloc[-1]), 3) if len(tnx) else None,
        "tnx_bp": round(float(tnx.iloc[-1] - tnx.iloc[-2]) * 100, 1) if len(tnx) >= 2 else None,
        "vix": round(float(vix.iloc[-1]), 2) if len(vix) else None,
        "verdict": verdict(grp, idx),
    }
    movers = [{"t": s, "ret": round(rets[s], 1), "vol": round(vr.get(s, 0), 1)}
              for g in GROUPS.values() for s in g if s in rets and abs(rets[s]) >= 8 and vr.get(s, 0) >= 1.8]
    if movers:
        entry["movers"] = movers

    try:
        with open(OUT, encoding="utf-8") as f:
            log = json.load(f)
    except (OSError, ValueError):
        log = {"entries": []}
    entries = [e for e in log.get("entries", []) if e.get("date") != date] + [entry]
    entries.sort(key=lambda e: e["date"])
    log = {"updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "entries": entries[-MAX_ENTRIES:]}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=1)
    print(f"logged {date}: {entry['verdict']}")

    opt, smh, spy = grp.get("光通信"), idx.get("SMH"), idx.get("SPY")
    reasons = []
    if opt is not None and opt <= -3:
        reasons.append(f"光通信 {opt:+.1f}%")
    if smh is not None and smh <= -3:
        reasons.append(f"半導体ETF {smh:+.1f}%")
    if spy is not None and spy <= -1.5:
        reasons.append(f"S&P500 {spy:+.1f}%")
    if movers:
        reasons.append("大きな値動き＋出来高急増: " + "、".join(f"{m['t']}{m['ret']:+.1f}%({m['vol']:.1f}倍)" for m in movers[:5]))
    if reasons:
        lines = [f"🔎 急落の広がり（{date}）: {entry['verdict']}", "・" + " / ".join(reasons)]
        lines.append("・グループ別: " + "、".join(f"{g}{v:+.1f}%" for g, v in grp.items()))
        if entry["tnx"] is not None:
            lines.append(f"・米10年債 {entry['tnx']:.2f}%（{entry['tnx_bp']:+.0f}bp）／VIX {entry['vix']}")
        lines.append("※ 数値は前営業日の終値ベース。原因は断定できません。詳しくはダッシュボードで。")
        _post("\n".join(lines))
    else:
        print("no alert")


if __name__ == "__main__":
    main()
