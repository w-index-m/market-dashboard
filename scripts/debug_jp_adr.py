"""日本株の夜間価格（米国ADRの円換算・日経先物）が、yfinanceから取れるか・ADRの比率を自動推定できるかを確認する（公開データ・汎用ティッカー）。"""
import numpy as np
import pandas as pd
import yfinance as yf

PAIRS = {  # 東証コード: [ADRティッカー候補]
    "5803.T": ["FJIKY", "FJIKF"], "5801.T": ["FUWAY", "FUWAF", "FUEEY"], "5802.T": ["SMTOY", "SMTUY"], "9984.T": ["SFTBY", "SFTBF"],
    "8035.T": ["TOELY", "TOELF"], "6857.T": ["ATEYY", "ADTTF"], "7203.T": ["TM"], "6758.T": ["SONY"], "6501.T": ["HTHIY"], "9983.T": ["FRCOY"],
    "8306.T": ["MUFG"], "7974.T": ["NTDOY"], "6861.T": ["KYCCF"], "6920.T": ["LSRCF"], "285A.T": ["KXIAY"],
}
adrs = sorted({a for v in PAIRS.values() for a in v})
tse = list(PAIRS)
raw = yf.download(adrs + tse + ["JPY=X", "NIY=F", "NKD=F", "^N225"], period="3mo", interval="1d", auto_adjust=False, progress=False, group_by="column")
cl = raw["Close"]
fx = cl["JPY=X"].dropna()
print("最新日:", cl.index[-1].date(), "USDJPY", round(float(fx.iloc[-1]), 2))
for k in ("NIY=F", "NKD=F", "^N225"):
    if k in cl.columns and cl[k].dropna().shape[0] > 2:
        s = cl[k].dropna()
        print(k, "最新", s.index[-1].date(), round(float(s.iloc[-1]), 1), "前日", round(float(s.iloc[-2]), 1))
    else:
        print(k, "取得不可")
print(f"\n{'東証':<8}{'ADR':<7}{'ADR最新':>9}{'比率k(推定)':>12}{'東証終値':>10}{'ADR円換算':>11}{'差%':>7}  ADR最新日")
for code, cands in PAIRS.items():
    t = cl[code].dropna() if code in cl.columns else pd.Series(dtype=float)
    for a in cands:
        if a not in cl.columns or cl[a].dropna().shape[0] < 20 or t.empty:
            continue
        s = cl[a].dropna()
        # 比率kの推定: 東証終値 ÷ (ADR終値×USDJPY) を、共通日付の中央値で
        j = pd.concat([t.rename("t"), s.rename("a"), fx.rename("fx")], axis=1).dropna()
        if len(j) < 15:
            continue
        ratio = float(np.median(j["t"] / (j["a"] * j["fx"])))
        snap = min([0.05, 0.1, 0.2, 0.25, 0.5, 1, 2, 4, 5, 10, 20, 50], key=lambda x: abs(np.log(x / ratio)))
        implied = float(s.iloc[-1]) * float(fx.iloc[-1]) * snap
        print(f"{code:<8}{a:<7}{float(s.iloc[-1]):>9.2f}{ratio:>8.3f}→{snap:<5}{float(t.iloc[-1]):>10,.0f}{implied:>11,.0f}{(implied / float(t.iloc[-1]) - 1) * 100:>+7.1f}  {s.index[-1].date()}")
