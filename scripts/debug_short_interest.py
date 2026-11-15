"""ETFの空売り残高を無料で取れるか確認する（yfinanceのinfo・Nasdaqの公開API・FINRAの日次空売り出来高）。"""
import requests
import yfinance as yf

for s in ("QQQ", "SMH", "SPY"):
    try:
        i = yf.Ticker(s).info
        print(s, "yfinance:", {k: i.get(k) for k in ("sharesShort", "sharesShortPriorMonth", "dateShortInterest", "sharesOutstanding", "shortPercentOfFloat", "shortRatio")})
    except Exception as e:
        print(s, "yfinance ERR", e)
UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
try:
    r = requests.get("https://api.nasdaq.com/api/quote/QQQ/short-interest?assetClass=etf", headers=UA, timeout=20)
    print("nasdaq", r.status_code, r.text[:1500])
except Exception as e:
    print("nasdaq ERR", e)
try:
    r = requests.get("https://cdn.finra.org/equity/regsho/daily/CNMSshvol20261113.txt", timeout=20)
    print("finra daily", r.status_code, [ln for ln in r.text.splitlines() if ln.split("|")[1:2] in (["QQQ"], ["SMH"])][:3], r.text.splitlines()[:1])
except Exception as e:
    print("finra ERR", e)
