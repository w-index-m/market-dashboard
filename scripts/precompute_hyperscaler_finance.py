#!/usr/bin/env python3
"""
AI設備投資の大手各社の、四半期ごとの営業CF・設備投資・FCF・有利子負債・現金を、yfinanceの財務諸表から集めて data/hyperscaler_finance.json に保存する（公開データのみ）。
単位は10億ドル。取得できない項目は null。yfinanceの値は集計の都合で、各社の決算書の表示とずれることがあるため、最終確認は決算資料で行う。
"""
import json
import os
import sys
from datetime import datetime, timezone

import pandas as pd
import yfinance as yf

OUT = os.path.join(os.path.dirname(__file__), "..", "data", "hyperscaler_finance.json")
COMPANIES = {"MSFT": "マイクロソフト", "GOOGL": "アルファベット", "AMZN": "アマゾン", "META": "メタ", "ORCL": "オラクル", "CRWV": "コアウィーブ"}
CF_ROWS = {"ocf": ("Operating Cash Flow", "Cash Flow From Continuing Operating Activities"), "capex": ("Capital Expenditure",), "fcf": ("Free Cash Flow",)}
BS_ROWS = {"debt": ("Total Debt",), "cash": ("Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents"), "lease": ("Capital Lease Obligations",)}


def _pick(df: pd.DataFrame, names: tuple, col) -> float | None:
    for n in names:
        if df is not None and n in df.index:
            v = df.at[n, col]
            if pd.notna(v):
                return round(float(v) / 1e9, 2)
    return None


def collect(sym: str) -> dict:
    t = yf.Ticker(sym)
    cf, bs = t.quarterly_cashflow, t.quarterly_balance_sheet
    rows = []
    cols = sorted(set(cf.columns) | set(bs.columns), reverse=True)[:6] if cf is not None and bs is not None else []
    for c in cols:
        r = {"period": str(pd.Timestamp(c).date())}
        for k, names in CF_ROWS.items():
            r[k] = _pick(cf, names, c) if c in cf.columns else None
        for k, names in BS_ROWS.items():
            r[k] = _pick(bs, names, c) if c in bs.columns else None
        if r["capex"] is not None:
            r["capex"] = abs(r["capex"])
        if r["fcf"] is None and r["ocf"] is not None and r["capex"] is not None:
            r["fcf"] = round(r["ocf"] - r["capex"], 2)
        rows.append(r)
    return {"name": COMPANIES[sym], "quarters": rows}


def main() -> None:
    res = {}
    for s in COMPANIES:
        try:
            res[s] = collect(s)
        except Exception as e:
            print(f"{s} failed: {type(e).__name__}: {e}", file=sys.stderr)
    for s, v in res.items():
        print(s, v["name"])
        for q in v["quarters"][:5]:
            print("  ", q)
    if "--write" in sys.argv and res:
        with open(OUT, "w", encoding="utf-8") as f:
            json.dump({"updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "unit": "10億ドル（yfinanceの財務諸表）", "companies": res}, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
