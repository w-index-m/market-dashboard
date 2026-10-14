"""下落局面で、どの指数・銘柄群のRSIが「その後の戻り」を最もよく示すかを比べる（公開データ・汎用ティッカー）。"""
import numpy as np
import pandas as pd
import yfinance as yf

OPT = ["COHR", "LITE", "GLW", "AAOI", "CIEN", "VIAV"]
MEM = ["MU", "WDC", "STX"]
INFRA = ["VRT", "APH", "ANET"]


def rsi(c, n=14):
    d = c.diff()
    up, dn = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean(), (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def basket(close, names):
    cols = [c for c in names if c in close.columns]
    return (1 + close[cols].pct_change(fill_method=None).mean(axis=1, skipna=True).fillna(0)).cumprod()


def events(cond, state, gap=20):
    out, last = [], -999
    v = (cond & state).values
    for i, x in enumerate(v):
        if x and i - last > gap:
            out.append(i)
            last = i
    return out


def evaluate(target_name, target_px, cands, start):
    t = target_px.dropna()
    t = t[t.index >= start]
    down = ((t / t.rolling(60).max() - 1) <= -0.10) & (t < t.rolling(50).mean())
    f20 = (t.shift(-20) / t - 1) * 100
    f60 = (t.shift(-60) / t - 1) * 100
    mn = t[::-1].rolling(20).min()[::-1].shift(-1)
    again = (mn / t - 1) <= -0.05
    ok = f60.notna()
    print(f"\n===== 戻りを見る対象: {target_name}（{t.index[0].date()}〜{t.index[-1].date()}）下落局面の日数 {int((down & ok).sum())} =====")
    print(f"基準(下落局面の全日): 20日後 中央値{f20[down & ok].median():+.1f}% プラス{(f20[down & ok] > 0).mean() * 100:.0f}% | 60日後 中央値{f60[down & ok].median():+.1f}% プラス{(f60[down & ok] > 0).mean() * 100:.0f}% | 20日内-5%再下落 {again[down & ok].mean() * 100:.0f}%")
    print(f"{'RSIを見る対象':<22}{'条件':<10}{'事例':>5} | 20日後 中央値/プラス% | 60日後 中央値/プラス% | 20日内-5%再下落 | 相関(RSI↔20日後)")
    for cname, cpx in cands.items():
        r = rsi(cpx.dropna()).reindex(t.index).ffill()
        corr = r[down & ok].corr(f20[down & ok], method="spearman")
        for thr in (30, 25):
            ev = events(r < thr, down & ok)
            if len(ev) < 4:
                print(f"{cname:<22}RSI<{thr:<5}{len(ev):>5} | (事例少なすぎ)")
                continue
            v20 = [f20.iloc[i] for i in ev]
            v60 = [f60.iloc[i] for i in ev]
            ag = [bool(again.iloc[i]) for i in ev]
            print(f"{cname:<22}RSI<{thr:<5}{len(ev):>5} | {np.median(v20):+5.1f}%/{np.mean([x > 0 for x in v20]) * 100:3.0f}%      | {np.median(v60):+5.1f}%/{np.mean([x > 0 for x in v60]) * 100:3.0f}%      | {np.mean(ag) * 100:3.0f}%           | {corr:+.2f}")


def main():
    idx = yf.download(["SPY", "QQQ", "SMH", "SOXX", "XLK", "IWM", "IGV"], period="max", interval="1d", auto_adjust=True, progress=False)["Close"]
    st = yf.download(OPT + MEM + INFRA, period="max", interval="1d", auto_adjust=True, progress=False)["Close"]
    st = st[st.index >= "2016-01-01"]
    opt_b, mem_b, inf_b = basket(st, OPT), basket(st, MEM), basket(st, INFRA)
    cands = {"S&P500(SPY)": idx["SPY"], "ナスダック100(QQQ)": idx["QQQ"], "半導体ETF(SMH)": idx["SMH"], "半導体ETF(SOXX)": idx["SOXX"],
             "ハイテク(XLK)": idx["XLK"], "ソフトウェア(IGV)": idx["IGV"], "小型株(IWM)": idx["IWM"],
             "光通信バスケット": opt_b, "メモリ(MU/WDC/STX)": mem_b, "AIインフラ(VRT/APH/ANET)": inf_b}
    evaluate("光通信バスケット", opt_b, cands, "2016-06-01")
    evaluate("半導体ETF(SMH)", idx["SMH"], cands, "2016-06-01")
    # 「構成銘柄のうちRSI30未満が過半数」
    rs = pd.DataFrame({c: rsi(st[c].dropna()) for c in OPT if c in st.columns})
    frac = (rs < 30).sum(axis=1) / rs.notna().sum(axis=1)
    t = opt_b[opt_b.index >= "2016-06-01"]
    down = ((t / t.rolling(60).max() - 1) <= -0.10) & (t < t.rolling(50).mean())
    f20 = (t.shift(-20) / t - 1) * 100
    f60 = (t.shift(-60) / t - 1) * 100
    ok = f60.notna()
    fr = frac.reindex(t.index).ffill()
    for thr in (0.34, 0.5, 0.67):
        ev = events(fr >= thr, down & ok)
        if len(ev) >= 4:
            v20 = [f20.iloc[i] for i in ev]
            v60 = [f60.iloc[i] for i in ev]
            print(f"光通信の構成銘柄のうち RSI<30 が{int(thr * 100)}%以上: 事例{len(ev)} | 20日後 中央値{np.median(v20):+.1f}% プラス{np.mean([x > 0 for x in v20]) * 100:.0f}% | 60日後 中央値{np.median(v60):+.1f}% プラス{np.mean([x > 0 for x in v60]) * 100:.0f}%")
        else:
            print(f"光通信の構成銘柄のうち RSI<30 が{int(thr * 100)}%以上: 事例{len(ev)}（少なすぎ）")
    # 現在値
    print("\n===== 現在のRSI（最新日）=====")
    for cname, cpx in cands.items():
        print(f"{cname:<22}{rsi(cpx.dropna()).iloc[-1]:5.1f}")
    print(f"光通信の構成銘柄でRSI<30の割合: {frac.iloc[-1] * 100:.0f}%")


if __name__ == "__main__":
    main()
