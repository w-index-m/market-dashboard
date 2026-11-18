"""今年（2026年）の値動きの安定度: 年初来騰落・年内の最大下落・年率ボラ・高値からの位置（公開データ・汎用ティッカー）。"""
import numpy as np
import pandas as pd
import yfinance as yf

U = ["AAOI", "LITE", "COHR", "CIEN", "GLW", "APH", "ANET", "VRT", "ETN", "GEV", "AVGO", "TSM", "NVDA", "MU", "AMAT", "KLAC", "LRCX", "ASML",
     "MSFT", "GOOGL", "AMZN", "META", "AAPL", "ORCL", "EQIX", "DLR", "SMH", "QQQ", "VOO", "VT"]
px = yf.download(U, start="2025-12-20", interval="1d", auto_adjust=True, progress=False)["Close"]
rows = []
for t in U:
    s = px[t].dropna()
    s0 = s[s.index <= "2025-12-31"]
    y = s[s.index >= "2026-01-01"]
    if s0.empty or len(y) < 50:
        continue
    base = s0.iloc[-1]
    ytd = (y.iloc[-1] / base - 1) * 100
    path = pd.concat([pd.Series([base]), y.reset_index(drop=True)])
    mdd = ((path / path.cummax()) - 1).min() * 100
    vol = y.pct_change().std() * np.sqrt(252) * 100
    off = (y.iloc[-1] / y.max() - 1) * 100
    rows.append((t, ytd, mdd, vol, off))
df = pd.DataFrame(rows, columns=["t", "ytd", "mdd", "vol", "off"]).sort_values("vol")
print("最終日", px.index[-1].date())
for r in df.itertuples():
    print(f"{r.t:6s} 年初来{r.ytd:+7.1f}%  年内最大下落{r.mdd:6.1f}%  年率ボラ{r.vol:5.0f}%  高値から{r.off:6.1f}%")
