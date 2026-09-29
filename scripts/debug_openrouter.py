"""OpenRouterが応答しない原因を調べるデバッグ用スクリプト（キーの値は表示しない）。"""
import os

import requests

key = os.environ.get("OPENROUTER_API_KEY", "")
print(f"key set: {bool(key)} (length {len(key)})")
h = {"Authorization": f"Bearer {key}"}

r = requests.get("https://openrouter.ai/api/v1/key", headers=h, timeout=15)
print("GET /key:", r.status_code, r.text[:400])

r = requests.get("https://openrouter.ai/api/v1/models", timeout=15)
models = r.json().get("data", [])
free = [m for m in models if str(m.get("id", "")).endswith(":free")]
print(f"models total={len(models)} free={len(free)}")
print("first 4 free (what the app uses):", [m["id"] for m in free[:4]])
print("free models:", [m["id"] for m in free][:40])

# アプリ本体の呼び出し経路（モデルの選び方・上限時の打ち切りを含む）でも確認する
try:
    import app
    print("app model order:", app._fetch_openrouter_free_models()[:4])
    text, model = app.summarize_with_openrouter("日本の首都はどこ？一語で答えて", max_tokens=200)
    print(f"app.summarize_with_openrouter -> model={model!r} text={text[:200]!r}")
    print("app._call_single_ai_provider ->", app._call_single_ai_provider("openrouter", "1+1は？数字だけ答えて", 200, 0.1))
except Exception as e:
    print("app path error:", e)
