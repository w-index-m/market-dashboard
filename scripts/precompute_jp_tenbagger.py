"""
🚀日本株10倍株候補モード用に、東証スタンダード小型株の財務データ（yfinance .info）を事前取得し、
data/jp_tenbagger_raw.json に保存する。

背景: Streamlit Cloudの共有IPからYahoo Financeへ約1,400銘柄分の.info()を叩くと全滅し、
候補が0件になる（🌱長期育成モードのS&P600と同じ問題。GitHub Actionsからは成功する）。
STEP1の判定・スコアリングはapp.pyの_jp_tenbagger_step1()に任せ、ここでは判定に必要な
生データ（app.pyの_JP_TENBAGGER_INFO_KEYSと同じキー）だけを全銘柄分保存する。
"""
import concurrent.futures as cf
import json
import sys
import time
from datetime import datetime, timezone

import pandas as pd
import yfinance as yf

OUTPUT_PATH = "data/jp_tenbagger_raw.json"
# app.pyの_JP_TENBAGGER_INFO_KEYSと揃えること
INFO_KEYS = (
    "currentPrice", "regularMarketPrice", "trailingPE", "forwardPE", "pegRatio", "revenueGrowth",
    "earningsGrowth", "operatingMargins", "operatingCashflow", "debtToEquity", "totalCash", "totalDebt",
    "marketCap", "sector", "industry", "longBusinessSummary", "longName", "shortName",
)
JPX_URL = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j"


def fetch_universe() -> dict:
    """app.pyのfetch_jp_smallcap_universe()と同じ条件: 東証スタンダード市場かつ
    規模区分がLarge70/Mid400以外（Small1/Small2/番付外）。"""
    df = None
    for ext in (".xlsx", ".xls"):
        try:
            df = pd.read_excel(JPX_URL + ext)
            break
        except Exception as e:
            print(f"[JPX] {ext} failed: {e}")
    if df is None:
        return {}
    code_col = next(c for c in df.columns if "コード" in str(c))
    name_col = next(c for c in df.columns if "銘柄名" in str(c))
    market_col = next(c for c in df.columns if "市場" in str(c) and "商品" in str(c))
    size_col = next(c for c in df.columns if "規模" in str(c) and "区分" in str(c))
    out = {}
    for _, row in df.iterrows():
        if "スタンダード" not in str(row[market_col]):
            continue
        size = str(row[size_col])
        if "Large70" in size or "Mid400" in size:
            continue
        code = str(row[code_col]).strip()
        if code.isdigit():
            out[f"{code}.T"] = str(row[name_col]).strip()
    return out


def fetch_info(ticker: str):
    try:
        info = yf.Ticker(ticker).info or {}
    except Exception:
        return None
    if not info:
        return None
    sub = {k: info.get(k) for k in INFO_KEYS if info.get(k) is not None}
    if sub.get("longBusinessSummary"):
        sub["longBusinessSummary"] = sub["longBusinessSummary"][:300]
    return sub or None


def main():
    universe = fetch_universe()
    print(f"Universe (TSE Standard small caps): {len(universe)}")
    if len(universe) < 300:
        print("Universe too small — aborting, not overwriting existing output file")
        sys.exit(1)

    tickers = {}
    # 1回目は並列10、取りこぼし（Yahooのレート制限で失敗した銘柄）は待機してから並列数を
    # 下げて再取得する（1回目の成功数は実行ごとに約800〜1,200件とばらつきがあったため）。
    for attempt, (workers, wait) in enumerate(((10, 0), (4, 60), (2, 120)), start=1):
        todo = [t for t in universe if t not in tickers]
        if not todo:
            break
        if wait:
            time.sleep(wait)
        with cf.ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(fetch_info, t): t for t in todo}
            for fut in cf.as_completed(futs, timeout=1500):
                t = futs[fut]
                info = fut.result()
                if info:
                    tickers[t] = {"name": universe[t], "info": info}
        print(f"Pass {attempt} (workers={workers}): total {len(tickers)} / {len(universe)}")

    print(f"Fetched info for {len(tickers)} / {len(universe)} tickers")
    if len(tickers) < len(universe) * 0.3:
        print("Too few tickers succeeded — aborting, not overwriting existing output file")
        sys.exit(1)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump({
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "market": "東証スタンダード（Small1/Small2/番付外）",
            "universe_size": len(universe),
            "tickers": tickers,
        }, f, ensure_ascii=False, separators=(",", ":"))
    print(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
