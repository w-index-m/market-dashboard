"""マイクロン決算直後の時間外取引の株価と、アナリストのコンセンサス・格付け変更を確認する。"""
import warnings

import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")
pd.set_option("display.width", 220)
mu = yf.Ticker("MU")

print("=== 時間外を含む直近の5分足（終値）===")
try:
    h = yf.download("MU", period="2d", interval="5m", prepost=True, progress=False, auto_adjust=True)
    c = h["Close"].squeeze().dropna()
    print(c.tail(40).round(2).to_string())
except Exception as e:
    print("intraday failed:", e)

print("\n=== EPS予想（コンセンサス）===")
try:
    print(mu.earnings_estimate.to_string())
except Exception as e:
    print("earnings_estimate failed:", e)
print("\n=== 売上高予想（コンセンサス）===")
try:
    print(mu.revenue_estimate.to_string())
except Exception as e:
    print("revenue_estimate failed:", e)
print("\n=== EPS予想の推移 ===")
try:
    print(mu.eps_trend.to_string())
except Exception as e:
    print("eps_trend failed:", e)

print("\n=== 直近の格付け・目標株価の変更 ===")
try:
    ud = mu.upgrades_downgrades
    print(ud.head(15).to_string())
except Exception as e:
    print("upgrades failed:", e)

print("\n=== 直近の四半期の売上推移（QoQ） ===")
q = mu.quarterly_income_stmt.loc["Total Revenue"].sort_index()
print((q / 1e9).round(2).to_string())
print("QoQ:", (q.pct_change() * 100).round(1).dropna().to_string())
