"""Mistralのキーが有効か、アプリ本体の呼び出し経路で応答が取れるかを確認する（キーの値は表示しない）。"""
import os

import requests

key = os.environ.get("MISTRAL_API_KEY", "")
print(f"MISTRAL_API_KEY set: {bool(key)} (length {len(key)})")
if key:
    r = requests.get("https://api.mistral.ai/v1/models", headers={"Authorization": f"Bearer {key}"}, timeout=20)
    print("GET /v1/models:", r.status_code)
    if r.ok:
        ids = sorted(m["id"] for m in r.json().get("data", []))
        print("has small/large-latest:", "mistral-small-latest" in ids, "mistral-large-latest" in ids, "| total models", len(ids))

import app  # noqa: E402

print("summarize_with_mistral:", app.summarize_with_mistral("日本の首都はどこ？一語で答えて", max_tokens=50))
prompt = app._ai_forecast_prompt(app._ai_forecast_snapshot())
text, model = app._call_single_ai_provider("mistral", prompt, 3000, 0.3)
print("forecast call:", model, "->", app._parse_ai_forecast(text) if text else None)
row = {"ticker": "6150.T", "name": "タケダ機械", "sector": "Industrials", "industry": "Machinery", "per": 8.6,
       "rev_growth": 55.6, "earnings_growth": 114.3, "op_margin": 12.0, "summary": "特殊産業機械の製造", "score_moat": 12,
       "score_10x": 14, "ai_comment": "高成長の専門機械", "ai_scored_by": "Groq (openai/gpt-oss-20b)"}
print("judge (skips scorer's provider, tries mistral 2nd):", app._judge_one_jp_tenbagger(row))
