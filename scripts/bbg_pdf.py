"""
블룸버그 최신 기사(bloomberg.com/latest) — 직전 18시간 기사 제목을 PDF로 (일일 메일 첨부용, 번역판 + 원문판)

  python scripts/bbg_pdf.py --out reports --cache .cache/ko.json

  · /latest 화면은 로봇 접속을 막아 두어서, 같은 목록을 담은 블룸버그 공개 뉴스 사이트맵
    (sitemaps/news/latest.xml)과 공식 RSS(feeds.bloomberg.com) 를 합쳐 쓴다
  · 최신순, 같은 기사는 한 번만, 분류(기사·오피니언·시장·정치 …) 표시
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from daily_pdf import KST, render_pdfs  # noqa: E402
from fj_pdf import _get, _parse, page  # noqa: E402
from wscn_pdf import translate  # noqa: E402

HOURS = 18
SITEMAP = "https://www.bloomberg.com/sitemaps/news/latest.xml"
RSS = [("시장", "markets"), ("정치", "politics"), ("기술", "technology"), ("경제", "economics"),
       ("산업", "industries"), ("비즈니스", "business"), ("오피니언", "bview"), ("크립토", "crypto"), ("주요", None)]
KIND = {"news": "기사", "opinion": "오피니언", "features": "피처", "graphics": "그래픽", "newsletters": "뉴스레터"}
ENT = {"&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&apos;": "'", "&#39;": "'"}


def _unesc(s):
    for a, b in ENT.items():
        s = s.replace(a, b)
    return s.strip()


def fetch():
    items = {}
    try:
        x = _get(SITEMAP).text
        for blk in re.findall(r"<url>(.*?)</url>", x, re.S):
            loc = re.search(r"<loc>(.*?)</loc>", blk)
            d = re.search(r"<news:publication_date>(.*?)</news:publication_date>", blk)
            t = re.search(r"<news:title>(.*?)</news:title>", blk, re.S)
            if not (loc and d and t):
                continue
            url = loc.group(1).strip()
            kind = url.split("/")[3] if url.count("/") > 3 else "news"
            items[url] = {"ts": int(datetime.fromisoformat(d.group(1).replace("Z", "+00:00")).timestamp()),
                          "headline": _unesc(t.group(1)), "source": KIND.get(kind, kind), "url": url}
    except Exception as ex:
        print(f"[bbg] sitemap: {ex}")
    for label, sec in RSS:   # 사이트맵에 빠진 기사 보충 + 분류 표시
        url = f"https://feeds.bloomberg.com/{sec}/news.rss" if sec else "https://feeds.bloomberg.com/news.rss"
        try:
            for x in _parse(_get(url).content, label):
                k = x["url"].split("?")[0]
                if k in items:
                    if items[k]["source"] == "기사" and sec:
                        items[k]["source"] = label
                else:
                    x["url"] = k
                    items[k] = x
        except Exception as ex:
            print(f"[bbg] rss {label}: {ex}")
    return list(items.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="reports")
    ap.add_argument("--cache", default=".cache/ko.json")
    a = ap.parse_args()
    cut = time.time() - HOURS * 3600
    seen, items = set(), []
    for x in sorted(fetch(), key=lambda x: -x["ts"]):
        k = re.sub(r"\W+", " ", x["headline"].lower()).strip()
        if cut <= x["ts"] <= time.time() + 600 and k not in seen:
            seen.add(k)
            items.append(x)
    print(f"[bbg] 직전 {HOURS}시간 {len(items)}건")
    if not items:
        return
    try:
        cache = json.load(open(a.cache, encoding="utf-8"))
    except Exception:
        cache = {}
    translate([x["headline"] for x in items], cache)
    os.makedirs(os.path.dirname(a.cache) or ".", exist_ok=True)
    json.dump(cache, open(a.cache, "w", encoding="utf-8"), ensure_ascii=False)
    stamp = datetime.now(KST).strftime("%Y%m%d_%H%M")
    title = "블룸버그 최신 기사 (bloomberg.com/latest)"
    render_pdfs([(os.path.join(a.out, f"12_미니블벅터_{stamp}_블룸버그_{HOURS}시간.pdf"), page(items, cut, cache, title=title, src_word="분류")),
                 (os.path.join(a.out, f"13_미니블벅터_{stamp}_블룸버그_{HOURS}시간_원문.pdf"),
                  page(items, cut, cache, True, title=title, src_word="분류"))])


if __name__ == "__main__":
    main()
