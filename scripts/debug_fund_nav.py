"""投信コードの基準価額がみんかぶから取得できるか確認する（Actions上で実行）。"""
import app

for code in ("0431218A", "04311181"):
    print(code, app._JP_FUND_MAP.get(code), app._fetch_jp_fund_nav(code))
print(app._resolve_fund_ticker_alias("ｉＦｒｅｅレバレッジ　ＮＡＳＤＡＱ１００".upper()))
