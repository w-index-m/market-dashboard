"""下落局面で「反転のサイン」とされるものが、過去にどれだけ当たったかを調べる（公開データのみ・汎用ティッカー）。"""
import numpy as np
import pandas as pd
import yfinance as yf

OPT = ["COHR", "LITE", "GLW", "AAOI", "CIEN", "VIAV"]


def rsi(c, n=14):
    d = c.diff()
    up, dn = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean(), (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def dl(sym):
    r = yf.download(sym, period="max", interval="1d", auto_adjust=True, progress=False)
    if isinstance(r.columns, pd.MultiIndex):
        r.columns = r.columns.get_level_values(0)
    return r.dropna(subset=["Close"])


def first_events(sig: pd.Series, cooldown=10):
    out, last = [], -999
    idx = list(sig.index)
    arr = sig.values
    for i, v in enumerate(arr):
        if v and i - last > cooldown:
            out.append(idx[i])
            last = i
    return out


def stats(name, dates, px, base_mask):
    res = {"name": name, "n": len(dates)}
    for h in (5, 20, 60):
        fw = []
        for d in dates:
            i = px.index.get_loc(d)
            if i + h < len(px):
                fw.append((px.iloc[i + h] / px.iloc[i] - 1) * 100)
        res[h] = (np.median(fw) if fw else np.nan, np.mean([x > 0 for x in fw]) * 100 if fw else np.nan, min(fw) if fw else np.nan)
    # 再下落: 20日以内に5%以上さらに下がった割合
    again = []
    for d in dates:
        i = px.index.get_loc(d)
        if i + 20 < len(px):
            again.append(px.iloc[i + 1:i + 21].min() / px.iloc[i] - 1 <= -0.05)
    res["again"] = np.mean(again) * 100 if again else np.nan
    return res


def study(label, px, vol, vix, tnx):
    px = px.dropna()
    ma20, ma50 = px.rolling(20).mean(), px.rolling(50).mean()
    hi60 = px.rolling(60).max()
    dd = px / hi60 - 1
    state = (dd <= -0.10) & (px < ma50)
    r = rsi(px)
    sigs = {}
    sigs["RSIが30を割り込んだ"] = (r < 30)
    sigs["RSIが30を回復(直近10日に30割れ)"] = (r >= 30) & (r.shift(1) < 30) | ((r >= 30) & (r.rolling(10).min() < 30) & (r.shift(1) < 30))
    sigs["高値から-20%に到達"] = (dd <= -0.20)
    sigs["20日線を回復(下落局面から)"] = (px > ma20) & (px.shift(1) <= ma20.shift(1)) & state.shift(1).fillna(False)
    chg = px.pct_change()
    if vol is not None:
        v20 = vol.rolling(20).mean()
        capit = (chg <= -0.03) & (vol >= 2 * v20)
        sigs["投げ売り(-3%以下・出来高2倍)の翌日に反発"] = capit.shift(1).fillna(False) & (chg > 0)
    up3 = (chg > 0) & (chg.shift(1) > 0) & (chg.shift(2) > 0)
    sigs["3日続伸(下落局面から)"] = up3 & state.shift(3).fillna(False)
    if vix is not None:
        vx = vix.reindex(px.index).ffill()
        sigs["VIXが30以上に急騰"] = (vx >= 30)
        peak = vx.rolling(10).max()
        sigs["VIXがピークから15%低下(ピーク25以上)"] = (peak >= 25) & (vx <= peak * 0.85) & (vx.shift(1) > peak.shift(1) * 0.85)
    if tnx is not None:
        tx = tnx.reindex(px.index).ffill()
        hi20 = tx.rolling(20).max()
        sigs["金利が高値から0.15pt低下(直前に0.3pt上昇)"] = ((tx.rolling(20).max() - tx.rolling(40).min()) >= 0.30) & (tx <= hi20 - 0.15) & (tx.shift(1) > hi20.shift(1) - 0.15)
    base = px.index[state.fillna(False).values]
    rows = [stats("(基準)下落局面にいる日すべて", list(base), px, None)]
    for nm, s in sigs.items():
        s = s.fillna(False)
        ev = first_events(s & state.reindex(s.index).fillna(False) if "回復" in nm or "続伸" in nm or "反発" in nm or "VIX" in nm or "金利" in nm else s)
        # 下落局面（10%以上の下落）に絞る
        ev = [d for d in ev if bool(state.get(d, False)) or dd.get(d, 0) <= -0.10]
        rows.append(stats(nm, ev, px, None))
    print(f"\n===== {label}（{px.index[0].date()}〜{px.index[-1].date()}）=====")
    print(f"{'サイン':<40}{'回数':>5} | 5日後:中央値/プラス% | 20日後:中央値/プラス%/最悪 | 60日後:中央値/プラス% | 20日内に-5%再下落")
    for x in rows:
        f = lambda t: f"{t[0]:+5.1f}%/{t[1]:3.0f}%" if not np.isnan(t[0]) else "   -   "   # noqa: E731
        w20 = x[20]
        print(f"{x['name']:<40}{x['n']:>5} | {f(x[5])} | {w20[0]:+5.1f}%/{w20[1]:3.0f}%/{w20[2]:+6.1f}% | {f(x[60])} | {x['again']:3.0f}%")
    return px, r, dd


def episodes(label, px, r, vix, tnx, thresh=-0.15, top=14):
    peak = px.cummax()
    dd = px / peak - 1
    out, in_ep, start = [], False, None
    for i, d in enumerate(dd.index):
        if dd.iloc[i] <= thresh and not in_ep:
            _seg = px.loc[:d]
            in_ep, start = True, _seg[_seg == peak.loc[d]].index[-1]
        if in_ep and dd.iloc[i] > -0.02:
            seg = px.loc[start:d]
            tr = seg.idxmin()
            out.append((start, tr, d))
            in_ep = False
    if in_ep:
        seg = px.loc[start:]
        out.append((start, seg.idxmin(), None))
    print(f"\n===== {label}: {int(thresh*100)}%以上の下落局面（ピーク→底→回復）=====")
    for st_, tr, end in out[-top:]:
        depth = (px.loc[tr] / px.loc[st_] - 1) * 100
        days = len(px.loc[st_:tr])
        vx = vix.reindex(px.index).ffill().loc[tr] if vix is not None else np.nan
        tx = (tnx.reindex(px.index).ffill().loc[tr] - tnx.reindex(px.index).ffill().loc[st_]) if tnx is not None else np.nan
        rec = f"{len(px.loc[tr:end])}日で回復" if end is not None else "未回復"
        print(f"{st_.date()} → 底{tr.date()}  深さ{depth:6.1f}%  底まで{days:4d}日  底でのRSI{r.loc[tr]:5.1f}  VIX{vx:5.1f}  金利変化{tx:+.2f}pt  {rec}")


def main():
    vix = dl("^VIX")["Close"]
    tnx = dl("^TNX")["Close"]
    for sym, label in (("SMH", "半導体ETF(SMH)"), ("QQQ", "ナスダック100(QQQ)")):
        d = dl(sym)
        px, r, dd = study(label, d["Close"], d["Volume"], vix, tnx)
        episodes(label, px, r, vix, tnx)
    # 光通信バスケット（等加重・利用可能な銘柄のみ）
    raw = yf.download(OPT, period="max", interval="1d", auto_adjust=True, progress=False)["Close"]
    raw = raw[raw.index >= "2016-01-01"]
    ret = raw.pct_change(fill_method=None).mean(axis=1, skipna=True).fillna(0)
    basket = (1 + ret).cumprod()
    px, r, dd = study("光通信バスケット(等加重)", basket, None, vix, tnx)
    episodes("光通信バスケット(等加重)", px, r, vix, tnx, thresh=-0.25)


if __name__ == "__main__":
    main()
