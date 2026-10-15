#!/usr/bin/env python3
"""
SEC EDGARで、指定した会社（既定: Anthropic）のIPO関連書類（S-1・S-1/A・424B4など）が新しく公開されたら、LINE・Slackへ知らせる。
・売買はしない。公開データのみ。結果は data/edgar_watch.json に、見つけた書類の一覧（提出番号・種類・日付・リンク）だけ保存する。
・新しい書類が無い日は何も送らず、ファイルも更新しない。
環境変数: WATCH_NAMES（カンマ区切り、既定 "Anthropic"）、LINE_CHANNEL_ACCESS_TOKEN / SLACK_WEBHOOK_URL、EDGAR_DRY_RUN=1 で送信しない。
SECは User-Agent に連絡先を求めるため、リポジトリ名を入れている（アクセス頻度は1日数回で、制限の範囲内）。
"""
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests

OUT = os.path.join(os.path.dirname(__file__), "..", "data", "edgar_watch.json")
UA = {"User-Agent": "market-dashboard-watch w-index-m@users.noreply.github.com", "Accept": "application/atom+xml, application/json, text/xml, */*"}
FORMS = ("S-1", "S-1/A", "424B4", "424B1", "F-1", "F-1/A")


def _post(text: str) -> None:
    line, slack = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", ""), os.environ.get("SLACK_WEBHOOK_URL", "")
    if os.environ.get("EDGAR_DRY_RUN"):
        print("[DRY RUN]\n" + text)
        return
    if line:
        try:
            requests.post("https://api.line.me/v2/bot/message/broadcast",
                          headers={"Authorization": f"Bearer {line}", "Content-Type": "application/json"},
                          json={"messages": [{"type": "text", "text": text[:4900]}]}, timeout=15).raise_for_status()
        except Exception as e:
            print(f"LINE failed: {e}", file=sys.stderr)
    if slack:
        try:
            requests.post(slack, json={"text": text}, timeout=15).raise_for_status()
        except Exception as e:
            print(f"Slack failed: {e}", file=sys.stderr)


def search_atom(name: str, form: str) -> tuple:
    """EDGARの会社検索（Atomフィード）。Returns: (書類リスト, HTTPステータス)。"""
    r = requests.get("https://www.sec.gov/cgi-bin/browse-edgar",
                     params={"company": name, "type": form, "owner": "include", "count": 40, "action": "getcompany", "output": "atom"},
                     headers=UA, timeout=30)
    docs = []
    if r.status_code == 200 and r.text.strip().startswith("<"):
        ns = {"a": "http://www.w3.org/2005/Atom"}
        try:
            root = ET.fromstring(r.text)
            for e in root.findall("a:entry", ns):
                title = (e.findtext("a:title", default="", namespaces=ns) or "").strip()
                link = e.find("a:link", ns)
                docs.append({"title": title, "url": link.get("href") if link is not None else "",
                             "updated": (e.findtext("a:updated", default="", namespaces=ns) or "")[:10]})
        except ET.ParseError:
            pass
    return docs, r.status_code


def search_fts(name: str) -> tuple:
    """EDGARの全文検索API。Returns: (書類リスト, HTTPステータス)。"""
    r = requests.get("https://efts.sec.gov/LATEST/search-index", params={"q": f'"{name}"', "forms": ",".join(FORMS), "dateRange": "custom",
                                                                          "startdt": "2026-01-01", "enddt": datetime.now(timezone.utc).strftime("%Y-%m-%d")},
                     headers=UA, timeout=30)
    docs = []
    if r.status_code == 200:
        try:
            for h in (r.json().get("hits", {}).get("hits", []) or []):
                src = h.get("_source", {})
                names = ", ".join(src.get("display_names", []) or [])
                if name.lower() not in names.lower():
                    continue
                acc = (h.get("_id") or "").split(":")[0]
                cik = (src.get("ciks") or [""])[0].lstrip("0")
                docs.append({"title": f"{src.get('form', '')} {names}", "url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/" if cik and acc else "",
                             "updated": src.get("file_date", ""), "acc": acc})
        except ValueError:
            pass
    return docs, r.status_code


def main() -> None:
    names = [n.strip() for n in os.environ.get("WATCH_NAMES", "Anthropic").split(",") if n.strip()]
    try:
        with open(OUT, encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {"seen": {}, "log": []}
    seen = state.get("seen", {})
    new_docs, diag = [], []
    for name in names:
        found = []
        try:
            d, code = search_fts(name)
            diag.append(f"fts:{name}:{code}:{len(d)}")
            found += d
        except Exception as e:
            diag.append(f"fts:{name}:ERR:{type(e).__name__}")
        for form in ("S-1", "S-1/A", "424B4"):
            try:
                d, code = search_atom(name, form)
                d = [x for x in d if re.search(name, x["title"], re.I)]
                diag.append(f"atom:{name}:{form}:{code}:{len(d)}")
                found += d
            except Exception as e:
                diag.append(f"atom:{name}:{form}:ERR:{type(e).__name__}")
        for x in found:
            key = x.get("acc") or x["url"] or x["title"]
            if key and key not in seen:
                seen[key] = {"title": x["title"], "url": x["url"], "date": x.get("updated", "")}
                new_docs.append(x)
    print("diag:", " | ".join(diag))
    first_run = not state.get("initialized")
    if new_docs or first_run or diag != state.get("diag"):
        state["seen"] = seen
        state["initialized"] = True
        state["last_check"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        state["diag"] = diag
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        with open(OUT, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1)
    if new_docs and not first_run:
        lines = ["📄 SEC EDGAR: IPO関連書類が新しく見つかりました"]
        for x in new_docs[:8]:
            lines.append(f"・{x['title']}（{x.get('updated', '')}）\n  {x['url']}")
        lines.append("※ 内容の要約はこのあとClaudeに依頼してください。公開データのみ。")
        _post("\n".join(lines))
    elif new_docs:
        print(f"first run: recorded {len(new_docs)} existing docs without notifying")
    else:
        print("no new documents")


if __name__ == "__main__":
    main()
