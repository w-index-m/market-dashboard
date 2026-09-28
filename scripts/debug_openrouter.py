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

for mid in [m["id"] for m in free[:4]] + ["meta-llama/llama-3.3-70b-instruct:free", "openai/gpt-oss-20b:free"]:
    rr = requests.post("https://openrouter.ai/api/v1/chat/completions",
                       headers={**h, "Content-Type": "application/json"},
                       json={"model": mid, "messages": [{"role": "user", "content": "1+1は？数字だけ答えて"}],
                             "max_tokens": 50}, timeout=30)
    body = rr.text[:250].replace("\n", " ")
    print(f"POST {mid}: {rr.status_code} {body}")
