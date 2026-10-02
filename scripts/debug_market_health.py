"""サイトが使う主要な市場データ・経済指標が、今ちゃんと取得できて、値が新しいかを確認する。"""
import logging
import warnings

import pandas as pd
import requests
import yfinance as yf

warnings.filterwarnings("ignore")
logging.basicConfig(level=logging.ERROR)
ok_n = ng_n = 0


def row(ok, label, detail):
    global ok_n, ng_n
    ok_n += ok
    ng_n += (not ok)
    print(f"{'✅' if ok else '❌'} {label}: {detail}")


print("=== 価格データ（最終日が3営業日以内か）===")
syms = {"^GSPC": "S&P500", "^IXIC": "NASDAQ", "^DJI": "ダウ", "^N225": "日経平均", "^VIX": "VIX", "^VIX3M": "VIX3M",
        "^TNX": "米10年債", "^IRX": "米3ヶ月", "JPY=X": "ドル円", "DX-Y.NYB": "ドル指数", "CL=F": "原油", "GC=F": "金",
        "HYG": "HYG", "LQD": "LQD", "TLT": "TLT", "SPY": "SPY", "SMH": "SMH", "MU": "MU", "NVDA": "NVDA", "^JGB10Y": "日本10年(Yahoo)"}
df = yf.download(list(syms), period="10d", progress=False, auto_adjust=True)["Close"]
today = pd.Timestamp.now().normalize()
for s, nm in syms.items():
    ser = df[s].dropna() if s in df else pd.Series(dtype=float)
    if ser.empty:
        row(False, nm, "データなし" + ("（Yahooが提供終了。財務省CSVで代替済み）" if s == "^JGB10Y" else ""))
        continue
    age = (today - ser.index[-1].tz_localize(None).normalize()).days
    row(age <= 5, nm, f"{ser.iloc[-1]:,.2f}（{ser.index[-1].date()}、{age}日前）")

print("\n=== 経済指標・その他の取得元 ===")
checks = {
    "FRED CSV(CPI)": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=CPIAUCSL",
    "財務省 国債金利CSV": "https://www.mof.go.jp/jgbs/reference/interest_rate/jgbcm.csv",
    "multpl(シラーPER)": "https://www.multpl.com/shiller-pe/table/by-month",
    "CNN Fear&Greed": "https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
    "NAAIM": "https://www.naaim.org/programs/naaim-exposure-index/",
}
for nm, url in checks.items():
    try:
        r = requests.get(url, timeout=12, headers={"User-Agent": "Mozilla/5.0"})
        row(r.status_code == 200, nm, f"HTTP {r.status_code}, {len(r.content):,} bytes")
    except Exception as e:
        row(False, nm, f"{type(e).__name__}: {str(e)[:80]}")
try:
    r = requests.post("https://api.bls.gov/publicAPI/v2/timeseries/data/", json={"seriesid": ["CUUR0000SA0"], "startyear": "2026", "endyear": "2026"}, timeout=20)
    row(r.json().get("status") == "REQUEST_SUCCEEDED", "BLS API(CPI等)", r.json().get("status"))
except Exception as e:
    row(False, "BLS API", str(e)[:80])

print(f"\n=== 結果: 正常{ok_n}件 / 要確認{ng_n}件 ===")
