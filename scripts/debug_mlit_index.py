"""国交省 不動産価格指数(住宅・月次)の東京都シートの構造を確認する（Actions上で実行）。"""
import io

import pandas as pd
import requests

H = {"User-Agent": "Mozilla/5.0"}
x = requests.get("https://www.mlit.go.jp/totikensangyo/content/001473668.xlsx", headers=H, timeout=60)
xl = pd.ExcelFile(io.BytesIO(x.content))
print([s for s in xl.sheet_names if "東京" in s])
pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 30)
for sn in [s for s in xl.sheet_names if "東京都" in s]:
    df = xl.parse(sn, header=None)
    print("--", sn, df.shape)
    print(df.iloc[3:10].to_string()[:2500])
    print(df.tail(6).to_string()[:2500])
