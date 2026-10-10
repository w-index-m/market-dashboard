"""理由メモの答え合わせ用の価格取得が動くか確認する（Actions上、一般的な銘柄）。"""
import pandas as pd

import app

for t in ("AAPL", "7203.T"):
    h, b = app._fetch_note_prices.__wrapped__(t, "2025-10-01")
    print(t, len(h), len(b), h.columns.tolist())
    n = {"date": "2025-10-01", "action": "BUY", "price": float(h["Close"].iloc[0]), "target": float(h["Close"].iloc[0]) * 1.2,
         "stop": float(h["Close"].iloc[0]) * 0.85, "horizon_m": 6, "reason": "t"}
    print("  ", app._review_trade_note(n, h[h.index >= pd.Timestamp(n["date"])], b[b.index >= pd.Timestamp(n["date"])], pd.Timestamp.now().normalize()))
