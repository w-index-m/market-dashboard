"""
app.pyの金利カード（generate_rate_inflation_narrative）のデータ取得パイプラインを、
制限のないGitHub Actionsランナー上で実際に実行して確かめるデバッグ用スクリプト。
app.pyの該当範囲のソースをそのまま抜き出して実行する（streamlit・AI呼び出し・
Fed確率計算だけをスタブに差し替え、データ取得と要約ロジックは本物のコードを試す）。
"""
import logging
import re
import types
from datetime import datetime
from typing import Any, Dict, Optional

import pandas as pd
import requests
import yfinance as yf

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("debug_rate_card")

_src = open("app.py", encoding="utf-8").read()


def _extract(start_marker: str, end_marker: str) -> str:
    s = _src.index(start_marker)
    return _src[s:_src.index(end_marker, s)]


_st = types.SimpleNamespace(cache_data=lambda **_k: (lambda f: f))
_ns = {
    "requests": requests, "datetime": datetime, "Optional": Optional, "Dict": Dict, "Any": Any,
    "logger": logger, "re": re, "pd": pd, "yf": yf, "st": _st,
    "_compute_fed_hike_probability": lambda: {"ok": True, "current_rate": 3.89},
    "call_ai_with_fallback": lambda *a, **k: ('{"verdict": "stub", "narrative": "stub"}', "stub"),
}
exec(_extract("def _fetch_macro_cape(", "\ndef _fetch_macro_lei("), _ns)
exec(_extract("_RATE_INFLATION_CONTEXT_NOTES = {", "\ndef render_rate_inflation_card("), _ns)

print("=== BLS batch (raw series found) ===")
_raw = _ns["_fetch_bls_macro_batch"]()
print({k: f"{len(v)} months, latest {max(v)}" for k, v in _raw.items()})

r = _ns["generate_rate_inflation_narrative"](datetime.now().strftime("%Y-%m-%d"))
print("=== Result values ===")
for k in ("ff_rate", "tnx_cur", "tnx_1y", "irx_cur", "cpi_yoy", "cpi_core_yoy", "ppi_yoy", "ppi_yoy_3m_ago",
          "wage_yoy", "unemp", "unemp_12m_low", "unemp_1y_ago", "nfp_3m_avg", "unemp_asof",
          "taylor_basis", "taylor_rate", "taylor_gap", "trailing_pe", "forward_pe", "implied_eps_growth",
          "equity_yield_gap", "real_equity_gap", "cape", "excess_cape_yield", "credit_3m", "yield_curve"):
    print(f"  {k}: {r.get(k)}")
print(f"  curve_type: {(r.get('curve_type') or {}).get('label')}")

for title, fn in (("利上げの余地", "_build_fed_room_summary"), ("株式投資の妙味", "_build_stock_vs_bond_summary")):
    sb = _ns[fn](r)
    print(f"=== {title} ===")
    if not sb:
        print("  (None)")
        continue
    print("■", sb["headline"])
    for p in sb["points"]:
        print("  -", p)
    if sb.get("favored"):
        print("  有利:", sb["favored"])
