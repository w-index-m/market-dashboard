"""
国土交通省「不動産価格指数（住宅）」の東京都（季節調整値・月次）を取得し、
data/tokyo_re_index.json に保存する。

住宅総合・住宅地・戸建住宅・マンション（区分所有）の4系列。23区単位ではなく東京都全体の指数。
公表ページのExcelリンクのうち、東京都の季節調整シートを持つファイルを全て調べ、
最新月が最も新しいものを採用する（訂正版「(正)」シートがある場合はそちらを優先）。
"""
import io
import json
import re
import sys
from datetime import datetime, timezone

import pandas as pd
import requests

BASE = "https://www.mlit.go.jp"
PAGE = BASE + "/totikensangyo/totikensangyo_tk5_000085.html"
OUTPUT_PATH = "data/tokyo_re_index.json"
H = {"User-Agent": "Mozilla/5.0"}
# 東京都シートの列: 日付 / [指数, 前月比, サンプル数] × 住宅総合・住宅地・戸建住宅・マンション
SERIES_COLS = {"residential": 1, "land": 4, "house": 7, "condo": 10}


def _parse_sheet(df: pd.DataFrame) -> dict:
    out = {k: {} for k in SERIES_COLS}
    for _, row in df.iterrows():
        d = row.iloc[0]
        if not isinstance(d, (pd.Timestamp, datetime)):
            continue
        key = pd.Timestamp(d).strftime("%Y-%m")
        for name, col in SERIES_COLS.items():
            v = pd.to_numeric(row.iloc[col], errors="coerce")
            if pd.notna(v):
                out[name][key] = round(float(v), 2)
    return out


def main():
    r = requests.get(PAGE, headers=H, timeout=60)
    hrefs = sorted(set(re.findall(r'href="([^"]+\.xlsx?)"', r.text, re.I)))
    best, best_last, best_src = None, "", ""
    for href in hrefs:
        try:
            x = requests.get(BASE + href, headers=H, timeout=120)
            xl = pd.ExcelFile(io.BytesIO(x.content))
        except Exception as e:
            print(f"skip {href}: {e}")
            continue
        sheets = [s for s in xl.sheet_names if "東京都" in s and "季節調整" in s and "(誤)" not in s]
        # 訂正版「(正)」があれば優先
        sheets.sort(key=lambda s: 0 if "(正)" in s else 1)
        for sn in sheets[:1]:
            parsed = _parse_sheet(xl.parse(sn, header=None))
            months = sorted(parsed["residential"])
            if not months:
                continue
            print(f"{href} [{sn}] {months[0]} .. {months[-1]} ({len(months)}ヶ月)")
            if months[-1] > best_last:
                best, best_last, best_src = parsed, months[-1], f"{href}#{sn}"
    if not best or best_last == "":
        print("no tokyo sheet found — keeping the existing file")
        sys.exit(1)
    # 2010年以降に絞る（アプリで使うのは直近10年程度）
    best = {k: {m: v for m, v in d.items() if m >= "2010-01"} for k, d in best.items()}
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump({"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "source": best_src, "latest": best_last, "series": best},
                  f, ensure_ascii=False, separators=(",", ":"))
    print(f"wrote {OUTPUT_PATH}: latest={best_last} source={best_src}")


if __name__ == "__main__":
    main()
