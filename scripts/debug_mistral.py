"""Mistralのチャット呼び出しが失敗する理由を確認する（キーの値は表示しない）。"""
import os

import requests

key = os.environ.get("MISTRAL_API_KEY", "")
H = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
print(f"key set: {bool(key)} length={len(key)} has_whitespace={key != key.strip()} has_quote={chr(34) in key or chr(39) in key}")

r = requests.get("https://api.mistral.ai/v1/models", headers=H, timeout=20)
ids = sorted(m["id"] for m in r.json().get("data", []))
print("GET /v1/models:", r.status_code, "| chat-capable sample:", [i for i in ids if "latest" in i][:15])

for model in ("mistral-small-latest", "mistral-large-latest", "mistral-medium-latest", "open-mistral-nemo"):
    rr = requests.post("https://api.mistral.ai/v1/chat/completions", headers=H, timeout=30,
                       json={"model": model, "messages": [{"role": "user", "content": "1+1は？数字だけ"}], "max_tokens": 20})
    print(f"POST {model}: {rr.status_code} {rr.text[:300]!r}")

for path in ("/v1/models", "/v1/files", "/v1/fine_tuning/jobs"):
    rr = requests.get("https://api.mistral.ai" + path, headers=H, timeout=20)
    print(f"GET {path}: {rr.status_code} {rr.text[:120]!r}")
