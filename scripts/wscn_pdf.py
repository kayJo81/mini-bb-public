"""
월스트리트견문(华尔街见闻) 실시간 속보 — 직전 18시간 전체를 한국어로 번역해 PDF로 (일일 메일 첨부용)

  python scripts/wscn_pdf.py --out reports --cache .cache/ko.json

  · https://wallstreetcn.com/live 의 '요문(global)' 채널 전체 (키워드 필터 없음), 최신순
  · 각 속보: 시각(KST) · 한국어 번역 · 중문 원문, 중요 속보(사이트의 빨간 표시)는 강조
  · 번역은 translate_ko.py 와 같은 방식·캐시 사용, 실패한 문장은 원문만 표시
  · 번역 없는 중문 원문판도 함께 생성 (09_…_원문.pdf)
"""
import argparse
import html
import json
import os
import re
import sys
import time
from datetime import datetime

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from daily_pdf import KST, render_pdfs  # noqa: E402
from translate_ko import CJK, MAX_CHARS, translate_batch  # noqa: E402

URL = "https://api-one.wallstcn.com/apiv1/content/lives?channel=global-channel&limit=100"
HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
HOURS = 18
# 구글 번역 실패 시 대체: 터미널 번역 API (Workers AI 무료 한도 때문에 한 번에 최대 200문장)
FALLBACK_URL = os.environ.get("TRANSLATE_FALLBACK", "https://mini-bbterminal.pages.dev/api/translate")
FALLBACK_MAX = 200

CSS = """
body{font-family:'Noto Sans CJK KR','Noto Sans KR',sans-serif;color:#111827;font-size:9.5pt;margin:0}
h1{font-size:15pt;margin:0 0 2px}
.meta{color:#6b7280;font-size:8.5pt;margin-bottom:8px;border-bottom:1.5px solid #111827;padding-bottom:6px}
.it{display:grid;grid-template-columns:44px 1fr;gap:8px;padding:5px 0 6px;border-bottom:.5px solid #e5e7eb;break-inside:avoid}
.tm{font-family:monospace;font-weight:700;font-size:8.5pt;color:#374151;padding-top:1px}
.ko{line-height:1.45}
.imp .ko{font-weight:700;color:#b91c1c}
.imp .tm{color:#b91c1c}
.cn{color:#6b7280;font-size:7.5pt;line-height:1.4;margin-top:2px;font-family:'Noto Sans CJK SC','Noto Sans CJK KR',sans-serif}
.orig .ko{font-family:'Noto Sans CJK SC','Noto Sans CJK KR',sans-serif}
.day{font-size:9pt;font-weight:700;background:#f3f4f6;padding:3px 8px;margin:8px 0 2px;border-radius:4px}
"""


def fetch(hours=HOURS):
    cut = time.time() - hours * 3600
    out, cursor = [], None
    for _ in range(25):
        try:
            d = requests.get(URL + (f"&cursor={cursor}" if cursor else ""), headers=HDR, timeout=15).json()["data"]
        except Exception as ex:
            print(f"[wscn] {ex}")
            break
        items = d.get("items") or []
        for it in items:
            ts = int(it.get("display_time") or 0)
            text = re.sub(r"\s+", " ", (it.get("content_text") or it.get("title") or "")).strip()
            if ts >= cut and text:
                out.append({"ts": ts, "text": text[:600], "title": (it.get("title") or "").strip(),
                            "imp": (it.get("score") or 1) > 1, "url": it.get("uri") or "https://wallstreetcn.com/live"})
        cursor = d.get("next_cursor")
        if not items or not cursor or min(int(i.get("display_time") or 0) for i in items) < cut:
            break
    out.sort(key=lambda x: -x["ts"])
    return out, cut


def translate(texts, cache):
    todo = [t for t in dict.fromkeys(texts) if t not in cache]
    batch, size, ok = [], 0, 0

    def flush(b):
        nonlocal ok
        try:
            res = translate_batch(b)
            if res is None and len(b) > 1:
                mid = len(b) // 2
                return flush(b[:mid]) and flush(b[mid:])
            for src, ko in zip(b, res or []):
                if ko and len(CJK.findall(ko)) <= len(ko) * 0.15:
                    cache[src] = ko
                    ok += 1
            time.sleep(0.4)
            return True
        except Exception as ex:
            print(f"[wscn] 번역 중단: {ex}")
            return False
    for t in todo:
        if batch and size + len(t) > MAX_CHARS:
            if not flush(batch):
                break
            batch, size = [], 0
        batch.append(t)
        size += len(t) + 1
    else:
        if batch:
            flush(batch)
    left = [t for t in todo if t not in cache]
    if left:
        ok += fallback(left, cache)
    print(f"[wscn] 속보 {len(texts)}건, 새로 번역 {ok}/{len(todo)}")


