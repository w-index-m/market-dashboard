"""
🏁 AIの予想の答え合わせ: 複数のAIに同じ市場データで約4週間後の相場を予想させて記録し、
期日が来た予想を採点する（平日毎朝GitHub Actionsで実行、data/ai_forecasts.jsonに蓄積）。
判定ロジックはapp.py（_ai_forecast_*、_grade_ai_forecast、_judge_ai_forecast_scenario）にあり、
ここは記録・保存の手順だけを担う。
"""
import json
import logging
from datetime import datetime, timezone

import pandas as pd
import yfinance as yf

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

import app  # noqa: E402

PATH = "data/ai_forecasts.json"


def load() -> dict:
    try:
        with open(PATH, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"records": []}


def main():
    data = load()
    recs = data["records"]
    now = pd.Timestamp.now()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # 1) 期日が来た予想を採点（方向は機械的に、シナリオは別のAIが審査）
    due = [r for r in recs if not r.get("grade")
           and pd.Timestamp(r["date"]) + pd.Timedelta(days=app._AI_FORECAST_HORIZON_DAYS) <= now]
    if due:
        start = min(pd.Timestamp(r["date"]) for r in due) - pd.Timedelta(days=5)
        raw = yf.download([v[0] for v in app._AI_FORECAST_ASSETS.values()], start=start,
                          progress=False, auto_adjust=True)["Close"]
        closes = {k: raw[v[0]].dropna() for k, v in app._AI_FORECAST_ASSETS.items()}
        for r in due:
            g = app._grade_ai_forecast(r, closes)
            if not g:
                continue
            r["grade"] = g
            j = app._judge_ai_forecast_scenario(r)
            if j:
                r["judge"] = j
            print(f"graded {r['id']}: hits={g['hits']} judge={j and j['verdict']}")

    # 2) 今日の予想を各AIから記録（同じ日・同じAIは1件まで）
    snap = app._ai_forecast_snapshot()
    if snap:
        prompt = app._ai_forecast_prompt(snap)
        for prov in app._AI_FORECAST_PROVIDERS:
            if any(r["date"] == today and app._provider_of(r.get("model", "")) == prov for r in recs):
                continue
            text, model = app._call_single_ai_provider(prov, prompt, 3000, 0.3)
            parsed = app._parse_ai_forecast(text) if text else None
            if parsed:
                recs.append({"id": f"{today}-{prov}", "date": today, "model": model, "snapshot": snap, **parsed})
            print(f"forecast {prov}: {'recorded ' + str(parsed['calls']) if parsed else 'no usable answer'}")
    else:
        print("snapshot unavailable; no forecasts recorded today")

    data["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    graded = sum(1 for r in recs if r.get("grade"))
    print(f"records={len(recs)} graded={graded} pending={len(recs) - graded}")


if __name__ == "__main__":
    main()
