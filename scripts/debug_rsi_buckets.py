"""RSI水準ごとの、その後の値動き（下落局面の日だけ）。公開データ・汎用ティッカー。"""
import numpy as np
import pandas as pd
import yfinance as yf

OPT = ["COHR", "LITE", "GLW", "AAOI", "CIEN", "VIAV"]


def rsi(c, n=14):
    d = c.diff()
    up, dn = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean(), (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def run(label, px):
    px = px.dropna()
    r = rsi(px)
    hi60 = px.rolling(60).max()
    down = (px / hi60 - 1 <= -0.10) & (px < px.rolling(50).mean())
    f = {h: (px.shift(-h) / px - 1) * 100 for h in (5, 20, 60)}
    mn20 = px[::-1].rolling(20).min()[::-1].shift(-1)
    again = (mn20 / px - 1 <= -0.05)
    valid = f[60].notna()
    bins = [(0, 20), (20, 25), (25, 30), (30, 35), (35, 40), (40, 50), (50, 101)]
    print(f"\n===== {label}（下落局面の日のみ・{px.index[0].date()}〜）=====")
    print("RSI帯      日数 | 5日後 中央値/プラス% | 20日後 中央値/プラス% | 60日後 中央値/プラス% | 20日内に-5%再下落 | 20日内の最大下落(中央値)")
    for lo, hi in bins:
        m = down & (r >= lo) & (r < hi) & valid
        n = int(m.sum())
        if n < 8:
            print(f"{lo:>3}-{hi:<3}   {n:>5} | (日数少なすぎ)")
            continue
        g = lambda h: f"{f[h][m].median():+5.1f}%/{(f[h][m] > 0).mean() * 100:3.0f}%"   # noqa: E731
        worst = ((mn20 / px - 1) * 100)[m].median()
        print(f"{lo:>3}-{hi:<3}   {n:>5} | {g(5)} | {g(20)} | {g(60)} | {again[m].mean() * 100:3.0f}% | {worst:+5.1f}%")
    # 日数が重複を含む点の補正: RSIが25未満に初めて入った日（間隔20日以上）だけ
    for thr in (30, 25, 20):
        ev, last = [], -99
        for i in range(len(px)):
            if down.iloc[i] and r.iloc[i] < thr and i - last > 20 and f[60].iloc[i] == f[60].iloc[i]:
                ev.append(i)
                last = i
        if ev:
            v20 = [f[20].iloc[i] for i in ev]
            v60 = [f[60].iloc[i] for i in ev]
            print(f"独立した事例（RSI<{thr}に入った日・20日以上間隔）: {len(ev)}回 | 20日後 中央値{np.median(v20):+.1f}% プラス{np.mean([x > 0 for x in v20]) * 100:.0f}% | 60日後 中央値{np.median(v60):+.1f}% プラス{np.mean([x > 0 for x in v60]) * 100:.0f}%")


def dl(s):
    d = yf.download(s, period="max", interval="1d", auto_adjust=True, progress=False)
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)
    return d["Close"]


run("SMH", dl("SMH"))
run("QQQ", dl("QQQ"))
raw = yf.download(OPT, period="max", interval="1d", auto_adjust=True, progress=False)["Close"]
raw = raw[raw.index >= "2016-01-01"]
b = (1 + raw.pct_change(fill_method=None).mean(axis=1, skipna=True).fillna(0)).cumprod()
run("光通信バスケット", b)
