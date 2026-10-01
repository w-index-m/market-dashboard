"""決算後レビューが、AIの推奨プロンプト（新規ポートフォリオ生成・保有銘柄の総合分析）に実際に載るかを、
AI呼び出しを差し替えて確かめるテスト。送られるプロンプトの該当部分だけを表示する。"""
import json
import logging

logging.basicConfig(level=logging.WARNING)
import app  # noqa: E402

captured = []


def fake_ai(prompt, model_pref="auto", max_output_tokens=900, temperature=0.3):
    captured.append(prompt)
    return json.dumps({"portfolio": [], "metrics": {}}), "FAKE"


app._call_ai_for_trading = fake_ai


def show(label, prompt, key):
    print(f"=== {label} ===")
    i = prompt.find(key)
    print("block present:", i >= 0)
    if i >= 0:
        print(prompt[max(0, i - 40): i + 900])
    for rule in ("決算後レビュー】に🔴要見直し", "直近の決算後レビュー"):
        print(f"contains '{rule}':", rule in prompt)


ctx = {"fg_score": 50, "crash_risk_score": 3, "vix_val": 15.0, "naaim_exp": 70}
perf = {"MU": {"ret_3m": 20.0, "ret_6m": 50.0, "ret_1y": 300.0, "price": 1065.0},
        "NVDA": {"ret_3m": 5.0, "ret_6m": 10.0, "ret_1y": 40.0, "price": 228.0}}
agent_a = {"stance": "中立", "sector_bias": ["半導体"], "key_risks": ["金利"], "comment": "test"}
agent_b = {t: {"ticker": t, "score": 7, "thesis": "x", "conclusion": "買い", "merits": [{"point": "a"}], "demerits": [{"point": "b"}]}
           for t in perf}
for label, a, b in (("Agent C short prompt (agent A+B)", agent_a, agent_b), ("fallback full prompt", None, None)):
    captured.clear()
    try:
        app._generate_investment_portfolio_rec(1_000_000, "individual", "aggressive", ctx, [], "auto", "ai_mix", perf, a, b)
        show(label, captured[-1], "【直近の決算後レビュー")
    except Exception:
        import traceback
        traceback.print_exc()

print("=== holdings-based full recommendation ===")
captured.clear()
pos = {"MU": {"name": "Micron", "qty": 10, "avg_cost": 900.0, "cost": 9000.0, "market_value": 10650.0}}
sd = {"MU": {"price": 1065.0, "rsi": 60, "ma25": 1050, "ma75": 950}}
try:
    app._generate_full_portfolio_recommendation(pos, sd, ctx, "growth", "auto")
    show("holdings prompt", captured[0], "直近の決算後レビュー（数字のしきい値による機械判定）")
except Exception:
    import traceback
    traceback.print_exc()
