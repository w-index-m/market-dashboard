"""
🌱長期育成モードのS&P600候補取得パイプラインを、GitHub Actionsランナー（サンドボックスと
違いWikipedia/ssga.com/ishares.com/Yahoo Financeへの制限のない実際のインターネット環境）
から直接テストするための診断スクリプト。app.py本体からロジックをコピーしたもので、
app.pyへの反映は別途手動で行う（このスクリプト自体はデバッグ専用、常設機能ではない）。
"""
import io
import random
import sys
import time

import pandas as pd
import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
MCAP_MIN = 500_000_000
MCAP_MAX = 5_000_000_000


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
            print("[SPSM] Ticker header row not found")
            return {}
        df = pd.read_excel(io.BytesIO(resp.content), header=header_row)
        df.columns = [str(c).strip() for c in df.columns]
        print(f"[SPSM] columns: {list(df.columns)}")
        if "Ticker" not in df.columns or "Name" not in df.columns:
            print("[SPSM] expected columns missing")
            return {}
        result = {}
        for _, row in df.iterrows():
            ticker = str(row["Ticker"]).strip().replace(".", "-")
            name = str(row["Name"]).strip()
            if ticker and ticker.lower() not in ("nan", "") and "cash" not in ticker.lower():
                result[ticker] = name
        print(f"[SPSM] {len(result)} tickers")
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
            print("[IJR] Ticker header row not found")
            return {}
        df = pd.read_csv(io.StringIO("\n".join(lines[header_idx:])), on_bad_lines="skip")
        df.columns = [str(c).strip() for c in df.columns]
        print(f"[IJR] columns: {list(df.columns)}")
        if "Ticker" not in df.columns or "Name" not in df.columns:
            print("[IJR] expected columns missing")
            return {}
        result = {}
        for _, row in df.iterrows():
            ticker = str(row["Ticker"]).strip().replace(".", "-")
            name = str(row["Name"]).strip()
            if ticker and ticker.lower() not in ("nan", "") and "cash" not in ticker.lower():
                result[ticker] = name
        print(f"[IJR] {len(result)} tickers")
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
        import re
        result = {}
        for line in resp.text.splitlines():
            ticker = line.strip().replace(".", "-")
            if not ticker or ticker.lower() == "cash" or not re.match(r"^[A-Z][A-Z0-9\-]{0,6}$", ticker):
                continue
            result[ticker] = ticker
        print(f"[GitHub mirror] {len(result)} tickers")
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
            print(f"[Wikipedia] expected columns missing: {list(df.columns)}")
            return {}
        result = {}
        for _, row in df.iterrows():
            ticker = str(row[sym_col]).strip().replace(".", "-")
            name = str(row[name_col]).strip()
            if ticker and ticker.lower() != "nan":
                result[ticker] = name
        print(f"[Wikipedia] {len(result)} tickers")
        return result
    except Exception as e:
        print(f"[Wikipedia] FAILED: {e}")
        return {}


def test_yfinance_batch(tickers: list, n: int = 40):
    try:
        import yfinance as yf
    except ImportError:
        print("[yfinance] not installed, skipping")
        return
    sample = random.sample(tickers, min(n, len(tickers)))
    ok = 0
    mcap_in_range = 0
    mcap_values = []
    for t in sample:
        try:
            info = yf.Ticker(t).info or {}
            if info:
                ok += 1
                mcap = info.get("marketCap")
                mcap_values.append(mcap)
                in_range = mcap is not None and MCAP_MIN <= mcap <= MCAP_MAX
                if in_range:
                    mcap_in_range += 1
                print(f"[yfinance] {t}: marketCap={mcap} in_range={in_range} "
                      f"price={info.get('currentPrice') or info.get('regularMarketPrice')}")
            else:
                print(f"[yfinance] {t}: EMPTY info")
        except Exception as e:
            print(f"[yfinance] {t}: EXCEPTION {e}")
        time.sleep(0.3)
    print(f"\n[yfinance summary] {ok}/{len(sample)} info() calls succeeded, "
          f"{mcap_in_range}/{len(sample)} within ${MCAP_MIN/1e8:.0f}0M-${MCAP_MAX/1e9:.0f}B range")
    _valid_mcaps = [m for m in mcap_values if m is not None]
    if _valid_mcaps:
        print(f"[yfinance summary] marketCap sample stats: min={min(_valid_mcaps):,} "
              f"max={max(_valid_mcaps):,} median={sorted(_valid_mcaps)[len(_valid_mcaps)//2]:,}")


def main():
    print("=" * 60)
    print("S&P600 universe fetch chain diagnostic")
    print("=" * 60)

    universe = {}
    source_used = None
    for name, fn in (("SPSM", fetch_spsm), ("IJR", fetch_ijr),
                      ("GitHub mirror", fetch_github_mirror), ("Wikipedia", fetch_wikipedia)):
        universe = fn()
        if len(universe) >= 400:
            source_used = name
            break
        print(f"[{name}] insufficient ({len(universe)} < 400), trying next source\n")

    print(f"\n>>> Universe source used: {source_used}, size={len(universe)}\n")

    if not universe:
        print("!!! ALL SOURCES FAILED — universe stage itself is broken")
        sys.exit(1)

    print("=" * 60)
    print("Sampling yfinance .info() calls for market-cap filter stage")
    print("=" * 60)
    test_yfinance_batch(list(universe.keys()), n=40)


if __name__ == "__main__":
    main()
