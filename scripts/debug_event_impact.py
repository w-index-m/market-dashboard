"""今回の下落の要因に近い過去の出来事が、光通信・半導体に与えた影響と、その後の推移を調べる（公開データ・汎用ティッカー）。"""
import numpy as np
import yfinance as yf

OPT = ["COHR", "LITE", "GLW", "AAOI", "CIEN", "VIAV"]


def dl(syms, start="2016-01-01"):
    d = yf.download(syms, period="max", interval="1d", auto_adjust=True, progress=False)["Close"]
    return d[d.index >= start]


def fwd(px, i, h):
    return (px.iloc[i + h] / px.iloc[i] - 1) * 100 if i + h < len(px) else np.nan


def report(title, idxs, opt, smh, ret_opt, ret_smh, extra=None):
    print(f"\n===== {title}: {len(idxs)}回 =====")
    if not idxs:
        return
    cols = {"当日(光通信)": [ret_opt.iloc[i] for i in idxs], "当日(SMH)": [ret_smh.iloc[i] for i in idxs]}
    for h in (1, 5, 20, 60):
        cols[f"その後{h}日(光通信)"] = [fwd(opt, i, h) for i in idxs]
    for h in (5, 20):
        cols[f"その後{h}日(SMH)"] = [fwd(smh, i, h) for i in idxs]
    print(f"{'指標':<18}{'中央値':>8}{'プラス%':>8}{'最悪':>8}{'最良':>8}")
    for k, v in cols.items():
        a = np.array([x for x in v if x == x])
        if len(a):
            print(f"{k:<18}{np.median(a):+7.1f}%{(a > 0).mean() * 100:7.0f}%{a.min():+7.1f}%{a.max():+7.1f}%")
    # 20日内にさらに5%下がった割合
    ag = []
    for i in idxs:
        if i + 20 < len(opt):
            ag.append(opt.iloc[i + 1:i + 21].min() / opt.iloc[i] - 1 <= -0.05)
    print(f"光通信が20日以内にさらに-5%下がった割合: {np.mean(ag) * 100:.0f}%（{len(ag)}回中）")
    if extra:
        for line in extra(idxs):
            print(line)


def dedupe(idx, gap=5):
    out, last = [], -99
    for i in idx:
        if i - last > gap:
            out.append(i)
            last = i
    return out


def main():
    px = dl(OPT + ["SMH", "QQQ", "^TNX", "^VIX", "CRWV", "NBIS", "IREN", "ORCL"])
    ret = px.pct_change(fill_method=None) * 100
    opt = (1 + ret[[c for c in OPT if c in px.columns]].mean(axis=1, skipna=True).fillna(0) / 100).cumprod()
    ret_opt = opt.pct_change() * 100
    smh = px["SMH"].dropna().reindex(px.index).ffill()
    ret_smh = smh.pct_change() * 100
    dates = list(px.index)

    # 1) 信用不安: ネオクラウド等（CRWV/NBIS/IREN）の平均が1日で-8%以下
    nc = ret[[c for c in ("CRWV", "NBIS", "IREN") if c in px.columns]].mean(axis=1, skipna=True)
    ok = ret[[c for c in ("CRWV", "NBIS", "IREN") if c in px.columns]].notna().sum(axis=1) >= 2
    i1 = dedupe([i for i, d in enumerate(dates) if ok.iloc[i] and nc.iloc[i] <= -8])
    report("AI資金繰り関連（CRWV・NBIS・IREN平均）が1日で-8%以下", i1, opt, smh, ret_opt, ret_smh,
           extra=lambda ix: [f"  日付: {', '.join(str(dates[i].date()) for i in ix)}"])
    # 2) 金利急騰: 10年債が1日で+10bp以上（^TNXは%値）
    dt = px["^TNX"].diff() * 100
    i2 = dedupe([i for i, d in enumerate(dates) if dt.iloc[i] == dt.iloc[i] and dt.iloc[i] >= 10])
    report("10年債利回りが1日で+10bp以上上昇した日", i2, opt, smh, ret_opt, ret_smh)
    # 3) 10年債が5%以上（水準）に初めて入った日以降の月次は難しいため、20日で+40bp以上上昇した局面の最初の日
    d20 = px["^TNX"].diff(20) * 100
    i3 = dedupe([i for i, d in enumerate(dates) if d20.iloc[i] == d20.iloc[i] and d20.iloc[i] >= 40], gap=30)
    report("10年債が20日で+40bp以上上昇した局面の入り口", i3, opt, smh, ret_opt, ret_smh)
    # 4) 半導体の急落日: SMHが1日で-4%以下
    i4 = dedupe([i for i, d in enumerate(dates) if ret_smh.iloc[i] == ret_smh.iloc[i] and ret_smh.iloc[i] <= -4])
    report("SMHが1日で-4%以下", i4, opt, smh, ret_opt, ret_smh,
           extra=lambda ix: ["  そのときのVIX（中央値）: %.1f" % np.nanmedian([px['^VIX'].iloc[i] for i in ix])])
    # 5) 光通信が1日で-6%以下、かつSMHが-2.5%以下（今回に近い）
    i5 = dedupe([i for i, d in enumerate(dates) if ret_opt.iloc[i] <= -6 and ret_smh.iloc[i] <= -2.5])
    report("光通信が1日で-6%以下かつSMHが-2.5%以下（今回に近い日）", i5, opt, smh, ret_opt, ret_smh,
           extra=lambda ix: [f"  日付: {', '.join(str(dates[i].date()) for i in ix)}"])
    # 6) 上の5)のうち、VIXが25未満（低VIX型）だった日
    i6 = [i for i in i5 if px["^VIX"].iloc[i] < 25]
    report("↑のうちVIXが25未満（今回のような低VIX型）", i6, opt, smh, ret_opt, ret_smh,
           extra=lambda ix: [f"  日付: {', '.join(str(dates[i].date()) for i in ix)}"])


if __name__ == "__main__":
    main()
