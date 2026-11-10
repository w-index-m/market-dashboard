"""CNN Fear & Greedが「恐怖」「極度の恐怖」のときに買った場合の、その後の成績を調べる（公開データ・汎用ティッカー）。"""
import numpy as np
import pandas as pd
import requests
import yfinance as yf

H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36", "Accept": "application/json, text/plain, */*",
     "Origin": "https://edition.cnn.com", "Referer": "https://edition.cnn.com/markets/fear-and-greed"}
fg = None
for start in ("2011-01-01", "2018-01-01", "2020-01-01"):
    r = requests.get(f"https://production.dataviz.cnn.io/index/fearandgreed/graphdata/{start}", headers=H, timeout=30)
    print("start", start, "HTTP", r.status_code)
    if r.ok:
        j = r.json()
        h = j["fear_and_greed_historical"]["data"]
        s = pd.Series({pd.Timestamp(x["x"], unit="ms").normalize(): float(x["y"]) for x in h}).sort_index()
        print("points", len(s), s.index[0].date(), s.index[-1].date(), "latest", round(s.iloc[-1], 1), j["fear_and_greed"]["rating"])
        fg = s
        break
if fg is None:
    raise SystemExit("取得できませんでした")
fg = fg[~fg.index.duplicated(keep="last")]
px = yf.download(["SPY", "QQQ", "SMH", "^VIX"], period="max", interval="1d", auto_adjust=True, progress=False)["Close"]
px = px[px.index >= fg.index[0]]
vix = px["^VIX"]


def rsi(c, n=14):
    d = c.diff()
    up, dn = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean(), (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def events(cond, gap=10):
    out, last = [], -999
    for i, v in enumerate(cond.values):
        if v and i - last > gap:
            out.append(i)
            last = i
    return out


def report(tk):
    c = px[tk].dropna()
    f = fg.reindex(c.index).ffill()
    r = rsi(c)
    f5, f20, f60 = (c.shift(-5) / c - 1) * 100, (c.shift(-20) / c - 1) * 100, (c.shift(-60) / c - 1) * 100
    mn = c[::-1].rolling(20).min()[::-1].shift(-1)
    again = (mn / c - 1) <= -0.05
    ok = f60.notna() & f.notna()
    print(f"\n===== {tk}（{c.index[0].date()}〜）=====")
    print(f"{'条件':<54}{'事例':>5} | 5日後 中央値/プラス% | 20日後 中央値/プラス% | 60日後 中央値/プラス% | 20日内-5%再下落")
    base = ok
    rows = [("基準（全日）", base)]
    rows.append(("F&G<=45（恐怖）に入った日", (f <= 45) & ok))
    rows.append(("F&G<=25（極度の恐怖）に入った日", (f <= 25) & ok))
    rows.append(("F&G<=25 が続いたあと、25を上回った日（恐怖からの脱出）", (f > 25) & (f.shift(1) <= 25) & ok))
    rows.append(("F&G<=25 かつ RSI<=30", (f <= 25) & (r <= 30) & ok))
    rows.append(("F&G<=25 かつ VIXがピークから10%以上低下", (f <= 25) & (vix.reindex(c.index).ffill() <= vix.reindex(c.index).ffill().rolling(10).max() * 0.9) & ok))
    rows.append(("F&G<=45 かつ 前日より上昇（F&Gが反転）", (f <= 45) & (f > f.shift(1)) & (f.shift(1) <= f.shift(2)) & ok))
    for nm, m in rows:
        ev = list(range(len(m))) if nm.startswith("基準") else events(m)
        ev = [i for i in ev if i < len(c)]
        if len(ev) < 5:
            print(f"{nm:<54}{len(ev):>5} (少なすぎ)")
            continue
        g = lambda s: f"{np.nanmedian([s.iloc[i] for i in ev]):+5.1f}%/{np.mean([s.iloc[i] > 0 for i in ev]) * 100:3.0f}%"   # noqa: E731
        ag = np.mean([bool(again.iloc[i]) for i in ev]) * 100
        print(f"{nm:<54}{len(ev):>5} | {g(f5)} | {g(f20)} | {g(f60)} | {ag:3.0f}%")


for tk in ("SPY", "QQQ", "SMH"):
    report(tk)
print("\nF&G 現在:", round(fg.iloc[-1], 1), "直近10日:", [round(x) for x in fg.iloc[-10:]])
print("F&G<=25 の日数:", int((fg <= 25).sum()), "/", len(fg), "年別:", fg[fg <= 25].groupby(fg[fg <= 25].index.year).size().to_dict())
