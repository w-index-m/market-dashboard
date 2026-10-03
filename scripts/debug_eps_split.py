"""決算ごとの実績EPSが株式分割で調整済みか確認する（Actions上）。一般的な銘柄で確認。"""
import warnings

import yfinance as yf

warnings.filterwarnings("ignore")
for t in ["AAPL", "AMZN", "TSLA"]:
    ed = yf.Ticker(t).get_earnings_dates(limit=80)
    done = ed[ed["Reported EPS"].notna()].sort_index()
    print("==", t, "n", len(done), "oldest", done.index.min().date())
    for ts, r in done[(done.index.year >= 2014) & (done.index.year <= 2023)].iloc[::6].iterrows():
        print("  ", ts.date(), r["Reported EPS"])
