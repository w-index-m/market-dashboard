"""日経先物(NIY=F)の15分足から、東証の引け(15:30)以降の動きを計算できるか確認する。"""
import pandas as pd
import yfinance as yf

JST = "Asia/Tokyo"
f = yf.download("NIY=F", period="5d", interval="15m", auto_adjust=False, progress=False)
fc = f["Close"]
fc = fc["NIY=F"] if isinstance(fc, pd.DataFrame) else fc
fc = fc.dropna()
print("bars", len(fc), "tz", fc.index.tz)
fc.index = fc.index.tz_convert(JST) if fc.index.tz is not None else fc.index.tz_localize("UTC").tz_convert(JST)
last_ts = fc.index[-1]
cand = fc[(fc.index.hour == 15) & (fc.index.minute >= 15) & (fc.index.minute <= 30)]
cand = cand[cand.index < last_ts]
print("last", last_ts, float(fc.iloc[-1]))
print("ref candidates", [(str(i), round(float(v))) for i, v in cand.tail(4).items()])
if len(cand):
    print("pct", round((float(fc.iloc[-1]) / float(cand.iloc[-1]) - 1) * 100, 2))
