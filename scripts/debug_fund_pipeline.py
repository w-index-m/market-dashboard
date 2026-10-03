"""業績取得とポートフォリオ合算のロジックを、一般的な銘柄とダミーの保有数で確認する（Actions上）。"""
import json

import app

pos = {"AAPL": {"qty": 10, "is_jp": False}, "KO": {"qty": 20, "is_jp": False},
       "7203.T": {"qty": 100, "is_jp": True}, "6758.T": {"qty": 50, "is_jp": True}}
funds = {}
for t in pos:
    d = app._fetch_fundamentals_history(t)
    funds[t] = d
    q = d.get("q", {})
    print(t, "eps_hist", len(d.get("eps_hist", [])), "q", len(q), "a", len(d.get("a", {})),
          "last q:", (sorted(q)[-1], q[sorted(q)[-1]]) if q else None)
    print("   eps_hist tail:", d.get("eps_hist", [])[-3:])
print("json bytes:", {t: len(json.dumps(d, ensure_ascii=False, separators=(",", ":"))) for t, d in funds.items()})
lt = app._look_through_series(pos, funds, 150.0)
print("earnings:\n", (lt["earnings"] / 1e4).round(0).tail(8).to_string(), "n", lt["n_earn"])
print("book:\n", (lt["book"] / 1e4).round(0).tail(8).to_string(), "n", lt["n_book"])
