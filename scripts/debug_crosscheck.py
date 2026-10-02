"""サイトが使うYahoo Financeの値を、独立した別の情報源(Stooq・米財務省)と突き合わせて、値が合っているかを確認する。"""
import csv
import io
import warnings
import xml.etree.ElementTree as ET

import pandas as pd
import requests
import yfinance as yf

warnings.filterwarnings("ignore")
UA = {"User-Agent": "Mozilla/5.0"}


def stooq(sym):
    r = requests.get(f"https://stooq.com/q/d/l/?s={sym}&i=d", headers=UA, timeout=20)
    rows = list(csv.DictReader(io.StringIO(r.text)))
    if not rows or "Close" not in rows[0]:
        return None
    last = rows[-1]
    return float(last["Close"]), last["Date"]


pairs = [("S&P500", "^GSPC", "^spx"), ("NASDAQ", "^IXIC", "^ndq"), ("ダウ", "^DJI", "^dji"), ("日経平均", "^N225", "^nkx"),
         ("VIX", "^VIX", "^vix"), ("ドル円", "JPY=X", "usdjpy"), ("金(USD/oz)", "GC=F", "xauusd"), ("原油WTI", "CL=F", "cl.f"),
         ("ドル指数", "DX-Y.NYB", "dx.f")]
y = yf.download([p[1] for p in pairs], period="7d", progress=False, auto_adjust=False)["Close"]
print("=== Yahoo vs Stooq（独立した別の情報源）===")
for nm, ys, ss in pairs:
    yv = y[ys].dropna()
    yval, yd = float(yv.iloc[-1]), yv.index[-1].date()
    try:
        sv = stooq(ss)
    except Exception as e:
        sv = None
        print(f"  ({nm}: Stooq取得失敗 {type(e).__name__})")
    if sv is None:
        print(f"？ {nm}: Yahoo {yval:,.2f}（{yd}） / Stooq 取得できず")
        continue
    diff = (yval / sv[0] - 1) * 100
    same_day = str(yd) == sv[1]
    flag = "✅" if abs(diff) < 0.5 else "⚠️" if abs(diff) < 2 else "❌"
    print(f"{flag} {nm}: Yahoo {yval:,.2f}（{yd}） / Stooq {sv[0]:,.2f}（{sv[1]}） 差{diff:+.2f}%" + ("" if same_day else "  ※日付が異なる"))

print("\n=== 米10年債・3ヶ月: Yahoo vs 米財務省（公式）===")
try:
    ym = pd.Timestamp.now().strftime("%Y%m")
    r = requests.get("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml",
                     params={"data": "daily_treasury_yield_curve", "field_tdr_date_value_month": ym}, headers=UA, timeout=25)
    root = ET.fromstring(r.content)
    ns = {"a": "http://www.w3.org/2005/Atom", "m": "http://schemas.microsoft.com/ado/2007/08/dataservices/metadata",
          "d": "http://schemas.microsoft.com/ado/2007/08/dataservices"}
    ent = root.findall("a:entry", ns)[-1].find("a:content/m:properties", ns)
    tdate = ent.find("d:NEW_DATE", ns).text[:10]
    t10 = float(ent.find("d:BC_10YEAR", ns).text)
    t3m = float(ent.find("d:BC_3MONTH", ns).text)
    yy = yf.download(["^TNX", "^IRX"], period="7d", progress=False, auto_adjust=False)["Close"]
    for nm, ys, tv in (("米10年債", "^TNX", t10), ("米3ヶ月", "^IRX", t3m)):
        yv = float(yy[ys].dropna().iloc[-1])
        print(f"{'✅' if abs(yv - tv) < 0.1 else '⚠️'} {nm}: Yahoo {yv:.2f}% / 米財務省 {tv:.2f}%（{tdate}） 差{yv - tv:+.2f}pt")
except Exception as e:
    print("米財務省の取得失敗:", type(e).__name__, str(e)[:100])

print("\n=== 日本10年債: 財務省CSV（アプリの取得経路）===")
try:
    r = requests.get("https://www.mof.go.jp/jgbs/reference/interest_rate/jgbcm.csv", headers=UA, timeout=20)
    lines = r.content.decode("shift_jis", errors="replace").splitlines()
    hdr = next(i for i, ln in enumerate(lines[:5]) if ln.startswith("基準日"))
    cols = lines[hdr].split(",")
    last = [ln for ln in lines[hdr + 1:] if ln.strip()][-1].split(",")
    print(f"✅ 基準日 {last[0]} 10年 {last[cols.index('10年')]}%")
except Exception as e:
    print("財務省CSV失敗:", e)
