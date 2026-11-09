"""古河電気工業の決算短信（2026-11-09）の本文を、ページ順に表示する（公開データのみ）。"""
import io
import subprocess
import sys

import requests

subprocess.run([sys.executable, "-m", "pip", "install", "-q", "pypdf"], check=False)
from pypdf import PdfReader  # noqa: E402

URL = "https://www.furukawaelectric.com/ir/library/finalreport/pdf/2027/20261109.pdf"
d = requests.get(URL, headers={"User-Agent": "Mozilla/5.0 (compatible; market-dashboard-research)"}, timeout=60)
print("HTTP", d.status_code, len(d.content))
rd = PdfReader(io.BytesIO(d.content))
for i, p in enumerate(rd.pages[1:8], start=2):
    t = (p.extract_text() or "")
    print(f"\n######## page {i} ({len(t)}字)")
    for line in t.splitlines():
        s = line.strip()
        if s:
            print(s[:210])
