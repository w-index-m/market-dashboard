#!/usr/bin/env python3
"""
反転チェックリストの「過去の検証結果」を、毎月、最新の価格データで再計算して data/reversal_stats.json に保存する（公開データ・汎用ティッカーのみ）。
・指数のサイン別の成績（SMH・QQQ・光通信バスケット）と、個別株のRSI30未満の検証（AI・半導体・光通信・大型グロースの34銘柄）。
・アプリはこのJSONがあればそれを表示し、無ければ従来の固定の数字を表示する。
・保有銘柄・取引・金額は一切使わない。
"""
import json
import os
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import yfinance as yf

OUT = os.path.join(os.path.dirname(__file__), "..", "data", "reversal_stats.json")
OPT = ["COHR", "LITE", "GLW", "AAOI", "CIEN", "VIAV"]
UNIVERSE = ["NVDA", "AMD", "AVGO", "QCOM", "INTC", "AMAT", "MU", "TSM", "MRVL", "LRCX", "KLAC", "COHR", "LITE", "GLW", "CIEN", "VIAV", "AAOI",
            "VRT", "APH", "ANET", "SMCI", "DELL", "ORCL", "MSFT", "GOOGL", "AMZN", "META", "TSLA", "AAPL", "PLTR", "NFLX", "CRM", "ADBE", "NOW"]


