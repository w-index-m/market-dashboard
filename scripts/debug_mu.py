"""マイクロン(MU)の直近の決算と値動きを調べるアドホック確認用スクリプト。"""
import warnings

import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")
pd.set_option("display.width", 200)
mu = yf.Ticker("MU")

print("=== 決算日・サプライズ ===")
try:
    ed = mu.get_earnings_dates(limit=6)
    print(ed.to_string())
except Exception as e:
    print("earnings_dates failed:", e)
try:
    print("calendar:", mu.calendar)
except Exception as e:
    print("calendar failed:", e)

print("\n=== 直近の値動き(終値) ===")
h = yf.download(["MU", "NVDA", "AMD", "SMH", "^GSPC", "SOXX"], period="2mo", progress=False, auto_adjust=True)["Close"].dropna(how="all")
print(h.tail(14).round(2).to_string())
print("\n直近の変化率(%):")
for n in (1, 2, 3, 5, 10):
    print(f"  {n}営業日: " + "  ".join(f"{c}={(h[c].iloc[-1] / h[c].iloc[-1 - n] - 1) * 100:+.1f}" for c in h.columns))

print("\n=== 株価・目標株価・予想 ===")
info = mu.info
for k in ("currentPrice", "targetMeanPrice", "targetHighPrice", "targetLowPrice", "numberOfAnalystOpinions", "recommendationKey",
          "trailingPE", "forwardPE", "trailingEps", "forwardEps", "revenueGrowth", "earningsGrowth", "grossMargins",
          "fiftyTwoWeekHigh", "fiftyTwoWeekLow", "marketCap"):
    print(f"  {k}: {info.get(k)}")

print("\n=== 直近ニュース見出し ===")
try:
    for n in (mu.news or [])[:10]:
        c = n.get("content", n)
        print(" -", (c.get("title") or "")[:120], "|", (c.get("pubDate") or "")[:10])
except Exception as e:
    print("news failed:", e)

print("\n=== 四半期損益 ===")
try:
    q = mu.quarterly_income_stmt
    print(q.loc[[r for r in ("Total Revenue", "Gross Profit", "Operating Income", "Net Income") if r in q.index]].iloc[:, :5].to_string())
except Exception as e:
    print("quarterly failed:", e)
