"""
🏁 AIの答え合わせの採点経路（機械的な方向判定＋別AIによるシナリオ審査）を、実際の株価と実AIで
今すぐ確かめるためのデバッグ用スクリプト。35日前に予想した体の記録を1件作って採点する。
"""
import logging

import pandas as pd
import yfinance as yf

logging.basicConfig(level=logging.WARNING)
import app  # noqa: E402

d0 = pd.Timestamp.now().normalize() - pd.Timedelta(days=35)
raw = yf.download([v[0] for v in app._AI_FORECAST_ASSETS.values()], start=d0 - pd.Timedelta(days=45),
                  progress=False, auto_adjust=True)["Close"]
closes = {k: raw[v[0]].dropna() for k, v in app._AI_FORECAST_ASSETS.items()}
snap = {}
for k, s in closes.items():
    before = s[s.index <= d0]
    snap[k] = {"value": float(before.iloc[-1]), "chg20_pct": round((float(before.iloc[-1]) / float(before.iloc[-21]) - 1) * 100, 2)}
rec = {"id": "test", "date": str(d0.date()), "model": "Gemini (gemini-2.5-flash)", "snapshot": snap,
       "calls": {"sp500": "up", "nikkei": "up", "tnx": "down", "usdjpy": "down"},
       "scenario": "インフレ鈍化で利下げ観測が強まり、金利低下と円高が進む一方、株価は金利低下を好感して上昇する"}
rec["grade"] = app._grade_ai_forecast(rec, closes)
print("grade:", rec["grade"])
print("judge:", app._judge_ai_forecast_scenario(rec))

snapshot_now = app._ai_forecast_snapshot()
print("snapshot now:", snapshot_now)
for prov in app._AI_FORECAST_PROVIDERS:
    text, model = app._call_single_ai_provider(prov, app._ai_forecast_prompt(snapshot_now), 1500, 0.3)
    print(f"{prov}: {model} -> {app._parse_ai_forecast(text) if text else None}")
