"""VIX期間構造・SKEW・VVIXのティッカーと、CFTC建玉報告の取得元が使えるか確認する（Actions上）。"""
import io
import zipfile

import requests
import yfinance as yf

for t in ("^VIX9D", "^VIX", "^VIX3M", "^VIX6M", "^SKEW", "^VVIX", "^VXN", "^MOVE"):
    try:
        h = yf.Ticker(t).history(period="1y")["Close"].dropna()
        print(t, len(h), None if h.empty else (h.index[0].date(), h.index[-1].date(), round(float(h.iloc[-1]), 2)))
    except Exception as e:
        print(t, "ERR", str(e)[:60])

H = {"User-Agent": "Mozilla/5.0"}
for u in ("https://www.cftc.gov/dea/newcot/FinFutYY.txt", "https://www.cftc.gov/dea/newcot/FinFutWk.txt",
          "https://www.cftc.gov/files/dea/history/fut_fin_txt_2026.zip", "https://www.cftc.gov/files/dea/history/fut_fin_txt_2025.zip",
          "https://www.cftc.gov/files/dea/history/deacot2026.zip", "https://www.cftc.gov/dea/newcot/deafut.txt"):
    try:
        r = requests.get(u, headers=H, timeout=40)
        print(u, r.status_code, len(r.content), r.headers.get("content-type"))
        if r.status_code == 200 and u.endswith(".zip"):
            z = zipfile.ZipFile(io.BytesIO(r.content))
            print("   files:", z.namelist())
            txt = z.read(z.namelist()[0]).decode("latin-1").splitlines()
            print("   header:", txt[0][:400])
            names = sorted({row.split(",")[0].strip('"') for row in txt[1:]})
            print("   markets:", [n for n in names if any(k in n.upper() for k in ("S&P 500", "NASDAQ", "NIKKEI", "VIX", "UST", "10Y", "DOW"))][:20])
        elif r.status_code == 200:
            print("   head:", r.text[:300].replace("\n", " | "))
    except Exception as e:
        print(u, "ERR", str(e)[:80])
