"""
🌱長期育成モード用のS&P600候補データを事前計算し、data/sp600_candidates.jsonに保存する。

背景: Streamlit Cloudの共有IPからYahoo Finance(yfinance)への大量の個別リクエストが
失敗する（GitHub Actionsランナーからは同じリクエストが問題なく成功することを
scripts/debug_sp600_fetch.pyで確認済み）。そのため、Yahooへのアクセスが正常な
GitHub Actions側でこのスクリプトを毎日実行し、結果をリポジトリにコミットする。
Streamlit Cloud側のapp.pyはこのJSONファイルをローカルディスクから読むだけになり、
実行時にYahoo Financeへ一切アクセスしない（＝ブロックの影響を受けない）。

app.py側の_fetch_tenbagger_candidates()と同じ判定基準（時価総額フィルタ等）を
将来変更しても再取得なしで反映できるよう、時価総額フィルタは適用せず取得できた
生データを全件保存する（フィルタ・スコアリングはapp.py側で行う）。
"""
import io
import json
import re
import sys
from datetime import datetime, timezone

import pandas as pd
import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
MIN_UNIVERSE_SIZE = 400
OUTPUT_PATH = "data/sp600_candidates.json"


def fetch_spsm() -> dict:
    try:
        resp = requests.get(
            "https://www.ssga.com/library-content/products/fund-data/etfs/us/holdings-daily-us-en-spsm.xlsx",
            timeout=15, headers=HEADERS,
        )
        resp.raise_for_status()
        raw = pd.read_excel(io.BytesIO(resp.content), header=None)
        header_row = next(
            (i for i in range(min(10, len(raw))) if raw.iloc[i].astype(str).str.strip().eq("Ticker").any()),
            None,
        )
        if header_row is None:
            return {}
        df = pd.read_excel(io.BytesIO(resp.content), header=header_row)
        df.columns = [str(c).strip() for c in df.columns]
        if "Ticker" not in df.columns or "Name" not in df.columns:
            return {}
        result = {}
        for _, row in df.iterrows():
            ticker = str(row["Ticker"]).strip().replace(".", "-")
            name = str(row["Name"]).strip()
            if ticker and ticker.lower() not in ("nan", "") and "cash" not in ticker.lower():
                result[ticker] = name
        return result
    except Exception as e:
        print(f"[SPSM] FAILED: {e}")
        return {}


def fetch_ijr() -> dict:
    try:
        resp = requests.get(
            "https://www.ishares.com/us/products/239774/ishares-core-sp-smallcap-etf/1467271812596.ajax"
            "?fileType=csv&fileName=IJR_holdings&dataType=fund",
            timeout=15, headers=HEADERS,
        )
        resp.raise_for_status()
        lines = resp.text.splitlines()
        header_idx = next((i for i, ln in enumerate(lines) if ln.strip().startswith("Ticker,")), None)
        if header_idx is None:
            return {}
        df = pd.read_csv(io.StringIO("\n".join(lines[header_idx:])), on_bad_lines="skip")
        df.columns = [str(c).strip() for c in df.columns]
        if "Ticker" not in df.columns or "Name" not in df.columns:
            return {}
        result = {}
        for _, row in df.iterrows():
            ticker = str(row["Ticker"]).strip().replace(".", "-")
            name = str(row["Name"]).strip()
            if ticker and ticker.lower() not in ("nan", "") and "cash" not in ticker.lower():
                result[ticker] = name
        return result
    except Exception as e:
        print(f"[IJR] FAILED: {e}")
        return {}


def fetch_github_mirror() -> dict:
    try:
        resp = requests.get(
            "https://raw.githubusercontent.com/major/index-etfs/main/tickers/spsm.txt",
            timeout=15, headers=HEADERS,
        )
        resp.raise_for_status()
        result = {}
        for line in resp.text.splitlines():
            ticker = line.strip().replace(".", "-")
            if not ticker or ticker.lower() == "cash" or not re.match(r"^[A-Z][A-Z0-9\-]{0,6}$", ticker):
                continue
            result[ticker] = ticker
        return result
    except Exception as e:
        print(f"[GitHub mirror] FAILED: {e}")
        return {}


