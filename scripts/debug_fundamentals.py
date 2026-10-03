"""四半期EPS・BPSなどの過去推移がどこまで取れるか確認する（Actions上で実行）。
公開ブランチに出力されるため、保有銘柄ではなく一般的な銘柄で確認する。"""
import os
import warnings

import requests
import yfinance as yf

warnings.filterwarnings("ignore")
for t in ["AAPL", "KO", "7203.T", "6758.T"]:
    print("==", t)
    tk = yf.Ticker(t)
    try:
        q = tk.quarterly_income_stmt
        print(" q income cols:", len(q.columns), [str(c.date()) for c in q.columns][:8])
        for k in ("Diluted EPS", "Total Revenue", "Net Income", "Research And Development"):
            print("   ", k, "present" if k in q.index else "-")
        b = tk.quarterly_balance_sheet
        print(" q balance cols:", len(b.columns), [k for k in ("Stockholders Equity", "Ordinary Shares Number", "Share Issued", "Common Stock Equity") if k in b.index])
        cf = tk.quarterly_cashflow
        print(" q cashflow cols:", len(cf.columns), [k for k in ("Capital Expenditure", "Operating Cash Flow", "Free Cash Flow") if k in cf.index])
        a = tk.income_stmt
        ab = tk.balance_sheet
        print(" annual income cols:", len(a.columns), "balance cols:", len(ab.columns))
        ed = tk.get_earnings_dates(limit=60)
        n = 0 if ed is None else int(ed["Reported EPS"].notna().sum())
        print(" earnings_dates reported EPS count:", n, "oldest:", None if not n else str(ed[ed["Reported EPS"].notna()].index.min().date()))
        info = tk.info or {}
        print(" bookValue", info.get("bookValue"), "priceToBook", info.get("priceToBook"), "ROE", info.get("returnOnEquity"))
    except Exception as e:
        print(" yf ERR", str(e)[:120])
    key = os.environ.get("FINNHUB_API_KEY")
    if key and not t.endswith(".T"):
        try:
            r = requests.get("https://finnhub.io/api/v1/stock/metric", params={"symbol": t, "metric": "all", "token": key}, timeout=20)
            j = r.json()
            ser = j.get("series", {})
            for per in ("quarterly", "annual"):
                sd = ser.get(per, {})
                print(" finnhub", per, {k: len(v) for k, v in sd.items() if k in ("eps", "bookValue", "roe", "currentRatio", "salesPerShare", "netMargin")})
        except Exception as e:
            print(" finnhub ERR", str(e)[:100])
