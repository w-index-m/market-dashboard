"""MUの直近の値動き・予想・目標株価・FCF・株数を確認する（Actions上。MUはアプリの固定バスケットにある銘柄）。"""
import warnings

import yfinance as yf

import app

warnings.filterwarnings("ignore")
tk = yf.Ticker("MU")
h = tk.history(period="2y", auto_adjust=True)["Close"].dropna()
def r(n):
    return round((h.iloc[-1] / h.iloc[-n - 1] - 1) * 100, 1)
info = tk.info or {}
e = app._fetch_eps_estimates.__wrapped__("MU")
cy, ny = e.get("cy") or {}, e.get("ny") or {}
px = e.get("price")
print(f"px={px} 1m={r(21)}% 3m={r(63)}% 6m={r(126)}% 1y={r(252)}% high52={h.tail(252).max():.1f} fromHigh={(h.iloc[-1] / h.tail(252).max() - 1) * 100:.1f}%")
print(f"mcap={info.get('marketCap')} EPS今期={cy.get('avg')}({cy.get('low')}〜{cy.get('high')}, n={cy.get('n')}) 来期={ny.get('avg')}({ny.get('low')}〜{ny.get('high')}, n={ny.get('n')})")
print(f"PER今期={px / cy['avg']:.1f} 来期={px / ny['avg']:.1f} target mean={info.get('targetMeanPrice')} high={info.get('targetHighPrice')} low={info.get('targetLowPrice')} n={info.get('numberOfAnalystOpinions')}")
print(f"FCF={info.get('freeCashflow')} OCF={info.get('operatingCashflow')} cash={info.get('totalCash')} debt={info.get('totalDebt')} sharesOut={info.get('sharesOutstanding')} rev_growth={info.get('revenueGrowth')} opMargin={info.get('operatingMargins')}")
try:
    cf = tk.quarterly_cashflow
    for k in ("Free Cash Flow", "Repurchase Of Capital Stock", "Capital Expenditure", "Operating Cash Flow"):
        if k in cf.index:
            print(" ", k, [(str(c.date()), round(v / 1e9, 2)) for c, v in cf.loc[k].items()][:5])
    b = tk.quarterly_balance_sheet
    if "Ordinary Shares Number" in b.index:
        print("  shares", [(str(c.date()), round(v / 1e6, 1)) for c, v in b.loc["Ordinary Shares Number"].items()][:6])
except Exception as ex:
    print("cf err", ex)
