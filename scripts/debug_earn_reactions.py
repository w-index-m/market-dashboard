"""決算後の値動きの計算を、app.pyの関数そのもので、代表銘柄に対して試す。"""
import logging
import pandas as pd
import yfinance as yf

s = open("app.py", encoding="utf-8").read()
a = s.index("def _earn_reactions")
b = s.index("@st.cache_data(ttl=86400", a)
ns = {"pd": pd}
exec(s[a:b], ns)
for t in ("NVDA", "MRVL", "LITE", "ORCL"):
    tk = yf.Ticker(t)
    ed = tk.get_earnings_dates(limit=16)
    cl = tk.history(period="3y", auto_adjust=False)["Close"].dropna()
    cl.index = cl.index.tz_localize(None).normalize()
    idx = ed.index.tz_localize(None) if ed.index.tz is not None else ed.index
    fut = sorted(x for x, e in zip(idx, ed["Reported EPS"].isna()) if e and x >= pd.Timestamp.now().normalize())
    print(t, "next:", fut[0] if fut else None, "hours:", sorted({x.hour for x in idx})[:6])
    print("  ", ns["_earn_reactions"](ed, cl, 8))
