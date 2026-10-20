"""
FinancialJuice 스타일 속보 모음 — 직전 18시간 헤드라인을 PDF로 (일일 메일 첨부용, 번역판 + 원문판)

  python scripts/fj_pdf.py --accumulate --store .cache/fj.json        # 15분 수집마다: FJ 공개 피드 누적
  python scripts/fj_pdf.py --out reports --store .cache/fj.json --cache .cache/ko.json   # 05:30: PDF 생성
  python scripts/fj_pdf.py --publish public/briefings/fj.json --store .cache/fj.json --cache .cache/ko.json  # 화면용(⚡ 속보)

  · FinancialJuice 공개 피드는 최근 100건만 주므로 수집 때마다 모아 두고(최대 30시간), PDF 때 합친다
  · FinancialJuice 가 모아 보여주는 '뉴스 소스'(WSJ·CNBC·연준·블룸버그·로이터 …)는 각 언론사 RSS 에서 직접 가져온다
    (공식 RSS 가 없거나 막힌 곳은 구글뉴스 site: 검색으로 대체)
  · 최신순 한 목록, 같은 제목은 한 번만, 출처 표시
"""
import argparse
import html
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from email.utils import parsedate_to_datetime

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from daily_pdf import KST, render_pdfs  # noqa: E402
from wscn_pdf import translate  # noqa: E402

HOURS = 18
KEEP_HOURS = 30
HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
FJ_URL = "https://www.financialjuice.com/feed.ashx?xy=1"
CNBC = "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id={}"
DJ = "https://feeds.content.dowjones.io/public/rss/"
GN = "https://news.google.com/rss/search?q={}+when:1d&hl=en-US&gl=US&ceid=US:en"

# (출처 표시, RSS 주소, RSS 가 안 될 때 구글뉴스 검색어)
FEEDS = [
    ("WSJ 시장", DJ + "RSSMarketsMain", None),
    ("WSJ 기술", DJ + "RSSWSJD", None),
    ("WSJ 세계", DJ + "RSSWorldNews", None),
    ("WSJ 미국기업", DJ + "WSJcomUSBusiness", None),
    ("WSJ 경제", DJ + "socialeconomyfeed", None),
    ("마켓워치", DJ + "mw_topstories", None),
    ("CNBC 주요", CNBC.format(100003114), "site:cnbc.com"),
    ("CNBC 미국", CNBC.format(15837362), None),
    ("CNBC 투자", CNBC.format(15839069), None),
    ("CNBC 실적", CNBC.format(15839135), None),
    ("CNBC 경제", CNBC.format(20910258), None),
    ("CNBC 유럽", CNBC.format(19794221), None),
    ("CNBC 아시아", CNBC.format(19832390), None),
    ("블룸버그", GN.format("site:bloomberg.com"), None),
    ("로이터", GN.format("site:reuters.com"), None),
    ("파이낸셜타임스", "https://www.ft.com/news-feed?format=rss", "site:ft.com"),
    ("연준 보도자료", "https://www.federalreserve.gov/feeds/press_all.xml", None),
    ("연준 연설·증언", "https://www.federalreserve.gov/feeds/speeches_and_testimony.xml", None),
    ("뉴욕 연준", GN.format("site:newyorkfed.org"), None),
    ("벤징가", "https://www.benzinga.com/feed", "site:benzinga.com"),
    ("피치", GN.format("site:fitchratings.com"), None),
    ("코인데스크", "https://www.coindesk.com/arc/outboundfeeds/rss/", "site:coindesk.com"),
    ("PR뉴스와이어", "https://www.prnewswire.com/rss/news-releases-list.rss", "site:prnewswire.com"),
    ("NYT 딜북", "https://rss.nytimes.com/services/xml/rss/nyt/Dealbook.xml", None),
    ("마켓비트", "https://www.marketbeat.com/feed/", None),
    ("야후 파이낸스", "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EGSPC,%5EIXIC,%5EDJI&region=US&lang=en-US",
     "site:finance.yahoo.com"),
    ("시킹알파", "https://seekingalpha.com/market_currents.xml", None),
    ("SCMP 경제", "https://www.scmp.com/rss/91/feed/", None),
    ("SCMP 중국", "https://www.scmp.com/rss/4/feed/", None),
    ("SCMP 정치", "https://www.scmp.com/rss/5/feed/", None),
    ("ET마켓 상품", "https://economictimes.indiatimes.com/markets/commodities/rssfeeds/1808152121.cms", None),
    ("오일프라이스", "https://oilprice.com/rss/main", None),
    ("밸류워크", "https://www.valuewalk.com/feed/", None),
    ("스팟감마", "https://spotgamma.com/feed/", None),
    ("트럼프 트루스소셜", "https://www.trumpstruth.org/feed", None),
    ("USGS 지진(4.5+)", "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_day.atom", None),
]
GN_SUFFIX = re.compile(r"\s+-\s+[^-]{2,40}$")


