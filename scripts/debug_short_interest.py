"""ETFの空売り残高（Nasdaqの公開API、月2回）の推移を確認する。"""
import requests
import yfinance as yf

UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
for s in ("QQQ", "SMH", "SPY"):
    r = requests.get(f"https://api.nasdaq.com/api/quote/{s}/short-interest?assetClass=etf", headers=UA, timeout=20)
    rows = (((r.json().get("data") or {}).get("shortInterestTable") or {}).get("rows") or [])
    fi = yf.Ticker(s).fast_info
    print(s, r.status_code, len(rows), "rows | shares(fast_info):", fi.get("shares"))
    for x in rows[:26]:
        print("  ", x["settlementDate"], x["interest"], x["daysToCover"])
