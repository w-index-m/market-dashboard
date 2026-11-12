"""日本株と、そのADR・ドル円の5分足が取れるか（チャート用）を確認する。"""
import yfinance as yf

for code, adr in (("5803.T", "FJIKY"), ("5801.T", "FUWAY"), ("9984.T", "SFTBY"), ("8035.T", "TOELF")):
    d = yf.download([code, adr, "JPY=X"], period="5d", interval="5m", auto_adjust=False, progress=False, group_by="column")
    c = d["Close"]
    print(code, adr, {k: int(c[k].dropna().shape[0]) for k in c.columns})
    for k in (code, adr):
        s = c[k].dropna()
        if len(s):
            idx = s.index.tz_convert("Asia/Tokyo")
            print("  ", k, "first", idx[0].strftime("%m/%d %H:%M"), "last", idx[-1].strftime("%m/%d %H:%M"), "values", round(float(s.iloc[-1]), 2))
