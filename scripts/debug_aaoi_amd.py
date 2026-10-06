"""AAOI・AMDの直近の値動きと予想PERを確認する（Actions上）。どちらもアプリの固定バスケットに入っている銘柄。"""
import warnings

import yfinance as yf

import app

warnings.filterwarnings("ignore")
for t in ("AAOI", "AMD", "LITE", "COHR"):
    tk = yf.Ticker(t)
    h = tk.history(period="2y", auto_adjust=True)["Close"].dropna()
    def r(n):
        return round((h.iloc[-1] / h.iloc[-n - 1] - 1) * 100, 1) if len(h) > n else None
    info = tk.info or {}
    e = app._fetch_eps_estimates.__wrapped__(t)
    m = app._marks_valuation_metrics([t], allow_live=False) if False else {}
    px = e.get("price")
    cy, ny = (e.get("cy") or {}), (e.get("ny") or {})
    print(f"{t} {info.get('shortName')} px={px} 1m={r(21)}% 3m={r(63)}% 6m={r(126)}% 1y={r(252)}% "
          f"high52={round(h.tail(252).max(), 2)} fromHigh={round((h.iloc[-1] / h.tail(252).max() - 1) * 100, 1)}% mcap={info.get('marketCap')}")
    print(f"   EPS今期avg={cy.get('avg')} (n={cy.get('n')}, {cy.get('low')}〜{cy.get('high')}) 来期avg={ny.get('avg')} "
          f"PER今期={round(px / cy['avg'], 1) if px and cy.get('avg') and cy['avg'] > 0 else None} "
          f"来期={round(px / ny['avg'], 1) if px and ny.get('avg') and ny['avg'] > 0 else None} "
          f"trailEPS={info.get('trailingEps')} rev_growth={info.get('revenueGrowth')} opMargin={info.get('operatingMargins')} "
          f"debt/eq={info.get('debtToEquity')} target={info.get('targetMeanPrice')} rec={info.get('recommendationKey')}")
