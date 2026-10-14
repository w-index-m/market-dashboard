"""セクター資金フロー・インサイダー集計の取得を確認する（Actions上、一般的な銘柄）。"""
import app

for m in ("US", "JP"):
    df = app._fetch_sector_flow.__wrapped__(m)
    print(m, df.shape)
    print(df.sort_values("r1m", ascending=False).head(4).round(1).to_string())
for t in ("AAPL", "NVDA", "KO"):
    print(t, app._insider_summary.__wrapped__(t))
