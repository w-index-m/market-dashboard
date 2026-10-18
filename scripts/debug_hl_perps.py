"""Hyperliquidの株式パーペチュアル（HIP-3）が、公開APIから読めるか・どの銘柄があるかを確認する（公開データのみ・汎用ティッカー）。"""

import requests

URL = "https://api.hyperliquid.xyz/info"
WATCH = ["NVDA", "AMD", "AVGO", "MU", "INTC", "TSM", "MRVL", "COHR", "LITE", "GLW", "CIEN", "AAOI", "VRT", "ORCL", "CRWV", "MSFT", "GOOGL", "META", "AMZN", "TSLA", "AAPL", "PLTR", "SMH", "QQQ", "SPY", "SP500", "NDX", "JP225", "KIOXIA", "SNDK", "WDC", "STX", "ANET", "APH"]


def post(body):
    r = requests.post(URL, json=body, timeout=30)
    return r.status_code, r.text[:300], (r.json() if r.status_code == 200 else None)


code, txt, dexs = post({"type": "perpDexs"})
print("perpDexs:", code, txt[:200])
names = [d.get("name") for d in (dexs or []) if d]
print("dexs:", names)
for dex in names[:12]:
    c, t, j = post({"type": "metaAndAssetCtxs", "dex": dex})
    if c != 200 or not j:
        print(dex, "ERR", c, t[:100])
        continue
    meta, ctxs = j
    uni = [u["name"] for u in meta["universe"]]
    hits = [u for u in uni if any(w.lower() == u.split(":")[-1].lower() for w in WATCH)]
    print(f"dex={dex} 銘柄数={len(uni)} 監視銘柄の一致={hits}")
    if hits:
        for u, x in zip(meta["universe"], ctxs):
            if u["name"] in hits[:8]:
                print("  ", u["name"], {k: x.get(k) for k in ("markPx", "oraclePx", "midPx", "prevDayPx", "dayNtlVlm", "openInterest", "funding")}, "maxLev", u.get("maxLeverage"))
