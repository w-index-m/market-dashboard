#!/usr/bin/env python3
"""
CFTC「Traders in Financial Futures（TFF）」建玉報告（先物のみ）から、株価指数・米国債などの先物について、
レバレッジドファンド（ヘッジファンド等）・アセットマネージャー（年金・投信等）・ディーラーの建玉（買い－売りの差）を
週次で集計し、data/cot_positions.json に保存する（GitHub Actionsで週1回実行）。公開データのみを使う。
"""
import io
import json
import sys
import zipfile
from datetime import datetime, timezone

import pandas as pd
import requests

OUTPUT_PATH = "data/cot_positions.json"
H = {"User-Agent": "Mozilla/5.0"}
# 表示名 → 市場名に含まれる文字列（CFTCの市場名。最初に見つかった1市場を使う）
MARKETS = {
    "E-mini S&P500先物": "E-MINI S&P 500 - CHICAGO MERCANTILE EXCHANGE",
    "E-mini ナスダック100先物": "NASDAQ MINI - CHICAGO MERCANTILE EXCHANGE",
    "日経225先物（ドル建て）": "NIKKEI STOCK AVERAGE - CHICAGO MERCANTILE EXCHANGE",
    "米国債10年先物": "UST 10Y NOTE - CHICAGO BOARD OF TRADE",
    "米国債2年先物": "UST 2Y NOTE - CHICAGO BOARD OF TRADE",
    "米国債30年（長期債）先物": "UST BOND - CHICAGO BOARD OF TRADE",
    "円先物": "JAPANESE YEN - CHICAGO MERCANTILE EXCHANGE",
    "VIX先物": "VIX FUTURES - CBOE FUTURES EXCHANGE",
}


def _year_df(year: int) -> pd.DataFrame:
    r = requests.get(f"https://www.cftc.gov/files/dea/history/fut_fin_txt_{year}.zip", headers=H, timeout=90)
    r.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(r.content))
    return pd.read_csv(io.BytesIO(z.read(z.namelist()[0])), low_memory=False)


def main() -> None:
    this = datetime.now(timezone.utc).year
    frames = []
    for y in range(this - 3, this + 1):
        try:
            frames.append(_year_df(y))
        except Exception as e:
            print(f"{y}: failed {e}")
    if not frames:
        print("取得できなかったため終了", file=sys.stderr)
        sys.exit(1)
    df = pd.concat(frames, ignore_index=True)
    df["d"] = pd.to_datetime(df["Report_Date_as_YYYY-MM-DD"])
    out = {}
    for label, key in MARKETS.items():
        sub = df[df["Market_and_Exchange_Names"].astype(str).str.strip() == key]
        if sub.empty:
            # 完全一致が無ければ、先頭部分（市場名）の一致で探す
            head = key.split(" - ")[0]
            cand = df[df["Market_and_Exchange_Names"].astype(str).str.startswith(head)]
            sub = cand[cand["Market_and_Exchange_Names"] == cand["Market_and_Exchange_Names"].iloc[0]] if len(cand) else cand
        if sub.empty:
            print(f"{label}: not found")
            continue
        sub = sub.sort_values("d").drop_duplicates("d")
        rows = []
        for _, r in sub.iterrows():
            rows.append([r["d"].strftime("%Y-%m-%d"),
                         int(r["Lev_Money_Positions_Long_All"] - r["Lev_Money_Positions_Short_All"]),
                         int(r["Asset_Mgr_Positions_Long_All"] - r["Asset_Mgr_Positions_Short_All"]),
                         int(r["Dealer_Positions_Long_All"] - r["Dealer_Positions_Short_All"]),
                         int(r["Open_Interest_All"])])
        out[label] = {"market": str(sub["Market_and_Exchange_Names"].iloc[0]), "rows": rows}
        print(f"{label}: {len(rows)} weeks, latest {rows[-1][0]}")
    if len(out) < 4:
        print("取得できた市場が少なすぎるため、既存ファイルを残して終了", file=sys.stderr)
        sys.exit(1)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump({"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "columns": ["date", "lev_net", "am_net", "dealer_net", "open_interest"], "markets": out},
                  f, ensure_ascii=False, separators=(",", ":"))
    print(f"wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
