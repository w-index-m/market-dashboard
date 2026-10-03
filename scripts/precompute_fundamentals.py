#!/usr/bin/env python3
"""
保有銘柄の業績の推移（四半期EPS・BPS・売上・純利益・設備投資・研究開発費など）を事前に取得し、
Google Sheetsの fundamentals_cache タブ（非公開）に保存する。アプリは開いた時にこのシートを読むだけで済む。

保有銘柄は取引記録（Google Sheets）から実行時に読む。公開リポジトリのため、保有銘柄の一覧は
リポジトリ・ログには出さない（件数だけ表示）。

環境変数: GOOGLE_SHEETS_ID, GOOGLE_SERVICE_ACCOUNT_JSON, TRADING_USERNAME（既定 admin）
実行: python scripts/precompute_fundamentals.py
"""
import concurrent.futures as cf
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402


def main() -> None:
    username = os.environ.get("TRADING_USERNAME", "admin")
    trades_df, err = app._load_trades(username)
    if trades_df.empty:
        print(f"取引記録なし: {err}")
        return
    positions = app._calc_positions_from_df(trades_df)
    tickers = [t for t in positions if t not in app._JP_FUND_MAP]
    print(f"対象 {len(tickers)} 銘柄")
    items = {}
    with cf.ThreadPoolExecutor(max_workers=4) as ex:
        for t, d in zip(tickers, ex.map(app._fetch_fundamentals_history, tickers)):
            if d:
                items[t] = d
    print(f"業績データ取得 {len(items)}/{len(tickers)} 銘柄")
    if not items:
        print("1銘柄も取得できなかったため、保存せず異常終了", file=sys.stderr)
        sys.exit(1)
    n = app._save_fundamentals_cache(items)
    print(f"Sheetsへ保存 {n} 銘柄")
    if n == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