def _ts(s):
    s = (s or "").strip()
    if not s:
        return 0
    try:
        return int(parsedate_to_datetime(s).timestamp())
    except Exception:
        pass
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except Exception:
        return 0


def _parse(xml, label, google=False):
    """RSS(item) / Atom(entry) 공통 파서"""
    out = []
    root = ET.fromstring(xml)
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        f = {c.tag.split("}")[-1]: c for c in el}
        title = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", (f["title"].text or "") if "title" in f else ""))).strip()
        if not title:
            continue
        link = ""
        if "link" in f:
            link = (f["link"].text or "").strip() or f["link"].get("href", "")
        ts = _ts(next((f[k].text for k in ("pubDate", "published", "updated", "date") if k in f and f[k].text), ""))
        if google:
            title = GN_SUFFIX.sub("", title)
        out.append({"ts": ts, "headline": title[:300], "source": label, "url": link})
    return out


def _get(url):
    for i in range(3):
        r = requests.get(url, headers=HDR, timeout=20)
        if r.status_code not in (429, 503):
            break
        time.sleep(3 * (i + 1))
    r.raise_for_status()
    return r


def fetch_feed(label, url, fallback):
    try:
        r = _get(url)
        items = _parse(r.content, label, google="news.google.com" in url)
        if items:
            return items
    except Exception as ex:
        print(f"[fj] {label}: {ex}")
    if fallback:
        try:
            r = _get(GN.format(fallback))
            return _parse(r.content, label, google=True)
        except Exception as ex:
            print(f"[fj] {label} 대체: {ex}")
    return []


def fetch_fj():
    items = None
    for i in range(3):   # 가끔 빈 응답(차단·지연)이 와서 잠시 뒤 다시 시도
        try:
            r = requests.get(FJ_URL, headers=HDR, timeout=20)
            items = _parse(r.content, "FinancialJuice")
            if items:
                break
        except Exception as ex:
            print(f"[fj] FinancialJuice ({i + 1}/3): {ex}")
        time.sleep(5 * (i + 1))
    if not items:
        return []
    for x in items:
        x["headline"] = re.sub(r"^FinancialJuice:\s*", "", x["headline"])
    return items


