"""MUの年末・来年末の株価レンジの目安（実績ボラ・過去の下落幅・EPS×PERシナリオ）をActions上で計算する。"""
import warnings

import numpy as np
import pandas as pd
import yfinance as yf

import app

warnings.filterwarnings("ignore")
h = yf.Ticker("MU").history(period="max", auto_adjust=True)["Close"].dropna()
h.index = h.index.tz_localize(None)
px = float(h.iloc[-1])
r = h.pct_change().dropna()
print(f"px={px:.1f} last={h.index[-1].date()}")
for lab, n in (("直近1年", 252), ("直近3か月", 63), ("直近1か月", 21), ("過去5年", 1260)):
    print(f"  日次ボラ年率 {lab}: {r.tail(n).std() * np.sqrt(252) * 100:.0f}%")
today = pd.Timestamp("2026-10-11")
for lab, end in (("年末(2026-12-31)", pd.Timestamp("2026-12-31")), ("来年末(2027-12-31)", pd.Timestamp("2027-12-31"))):
    days = len(pd.bdate_range(today, end))
    rng = np.random.default_rng(3)
    out = {}
    for vlab, rr in (("直近1年の値動き", r.tail(252)), ("過去5年の値動き", r.tail(1260))):
        arr = (rr - rr.mean()).to_numpy()          # 平均0（ドリフト無し）で、ばらつき方だけ借りる
        idx = rng.integers(0, len(arr), size=(20000, days))
        fin = px * np.prod(1 + arr[idx], axis=1)
        out[vlab] = [round(float(np.percentile(fin, q))) for q in (10, 25, 50, 75, 90)]
    print(lab, "営業日", days, out)
# 過去の山からの下落（サイクルの深さ）
roll = h / h.cummax() - 1
for y in (2018, 2019, 2022, 2023, 2024, 2025):
    s = roll[roll.index.year == y]
    print(f"  {y}年の最大下落（その時点までの高値から）: {s.min() * 100:.0f}%")
e = app._fetch_eps_estimates.__wrapped__("MU")
cy, ny = e["cy"], e["ny"]
print("EPS今期", cy["avg"], "来期", ny["avg"], "範囲", ny["low"], ny["high"])
for per in (4, 5, 6, 7, 8, 10, 12):
    print(f"  来期EPS{ny['avg']:.0f}×PER{per} = {ny['avg'] * per:,.0f}  (低位{ny['low'] * per:,.0f}〜高位{ny['high'] * per:,.0f})")
