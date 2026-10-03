#!/usr/bin/env python3
"""
💎 マークスモード用: 候補銘柄（app._TRADING_CANDIDATES）のアナリスト予想EPSと現在株価を取得し、
data/marks_valuation.json に保存する（GitHub Actionsで毎日実行）。

Streamlit Cloudの共有IPからYahoo Financeへ多数の銘柄を問い合わせると失敗しやすいため、
Yahooに繋がるActions側で事前取得し、アプリは予想PERの計算（価格÷予想EPS）だけを行う。
保有銘柄ではなくアプリの固定の候補リストを使う（公開リポジトリのため）。
"""
import concurrent.futures as cf
import json
import sys
from datetime import datetime, timezone

import app

OUTPUT_PATH = "data/marks_valuation.json"


def main() -> None:
    tickers = [t for t in dict.fromkeys(app._TRADING_CANDIDATES) if t not in app._JP_ETF_TICKERS]
    print(f"universe: {len(tickers)} tickers")
    items = {}
    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        for t, e in zip(tickers, ex.map(app._fetch_eps_estimates.__wrapped__, tickers)):
            if not e or not e.get("price") or not e.get("cy"):
                continue
            items[t] = {"price": round(e["price"], 4), "cy": round(e["cy"]["avg"], 4),
                        "yago": e["cy"].get("year_ago"),
                        "ny": round(e["ny"]["avg"], 4) if e.get("ny") else None}
    print(f"got estimates for {len(items)}/{len(tickers)}")
    if len(items) < max(10, len(tickers) // 4):
        print("取得できた銘柄が少なすぎるため、既存ファイルを残して終了", file=sys.stderr)
        sys.exit(1)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump({"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "items": items},
                  f, ensure_ascii=False, separators=(",", ":"))
    print(f"wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