def accumulate(store, dump=None):
    try:
        old = json.load(open(store, encoding="utf-8"))
    except Exception:
        old = []
    new = []
    if dump:   # 같은 실행의 뉴스 수집이 이미 받아 둔 목록 (같은 피드를 곧바로 다시 요청하면 빈 응답이 오는 경우가 있음)
        try:
            new = json.load(open(dump, encoding="utf-8"))
        except Exception:
            new = []
    if not new:
        new = fetch_fj()
    cut = time.time() - KEEP_HOURS * 3600
    merged = {(x["ts"], x["headline"]): x for x in old + new if x["ts"] >= cut}
    os.makedirs(os.path.dirname(store) or ".", exist_ok=True)
    json.dump(sorted(merged.values(), key=lambda x: -x["ts"]), open(store, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"[fj] 누적 {len(merged)}건 (직전 {KEEP_HOURS}시간)")


CSS = """
body{font-family:'Noto Sans CJK KR','Noto Sans KR',sans-serif;color:#111827;font-size:9.5pt;margin:0}
h1{font-size:15pt;margin:0 0 2px}
.meta{color:#6b7280;font-size:8.5pt;margin-bottom:8px;border-bottom:1.5px solid #111827;padding-bottom:6px}
.it{display:grid;grid-template-columns:44px 1fr;gap:8px;padding:4px 0 5px;border-bottom:.5px solid #e5e7eb;break-inside:avoid}
.tm{font-family:monospace;font-weight:700;font-size:8.5pt;color:#374151;padding-top:1px}
.src{display:inline-block;font-size:7pt;font-weight:700;border-radius:4px;padding:0 5px;margin-right:5px;background:#e5e7eb;color:#374151}
.src.fj{background:#dbeafe;color:#1e40af}
.hl{line-height:1.45;color:#111827;text-decoration:none}
.org{color:#6b7280;font-size:7.5pt;line-height:1.35;margin-top:1px}
.day{font-size:9pt;font-weight:700;background:#f3f4f6;padding:3px 8px;margin:8px 0 2px;border-radius:4px}
"""


def page(items, cut, cache, original=False, title="FinancialJuice + 뉴스 소스 속보", src_word="출처"):
    e = html.escape
    rows, day = [], None
    for x in items:
        dt = datetime.fromtimestamp(x["ts"], KST)
        if dt.strftime("%m/%d") != day:
            day = dt.strftime("%m/%d")
            rows.append(f'<div class="day">{day} ({"월화수목금토일"[dt.weekday()]})</div>')
        ko = None if original else cache.get(x["headline"])
        org = f'<div class="org">{e(x["headline"])}</div>' if ko else ""
        src = f'<span class="src{" fj" if x["source"] == "FinancialJuice" else ""}">{e(x["source"])}</span>'
        rows.append(f'<div class="it"><div class="tm">{dt:%H:%M}</div><div>{src}'
                    f'<a class="hl" href="{e(x["url"] or "#")}">{e(ko or x["headline"])}</a>{org}</div></div>')
    n_src = len({x["source"] for x in items})
    sub = (f"{datetime.fromtimestamp(cut, KST):%m/%d %H:%M} ~ {datetime.now(KST):%m/%d %H:%M} KST (직전 {HOURS}시간) · "
           f"{len(items)}건 · {f'{src_word} {n_src}곳 · ' if src_word else ''}최신순 · {'원문' if original else '한국어 기계번역 + 원문'}")
    return (f'<!doctype html><html lang="ko"><head><meta charset="utf-8"><style>{CSS}</style></head><body>'
            f'<h1>{e(title)}{" — 원문" if original else ""}</h1><div class="meta">{e(sub)}</div>'
            f'{"".join(rows)}</body></html>')


def publish(path, store, cache_path):
    """터미널 '⚡ 속보' 화면용: 누적한 FinancialJuice 헤드라인(최대 30시간) + 한국어 번역 → JSON"""
    try:
        fj = json.load(open(store, encoding="utf-8"))
    except Exception:
        fj = []
    try:
        cache = json.load(open(cache_path, encoding="utf-8"))
    except Exception:
        cache = {}
    translate([x["headline"] for x in fj], cache)
    os.makedirs(os.path.dirname(cache_path) or ".", exist_ok=True)
    json.dump(cache, open(cache_path, "w", encoding="utf-8"), ensure_ascii=False)
    items = [{"ts": x["ts"], "h": x["headline"], "ko": cache.get(x["headline"]), "u": x.get("url") or ""} for x in fj]
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    json.dump({"at": int(time.time()), "items": items}, open(path, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f"[fj] 화면용 {len(items)}건 (번역 {sum(1 for x in items if x['ko'])}) → {path}")


def build(out, store, cache_path):
    cut = time.time() - HOURS * 3600
    try:
        fj = json.load(open(store, encoding="utf-8"))
    except Exception:
        fj = []
    with ThreadPoolExecutor(6) as ex:
        futs = [ex.submit(fetch_feed, *f) for f in FEEDS] + [ex.submit(fetch_fj)]
        items = fj + [x for f in futs for x in f.result()]
    seen, uniq = set(), []
    for x in sorted(items, key=lambda x: -x["ts"]):
        k = re.sub(r"\W+", " ", x["headline"].lower()).strip()
        if cut <= x["ts"] <= time.time() + 600 and k not in seen:
            seen.add(k)
            uniq.append(x)
    counts = {}
    for x in uniq:
        counts[x["source"]] = counts.get(x["source"], 0) + 1
    print(f"[fj] {len(uniq)}건: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda p: -p[1])))
    if not uniq:
        return
    try:
        cache = json.load(open(cache_path, encoding="utf-8"))
    except Exception:
        cache = {}
    translate([x["headline"] for x in uniq], cache)
    os.makedirs(os.path.dirname(cache_path) or ".", exist_ok=True)
    json.dump(cache, open(cache_path, "w", encoding="utf-8"), ensure_ascii=False)
    stamp = datetime.now(KST).strftime("%Y%m%d_%H%M")
    render_pdfs([(os.path.join(out, f"10_미니블벅터_{stamp}_파이낸셜쥬스_{HOURS}시간.pdf"), page(uniq, cut, cache)),
                 (os.path.join(out, f"11_미니블벅터_{stamp}_파이낸셜쥬스_{HOURS}시간_원문.pdf"), page(uniq, cut, cache, True))])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--accumulate", action="store_true")
    ap.add_argument("--store", default=".cache/fj.json")
    ap.add_argument("--dump", default="", help="뉴스 수집이 남긴 FinancialJuice 목록 (있으면 다시 요청하지 않음)")
    ap.add_argument("--out", default="reports")
    ap.add_argument("--cache", default=".cache/ko.json")
    ap.add_argument("--publish", default="", help="화면용 JSON 경로 (예: public/briefings/fj.json)")
    a = ap.parse_args()
    if a.accumulate:
        accumulate(a.store, a.dump)
    elif a.publish:
        publish(a.publish, a.store, a.cache)
    else:
        build(a.out, a.store, a.cache)
