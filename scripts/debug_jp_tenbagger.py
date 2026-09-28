"""
🚀日本株10倍株候補モードの候補選定（事前計算データ→STEP1→AI評価→70点基準）を、
本番と同じapp.pyのコード・実データ・実AIでGitHub Actions上で通しで確かめるデバッグ用スクリプト。
"""
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
for _noisy in ("yfinance", "urllib3", "httpx", "google_genai", "httpcore"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

import app  # noqa: E402

pre = app._load_precomputed_jp_tenbagger()
print(f"precomputed: generated={pre and pre.get('generated_at')} tickers={pre and len(pre['tickers'])}")
rows = [r for r in (app._jp_tenbagger_step1(t, d.get("name"), d.get("info") or {})
                    for t, d in pre["tickers"].items()) if r]
for r in rows:
    r["_det"] = r["score_growth"] + r["score_value"] + r["score_finance"]
rows.sort(key=lambda r: -r["_det"])
print(f"STEP1 passed: {len(rows)}  top20 STEP1 range: {rows[19]['_det']:.1f}〜{rows[0]['_det']:.1f}")

ai = app._score_jp_tenbagger_qualitative(rows[:20])
print(f"AI scored: {len(ai)}/20")
totals = []
for r in rows[:20]:
    a = ai.get(r["ticker"], {})
    tot = r["_det"] + a.get("score_moat", 0) + a.get("score_10x", 0)
    totals.append(tot)
    print(f"  {r['ticker']:8s} {r['name'][:12]:12s} STEP1={r['_det']:5.1f} 競争優位={a.get('score_moat', '-')!s:>4} "
          f"10倍余地={a.get('score_10x', '-')!s:>4} 合計={tot:5.1f} {a.get('comment', '')}")
print(f">=70: {sum(t >= 70 for t in totals)}")

res = app._fetch_jp_tenbagger_candidates()
print(f"_fetch_jp_tenbagger_candidates(): {len(res)} candidates -> {list(res)[:10]}")
