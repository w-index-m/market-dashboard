"""S-1抜き出しの動作確認: 既知の公開S-1（Reddit, 2024年）の本文で試す。"""
import sys
sys.path.insert(0, "scripts")
import edgar_watch as ew
import s1_extract

docs, code = ew.search_fts("Reddit", ("S-1",), "2024-02-01")
print("fts", code, len(docs))
docs = [d for d in docs if d.get("file")]
if docs:
    url = ew._main_doc_url(docs[0])
    print("url", url)
    res = s1_extract.fetch_and_extract(url, ew._ua())
    print("length", res["length"])
    for k, v in res["topics"].items():
        print(f"## {k}: {len(v)}")
        for s in v[:3]:
            print("   -", s[:220])
