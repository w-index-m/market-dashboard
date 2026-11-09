"""古河電気工業のIRページから、最新の決算短信（PDF）を探し、主要な数字の行を表示する（公開データのみ）。"""
import io
import re
import subprocess
import sys

import requests

subprocess.run([sys.executable, "-m", "pip", "install", "-q", "pypdf"], check=False)
from pypdf import PdfReader  # noqa: E402

H = {"User-Agent": "Mozilla/5.0 (compatible; market-dashboard-research)"}
BASE = "https://www.furukawaelectric.com"
r = requests.get(BASE + "/ir/library/finalreport/", headers=H, timeout=40)
print("IR page:", r.status_code, len(r.text))
links = sorted(set(re.findall(r'href="([^"]+\.pdf)"', r.text)))
for u in links[:40]:
    print("link:", u)
cands = [u for u in links if "2026" in u and ("20261109" in u or "1109" in u)]
if not cands:
    cands = [BASE + "/ir/library/finalreport/pdf/2026/20261109.pdf", BASE + "/ir/library/finalreport/pdf/2026/20261109_pre.pdf"]
for u in cands[:4]:
    u = u if u.startswith("http") else BASE + u
    d = requests.get(u, headers=H, timeout=60)
    print("\n=====", u, d.status_code, len(d.content))
    if d.status_code != 200:
        continue
    try:
        rd = PdfReader(io.BytesIO(d.content))
    except Exception as e:
        print("pdf error", type(e).__name__)
        continue
    print("pages:", len(rd.pages))
    txt = "\n".join((p.extract_text() or "") for p in rd.pages[:6])
    keys = ("売上高", "営業利益", "経常利益", "親会社株主", "通期", "予想", "配当", "修正", "データセンター", "光ソリューション", "情報コンポーネント", "セグメント")
    shown = 0
    for line in txt.splitlines():
        if any(k in line for k in keys) and len(line.strip()) > 4:
            print(line.strip()[:200])
            shown += 1
            if shown > 90:
                break
