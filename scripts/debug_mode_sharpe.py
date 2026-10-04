"""モード別バスケットのシャープレシオが計算できるか確認する（Actions上、固定バスケットのみ）。"""
import app

for k in ("dividend_stable", "ai_mix"):
    r = app._compute_mode_basket_backtest.__wrapped__(k)
    print(k, r.get("ok"), "1y", r.get("ret_1y"), "sh1y", r.get("sharpe_1y"), "sh3y", r.get("sharpe_3y"),
          "bench", r.get("bench_sharpe_1y"), r.get("bench_sharpe_3y"), "rf", r.get("rf"))
    print("  ticker_sharpe", {t: (None if v is None else round(v, 2)) for t, v in (r.get("ticker_sharpe") or {}).items()})
