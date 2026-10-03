"""長期（20/30/50年）の複利チャート用データが取れるか確認する（Actions上で実行）。"""
import io
import warnings

import pandas as pd
import requests
import yfinance as yf

warnings.filterwarnings("ignore")
for t in ["VFINX", "^SP500TR", "^GSPC", "FRESX", "VGSIX", "VUSTX", "VFITX", "VBMFX", "^TNX", "^IRX", "IYR", "VNQ", "TLT", "SPY"]:
    try:
        h = yf.Ticker(t).history(period="max", auto_adjust=True)["Close"].dropna()
        print(t, "first", h.index[0].date() if len(h) else None, "last", h.index[-1].date() if len(h) else None, "n", len(h))
    except Exception as e:
        print(t, "ERR", e)
for sid in ["GS10", "GS3M", "DGS10", "CPIAUCSL"]:
    try:
        r = requests.get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}", timeout=30)
        df = pd.read_csv(io.StringIO(r.text))
        print("FRED", sid, r.status_code, df.iloc[0, 0], df.iloc[-1, 0], len(df))
    except Exception as e:
        print("FRED", sid, "ERR", e)
