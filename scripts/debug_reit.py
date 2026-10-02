"""REIT系ティッカーがyfinanceで取得できるか確認する（Actions上で実行）。"""
import warnings

import yfinance as yf

warnings.filterwarnings("ignore")
CANDS = ["1343.T", "1345.T", "1476.T", "1488.T", "2556.T", "2565.T", "1398.T",
         "3282.T", "3278.T", "3459.T", "8986.T", "3269.T", "3226.T", "8953.T", "8951.T", "3283.T"]
for t in CANDS:
    try:
        tk = yf.Ticker(t)
        h = tk.history(period="3y", auto_adjust=True)["Close"].dropna()
        nm = (tk.info or {}).get("shortName") or (tk.info or {}).get("longName")
        if h.empty:
            print(t, "NO DATA", nm)
            continue
        r1 = (h.iloc[-1] / h.iloc[-253] - 1) * 100 if len(h) > 253 else None
        print(t, nm, "n=%d" % len(h), "last=%s" % h.index[-1].date(), "px=%.1f" % h.iloc[-1],
              "ret1y=%s" % (f"{r1:.1f}%" if r1 is not None else "-"))
    except Exception as e:
        print(t, "ERR", e)
