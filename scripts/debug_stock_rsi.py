"""個別株（AI・半導体・光通信・大型グロースの汎用ユニバース）のRSIが低いとき、その後どうなったか（公開データ・汎用ティッカー）。"""
import numpy as np
import pandas as pd
import yfinance as yf

U = ["NVDA", "AMD", "AVGO", "QCOM", "INTC", "AMAT", "MU", "TSM", "MRVL", "LRCX", "KLAC", "COHR", "LITE", "GLW", "CIEN", "VIAV", "AAOI",
     "VRT", "APH", "ANET", "SMCI", "DELL", "ORCL", "MSFT", "GOOGL", "AMZN", "META", "TSLA", "AAPL", "PLTR", "NFLX", "CRM", "ADBE", "NOW"]


def rsi(c, n=14):
    d = c.diff()
    up, dn = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean(), (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def main():
    raw = yf.download(U + ["QQQ"], period="max", interval="1d", auto_adjust=True, progress=False)["Close"]
    raw = raw[raw.index >= "2012-01-01"]
    q = raw["QQQ"].dropna()
    mkt_down = ((q / q.rolling(60).max() - 1) <= -0.08) & (q < q.rolling(50).mean())
    rows = []
    base = []
    for t in U:
        if t not in raw.columns:
            continue
        s = raw[t].dropna()
        if len(s) < 400:
            continue
        r = rsi(s)
        ma200 = s.rolling(200).mean()
        f = {h: (s.shift(-h) / s - 1) * 100 for h in (5, 20, 60)}
        qf20 = (q.reindex(s.index).ffill().shift(-20) / q.reindex(s.index).ffill() - 1) * 100
        mn20 = s[::-1].rolling(20).min()[::-1].shift(-1)
        dd5 = (mn20 / s - 1) <= -0.05
        dd10 = (mn20 / s - 1) <= -0.10
        md = mkt_down.reindex(s.index).fillna(False)
        valid = f[60].notna()
        for i in range(len(s)):
            if valid.iloc[i] and not np.isnan(ma200.iloc[i]):
                base.append((f[20].iloc[i], f[60].iloc[i], bool(dd5.iloc[i])))
        last = -99
        for i in range(len(s)):
            if r.iloc[i] < 30 and i - last > 20 and valid.iloc[i] and not np.isnan(ma200.iloc[i]):
                last = i
                rows.append({"t": t, "date": s.index[i], "rsi": r.iloc[i], "above200": bool(s.iloc[i] > ma200.iloc[i]),
                             "mkt_down": bool(md.iloc[i]), "f5": f[5].iloc[i], "f20": f[20].iloc[i], "f60": f[60].iloc[i],
                             "ex20": f[20].iloc[i] - qf20.iloc[i], "dd5": bool(dd5.iloc[i]), "dd10": bool(dd10.iloc[i]),
                             "min_rsi": r.iloc[i:i + 10].min()})
    ev = pd.DataFrame(rows)
    b20 = np.array([b[0] for b in base])
    b60 = np.array([b[1] for b in base])
    print(f"ユニバース{len(U)}銘柄（2012年〜）。RSI<30に初めて入った日（20日以上間隔）: 全{len(ev)}回")
    print(f"基準（全日・同じ銘柄）: 20日後 中央値{np.median(b20):+.1f}% プラス{(b20 > 0).mean() * 100:.0f}% | 60日後 中央値{np.median(b60):+.1f}% プラス{(b60 > 0).mean() * 100:.0f}% | 20日内-5%再下落 {np.mean([b[2] for b in base]) * 100:.0f}%")

    def show(name, d):
        if len(d) < 8:
            print(f"{name:<44}{len(d):>5} (少なすぎ)")
            return
        print(f"{name:<44}{len(d):>5} | 20日後 {d.f20.median():+5.1f}%/プラス{(d.f20 > 0).mean() * 100:3.0f}% | 60日後 {d.f60.median():+5.1f}%/プラス{(d.f60 > 0).mean() * 100:3.0f}% "
              f"| QQQ超過20日 {d.ex20.median():+5.1f}% | 20日内-5%再下落{d.dd5.mean() * 100:3.0f}% -10%再下落{d.dd10.mean() * 100:3.0f}%")
    print("\n--- RSI<30に入った日の、その後 ---")
    show("全体", ev)
    show("RSI<25まで下がった（直近10日の最低<25）", ev[ev.min_rsi < 25])
    show("200日線の上（上昇トレンド中の押し目）", ev[ev.above200])
    show("200日線の下（下降トレンド中）", ev[~ev.above200])
    show("市場(QQQ)も下落局面", ev[ev.mkt_down])
    show("市場(QQQ)は下落局面ではない（個別要因）", ev[~ev.mkt_down])
    show("200日線の上 かつ 市場も下落局面", ev[ev.above200 & ev.mkt_down])
    show("200日線の上 かつ 個別要因", ev[ev.above200 & ~ev.mkt_down])
    show("200日線の下 かつ 市場も下落局面", ev[~ev.above200 & ev.mkt_down])
    show("200日線の下 かつ 個別要因", ev[~ev.above200 & ~ev.mkt_down])
    print("\n--- 銘柄別（RSI<30の回数が多い順・上位12）---")
    g = ev.groupby("t").agg(n=("f20", "size"), m20=("f20", "median"), w20=("f20", lambda x: (x > 0).mean() * 100), m60=("f60", "median")).sort_values("n", ascending=False).head(12)
    for t, x in g.iterrows():
        print(f"{t:<7}{int(x.n):>3}回 | 20日後 中央値{x.m20:+5.1f}% プラス{x.w20:3.0f}% | 60日後 中央値{x.m60:+5.1f}%")
    print("\n--- 年別の回数（偏りの確認）---")
    print(ev.groupby(ev.date.dt.year).size().to_string())


if __name__ == "__main__":
    main()
