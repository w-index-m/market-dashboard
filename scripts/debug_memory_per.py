"""メモリ銘柄の過去PERをyfinanceの財務データから復元できるか確認する（Actions上で実行）。"""
import warnings

import yfinance as yf

warnings.filterwarnings("ignore")
for t in ["MU", "WDC", "STX", "SNDK", "000660.KS", "005930.KS", "285A.T", "NYT"][:7]:
    try:
        tk = yf.Ticker(t)
        a = tk.income_stmt
        q = tk.quarterly_income_stmt
        px = tk.history(period="max", auto_adjust=False)["Close"]
        print(f"== {t}: price from {px.index[0].date() if len(px) else None}, annual cols={len(a.columns) if a is not None else 0}, quarterly cols={len(q.columns) if q is not None else 0}")
        if a is not None and not a.empty:
            for c in a.columns:
                eps = a.loc["Diluted EPS", c] if "Diluted EPS" in a.index else None
                ni = a.loc["Net Income", c] if "Net Income" in a.index else None
                rev = a.loc["Total Revenue", c] if "Total Revenue" in a.index else None
                p = px[px.index <= c.tz_localize(px.index.tz)].iloc[-1] if len(px) else None
                per = (p / eps) if (eps and eps == eps and eps > 0 and p) else None
                print("  ", c.date(), "EPS", eps, "NI", ni, "Rev", rev, "px", None if p is None else round(float(p), 1), "PER", None if per is None else round(float(per), 1))
    except Exception as e:
        print(t, "ERR", e)
