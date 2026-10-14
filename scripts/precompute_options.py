#!/usr/bin/env python3
"""
保有している米国株のオプション（決算前の想定変動幅・IV・Put/Call建玉）を、取引時間中に取得して、
非公開のGoogle Sheets（options_cacheタブ）へ保存する。アプリは開いた時にこのシートを読むだけで済む。
保有銘柄は実行時に取引記録から読み、リポジトリ・ログには出さない（件数だけ表示）。
環境変数: GOOGLE_SHEETS_ID, GOOGLE_SERVICE_ACCOUNT_JSON, TRADING_USERNAME（既定 admin）
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
        print("::notice title=options::取引記録なし")
        return
    positions = app._calc_positions_from_df(trades_df)
    tickers = [t for t in positions if not t.endswith(".T") and t not in app._JP_FUND_MAP]
    print(f"対象 {len(tickers)} 銘柄（米国）")
    items = {}
    with cf.ThreadPoolExecutor(max_workers=4) as ex:
        for t, d in zip(tickers, ex.map(app._fetch_option_summary, tickers)):
            if d:
                items[t] = d
    print(f"オプション取得 {len(items)}/{len(tickers)} 銘柄")
    if tickers and not items:
        print("1銘柄も取得できなかったため、保存せず異常終了", file=sys.stderr)
        sys.exit(1)
    n = app._save_options_cache(items) if items else 0
    print(f"::notice title=options::対象{len(tickers)}銘柄 / 取得{len(items)} / 保存{n}")


if __name__ == "__main__":
    main()
