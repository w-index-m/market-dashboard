"""増配予想に使えるデータ（配当履歴・予想配当）がyfinanceで取れるか確認する（Actions上で実行）。"""
import warnings

import yfinance as yf

warnings.filterwarnings("ignore")
for t in ["AAPL", "MSFT", "KO", "JNJ", "7203.T", "8306.T", "9432.T"]:
    try:
        tk = yf.Ticker(t)
        d = tk.dividends
        if d is None or len(d) == 0:
            print(t, "no dividends")
            continue
        if getattr(d.index, "tz", None) is not None:
            d.index = d.index.tz_localize(None)
        ann = d.groupby(d.index.year).sum().round(3)
        info = tk.info or {}
        print(t, "annual:", dict(ann.tail(7)),
              "| fwdRate", info.get("dividendRate"), "trailRate", info.get("trailingAnnualDividendRate"),
              "payout", info.get("payoutRatio"), "yield", info.get("dividendYield"), "5yAvgYld", info.get("fiveYearAvgDividendYield"))
    except Exception as e:
        print(t, "ERR", e)
