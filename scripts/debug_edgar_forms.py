"""EDGAR全文検索APIの forms 指定のどれが有効かを確認する（汎用）。"""
import os
import sys
from datetime import datetime, timezone

import requests

c = os.environ.get("EDGAR_CONTACT", "").strip()
if not c:
    print("EDGAR_CONTACT 未設定")
    sys.exit(0)
H = {"User-Agent": f"market-dashboard-watch {c}", "Accept": "application/json"}
end = datetime.now(timezone.utc).strftime("%Y-%m-%d")
for forms in ["8-K", "8-K,425", "8-K,425,S-4,SC 13D", "S-4", "S-4/A", "DEFM14A", "15-12G", "8-K,S-4,S-4/A,DEFM14A,425,15-12G,SC 13D", "8-K,S-4,425,DEFM14A", None]:
    p = {"q": '"Lumentum"', "dateRange": "custom", "startdt": "2026-11-02", "enddt": end}
    if forms:
        p["forms"] = forms
    r = requests.get("https://efts.sec.gov/LATEST/search-index", params=p, headers=H, timeout=30)
    n = len(r.json().get("hits", {}).get("hits", [])) if r.status_code == 200 else -1
    print(f"forms={forms!s:<48} HTTP {r.status_code} hits={n}")
