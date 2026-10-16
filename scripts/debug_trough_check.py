"""『昨日が谷か』を数値で確認する: ①最新の状態（反転チェックリスト相当）②過去の似た日の成績（公開データ・汎用ティッカー）。"""
import numpy as np
import pandas as pd
import yfinance as yf

OPT = ["COHR", "LITE", "GLW", "AAOI", "CIEN", "VIAV"]


def rsi(c, n=14):
    d = c.diff()
    up, dn = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean(), (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def ohlcv(sym, start="2000-01-01"):
    d = yf.download(sym, period="max", interval="1d", auto_adjust=True, progress=False)
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)
    d = d.dropna(subset=["Close"])
    return d[d.index >= start]


def feats(d):
    c, o, h, lo, v = d["Close"], d["Open"], d["High"], d["Low"], d["Volume"]
    f = pd.DataFrame({"c": c, "ret": c.pct_change() * 100})
    f["gap"] = (o / c.shift(1) - 1) * 100
    f["intra"] = (c / o - 1) * 100
    rng = (h - lo).replace(0, np.nan)
    f["clv"] = ((c - lo) / rng * 100)
    f["vr"] = v / v.rolling(20).mean().shift(1) if v.sum() > 0 else np.nan
    f["rsi"] = rsi(c)
    f["dd60"] = (c / c.rolling(60).max() - 1) * 100
    f["down"] = (f["dd60"] <= -10) & (c < c.rolling(50).mean())
    return f


def latest(name, d):
    f = feats(d)
    la = f.iloc[-1]
    print(f"{name:<14}{f.index[-1].date()} 騰落{la.ret:+5.1f}% 前日{f.ret.iloc[-2]:+5.1f}% ギャップ{la.gap:+5.1f}% 日中{la.intra:+5.1f}% 終値の位置{la.clv:4.0f}% 出来高倍率{la.vr:4.1f} RSI{la.rsi:5.1f} 高値から{la.dd60:+6.1f}% 下落局面{'はい' if la.down else 'いいえ'}")
    return f


def analog(name, d, label_filter):
    f = feats(d)
    c = f["c"]
    f20 = (c.shift(-20) / c - 1) * 100
    f60 = (c.shift(-60) / c - 1) * 100
    mn = c[::-1].rolling(20).min()[::-1].shift(-1)
    again = (mn / c - 1) <= -0.05
    ok = f60.notna() & f["down"]
    base = ok
    out = [("基準（下落局面の全日）", base)]
    for nm, cond in label_filter(f):
        out.append((nm, cond & ok))
    print(f"\n--- {name}（{c.index[0].date()}〜）---")
    for nm, m in out:
        idx = [i for i, x in enumerate(m.values) if x]
        ev, last = [], -99
        for i in idx:
            if i - last > 5 or nm.startswith("基準"):
                ev.append(i)
                last = i
        if len(ev) < 5:
            print(f"{nm:<46}事例{len(ev):>4}（少なすぎ）")
            continue
        v20 = np.array([f20.iloc[i] for i in ev])
        v60 = np.array([f60.iloc[i] for i in ev])
        ag = np.array([bool(again.iloc[i]) for i in ev])
        print(f"{nm:<46}事例{len(ev):>4} | 20日後 中央値{np.median(v20):+5.1f}% プラス{(v20 > 0).mean() * 100:3.0f}% | 60日後 中央値{np.median(v60):+5.1f}% プラス{(v60 > 0).mean() * 100:3.0f}% | 20日内-5%再下落{ag.mean() * 100:3.0f}%")


def filters(f):
    return [
        ("A: 終値の位置60%以上（高値寄りで引け）", f.clv >= 60),
        ("B: 寄りで-1%以上ギャップダウン→日中+1%以上", (f.gap <= -1) & (f.intra >= 1)),
        ("C: 前日-2%以下 → 当日プラスで引け", (f.ret.shift(1) <= -2) & (f.ret > 0)),
        ("D: RSI40以下 かつ 終値の位置60%以上", (f.rsi <= 40) & (f.clv >= 60)),
        ("E: RSI40以下 かつ 出来高1.3倍以上 かつ 終値の位置60%以上", (f.rsi <= 40) & (f.vr >= 1.3) & (f.clv >= 60)),
        ("F: 前日-2%以下→当日プラス かつ RSI40以下", (f.ret.shift(1) <= -2) & (f.ret > 0) & (f.rsi <= 40)),
    ]


def main():
    print("===== ① 最新の状態 =====")
    data = {}
    for sym, name in (("SMH", "SMH"), ("QQQ", "QQQ"), ("SPY", "SPY"), ("IWM", "IWM"), ("^N225", "日経平均")):
        data[sym] = ohlcv(sym, "2005-01-01")
        latest(name, data[sym])
    raw = yf.download(OPT, period="max", interval="1d", auto_adjust=True, progress=False)
    cl = raw["Close"]
    cl = cl[cl.index >= "2016-01-01"]
    b = (1 + cl.pct_change(fill_method=None).mean(axis=1, skipna=True).fillna(0)).cumprod()
    bd = pd.DataFrame({"Close": b})
    bd["Open"] = b.shift(1)
    bd["High"] = bd[["Open", "Close"]].max(axis=1)
    bd["Low"] = bd[["Open", "Close"]].min(axis=1)
    bd["Volume"] = 0.0
    fb = feats(bd)
    la = fb.iloc[-1]
    print(f"{'光通信バスケット':<12}{fb.index[-1].date()} 騰落{la.ret:+5.1f}% 前日{fb.ret.iloc[-2]:+5.1f}% RSI{la.rsi:5.1f} 高値から{la.dd60:+6.1f}% 下落局面{'はい' if la.down else 'いいえ'}")
    last_rows = cl.iloc[-3:].pct_change(fill_method=None).iloc[-1] * 100
    print("光通信の構成銘柄 最新日の騰落:", {k: round(float(v), 1) for k, v in last_rows.items()})
    vx = yf.download(["^VIX", "^TNX"], period="1mo", interval="1d", progress=False)["Close"]
    print("VIX 直近5日:", [round(float(x), 1) for x in vx["^VIX"].dropna().iloc[-5:]], "| 10年債 直近5日:", [round(float(x), 2) for x in vx["^TNX"].dropna().iloc[-5:]])
    smh = data["SMH"]["Close"]
    w = smh.iloc[-120:-8]
    print(f"SMH 前回の安値(直近8日除く): {float(w.min()):.2f} ({w.idxmin().date()})／直近8日の最安値: {float(smh.iloc[-8:].min()):.2f}／最新終値: {float(smh.iloc[-1]):.2f}／年初来安値: {float(smh[smh.index.year == smh.index[-1].year].min()):.2f}")

    print("\n===== ② 過去の似た日の成績（下落局面の日のみ）=====")
    analog("SMH", data["SMH"], filters)
    analog("QQQ", data["QQQ"], filters)
    analog("日経平均", data["^N225"], filters)
    analog("光通信バスケット（出来高なし）", bd, lambda f: [x for x in filters(f) if x[0][0] in "ABC"])


if __name__ == "__main__":
    main()
