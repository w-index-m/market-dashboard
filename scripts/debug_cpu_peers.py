"""CPU/半導体メーカーの5〜10年の株価・業績の推移を確認する（Actions上）。市場シェアそのものはyfinanceでは取れない。"""
import warnings

import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")
for t in ("AMD", "INTC", "ARM", "NVDA", "QCOM", "TSM"):
    tk = yf.Ticker(t)
    h = tk.history(period="max", auto_adjust=True)["Close"].dropna()
    h.index = h.index.tz_localize(None)
    now = float(h.iloc[-1])
    line = [f"{t} 上場データ開始 {h.index[0].date()}"]
    for y in (1, 3, 5, 10):
        past = h[h.index <= h.index[-1] - pd.DateOffset(years=y)]
        if len(past):
            tot = now / float(past.iloc[-1])
            line.append(f"{y}年 {int((tot - 1) * 100):+d}% (年率{((tot ** (1 / y)) - 1) * 100:.0f}%)")
    print(" | ".join(line))
    inc = tk.income_stmt
    if inc is not None and not inc.empty:
        rows = []
        for c in sorted(inc.columns):
            rev = inc.loc["Total Revenue", c] if "Total Revenue" in inc.index else None
            op = inc.loc["Operating Income", c] if "Operating Income" in inc.index else None
            if rev and rev == rev:
                rows.append(f"{c.year}: 売上{rev / 1e9:.1f}B 営業利益率{(op / rev * 100) if op == op and op is not None else float('nan'):.0f}%")
        print("   年次:", " / ".join(rows))
    try:
        ed = tk.get_earnings_dates(limit=60)
        d = ed[ed["Reported EPS"].notna()].sort_index()
        e = d["Reported EPS"]
        ttm = e.rolling(4).sum().dropna()
        pick = [(i.year, round(float(v), 2)) for i, v in ttm.iloc[::4].items()]
        print("   TTM EPS(年1点):", pick[-11:])
    except Exception as ex:
        print("   EPS ERR", str(ex)[:60])