def fallback(texts, cache, limit=FALLBACK_MAX):
    """구글 번역이 막히면(429 등) 터미널의 /api/translate(Workers AI)로 앞쪽(보통 최신) 일부만 대신 번역"""
    ok = 0
    texts = [t for t in texts if len(t) <= 300]   # 대체 번역 API는 300자에서 자름 — 긴 문단(녹취록 등)은 보내지 않음
    for i in range(0, min(len(texts), limit), 25):
        b = texts[i:i + 25]
        try:
            ko = requests.post(FALLBACK_URL, json={"texts": b}, headers=HDR, timeout=60).json().get("ko") or []
        except Exception as ex:
            print(f"[wscn] 대체 번역 중단: {ex}")
            break
        if not any(ko):
            break
        for src, k in zip(b, ko):
            if k and len(CJK.findall(k)) <= len(k) * 0.15:
                cache[src] = k
                ok += 1
    print(f"[wscn] 대체 번역(Workers AI) {ok}/{len(texts)}")
    return ok


def page(items, cut, cache, original=False):
    """original=True: 번역 없이 중문 원문만"""
    e = html.escape
    end = datetime.now(KST)
    rows, day = [], None
    for x in items:
        dt = datetime.fromtimestamp(x["ts"], KST)
        if dt.strftime("%m/%d") != day:
            day = dt.strftime("%m/%d")
            rows.append(f'<div class="day">{day} ({"월화수목금토일"[dt.weekday()]})</div>')
        ko = None if original else cache.get(x["text"])
        cn = f'<div class="cn">{e(x["text"])}</div>' if ko else ""
        rows.append(f'<div class="it{" imp" if x["imp"] else ""}"><div class="tm">{dt:%H:%M}</div><div>'
                    f'<a class="ko" style="color:inherit;text-decoration:none" href="{e(x["url"])}">{e(ko or x["text"])}</a>'
                    f'{cn}</div></div>')
    n_imp = sum(x["imp"] for x in items)
    sub = (f"wallstreetcn.com/live · {datetime.fromtimestamp(cut, KST):%m/%d %H:%M} ~ {end:%m/%d %H:%M} KST (직전 {HOURS}시간) · "
           f"{len(items)}건 (중요 {n_imp}건 빨간색) · 최신순 · {'중문 원문' if original else '한국어 기계번역 + 중문 원문'}")
    return (f'<!doctype html><html lang="ko"><head><meta charset="utf-8"><style>{CSS}</style></head><body{" class=orig" if original else ""}>'
            f'<h1>월스트리트견문(华尔街见闻) 실시간 속보{" — 원문" if original else ""}</h1><div class="meta">{e(sub)}</div>{"".join(rows)}</body></html>')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="reports")
    ap.add_argument("--cache", default=".cache/ko.json")
    a = ap.parse_args()
    items, cut = fetch()
    if not items:
        print("[wscn] 속보를 가져오지 못해 PDF를 만들지 않습니다")
        return
    try:
        cache = json.load(open(a.cache, encoding="utf-8"))
    except Exception:
        cache = {}
    translate([x["text"] for x in items], cache)
    os.makedirs(os.path.dirname(a.cache) or ".", exist_ok=True)
    json.dump(cache, open(a.cache, "w", encoding="utf-8"), ensure_ascii=False)
    stamp = datetime.now(KST).strftime("%Y%m%d_%H%M")
    render_pdfs([(os.path.join(a.out, f"08_미니블벅터_{stamp}_월스트리트견문_{HOURS}시간.pdf"), page(items, cut, cache)),
                 (os.path.join(a.out, f"09_미니블벅터_{stamp}_월스트리트견문_{HOURS}시간_원문.pdf"), page(items, cut, cache, original=True))])


if __name__ == "__main__":
    main()
