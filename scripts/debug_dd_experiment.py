"""
下落リスクモデル（S&P500が20営業日以内に5%以上下落するか）の特徴量・設定の比較実験。
どの特徴量が時期をまたいで安定した信号を持つかを確かめるデバッグ用スクリプト。
"""
import warnings

import numpy as np
import pandas as pd
import yfinance as yf
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.model_selection import TimeSeriesSplit
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore")
H, THR = 20, -0.05

raw = yf.download(["SPY", "^VIX", "^VIX3M", "^TNX", "^IRX", "HYG", "LQD", "TLT", "JPY=X"],
                  period="15y", interval="1d", progress=False, auto_adjust=True)["Close"]
idx = raw["SPY"].dropna().index
px = raw.reindex(idx).ffill(limit=5)
spy = px["SPY"]
r = spy.pct_change()

f = pd.DataFrame(index=idx)
f["spy_ret5"] = spy.pct_change(5)
f["spy_ret20"] = spy.pct_change(20)
f["spy_ret60"] = spy.pct_change(60)
f["spy_dist200"] = spy / spy.rolling(200).mean() - 1
f["spy_dd252"] = spy / spy.rolling(252).max() - 1
f["spy_vol20"] = r.rolling(20).std() * np.sqrt(252)
f["vol_ratio"] = r.rolling(20).std() / r.rolling(120).std()
f["vix"] = px["^VIX"]
f["vix_pct1y"] = px["^VIX"].rolling(252).rank(pct=True)
f["vix_term"] = px["^VIX"] / px["^VIX3M"]
f["curve"] = px["^TNX"] - px["^IRX"]
f["curve_ch20"] = f["curve"].diff(20)
f["tnx_ch20"] = px["^TNX"].diff(20)
cr = px["HYG"] / px["LQD"]
f["credit_20"] = cr.pct_change(20)
f["credit_60"] = cr.pct_change(60)
f["sb_corr60"] = r.rolling(60).corr(px["TLT"].pct_change())
f["usdjpy_ch20"] = px["JPY=X"].pct_change(20)

fwd_min = pd.concat([spy.shift(-k) for k in range(1, H + 1)], axis=1).min(axis=1)
y = (fwd_min / spy - 1 <= THR).astype(float)
y[fwd_min.isna()] = np.nan
d = f.join(y.rename("y")).replace([np.inf, -np.inf], np.nan).dropna()
Y = d["y"].astype(int).values
print(f"samples={len(d)} {d.index[0].date()}~{d.index[-1].date()} prevalence={Y.mean():.3f}")

tscv = TimeSeriesSplit(n_splits=5, gap=H)
splits = list(tscv.split(d))
print("test periods:", [f"{d.index[te[0]].date()}~{d.index[te[-1]].date()} ev={Y[te].mean():.2f}" for _, te in splits])

print("\n=== 単一特徴量をそのままスコアにした場合のAUC（学習なし・検証期間ごと。>0.5=値が大きいほど下落しやすい）===")
rows = []
for c in f.columns:
    aucs = [roc_auc_score(Y[te], d[c].values[te]) if len(set(Y[te])) == 2 else np.nan for _, te in splits]
    rows.append((c, np.round(aucs, 2), round(np.nanmean(aucs), 3), round(np.nanmin(aucs), 2), round(np.nanmax(aucs), 2)))
for c, a, m, lo, hi in sorted(rows, key=lambda x: -abs(x[2] - 0.5)):
    print(f"  {c:12s} mean={m:.3f} min={lo:.2f} max={hi:.2f} folds={list(a)}")


def evaluate(name, cols, C=0.1, weight=None):
    P, YY, aucs = [], [], []
    for tr, te in splits:
        m = make_pipeline(StandardScaler(), LogisticRegression(C=C, max_iter=2000, class_weight=weight))
        m.fit(d[cols].values[tr], Y[tr])
        p = m.predict_proba(d[cols].values[te])[:, 1]
        P.append(p)
        YY.append(Y[te])
        aucs.append(roc_auc_score(Y[te], p) if len(set(Y[te])) == 2 else np.nan)
    p, yy = np.concatenate(P), np.concatenate(YY)
    print(f"  {name:38s} AUC={roc_auc_score(yy, p):.3f} PR-AUC={average_precision_score(yy, p):.3f} "
          f"(基準{yy.mean():.3f}) folds={list(np.round(aucs, 2))}")


