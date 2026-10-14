#!/usr/bin/env python3
"""
💎 マークス型の選定ルール（価格水準・成長・下落耐性・過熱回避）を、過去にさかのぼって検証し、
data/marks_backtest.json に保存する（GitHub Actionsで月1回）。

後知恵を避けるため、各時点（四半期の最初の取引日）で「その時点までに発表済みの決算」と「その時点までの株価」だけで銘柄を選ぶ。
  ・PER      ＝ その時点の株価 ÷ 直近4決算の実績EPS合計（実績ベース。当時のアナリスト予想は取得できないため、予想PERは使えない）
  ・成長     ＝ 直近4決算のEPS合計 ÷ その1年前の4決算のEPS合計 − 1（どちらもプラスの銘柄だけ）
  ・下落耐性 ＝ 過去2年の最大下落
  ・過熱     ＝ 過去1年の上昇率（2倍超を減点）
  スコア = 50 − min(|PER−18|×1.2, 40) + clip(成長%, −20, 30)×0.5 + 最大下落(負の%)×0.25 − max(0, 1年上昇率%−100)×0.15、PER40倍超は除外
上位20銘柄を等ウェイトで次の四半期まで保有し、①母集団（S&P500の対象銘柄の等ウェイト）②S&P500（SPY）③直近6か月の上昇率上位20銘柄（モメンタム）
④PERが低い上位20銘柄 と比べる。
限界: 母集団は「現在の」S&P500構成銘柄（生存者バイアス＝のちに除外された銘柄が入らない）。手数料・税金・売買の影響は含まない。
"""
import concurrent.futures as cf
import json
import sys
import warnings
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import yfinance as yf

import app

warnings.filterwarnings("ignore")
OUTPUT_PATH = "data/marks_backtest.json"
TOP_N = 20


def _download_close(tickers: list, chunk: int = 80) -> pd.DataFrame:
    parts = []
    for i in range(0, len(tickers), chunk):
        try:
            raw = yf.download(tickers[i:i + chunk], period="9y", interval="1d", auto_adjust=True, progress=False, group_by="column", threads=True)
            c = raw["Close"]
            if isinstance(c, pd.Series):
                c = c.to_frame(tickers[i])
            parts.append(c)
        except Exception as e:
            print(f"chunk {i} failed: {e}")
    close = pd.concat(parts, axis=1)
    close.index = pd.DatetimeIndex(close.index).tz_localize(None) if close.index.tz is not None else close.index
    return close.sort_index()


def _eps_reports(t: str):
    try:
        ed = yf.Ticker(t).get_earnings_dates(limit=44)
        d = ed[ed["Reported EPS"].notna()].sort_index()
        idx = pd.DatetimeIndex(d.index)
        idx = idx.tz_localize(None) if idx.tz is not None else idx
        return t, list(zip(idx.normalize(), d["Reported EPS"].astype(float)))
    except Exception:
        return t, []


def _score(per, growth_pct, dd2y_pct, ret1y_pct):
    return (50 - min(abs(per - 18) * 1.2, 40) + max(-20.0, min(growth_pct, 30.0)) * 0.5 + dd2y_pct * 0.25
            - max(0.0, ret1y_pct - 100) * 0.15)


