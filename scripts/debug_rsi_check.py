"""RSIの計算条件（期間・調整済み/未調整）による差を確認する（汎用ティッカー）。"""
import numpy as np
import yfinance as yf


def rsi(c, n=14):
    d = c.diff()
    up, dn = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean(), (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


for sym in ("SPY", "SMH"):
    for per in ("max", "1y", "6mo"):
        for adj in (True, False):
            d = yf.download(sym, period=per, interval="1d", auto_adjust=adj, progress=False)["Close"]
            if hasattr(d, "columns"):
                d = d[sym]
            d = d.dropna()
            print(sym, per, "adj" if adj else "raw", len(d), d.index[-1].date(), f"RSI={rsi(d).iloc[-1]:.1f}", "last5:", [round(float(x), 2) for x in d.iloc[-5:]])
# 複数銘柄同時ダウンロード時
m = yf.download(["SPY", "QQQ", "SMH"], period="max", interval="1d", auto_adjust=True, progress=False)["Close"]
print("multi max:", {c: round(float(rsi(m[c].dropna()).iloc[-1]), 1) for c in m.columns}, m.index[-1].date())