def fetch_wikipedia() -> dict:
    try:
        resp = requests.get(
            "https://en.wikipedia.org/wiki/List_of_S%26P_600_companies",
            timeout=15, headers=HEADERS,
        )
        resp.raise_for_status()
        tables = pd.read_html(resp.text)
        df = tables[0]
        sym_col = next((c for c in df.columns if "Symbol" in str(c)), None)
        name_col = next((c for c in df.columns if "Company" in str(c) or "Security" in str(c)), None)
        if sym_col is None or name_col is None:
            return {}
        result = {}
        for _, row in df.iterrows():
            ticker = str(row[sym_col]).strip().replace(".", "-")
            name = str(row[name_col]).strip()
            if ticker and ticker.lower() != "nan":
                result[ticker] = name
        return result
    except Exception as e:
        print(f"[Wikipedia] FAILED: {e}")
        return {}


def fetch_universe() -> tuple[dict, str]:
    for source_name, fn in (("SPSM", fetch_spsm), ("IJR", fetch_ijr),
                             ("GitHub mirror", fetch_github_mirror), ("Wikipedia", fetch_wikipedia)):
        universe = fn()
        print(f"[{source_name}] {len(universe)} tickers")
        if len(universe) >= MIN_UNIVERSE_SIZE:
            return universe, source_name
    return {}, "none"


def fetch_one_ticker(ticker: str, name_hint: str) -> dict | None:
    import yfinance as yf
    try:
        info = yf.Ticker(ticker).info or {}
    except Exception as e:
        print(f"[yfinance] {ticker}: {type(e).__name__}: {e}")
        return None
    if not info:
        return None
    price = info.get("currentPrice") or info.get("regularMarketPrice")
    if not price or price <= 0:
        return None
    mcap = info.get("marketCap")
    gm = info.get("grossMargins")
    insider = info.get("heldPercentInsiders")
    roe = info.get("returnOnEquity")
    debt = info.get("totalDebt")
    ebitda = info.get("ebitda")
    debt_ebitda = (debt / ebitda) if (debt is not None and ebitda and ebitda > 0) else None
    name = name_hint if name_hint and name_hint != ticker else (info.get("longName") or info.get("shortName") or ticker)
    return {
        "name": name,
        "price": float(price),
        "market_cap": mcap,
        "gross_margin": round(gm * 100, 1) if gm is not None else None,
        "insider_pct": round(insider * 100, 1) if insider is not None else None,
        "roe": round(roe * 100, 1) if roe is not None else None,
        "debt_ebitda": round(debt_ebitda, 2) if debt_ebitda is not None else None,
    }


def main():
    import concurrent.futures as cf

    universe, source_name = fetch_universe()
    if not universe:
        print("ALL SOURCES FAILED — aborting, not overwriting existing output file")
        sys.exit(1)

    print(f"Universe: {len(universe)} tickers from {source_name}")
    candidates = {}
    ok, failed = 0, 0
    with cf.ThreadPoolExecutor(max_workers=10) as ex:
        futs = {ex.submit(fetch_one_ticker, t, n): t for t, n in universe.items()}
        for fut in cf.as_completed(futs, timeout=600):
            ticker = futs[fut]
            try:
                res = fut.result()
            except Exception as e:
                print(f"[yfinance] {ticker}: future error {e}")
                res = None
            if res:
                candidates[ticker] = res
                ok += 1
            else:
                failed += 1

    print(f"Fetched {ok} tickers successfully, {failed} failed/skipped")
    if ok < 100:
        print("Too few tickers succeeded — aborting, not overwriting existing output file")
        sys.exit(1)

    output = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "universe_source": source_name,
        "universe_size": len(universe),
        "candidates": candidates,
    }
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=1)
    print(f"Wrote {len(candidates)} candidates to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
