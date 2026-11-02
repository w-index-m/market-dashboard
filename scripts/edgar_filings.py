"""SEC EDGARの提出書類一覧（data.sec.gov）から、指定会社の直近の提出書類と、合併契約に関する8-Kの主要条項を表示する（公開データのみ）。
環境変数: EDGAR_CONTACT（必須）、FILING_CIKS（カンマ区切り、既定 Lumentum=1633978, Marvell Technology, Inc.=1835632（旧Bermuda法人の1058057は2021年で止まっている））。"""
import os
import re
import sys

import requests

contact = os.environ.get("EDGAR_CONTACT", "").strip()
if not contact:
    print("EDGAR_CONTACT 未設定")
    sys.exit(0)
H = {"User-Agent": f"market-dashboard-watch {contact}", "Accept": "application/json, text/html, */*"}
CIKS = [c.strip() for c in os.environ.get("FILING_CIKS", "1633978,1835632").split(",") if c.strip()]
KEYS = ["Termination Fee", "Outside Date", "Exchange Ratio", "Required Regulatory", "HSR", "antitrust", "SAMR", "Financing", "Board of Directors of the Combined", "stockholder approval", "Voting Agreement"]


def strip_html(t):
    t = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", t)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    t = re.sub(r"&#8217;|&#8220;|&#8221;|&nbsp;|&#160;", " ", t)
    t = re.sub(r"&amp;", "&", t)
    return re.sub(r"\s+", " ", t)


for cik in CIKS:
    r = requests.get(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json", headers=H, timeout=30)
    print(f"== CIK {cik}: HTTP {r.status_code}")
    if r.status_code != 200:
        continue
    j = r.json()
    rec = j["filings"]["recent"]
    print(j.get("name"))
    rows = list(zip(rec["filingDate"], rec["form"], rec["items"], rec["accessionNumber"], rec["primaryDocument"], rec.get("primaryDocDescription", [""] * len(rec["form"]))))[:10]
    for d, f, it, acc, doc, desc in rows:
        print(f"- {d} {f} items={it} {desc}\n  https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/{doc}")
    for d, f, it, acc, doc, desc in rows:
        if f == "8-K" and d >= "2026-11-02" and "2.02" in it:
            # 決算の8-K: 添付の決算発表（Exhibit 99.1）の冒頭を表示する
            base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/"
            idx = requests.get(base, headers=H, timeout=30)
            exs = re.findall(r'href="([^"]*ex99[^"]*\.htm)"', idx.text, re.I) if idx.status_code == 200 else []
            if exs:
                t = requests.get("https://www.sec.gov" + exs[0] if exs[0].startswith("/") else base + exs[0], headers=H, timeout=40)
                txt = strip_html(t.text) if t.status_code == 200 else ""
                print(f"\n#### 決算発表 {d} {exs[0]} 本文{len(txt):,}文字\n{txt[:3200]}")
        if f == "8-K" and d >= "2026-11-02" and ("1.01" in it or "8.01" in it or "7.01" in it):
            url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/{doc}"
            t = requests.get(url, headers=H, timeout=40)
            txt = strip_html(t.text) if t.status_code == 200 else ""
            print(f"\n#### 合併関連8-K {d} items={it} 本文{len(txt):,}文字")
            i1 = txt.find("Item 1.01")
            print(txt[i1:i1 + 3800] if i1 >= 0 else txt[:3000])
            # 解約条項（アウトサイドデート・解約違約金）の本文を、まとまりで表示する
            for key, width in (("terminated by either", 1400), ("Termination Fees", 1800), ("Charter Amendment", 700)):
                m0 = re.search(re.escape(key), txt, re.I)
                if m0:
                    print(f"\n[[{key}]] {txt[max(0, m0.start() - 200):m0.start() + width]}")
            for k in KEYS:
                m = re.search(re.escape(k), txt, re.I)
                if m:
                    a = max(0, m.start() - 150)
                    print(f"[{k}] …{txt[a:m.end() + 350]}…")