def main() -> None:
    uni = app.fetch_sp500_constituents()
    tickers = list(uni)
    print(f"universe {len(tickers)}")
    close = _download_close(tickers + ["SPY"])
    print(f"prices {close.shape}")
    eps = {}
    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        for t, rows in ex.map(_eps_reports, tickers):
            if len(rows) >= 8:
                eps[t] = rows
    print(f"eps histories {len(eps)}")
    if len(eps) < 250 or "SPY" not in close.columns:
        print("データが少なすぎるため、既存ファイルを残して終了", file=sys.stderr)
        sys.exit(1)

    idx = close.index
    # 四半期の最初の取引日
    reb = []
    for y in range(idx[0].year + 2, idx[-1].year + 1):
        for m in (1, 4, 7, 10):
            d = pd.Timestamp(year=y, month=m, day=1)
            pos = idx.searchsorted(d)
            if pos < len(idx) and pos >= 504:
                reb.append(int(pos))
    reb = sorted(set(reb))
    results = {"picks": [], "uni": [], "spy": [], "mom": [], "lowper": []}
    dates, n_elig = [], []
    for a, b in zip(reb[:-1], reb[1:]):
        t0 = idx[a]
        feats = {}
        for t, rows in eps.items():
            if t not in close.columns:
                continue
            seen = [(d, v) for d, v in rows if d <= t0 - pd.Timedelta(days=1)]
            if len(seen) < 8:
                continue
            ttm, prev = sum(v for _, v in seen[-4:]), sum(v for _, v in seen[-8:-4])
            if ttm <= 0 or prev <= 0:
                continue
            c = close[t]
            p0, p1 = c.iloc[a], c.iloc[b]
            if not (np.isfinite(p0) and np.isfinite(p1)) or p0 <= 0:
                continue
            w2y = c.iloc[a - 504:a + 1]
            if w2y.isna().sum() > 30 or np.isnan(c.iloc[a - 252]):
                continue
            dd2y = float((w2y.ffill() / w2y.ffill().cummax() - 1).min() * 100)
            per = float(p0 / ttm)
            feats[t] = {"per": per, "g": (ttm / prev - 1) * 100, "dd": dd2y, "r1y": float((p0 / c.iloc[a - 252] - 1) * 100),
                        "r6m": float((p0 / c.iloc[a - 126] - 1) * 100) if np.isfinite(c.iloc[a - 126]) else -999, "fwd": float(p1 / p0 - 1)}
        el = {t: f for t, f in feats.items() if 3 <= f["per"] <= 40}
        if len(el) < 60:
            continue
        sc = sorted(el, key=lambda t: -_score(el[t]["per"], el[t]["g"], el[t]["dd"], el[t]["r1y"]))[:TOP_N]
        mom = sorted(el, key=lambda t: -el[t]["r6m"])[:TOP_N]
        low = sorted(el, key=lambda t: el[t]["per"])[:TOP_N]
        results["picks"].append(float(np.mean([el[t]["fwd"] for t in sc])))
        results["mom"].append(float(np.mean([el[t]["fwd"] for t in mom])))
        results["lowper"].append(float(np.mean([el[t]["fwd"] for t in low])))
        results["uni"].append(float(np.mean([f["fwd"] for f in el.values()])))
        results["spy"].append(float(close["SPY"].iloc[b] / close["SPY"].iloc[a] - 1))
        dates.append(str(t0.date()))
        n_elig.append(len(el))
    n = len(dates)
    print(f"periods {n}")
    if n < 8:
        print("検証期間が短すぎるため終了", file=sys.stderr)
        sys.exit(1)

    def _cum(r):
        return [round(x, 4) for x in np.cumprod(1 + np.array(r)).tolist()]

    def _stats(r, base):
        r, base = np.array(r), np.array(base)
        c = np.cumprod(1 + r)
        yrs = len(r) / 4
        return {"cagr": round((c[-1] ** (1 / yrs) - 1) * 100, 1), "mean_q": round(float(r.mean()) * 100, 2),
                "hit": round(float((r > base).mean()) * 100, 0), "mdd": round(float((c / np.maximum.accumulate(c) - 1).min()) * 100, 1),
                "vol_q": round(float(r.std()) * 100, 1)}

    out = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "n_periods": n, "top_n": TOP_N,
        "first": dates[0], "last": dates[-1], "avg_eligible": int(np.mean(n_elig)), "universe": len(eps),
        "dates": dates, "cum": {k: _cum(v) for k, v in results.items()},
        "stats": {k: _stats(v, results["uni"]) for k, v in results.items()},
    }
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    for k, v in out["stats"].items():
        print(k, v)
    print(f"wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
