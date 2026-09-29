"""アプリ本体の経路でMistralが応答するかを確認する（キーの値は表示しない）。"""
import time

import app

print("summarize_with_mistral:", app.summarize_with_mistral("日本の首都はどこ？一語で答えて", max_tokens=50))
time.sleep(3)
prompt = app._ai_forecast_prompt(app._ai_forecast_snapshot())
text, model = app._call_single_ai_provider("mistral", prompt, 3000, 0.3)
print("forecast call:", model, "->", app._parse_ai_forecast(text) if text else None)
time.sleep(3)
row = {"ticker": "6150.T", "name": "タケダ機械", "sector": "Industrials", "industry": "Machinery", "per": 8.6,
       "rev_growth": 55.6, "earnings_growth": 114.3, "op_margin": 12.0, "summary": "特殊産業機械の製造", "score_moat": 12,
       "score_10x": 14, "ai_comment": "高成長の専門機械", "ai_scored_by": "Groq (openai/gpt-oss-20b)"}
print("judge:", app._judge_one_jp_tenbagger(row))
