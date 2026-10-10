"""
위불(Webull) 종목 뉴스 — 미니 블벅터 추적 종목(59개)의 직전 18시간 뉴스를 PDF로 (일일 메일 첨부용, 번역판 + 원문판)

  python scripts/webull_pdf.py --out reports --cache .cache/ko.json

  · 로그인 없이 열리는 위불 종목별 뉴스 페이지(webull.com/quote/<거래소>-<티커>/news)에서 기사 목록만 읽는다
  · 같은 기사가 여러 종목에 걸리면 한 번만 넣고 종목 태그를 모두 붙인다 (예: NVDA·AMD)
  · 출처: 벤징가·시킹알파·모틀리풀·배런스·팁랭크스·나스닥 등 (위불이 제공하는 그대로)
"""
import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from daily_pdf import KST, render_pdfs  # noqa: E402
from fj_pdf import page  # noqa: E402
from wscn_pdf import translate  # noqa: E402

HOURS = 18


def tickers():
    from news_scanner import TOPICS
    out = []
    for t in TOPICS:
        for s in t["tickers"]:
            if s not in out:
                out.append(s)
    return out


def ticker_news(sym):
    from news_scanner import fetch_webull
    items = [{"id": x["wb_id"], "ts": x["published"], "headline": x["headline"], "source": x["source"],
              "url": x["url"], "tickers": [sym]} for x in fetch_webull(sym)]
    time.sleep(0.3)
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="reports")
    ap.add_argument("--cache", default=".cache/ko.json")
    a = ap.parse_args()
    cut = time.time() - HOURS * 3600
    syms = tickers()
    with ThreadPoolExecutor(4) as ex:
        results = list(ex.map(ticker_news, syms))
    by_id = {}
    for items in results:
        for x in items:
            if x["ts"] < cut:
                continue
            if x["id"] in by_id:
                by_id[x["id"]]["tickers"] += [s for s in x["tickers"] if s not in by_id[x["id"]]["tickers"]]
            else:
                by_id[x["id"]] = x
    seen, items = set(), []
    for x in sorted(by_id.values(), key=lambda x: -x["ts"]):
        k = re.sub(r"\W+", " ", x["headline"].lower()).strip()
        if k in seen:
            continue
        seen.add(k)
        x["source"] = f'{"·".join(x["tickers"][:4])} | {x["source"]}'
        items.append(x)
    print(f"[webull] 종목 {len(syms)}개, 직전 {HOURS}시간 기사 {len(items)}건 (종목 페이지 성공 {sum(1 for r in results if r)}개)")
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
    title = f"위불(Webull) 종목 뉴스 — 추적 종목 {len(syms)}개"
    render_pdfs([(os.path.join(a.out, f"14_미니블벅터_{stamp}_위불_{HOURS}시간.pdf"),
                  page(items, cut, cache, title=title, src_word=None)),
                 (os.path.join(a.out, f"15_미니블벅터_{stamp}_위불_{HOURS}시간_원문.pdf"),
                  page(items, cut, cache, True, title=title, src_word=None))])


if __name__ == "__main__":
    main()
