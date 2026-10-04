"""業績取得・PERの過去の位置の計算を、一般的な銘柄で確認する（Actions上）。"""
import yfinance as yf

import app

for t in ("AAPL", "KO", "7203.T"):
    d = app._fetch_fundamentals_history(t)
    px_now = float(yf.Ticker(t).history(period="5d")["Close"].dropna().iloc[-1])
    print(t, "eps_hist", len(d.get("eps_hist", [])), "px_at", len(d.get("px_at", {})),
          "per_position", app._per_position(d, px_now))
