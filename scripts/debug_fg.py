"""CNN Fear & Greedを、アプリと同じ条件で取得できるか・自前計算のフォールバックがあるかを確認する。"""
import requests

H_APP = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
H_BROWSER = {**H_APP, "Accept": "application/json, text/plain, */*", "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
             "Origin": "https://edition.cnn.com", "Referer": "https://edition.cnn.com/markets/fear-and-greed"}
for label, url, h in (
    ("app条件(日付付きURL・簡易UA)", "https://production.dataviz.cnn.io/index/fearandgreed/graphdata/2022-01-01", H_APP),
    ("日付なしURL・簡易UA", "https://production.dataviz.cnn.io/index/fearandgreed/graphdata", H_APP),
    ("日付付き・ブラウザ風ヘッダー(Origin/Referer)", "https://production.dataviz.cnn.io/index/fearandgreed/graphdata/2022-01-01", H_BROWSER),
    ("日付なし・ブラウザ風ヘッダー", "https://production.dataviz.cnn.io/index/fearandgreed/graphdata", H_BROWSER),
):
    try:
        r = requests.get(url, headers=h, timeout=15)
        extra = ""
        if r.ok:
            j = r.json()
            extra = f" score={j['fear_and_greed']['score']:.0f} rating={j['fear_and_greed']['rating']} ts={j['fear_and_greed']['timestamp'][:19]}"
        print(f"{label}: HTTP {r.status_code}{extra}")
    except Exception as e:
        print(f"{label}: {type(e).__name__} {str(e)[:80]}")
