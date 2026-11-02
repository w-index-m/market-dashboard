"""SEC EDGARで、指定した会社名の直近の8-K等を探し、提出書類の一覧と、契約書（Exhibit 2.1）の主要条項の抜粋を表示する（公開データのみ）。
環境変数: EDGAR_CONTACT（必須）、LOOKUP_NAMES（カンマ区切り、既定 "Lumentum,Marvell"）、LOOKUP_SINCE（既定 2026-10-29）。"""
import os
import re
import sys
from datetime import datetime, timezone

import requests

contact = os.environ.get("EDGAR_CONTACT", "").strip()
if not contact:
    print("EDGAR_CONTACT 未設定")
    sys.exit(0)
H = {"User-Agent": f"market-dashboard-watch {contact}", "Accept": "application/json, text/html, */*"}
names = [n.strip() for n in os.environ.get("LOOKUP_NAMES", "Lumentum,Marvell").split(",") if n.strip()]
since = os.environ.get("LOOKUP_SINCE", "2026-10-29")
end = datetime.now(timezone.utc).strftime("%Y-%m-%d")
KEYS = ["Outside Date", "Termination Fee", "Parent Termination Fee", "Company Termination Fee", "Exchange Ratio", "HSR", "SAMR", "regulatory approval", "Required Approvals", "financing", "collar"]


def strip_html(t):
    t = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", t)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    t = re.sub(r"&nbsp;|&#160;", " ", t)
    t = re.sub(r"&amp;", "&", t)
    return re.sub(r"\s+", " ", t)


seen = set()
for nm in names:
    r = requests.get("https://efts.sec.gov/LATEST/search-index", params={"q": f'"{nm}"', "forms": "8-K,425,S-4,SC 13D", "dateRange": "custom", "startdt": since, "enddt": end}, headers=H, timeout=30)
    print(f"== {nm}: HTTP {r.status_code}")
    if r.status_code != 200:
        continue
    hits = r.json().get("hits", {}).get("hits", [])
    for h in hits:
        s = h.get("_source", {})
        dn = ", ".join(s.get("display_names", []) or [])
        if nm.lower() not in dn.lower():
            continue
        adsh, _, fn = (h.get("_id") or "").partition(":")
        key = (adsh, fn)
        if key in seen:
            continue
        seen.add(key)
        cik = (s.get("ciks") or [""])[0].lstrip("0")
        url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{adsh.replace('-', '')}/{fn}"
        print(f"- {s.get('file_date')} {s.get('form')} items={s.get('items')} {dn}\n  {url}")
        if re.search(r"(ex2|ex-2|exhibit2|merger|agreement)", fn, re.I) or s.get("form") == "8-K":
            try:
                d = requests.get(url, headers=H, timeout=40)
                txt = strip_html(d.text) if d.status_code == 200 else ""
            except Exception as e:
                txt = ""
                print("  fetch error", type(e).__name__)
            if not txt:
                continue
            print(f"  (本文 {len(txt):,} 文字)")
            shown = 0
            for k in KEYS:
                for m in list(re.finditer(re.escape(k), txt, re.I))[:1]:
                    a = max(0, m.start() - 120)
                    print(f"  [{k}] …{txt[a:m.end() + 260]}…")
                    shown += 1
            if shown == 0:
                print("  先頭:", txt[:500])
