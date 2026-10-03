"""国交省 不動産価格指数のExcelの中身を確認する（Actions上で実行）。"""
import io
import re

import pandas as pd
import requests

H = {"User-Agent": "Mozilla/5.0"}
BASE = "https://www.mlit.go.jp"
PAGE = BASE + "/totikensangyo/totikensangyo_tk5_000085.html"
r = requests.get(PAGE, headers=H, timeout=30)
r.encoding = r.apparent_encoding
for m in re.finditer(r'<a[^>]+href="([^"]+\.xlsx?)"[^>]*>(.*?)</a>', r.text, re.S | re.I):
    print("LINK", m.group(1), re.sub(r"<[^>]+>|\s+", " ", m.group(2)).strip()[:80])
for href in sorted(set(re.findall(r'href="([^"]+\.xlsx?)"', r.text, re.I))):
    try:
        x = requests.get(BASE + href, headers=H, timeout=60)
        print("==", href, x.status_code, len(x.content))
        xl = pd.ExcelFile(io.BytesIO(x.content))
        print("sheets:", xl.sheet_names[:20])
        for sn in xl.sheet_names[:4]:
            df = xl.parse(sn, header=None, nrows=8)
            print("--", sn, df.shape)
            print(df.iloc[:8, :8].to_string()[:900])
    except Exception as e:
        print("ERR", href, e)
