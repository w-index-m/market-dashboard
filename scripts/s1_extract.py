#!/usr/bin/env python3
"""
IPO目論見書(S-1)などの本文から、決算・取引先・計算資源・リスクに関する該当箇所を抜き出す（要約の下書き用）。
・本文のHTMLを文章に分け、キーワードと数字を含む文を、項目ごとに最大5件、1件あたり最大300字まで保存する（全文の転載はしない）。
・使い方: python scripts/s1_extract.py <書類のURL> [出力先JSON]   環境変数 EDGAR_CONTACT が必要。
"""
import html
import json
import os
import re
import sys
from datetime import datetime, timezone

import requests

# 項目名: (キーワード正規表現, 数字を必須にするか)
TOPICS = {
    "売上": (r"\b(total )?revenues?\b.{0,80}\b(increased|decreased|grew|was|were|of)\b", True),
    "損益": (r"\b(net loss|loss from operations|operating loss|net income|gross margin)\b", True),
    "取引先の集中": (r"\b(customers?|partners?|resellers?)\b.{0,80}\b(accounted for|represented|concentration)\b|\baccounted for\b.{0,60}\b(\d+(\.\d+)?)%\s+of (our )?(total )?revenues?", True),
    "計算資源・クラウド契約": (r"\b(compute|cloud|data cent(er|re)s?|gpus?|tpus?|chips?|purchase (commitments|obligations)|capacity)\b.{0,120}\b(commitments?|agreements?|obligations?|billion|million|gigawatts?|GW)\b", True),
    "資金・株式": (r"\b(offering|net proceeds|dual[- ]class|voting power|valuation|shares of (our )?class [ab])\b", True),
    "主なリスク": (r"\b(we have (a history of )?(incurred )?(significant )?(net )?losses|may not (achieve|maintain) profitab|depend(s|ent)? (on|upon)|substantially all|significant(ly)? (portion|dependence))\b", False),
}
MAX_PER_TOPIC, MAX_LEN = 5, 300


def to_text(raw: str) -> str:
    """HTMLのタグ・スクリプトを除き、空白を整えた本文にする。"""
    t = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", raw)
    t = re.sub(r"(?i)</(p|div|tr|li|h\d|br)\s*>|<br\s*/?>", "\n", t)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    t = html.unescape(t).replace("\xa0", " ")
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", t))


def sentences(text: str) -> list:
    out = []
    for para in text.split("\n"):
        para = para.strip()
        if len(para) < 25:
            continue
        out += [s.strip() for s in re.split(r"(?<=[.;])\s+(?=[A-Z“\"(])", para) if 25 <= len(s.strip()) <= 900]
    return out


def extract(raw: str) -> dict:
    """本文から項目ごとの該当文を取り出す。同じ文は1回だけ。数字を含む文を優先し、短く切って保存する。"""
    sents = list(dict.fromkeys(x for x in sentences(to_text(raw)) if len(re.findall(r"[\d,.]+", x)) <= 12))   # 重複と、数字だらけの表の行を除く
    res, used = {}, set()
    for topic, (pat, need_num) in TOPICS.items():
        rx = re.compile(pat, re.I)
        hits = [s for s in sents if rx.search(s) and (not need_num or re.search(r"\d", s)) and s not in used]
        hits.sort(key=lambda s: (-len(re.findall(r"\$\s?\d|\d+(\.\d+)?\s?%", s)), sents.index(s)))
        picked = hits[:MAX_PER_TOPIC]
        used.update(picked)
        res[topic] = [s if len(s) <= MAX_LEN else s[:MAX_LEN].rstrip() + "…" for s in picked]
    return res


def fetch_and_extract(url: str, headers: dict) -> dict:
    r = requests.get(url, headers=headers, timeout=90)
    r.raise_for_status()
    return {"source": url, "extracted_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "length": len(r.text), "topics": extract(r.text)}


def main() -> None:
    contact = os.environ.get("EDGAR_CONTACT", "").strip()
    if not contact or len(sys.argv) < 2:
        print("usage: EDGAR_CONTACT=... python scripts/s1_extract.py URL [out.json]")
        return
    res = fetch_and_extract(sys.argv[1], {"User-Agent": f"market-dashboard-watch {contact}"})
    for k, v in res["topics"].items():
        print(f"## {k}: {len(v)}件")
        for s in v[:2]:
            print("  -", s[:160])
    if len(sys.argv) > 2:
        with open(sys.argv[2], "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
