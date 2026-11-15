"""空売り残高の比較（yfinanceのinfo＋Nasdaqの公開API。公開データ・汎用ティッカー）。"""
import requests
import yfinance as yf

UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
G = {"ETF": ["QQQ", "SMH"], "GAFAM": ["GOOGL", "AMZN", "META", "AAPL", "MSFT"], "GPU": ["NVDA"],
     "GPUクラウド": ["CRWV", "NBIS", "IREN", "APLD"], "光": ["LITE", "AAOI", "COHR"]}


def nasdaq(sym, etf):
    r = requests.get(f"https://api.nasdaq.com/api/quote/{sym}/short-interest?assetClass={'etf' if etf else 'stocks'}", headers=UA, timeout=20)
    rows = (((r.json().get("data") or {}).get("shortInterestTable") or {}).get("rows") or [])
    return [(x["settlementDate"], int(x["interest"].replace(",", "")), x["daysToCover"]) for x in rows]


for g, syms in G.items():
    for s in syms:
        etf = g == "ETF"
        try:
            i = yf.Ticker(s).info
        except Exception:
            i = {}
        try:
            n = nasdaq(s, etf)
        except Exception as e:
            n = []
            print("nasdaq err", s, e)
        latest, prev = (n[0] if n else None), (n[1] if len(n) > 1 else None)
        jan = next((x for x in n if x[0].startswith("01/15/2026")), None)
        float_ = i.get("floatShares") or i.get("sharesOutstanding")
        if etf and i.get("totalAssets") and (i.get("navPrice") or i.get("previousClose")):
            float_ = i["totalAssets"] / (i.get("navPrice") or i["previousClose"])
        pct = latest[1] / float_ * 100 if (latest and float_) else None
        print(f"{g}|{s}|nasdaq={latest}|prev={prev}|jan15={jan}|float={float_}|pct_calc={pct}|"
              f"yf_short={i.get('sharesShort')}|yf_pctfloat={i.get('shortPercentOfFloat')}|yf_date={i.get('dateShortInterest')}|yf_ratio={i.get('shortRatio')}")
