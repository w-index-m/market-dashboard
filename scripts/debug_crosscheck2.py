"""米3ヶ月金利(^IRX)のズレの性質と、日本10年債CSVの中身、Stooqが取れない理由を調べる。"""
import warnings
import xml.etree.ElementTree as ET

import pandas as pd
import requests
import yfinance as yf

warnings.filterwarnings("ignore")
UA = {"User-Agent": "Mozilla/5.0"}

print("=== ^IRX(Yahoo) vs 米財務省 3ヶ月(BC_3MONTH)・10年 の直近10営業日 ===")
ns = {"a": "http://www.w3.org/2005/Atom", "m": "http://schemas.microsoft.com/ado/2007/08/dataservices/metadata",
      "d": "http://schemas.microsoft.com/ado/2007/08/dataservices"}
rows = []
for ym in (pd.Timestamp.now().strftime("%Y%m"), (pd.Timestamp.now() - pd.DateOffset(months=1)).strftime("%Y%m")):
    r = requests.get("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml",
                     params={"data": "daily_treasury_yield_curve", "field_tdr_date_value_month": ym}, headers=UA, timeout=25)
    for e in ET.fromstring(r.content).findall("a:entry", ns):
        p = e.find("a:content/m:properties", ns)
        rows.append((p.find("d:NEW_DATE", ns).text[:10], float(p.find("d:BC_3MONTH", ns).text), float(p.find("d:BC_10YEAR", ns).text)))
t = pd.DataFrame(rows, columns=["date", "t3m", "t10"]).drop_duplicates("date").set_index("date").sort_index()
y = yf.download(["^IRX", "^TNX"], period="1mo", progress=False, auto_adjust=False)["Close"]
y.index = y.index.strftime("%Y-%m-%d")
j = t.join(y.rename(columns={"^IRX": "irx", "^TNX": "tnx"}), how="inner").tail(10)
j["3M差(IRX-公式)"] = (j["irx"] - j["t3m"]).round(3)
j["10Y差"] = (j["tnx"] - j["t10"]).round(3)
print(j.round(3).to_string())
print("10Y-3M: 公式(3Mの公式値) =", round(float(j['t10'].iloc[-1] - j['t3m'].iloc[-1]), 2), "| ^IRX基準 =", round(float(j['tnx'].iloc[-1] - j['irx'].iloc[-1]), 2))

print("\n=== 日本10年債 財務省CSV ===")
for url in ("https://www.mof.go.jp/jgbs/reference/interest_rate/data/jgbcm_all.csv",
            "https://www.mof.go.jp/jgbs/reference/interest_rate/jgbcm.csv"):
    r = requests.get(url, headers=UA, timeout=25)
    txt = r.content.decode("shift_jis", errors="replace")
    lines = [ln for ln in txt.splitlines() if ln.strip()]
    print(f"{url.split('/')[-1]}: HTTP {r.status_code}, {len(r.content):,} bytes, {len(lines)}行")
    print("  先頭2行:", [ln[:90] for ln in lines[:2]])
    print("  末尾2行:", [ln[:110] for ln in lines[-2:]])

print("\n=== Stooqの応答 ===")
for u in ("https://stooq.com/q/d/l/?s=^spx&i=d", "https://stooq.com/q/l/?s=^spx&f=sd2t2ohlcv&h&e=csv", "https://stooq.pl/q/d/l/?s=^spx&i=d"):
    try:
        r = requests.get(u, headers=UA, timeout=15)
        print(f"{u[:55]}: HTTP {r.status_code} {r.text[:120]!r}")
    except Exception as e:
        print(u[:55], type(e).__name__)
