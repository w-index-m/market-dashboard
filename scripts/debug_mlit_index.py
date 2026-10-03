"""国交省 不動産価格指数のデータ(CSV/Excel)の所在を確認する（Actions上で実行）。"""
import re

import requests

H = {"User-Agent": "Mozilla/5.0"}
PAGES = [
    "https://www.mlit.go.jp/totikensangyo/totikensangyo_tk5_000085.html",
    "https://www.mlit.go.jp/totikensangyo/real_estate_price_index.html",
    "https://www.mlit.go.jp/statistics/details/t-kakaku_tk5_000085.html",
]
for u in PAGES:
    try:
        r = requests.get(u, headers=H, timeout=30)
        print("==", u, r.status_code, len(r.text))
        if r.status_code == 200:
            t = re.search(r"<title>(.*?)</title>", r.text, re.S)
            print("title:", t.group(1).strip() if t else None)
            links = sorted(set(re.findall(r'href="([^"]+\.(?:csv|xlsx?|zip))"', r.text, re.I)))
            for link in links[:40]:
                print("  ", link)
    except Exception as e:
        print("ERR", u, e)