def rsi(c: pd.Series, n: int = 14) -> pd.Series:
    d = c.diff()
    up, dn = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean(), (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def dl(sym: str) -> pd.DataFrame:
    r = yf.download(sym, period="max", interval="1d", auto_adjust=True, progress=False)
    if isinstance(r.columns, pd.MultiIndex):
        r.columns = r.columns.get_level_values(0)
    return r.dropna(subset=["Close"])


def first_events(sig: pd.Series, cooldown: int = 10) -> list:
    out, last = [], -999
    for i, (ix, v) in enumerate(sig.items()):
        if v and i - last > cooldown:
            out.append(ix)
            last = i
    return out


def _stats(name: str, dates: list, px: pd.Series) -> dict:
    f20, f60, again = [], [], []
    for d in dates:
        i = px.index.get_loc(d)
        if i + 20 < len(px):
            f20.append((px.iloc[i + 20] / px.iloc[i] - 1) * 100)
            again.append(px.iloc[i + 1:i + 21].min() / px.iloc[i] - 1 <= -0.05)
        if i + 60 < len(px):
            f60.append((px.iloc[i + 60] / px.iloc[i] - 1) * 100)
    nz = lambda v: None if not v else round(float(v), 1)   # noqa: E731
    return {"name": name, "n": len(dates), "m20": nz(np.median(f20) if f20 else None), "p20": nz(np.mean([x > 0 for x in f20]) * 100 if f20 else None),
            "m60": nz(np.median(f60) if f60 else None), "p60": nz(np.mean([x > 0 for x in f60]) * 100 if f60 else None),
            "again": nz(np.mean(again) * 100 if again else None)}


def index_study(px: pd.Series, vol, vix: pd.Series, tnx: pd.Series) -> list:
    px = px.dropna()
    ma20, ma50 = px.rolling(20).mean(), px.rolling(50).mean()
    dd = px / px.rolling(60).max() - 1
    state = ((dd <= -0.10) & (px < ma50)).fillna(False)
    r = rsi(px)
    chg = px.pct_change()
    vx, tx = vix.reindex(px.index).ffill(), tnx.reindex(px.index).ffill()
    sigs = {"VIXが30以上に急騰": vx >= 30}
    peak = vx.rolling(10).max()
    sigs["VIXがピークから15%低下"] = (peak >= 25) & (vx <= peak * 0.85) & (vx.shift(1) > peak.shift(1) * 0.85)
    hi20 = tx.rolling(20).max()
    sigs["金利が高値から0.15pt低下"] = ((hi20 - tx.rolling(40).min()) >= 0.30) & (tx <= hi20 - 0.15) & (tx.shift(1) > hi20.shift(1) - 0.15)
    sigs["RSIが30を割り込む"] = r < 30
    sigs["RSIが30を回復"] = (r >= 30) & (r.shift(1) < 30)
    sigs["20日線を回復"] = (px > ma20) & (px.shift(1) <= ma20.shift(1)) & state.shift(1).fillna(False)
    sigs["3日続伸"] = (chg > 0) & (chg.shift(1) > 0) & (chg.shift(2) > 0) & state.shift(3).fillna(False)
    rows = [_stats("(基準)下落局面にいる日すべて", list(px.index[state.values]), px)]
    for nm, s in sigs.items():
        ev = [d for d in first_events(s.fillna(False)) if bool(state.get(d, False)) or dd.get(d, 0) <= -0.10]
        rows.append(_stats(nm, ev, px))
    return rows


def single_stock() -> dict:
    raw = yf.download(UNIVERSE + ["QQQ"], period="max", interval="1d", auto_adjust=True, progress=False)["Close"]
    raw = raw[raw.index >= "2012-01-01"]
    q = raw["QQQ"].dropna()
    mkt_down = ((q / q.rolling(60).max() - 1) <= -0.08) & (q < q.rolling(50).mean())
    rows, base = [], []
    for t in UNIVERSE:
        if t not in raw.columns:
            continue
        s = raw[t].dropna()
        if len(s) < 400:
            continue
        r, ma200 = rsi(s), s.rolling(200).mean()
        f20, f60 = (s.shift(-20) / s - 1) * 100, (s.shift(-60) / s - 1) * 100
        mn20 = s[::-1].rolling(20).min()[::-1].shift(-1)
        dd5 = (mn20 / s - 1) <= -0.05
        md = mkt_down.reindex(s.index).fillna(False)
        ok = f60.notna() & ma200.notna()
        base += [(f20.iloc[i], f60.iloc[i], bool(dd5.iloc[i])) for i in range(len(s)) if ok.iloc[i]]
        last = -99
        for i in range(len(s)):
            if r.iloc[i] < 30 and i - last > 20 and ok.iloc[i]:
                last = i
                rows.append({"f20": f20.iloc[i], "f60": f60.iloc[i], "above200": bool(s.iloc[i] > ma200.iloc[i]), "mkt_down": bool(md.iloc[i]),
                             "dd5": bool(dd5.iloc[i]), "min_rsi": r.iloc[i:i + 10].min()})
    ev = pd.DataFrame(rows)

    def row(name: str, d: pd.DataFrame) -> dict:
        if len(d) < 8:
            return {"name": name, "n": len(d)}
        return {"name": name, "n": int(len(d)), "m20": round(float(d.f20.median()), 1), "p20": round(float((d.f20 > 0).mean() * 100)),
                "m60": round(float(d.f60.median()), 1), "p60": round(float((d.f60 > 0).mean() * 100)), "again": round(float(d.dd5.mean() * 100))}
    b20, b60 = np.array([b[0] for b in base]), np.array([b[1] for b in base])
    tbl = [{"name": "基準（全日・同じ銘柄）", "n": None, "m20": round(float(np.median(b20)), 1), "p20": round(float((b20 > 0).mean() * 100)),
            "m60": round(float(np.median(b60)), 1), "p60": round(float((b60 > 0).mean() * 100)), "again": round(float(np.mean([b[2] for b in base]) * 100))},
           row("全体", ev), row("RSI25未満まで下がった", ev[ev.min_rsi < 25]), row("市場（QQQ）も下落局面", ev[ev.mkt_down]),
           row("市場は下落局面ではない（個別要因）", ev[~ev.mkt_down]), row("200日線の下かつ市場も下落", ev[~ev.above200 & ev.mkt_down]),
           row("200日線の上かつ市場も下落", ev[ev.above200 & ev.mkt_down])]
    return {"universe": len([t for t in UNIVERSE if t in raw.columns]), "since": "2012", "events": int(len(ev)), "table": tbl}


def main() -> None:
    vix, tnx = dl("^VIX")["Close"], dl("^TNX")["Close"]
    res = {"updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "indexes": {}}
    for sym, label in (("SMH", "半導体ETF(SMH)"), ("QQQ", "ナスダック100(QQQ)")):
        d = dl(sym)
        res["indexes"][sym] = {"label": label, "since": str(d.index[0].year), "rows": index_study(d["Close"], d["Volume"], vix, tnx)}
    raw = yf.download(OPT, period="max", interval="1d", auto_adjust=True, progress=False)["Close"]
    raw = raw[raw.index >= "2016-01-01"]
    basket = (1 + raw.pct_change(fill_method=None).mean(axis=1, skipna=True).fillna(0)).cumprod()
    res["indexes"]["OPT"] = {"label": "光通信バスケット(等加重)", "since": "2016", "rows": index_study(basket, None, vix, tnx)}
    res["single_stock"] = single_stock()
    for k, v in res["indexes"].items():
        print(k, v["since"], [(r["name"][:10], r["n"], r["m20"], r["p20"], r["again"]) for r in v["rows"]])
    print("single", res["single_stock"]["events"], res["single_stock"]["table"][:3])
    if "--write" in sys.argv:
        with open(OUT, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
