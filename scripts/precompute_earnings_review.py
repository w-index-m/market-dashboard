"""
📅 決算後レビュー用の生データを取得し、data/earnings_reviews.json に保存する。

背景: Streamlit Cloudの共有IPからYahoo Financeへ多数の銘柄を問い合わせると失敗するため、
Yahooに繋がるGitHub Actions側で毎朝取得してリポジトリに置く（🌱長期育成・🚀日本株10倍株候補と同じ方式）。
判定（上振れ幅・反応の弱さ・成長鈍化など）はapp.py側の_analyze_earnings_review()が行う。
ここでは生データだけを保存する。

対象銘柄はアプリの米国株リスト（app._earnings_review_universe()）。GitHubは公開リポジトリのため、
ユーザーの保有銘柄（取引記録）は使わない。
"""
import concurrent.futures as cf
import json
import logging
import sys
import warnings
from datetime import datetime, timedelta, timezone

import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")
logging.basicConfig(level=logging.WARNING)
import app  # noqa: E402

OUTPUT_PATH = "data/earnings_reviews.json"
LOOKBACK_DAYS = 21  # 直近この日数以内に決算発表があった銘柄を対象にする


def _recent_report(t: str):
    """直近LOOKBACK_DAYS日以内に決算を発表していれば、その履歴(新しい順)を返す。"""
    try:
        ed = yf.Ticker(t).get_earnings_dates(limit=10)
    except Exception:
        return None
    if ed is None or ed.empty or "Reported EPS" not in ed.columns:
        return None
    done = ed[ed["Reported EPS"].notna()].sort_index(ascending=False)
    if done.empty:
        return None
    latest = done.index[0]
    if latest.tz_convert("America/New_York").date() < (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).date():
        return None
    rows = []
    for ts, r in done.head(6).iterrows():
        et = ts.tz_convert("America/New_York")
        rows.append({"date": str(et.date()), "hour_et": et.hour,
                     "est": None if pd.isna(r.get("EPS Estimate")) else float(r["EPS Estimate"]),
                     "actual": float(r["Reported EPS"])})
    return rows


def _detail(t: str, rows: list) -> dict:
    tk = yf.Ticker(t)
    out = {"report": rows[0], "history": rows[1:]}
    info = tk.info or {}
    out["name"] = info.get("shortName") or info.get("longName") or t
    out["info"] = {k: info.get(k) for k in ("currentPrice", "targetMeanPrice", "targetHighPrice", "targetLowPrice",
                                            "numberOfAnalystOpinions", "recommendationKey") if info.get(k) is not None}
    try:
        q = tk.quarterly_income_stmt.loc["Total Revenue"].sort_index().dropna().tail(6)
        out["rev_q"] = [[str(d.date()), float(v)] for d, v in q.items()]
    except Exception:
        out["rev_q"] = []
    try:
        px = yf.download([t, "SPY"], period="3mo", interval="1d", progress=False, auto_adjust=True)["Close"]
        out["closes"] = {c: [[str(d.date()), round(float(v), 4)] for d, v in px[c].dropna().tail(40).items()]
                         for c in (t, "SPY") if c in px.columns}
    except Exception:
        out["closes"] = {}
    # 時間外を含む直近の株価（決算発表の直後は通常取引の終値がまだ無いため）
    try:
        ih = yf.download(t, period="2d", interval="5m", prepost=True, progress=False, auto_adjust=True)["Close"].squeeze().dropna()
        if len(ih):
            out["ext"] = {"price": round(float(ih.iloc[-1]), 4), "ts": str(ih.index[-1])}
    except Exception:
        pass
    try:
        ud = tk.upgrades_downgrades
        since = pd.Timestamp(rows[0]["date"]) - pd.Timedelta(days=1)
        ud = ud[ud.index.tz_localize(None) >= since] if ud.index.tz is not None else ud[ud.index >= since]
        out["revisions"] = [{"date": str(i.date()), "firm": str(r.get("Firm")), "to": str(r.get("ToGrade")),
                             "action": str(r.get("Action")), "pt_action": str(r.get("priceTargetAction")),
                             "target": float(r["currentPriceTarget"]) if pd.notna(r.get("currentPriceTarget")) else None,
                             "prior": float(r["priorPriceTarget"]) if pd.notna(r.get("priorPriceTarget")) else None}
                            for i, r in ud.head(15).iterrows()]
    except Exception:
        out["revisions"] = []
    try:
        et = tk.eps_trend
        out["eps_trend"] = {p: {c: (None if pd.isna(et.loc[p, c]) else float(et.loc[p, c]))
                                for c in ("current", "7daysAgo", "30daysAgo")} for p in ("0q", "0y") if p in et.index}
    except Exception:
        out["eps_trend"] = {}
    return out


def main():
    universe = app._earnings_review_universe()
    print(f"universe: {len(universe)} tickers")
    recent = {}
    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(_recent_report, t): t for t in universe}
        for fut in cf.as_completed(futs, timeout=900):
            rows = fut.result()
            if rows:
                recent[futs[fut]] = rows
    print(f"reported within {LOOKBACK_DAYS}d: {len(recent)} -> {sorted(recent)}")

    items = {}
    with cf.ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(_detail, t, rows): t for t, rows in recent.items()}
        for fut in cf.as_completed(futs, timeout=900):
            t = futs[fut]
            try:
                items[t] = fut.result()
            except Exception as e:
                print(f"detail failed {t}: {e}")
    if len(universe) >= 50 and not items and not recent:
        # 決算が1件も無い期間は正常にあり得るが、問い合わせ自体が全滅した場合と区別できないため
        # 既存ファイルは残して異常終了する（アプリは古いファイルのgenerated_atを表示する）
        print("no reports found at all — keeping the existing file")
        sys.exit(1)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump({"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "universe_size": len(universe), "lookback_days": LOOKBACK_DAYS, "items": items},
                  f, ensure_ascii=False, separators=(",", ":"))
    print(f"wrote {len(items)} items to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
