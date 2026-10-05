#!/usr/bin/env python3
"""
全モード比較表（1年・3年リターン、最大DD、シャープレシオ、銘柄リスト、チャート用の累積リターン）を
GitHub Actionsで事前計算し、data/mode_backtest.json に保存する。アプリは開いた時にこのファイルを読むだけで済む
（Streamlit Cloudの共有IPからYahooへ多数の銘柄を問い合わせる重い処理を避けるため）。
各モードの対象銘柄は、アプリの固定バスケット、または事前計算済みの候補（sp600/jp_tenbagger/marks）から決まる。保有銘柄は使わない。
チャート用の系列は週次に間引いて保存する（ファイルサイズを抑えるため。リターン・DD・シャープは日次の全データで計算済み）。
"""
import json
import sys
from datetime import datetime, timezone

import app

OUTPUT_PATH = "data/mode_backtest.json"
MODE_KEYS = ["growth", "momentum", "ai_mix", "optical_mix", "dividend_stable", "stable_growth", "marks", "jp_tenbagger"]


def _thin(r: dict) -> dict:
    """チャート用の dates/cum/bench_cum を5本おき（＋最終日）に間引く。"""
    if not r.get("ok") or not r.get("dates"):
        return r
    n = len(r["dates"])
    idx = sorted(set(list(range(0, n, 5)) + [n - 1]))
    out = dict(r)
    for k in ("dates", "cum", "bench_cum"):
        out[k] = [r[k][i] for i in idx]
    return out


def main() -> None:
    modes = {}
    for k in MODE_KEYS:
        try:
            r = app._compute_mode_basket_backtest_live.__wrapped__(k)
        except Exception as e:
            r = {"ok": False, "reason": f"計算エラー: {e}"}
        modes[k] = _thin(r)
        print(f"{k}: ok={r.get('ok')} 1y={r.get('ret_1y')} sharpe1y={r.get('sharpe_1y')}"
              + ("" if r.get("ok") else f" reason={str(r.get('reason'))[:80]}"))
    n_ok = sum(1 for r in modes.values() if r.get("ok"))
    print(f"ok {n_ok}/{len(MODE_KEYS)}")
    if n_ok < 6:
        print("成功したモードが少なすぎるため、既存ファイルを残して終了", file=sys.stderr)
        sys.exit(1)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump({"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "modes": modes},
                  f, ensure_ascii=False, separators=(",", ":"))
    print(f"wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
