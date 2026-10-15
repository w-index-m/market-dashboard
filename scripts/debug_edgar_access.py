"""GitHub ActionsからSEC EDGARに届くかを確認する（汎用）。"""
import requests

UAS = {"contact-ua": "market-dashboard-watch w-index-m@users.noreply.github.com",
       "browser-ua": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
       "org-ua": "Sample Company admin@example.com"}
URLS = ["https://data.sec.gov/submissions/CIK0001045810.json",
        "https://www.sec.gov/cgi-bin/browse-edgar?company=anthropic&type=S-1&owner=include&count=10&action=getcompany&output=atom",
        "https://efts.sec.gov/LATEST/search-index?q=%22Anthropic%22&forms=S-1",
        "https://www.sec.gov/files/company_tickers.json",
        "https://www.sec.gov/cgi-bin/srch-edgar?text=company-name%3Danthropic&first=2026&last=2026"]
for un, ua in UAS.items():
    for u in URLS:
        try:
            r = requests.get(u, headers={"User-Agent": ua, "Accept-Encoding": "gzip, deflate"}, timeout=20)
            print(un, r.status_code, u[:70], "|", r.text[:120].replace("\n", " "))
        except Exception as e:
            print(un, "ERR", u[:70], type(e).__name__, str(e)[:80])
