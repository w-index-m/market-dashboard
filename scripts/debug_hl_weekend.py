"""週末のHyperliquid株式パーペチュアルの価格を、金曜の終値と比べて『月曜の寄り付きの方向』の目安にする（公開データのみ・汎用ティッカー）。"""
import requests
import yfinance as yf

URL = "https://api.hyperliquid.xyz/info"
NAMES = {"xyz": ["NVDA", "AMD", "AVGO", "MU", "INTC", "TSM", "MRVL", "SNDK", "WDC", "LITE", "AAOI", "GLW", "ORCL", "CRWV", "MSFT", "GOOGL", "META", "AMZN", "TSLA", "AAPL", "PLTR", "SMH"],
         "para": ["COHR", "CIEN", "STX", "AAOI", "GLW", "AVGO", "SMH"]}


def fetch(dex):
    r = requests.post(URL, json={"type": "metaAndAssetCtxs", "dex": dex}, timeout=30).json()
    meta, ctxs = r
    return {u["name"].split(":")[-1]: c for u, c in zip(meta["universe"], ctxs)}


perp = {}
for dex in NAMES:
    for k, v in fetch(dex).items():
        if k in NAMES[dex] and (k not in perp or float(v.get("dayNtlVlm") or 0) > float(perp[k][1].get("dayNtlVlm") or 0)):
            perp[k] = (dex, v)
syms = sorted(perp)
cl = yf.download(syms + ["^GSPC", "^N225"], period="10d", interval="1d", auto_adjust=True, progress=False)["Close"]
print("最新日(米国):", cl.index[-1].date())
print(f"{'銘柄':<7}{'perp':>5}{'perp価格':>10}{'金曜終値':>10}{'差':>8}  24h出来高($)  OI($換算)")
rows = []
for t in syms:
    dex, v = perp[t]
    px = float(v["markPx"])
    last = float(cl[t].dropna().iloc[-1]) if t in cl.columns else float("nan")
    rows.append((t, dex, px, last, (px / last - 1) * 100, float(v.get("dayNtlVlm") or 0), float(v.get("openInterest") or 0) * px))
for t, dex, px, last, d, vol, oi in sorted(rows, key=lambda x: x[4]):
    print(f"{t:<7}{dex:>5}{px:>10.2f}{last:>10.2f}{d:>+7.1f}%  {vol:>12,.0f} {oi:>12,.0f}")
for idx in ("SP500", "JP225"):
    for dex in ("xyz",):
        d = fetch(dex).get(idx)
        if d:
            print(idx, "perp:", d["markPx"], "oracle:", d["oraclePx"], "prevDay:", d["prevDayPx"], "vol24h:", d["dayNtlVlm"])
print("^GSPC 金曜終値:", float(cl["^GSPC"].dropna().iloc[-1]), "| ^N225 最新終値:", float(cl["^N225"].dropna().iloc[-1]), cl["^N225"].dropna().index[-1].date())
