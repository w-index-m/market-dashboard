"""古河電気工業の決算説明資料（2026-11-09）の本文を、ページ順に表示する（公開データのみ）。"""
import io
import subprocess
import sys

import requests

subprocess.run([sys.executable, "-m", "pip", "install", "-q", "pypdf"], check=False)
from pypdf import PdfReader  # noqa: E402

URL = "https://www.furukawaelectric.com/ir/library/finalreport/pdf/2027/20261109_pre.pdf"
d = requests.get(URL, headers={"User-Agent": "Mozilla/5.0 (compatible; market-dashboard-research)"}, timeout=90)
print("HTTP", d.status_code, len(d.content))
rd = PdfReader(io.BytesIO(d.content))
print("pages", len(rd.pages))
for i, p in enumerate(rd.pages, start=1):
    t = p.extract_text() or ""
    lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
    print(f"\n######## page {i} ({len(t)}字)")
    for ln in lines[:60]:
        print(ln[:190])