print("\n=== モデル比較 ===")
ALL = list(f.columns)
CURRENT = ["spy_ret20", "spy_ret60", "spy_dist200", "spy_vol20", "vix", "vix_term", "curve", "curve_ch20",
           "tnx_ch20", "credit_20", "credit_60", "sb_corr60", "usdjpy_ch20"]
evaluate("現行14特徴量(JGB除く)", CURRENT)
evaluate("全特徴量 C=0.01", ALL, C=0.01)
for nm, cols in [
    ("ボラ系のみ(vol20,vix,vix_term,vol_ratio)", ["spy_vol20", "vix", "vix_term", "vol_ratio"]),
    ("ボラ+信用(credit_20,60)", ["spy_vol20", "vix", "vix_term", "credit_20", "credit_60"]),
    ("ボラ+トレンド(dist200,dd252)", ["spy_vol20", "vix", "vix_term", "spy_dist200", "spy_dd252"]),
    ("ボラ+トレンド+信用", ["spy_vol20", "vix", "vix_term", "spy_dist200", "spy_dd252", "credit_20"]),
    ("レベル依存を除く(pct/比率/変化のみ)", ["vix_pct1y", "vix_term", "vol_ratio", "spy_dist200", "spy_dd252",
                                   "credit_20", "curve_ch20", "tnx_ch20", "sb_corr60", "usdjpy_ch20"]),
]:
    evaluate(nm, cols)
    evaluate(nm + " C=0.01", cols, C=0.01)

print("\n=== 安定した指標だけの単純合成スコア（学習なし・1年パーセンタイルの平均）===")


def pct1y(s):
    return s.rolling(252).rank(pct=True)


g = pd.DataFrame(index=idx)
g["vix"] = pct1y(px["^VIX"])
g["volr"] = pct1y(r.rolling(20).std() / r.rolling(120).std())
g["vixterm"] = pct1y(px["^VIX"] / px["^VIX3M"])
g["below_trend"] = 1 - pct1y(spy / spy.rolling(200).mean() - 1)
g["dd252"] = 1 - pct1y(spy / spy.rolling(252).max() - 1)
gd = g.join(y.rename("y")).dropna()
GY = gd["y"].astype(int).values
gsplits = list(TimeSeriesSplit(n_splits=5, gap=H).split(gd))
for nm, cols in [("A: VIXのみ", ["vix"]), ("B: VIX+ボラ比", ["vix", "volr"]),
                 ("C: B+VIX期間構造", ["vix", "volr", "vixterm"]),
                 ("D: C+トレンド割れ", ["vix", "volr", "vixterm", "below_trend"]),
                 ("E: D+高値からの下落", ["vix", "volr", "vixterm", "below_trend", "dd252"]),
                 ("F: VIX+ボラ比+トレンド割れ", ["vix", "volr", "below_trend"])]:
    sc = gd[cols].mean(axis=1).values
    aucs = [roc_auc_score(GY[te], sc[te]) for _, te in gsplits]
    te_all = np.concatenate([te for _, te in gsplits])
    q = pd.qcut(sc[te_all], 5, labels=False, duplicates="drop")
    rates = [round(float(GY[te_all][q == k].mean()), 3) for k in range(int(q.max()) + 1)]
    print(f"  {nm:24s} AUC={roc_auc_score(GY[te_all], sc[te_all]):.3f} "
          f"PR-AUC={average_precision_score(GY[te_all], sc[te_all]):.3f} (基準{GY[te_all].mean():.3f}) "
          f"folds={list(np.round(aucs, 2))} 5分位別の発生率={rates}")
