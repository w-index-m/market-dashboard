#!/usr/bin/env python3
"""
日本時間0時（JST 00:00）に、登録済み全アカウント分のアセットクラス別
（日本株・米国株・投資信託・債券）評価額合計をasset_category_snapshotシートに記録する。

保有中ポジション表の「前日比（0時基準）」表示は、日本株・米国株で市場の開いている
時間帯が異なる（前営業日終値ベースだと更新タイミングがズレる）問題を避けるため、
全アセットクラス共通のこのゼロ時スナップショットを基準に計算する
（app._load_asset_category_snapshot / app._save_daily_asset_category_snapshot）。

通知は一切送らない（daily_portfolio_line.pyの市況サマリー等とは無関係の、記録専用
スクリプト）。GitHub Actionsのcronから00:00 JST（15:00 UTC）に1日1回だけ実行される想定。

必要な環境変数（GitHub Secretsから渡す想定）:
    GOOGLE_SHEETS_ID, GOOGLE_SERVICE_ACCOUNT_JSON  取引記録読込・スナップショット記録に必要
    TIINGO_API_KEY  任意（日本株の現在値取得フォールバックに使用）
"""
import os
import sys
from datetime import datetime

import pytz

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402  (app.py側でst.set_page_config()をimport時に実行しないようガード済み)

JST = pytz.timezone("Asia/Tokyo")


def main() -> None:
    today = datetime.now(JST).strftime("%Y-%m-%d")

    try:
        all_usernames = list(app._auth_get_users().keys())
    except Exception as e:
        print(f"failed to load user list: {e}", file=sys.stderr)
        all_usernames = ["admin"]

    saved = 0
    failures = []
    for username in all_usernames:
        try:
            ok, reason = app._save_daily_asset_category_snapshot(username, today)
            if ok:
                print(f"Saved asset category snapshot for {username}.")
                saved += 1
            else:
                print(f"asset category snapshot failed for {username}: {reason}", file=sys.stderr)
                failures.append(f"{username}: {reason}")
        except Exception as e:
            print(f"asset category snapshot failed for {username}: {e}", file=sys.stderr)
            failures.append(f"{username}: {e}")

    print(f"Done. {saved}/{len(all_usernames)} accounts saved.")
    # 1件も保存できなかった場合はジョブ自体を失敗させ、GitHub Actionsの実行結果
    # （conclusion）だけで異常に気付けるようにする（詳細ログを見なくても分かるように）。
    if all_usernames and saved == 0:
        print("All accounts failed:\n  " + "\n  ".join(failures), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
