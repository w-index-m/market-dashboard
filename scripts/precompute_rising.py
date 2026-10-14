#!/usr/bin/env python3
"""
日米の「過去に上昇した銘柄」スクリーナー用データを事前計算し、data/rising_candidates.json に保存する。
対象: 米国=S&P500（大型株）＋S&P600（小型株指数）の全構成銘柄、日本=日経225全構成銘柄（約1,300銘柄）。
各銘柄について、1か月/3か月/6か月/1年/2年のリターン、52週高値からの距離、1年の最大下落、
直近の出来高の増減、シャープレシオ（1年）を、配当調整済みの日足から計算する。
Yahoo Financeへの大量アクセスはStreamlit Cloudからは失敗しやすいため、Actions側で毎日計算し、アプリは読むだけにする。
保有銘柄ではなく指数の構成銘柄を使う（公開リポジトリのため）。
"""
import json
import sys
import warnings
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import yfinance as yf

import app

warnings.filterwarnings("ignore")
OUTPUT_PATH = "data/rising_candidates.json"


def _download(tickers: list, chunk: int = 80) -> tuple:
    closes, vols = [], []
    for i in range(0, len(tickers), chunk):
        part = tickers[i:i + chunk]
        try:
            raw = yf.download(part, period="2y", interval="1d", auto_adjust=True, progress=False, group_by="column", threads=True)
        except Exception as e:
            print(f"chunk {i} failed: {e}")
            continue
        if raw is None or raw.empty:
            continue
        c, v = raw["Close"], raw["Volume"]
        if isinstance(c, pd.Series):
            c, v = c.to_frame(part[0]), v.to_frame(part[0])
        closes.append(c)
        vols.append(v)
    if not closes:
        return pd.DataFrame(), pd.DataFrame()
    return pd.concat(closes, axis=1), pd.concat(vols, axis=1)


def _calc(c: pd.Series, v: pd.Series, rf: float) -> dict | None:
    c = c.dropna()
    if len(c) < 130:
        return None
    px = float(c.iloc[-1])

    def ret(n):
        return round((px / float(c.iloc[-n - 1]) - 1) * 100, 1) if len(c) > n else None

    d = c.pct_change().dropna()
    y = d.tail(252)
    hi = float(c.tail(252).max())
    cum = (1 + y).cumprod()
    vv = v.dropna()
    vr = None
    if len(vv) > 140 and float(vv.tail(120).iloc[:-20].mean()) > 0:
        vr = round(float(vv.tail(20).mean() / vv.tail(120).iloc[:-20].mean()), 2)
    sh = None
    if len(y) >= 150 and float(y.std()) > 0:
        sh = round(float((y.mean() - rf / 100 / 252) / y.std() * np.sqrt(252)), 2)
    return {"px": round(px, 2), "r1m": ret(21), "r3m": ret(63), "r6m": ret(126), "r1y": ret(252), "r2y": ret(504),
            "from_high": round((px / hi - 1) * 100, 1), "mdd1y": round(float((cum / cum.cummax() - 1).min()) * 100, 1),
            "vol_ratio": vr, "sharpe": sh}


def main() -> None:
    us = app.fetch_sp500_constituents()
    sm = app.fetch_sp600_constituents()
    jp = app.fetch_nikkei225_constituents()
    print(f"universe: S&P500 {len(us)}, S&P600 {len(sm)}, 日経225 {len(jp)}")
    group = {**{t: "US500" for t in us}, **{t: "US600" for t in sm if t not in us}, **{t: "JP225" for t in jp}}
    names = {**sm, **us, **jp}
    tickers = list(group)
    close, vol = _download(tickers)
    try:
        rf = float(app._fetch_treasury_3m_series().dropna().iloc[-1])
    except Exception:
        rf = 4.0
    items = {}
    for t in tickers:
        if t not in close.columns:
            continue
        r = _calc(close[t], vol[t] if t in vol.columns else pd.Series(dtype=float), rf)
        if r:
            r["name"] = names.get(t) or t
            r["grp"] = group[t]
            items[t] = r
    cnt = {g: sum(1 for x in items.values() if x["grp"] == g) for g in ("US500", "US600", "JP225")}
    print(f"computed: {cnt}")
    if cnt["US500"] < 300 or cnt["US600"] < 300 or cnt["JP225"] < 100:
        print("取得できた銘柄が少なすぎるため、既存ファイルを残して終了", file=sys.stderr)
        sys.exit(1)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump({"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "rf": rf, "items": items},
                  f, ensure_ascii=False, separators=(",", ":"))
    print(f"wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
