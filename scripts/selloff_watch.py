#!/usr/bin/env python3
"""
急落の広がりを毎営業日に記録し、大きな動きがあった日だけLINE・Slackへ知らせる（売買はしない）。

・グループ別（光通信 / AIインフラ / 半導体大型 / メモリ / AIの資金繰り）の前日比の中央値、S&P500・ナスダック100・SMH、
  米10年債利回りとVIXを data/selloff_log.json に1日1行で追記する（公開データのみ。保有銘柄は一切含めない）。
・判定ルールは app.py の _breadth_verdict と同じ（変更するときは両方を揃えること）。
・指数・バスケットのRSI(14)も毎日記録し、RSIが30・25を「新たに」下回った日は通知する（前回の記録では未達の場合のみ）。
・通知条件（どれか）: 光通信の中央値 ≤ -3% / SMH ≤ -3% / S&P500 ≤ -1.5% / 値動き±8%以上かつ出来高1.8倍以上の銘柄がある / RSIが30・25を新たに下回った。
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
# RSI(14)を毎日記録する対象。ETFは1銘柄、バスケットは等加重の指数にして計算する（公開データのみ）
RSI_ETFS = {"S&P500（SPY）": "SPY", "ナスダック100（QQQ）": "QQQ", "半導体ETF（SMH）": "SMH", "ハイテク（XLK）": "XLK",
            "ソフトウェア（IGV）": "IGV", "小型株（IWM）": "IWM"}
RSI_BASKETS = {"光通信バスケット": GROUPS["光通信"], "メモリ（MU・WDC・STX）": ["MU", "WDC", "STX"],
               "AIインフラ（VRT・APH・ANET）": ["VRT", "APH", "ANET"]}
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


def rsi14(c: pd.Series, n: int = 14) -> pd.Series:
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, float("nan")))


def pct_b(c: pd.Series) -> float:
    """ボリンジャーバンド（20日・2σ）の%B。0=下限、1=上限。0未満は下限割れ。"""
    ma, sd = c.rolling(20).mean(), c.rolling(20).std(ddof=0)
    lo, up = ma - 2 * sd, ma + 2 * sd
    w = float(up.iloc[-1] - lo.iloc[-1])
    return float((c.iloc[-1] - lo.iloc[-1]) / w) if w > 0 else float("nan")


def compute_bb(close: pd.DataFrame) -> dict:
    """RSIと同じ対象のボリンジャー%B（%表記）。"""
    out = {}
    for name, t in RSI_ETFS.items():
        if t in close.columns:
            c = close[t].dropna()
            if len(c) > 25:
                out[name] = round(pct_b(c) * 100, 0)
    for name, names in RSI_BASKETS.items():
        cols = [x for x in names if x in close.columns]
        if cols:
            b = (1 + close[cols].pct_change(fill_method=None).mean(axis=1, skipna=True).fillna(0)).cumprod()
            if len(b) > 25:
                out[name] = round(pct_b(b) * 100, 0)
    return out


def compute_rsi(close: pd.DataFrame) -> tuple:
    """指数・バスケットのRSI(14)の最新値と、光通信の構成銘柄のうちRSI30未満の割合（%）を返す。"""
    out = {}
    for name, t in RSI_ETFS.items():
        if t in close.columns:
            c = close[t].dropna()
            if len(c) > 40:
                out[name] = round(float(rsi14(c).iloc[-1]), 1)
    for name, names in RSI_BASKETS.items():
        cols = [x for x in names if x in close.columns]
        if cols:
            b = (1 + close[cols].pct_change(fill_method=None).mean(axis=1, skipna=True).fillna(0)).cumprod()
            if len(b) > 40:
                out[name] = round(float(rsi14(b).iloc[-1]), 1)
    cons = [float(rsi14(close[x].dropna()).iloc[-1]) for x in GROUPS["光通信"] if x in close.columns and close[x].dropna().shape[0] > 40]
    share = round(sum(1 for v in cons if v < 30) / len(cons) * 100) if cons else None
    return out, share


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


EQUITY_SKIP = ("^TNX", "^VIX")


def build_entry(close: pd.DataFrame, vol: pd.DataFrame, syms: list) -> tuple:
    """closeの最終行を『その日』として、1日分の記録（entry）と、通知用の中間値（rets, vr）を作る。
    ・『その日』は、株・ETFの過半数が最新の値を持つ最後の日。^TNX・^VIXだけが先に翌日の行を持つことがあり、
      それを基準にすると全銘柄が『古い』扱いになってしまう（2026-10-19〜23の記録が空になった不具合）ため、金利・VIXは基準に使わない。
    ・その日のデータが無い銘柄（ETFだけ1日遅れる等）は、前日の動きを取り違えないよう除外する。"""
    eq_cols = [c for c in close.columns if c not in EQUITY_SKIP]
    last_by = close[eq_cols].apply(lambda c: c.last_valid_index()).dropna()
    counts = last_by.value_counts()
    day = max(d for d, n in counts.items() if n >= max(3, len(eq_cols) * 0.5))
    close = close.loc[:day].copy()
    vol = vol.loc[:day]
    date = str(day.date())
    stale = sorted(c for c in eq_cols if close[c].last_valid_index() != day)
    for c in stale:
        close[c] = float("nan")
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
        "stale_skipped": stale[:12],
        "date": date, "groups": grp, "indexes": idx,
        "tnx": round(float(tnx.iloc[-1]), 3) if len(tnx) else None,
        "tnx_bp": round(float(tnx.iloc[-1] - tnx.iloc[-2]) * 100, 1) if len(tnx) >= 2 else None,
        "vix": round(float(vix.iloc[-1]), 2) if len(vix) else None,
        "verdict": verdict(grp, idx),
    }
    rsi_map, rsi_share = compute_rsi(close)
    entry["rsi"] = rsi_map
    entry["bb_pct"] = compute_bb(close)
    entry["rsi_opt_low_share"] = rsi_share
    movers = [{"t": s, "ret": round(rets[s], 1), "vol": round(vr.get(s, 0), 1)}
              for g in GROUPS.values() for s in g if s in rets and abs(rets[s]) >= 8 and vr.get(s, 0) >= 1.8]
    if movers:
        entry["movers"] = movers
    return entry, grp, idx, rsi_map, movers


def _entry_is_empty(e: dict) -> bool:
    return not e.get("groups") or not e.get("rsi")


def main() -> None:
    syms = sorted({s for v in GROUPS.values() for s in v} | set(INDEXES) | set(RSI_ETFS.values()) | {"^TNX", "^VIX", "VRT", "APH", "ANET", "MU", "WDC", "STX"})
    raw = yf.download(syms, period="6mo", interval="1d", auto_adjust=False, progress=False, group_by="column")
    close = raw["Close"].dropna(how="all")
    vol = raw["Volume"]
    if len(close) < 2:
        print("no data")
        return
    entry, grp, idx, rsi_map, movers = build_entry(close, vol, syms)
    date = entry["date"]

    try:
        with open(OUT, encoding="utf-8") as f:
            log = json.load(f)
    except (OSError, ValueError):
        log = {"entries": []}
    # 過去の記録のうち、値が空だった日（上の不具合の影響）を、取得できる範囲で作り直す
    redo = [e["date"] for e in log.get("entries", []) if _entry_is_empty(e) and e.get("date") != date]
    fixed = {}
    for d in redo:
        try:
            sub_close = close.loc[:d]
            if len(sub_close) < 30:
                continue
            e2, *_ = build_entry(sub_close, vol.loc[:d], syms)
            if e2["date"] == d and not _entry_is_empty(e2):
                fixed[d] = e2
        except Exception as ex:
            print(f"redo {d} failed: {type(ex).__name__}")
    if fixed:
        print("rebuilt:", sorted(fixed))
    prev_rsi = next((e.get("rsi") for e in reversed(log.get("entries", [])) if e.get("date") != date and e.get("rsi")), {}) or {}
    base = [fixed.get(e["date"], e) for e in log.get("entries", []) if e.get("date") != date]
    entries = base + [entry]
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
    # RSIが30・25を「新たに」下回った（前回の記録では未達）ものだけ知らせる。毎日同じ通知を繰り返さない
    rsi_new = []
    for name, v in rsi_map.items():
        pv = prev_rsi.get(name)
        if pv is None:
            continue
        if v < 25 <= pv:
            rsi_new.append(f"{name} RSI {v:.0f}（25を下回りました）")
        elif v < 30 <= pv:
            rsi_new.append(f"{name} RSI {v:.0f}（30を下回りました）")
    if rsi_new:
        reasons.append("RSI: " + "、".join(rsi_new))
    if movers:
        reasons.append("大きな値動き＋出来高急増: " + "、".join(f"{m['t']}{m['ret']:+.1f}%({m['vol']:.1f}倍)" for m in movers[:5]))
    if reasons:
        lines = [f"🔎 急落の広がり（{date}）: {entry['verdict']}", "・" + " / ".join(reasons)]
        lines.append("・グループ別: " + "、".join(f"{g}{v:+.1f}%" for g, v in grp.items()))
        if entry["tnx"] is not None:
            lines.append(f"・米10年債 {entry['tnx']:.2f}%（{entry['tnx_bp']:+.0f}bp）／VIX {entry['vix']}")
        _low = [f"{n} {v:.0f}" for n, v in sorted(rsi_map.items(), key=lambda x: x[1])[:3]]
        if _low:
            lines.append("・RSIの低い順: " + "、".join(_low))
        lines.append("※ 数値は前営業日の終値ベース。原因は断定できません。詳しくはダッシュボードで。")
        _post("\n".join(lines))
    else:
        print("no alert")


if __name__ == "__main__":
    main()
