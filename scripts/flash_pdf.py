"""
⚡ 실시간 속보(FinancialJuice·월스트리트견문·시나재경) 직전 24시간 → PDF (미국 장 마감 30분 뒤 일일 메일 첨부용)

  python scripts/flash_pdf.py public/briefings/flash.json --out reports --cache .cache/ko.json

  · 터미널 '⚡ 속보' 화면과 같은 자료(briefings/flash.json — 같은 실행에서 방금 만든 것)
  · 맨 앞: 출처별 건수 + '★ 중요 속보' 모음(월스트리트견문 빨간 표시·시나재경 焦点), 이어서 전체 최신순
  · 각 속보: 시각(KST) · 출처·분류 · 한국어 번역(빠진 것은 여기서 번역) · 원문
"""
import argparse
import html
import json
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from daily_pdf import KST, render_pdfs  # noqa: E402
from wscn_pdf import translate  # noqa: E402

HOURS = 24
SRC = {"fj": ("FinancialJuice", "#1d4ed8"), "wscn": ("월스트리트견문", "#b91c1c"), "sina": ("시나재경", "#c2410c")}

CSS = """
body{font-family:'Noto Sans CJK KR','Noto Sans KR',sans-serif;color:#111827;font-size:9.5pt;margin:0}
h1{font-size:15pt;margin:0 0 2px}
h2{font-size:11.5pt;margin:12px 0 4px;padding-bottom:3px;border-bottom:1px solid #111827}
.meta{color:#6b7280;font-size:8.5pt;margin-bottom:6px;border-bottom:1.5px solid #111827;padding-bottom:6px}
.cnt{display:flex;gap:6px;flex-wrap:wrap;margin:4px 0 2px}
.cnt span{font-size:8.5pt;font-weight:700;border-radius:5px;padding:2px 8px;background:#f3f4f6}
.it{display:grid;grid-template-columns:40px 1fr;gap:8px;padding:4px 0 5px;border-bottom:.5px solid #e5e7eb;break-inside:avoid}
.tm{font-family:monospace;font-weight:700;font-size:8.5pt;color:#374151;padding-top:1px}
.src{display:inline-block;font-size:6.8pt;font-weight:700;color:#fff;border-radius:3px;padding:0 4px;margin-right:4px;vertical-align:1px}
.tag{display:inline-block;font-size:6.8pt;font-weight:600;color:#4b5563;background:#f3f4f6;border-radius:3px;padding:0 4px;margin-right:4px;vertical-align:1px}
.ko{line-height:1.45;color:#111827;text-decoration:none}
.imp .ko{font-weight:700;color:#b91c1c}.imp .tm{color:#b91c1c}
.org{color:#6b7280;font-size:7.5pt;line-height:1.35;margin-top:1px;font-family:'Noto Sans CJK SC','Noto Sans CJK KR',sans-serif}
.day{font-size:9pt;font-weight:700;background:#f3f4f6;padding:3px 8px;margin:8px 0 2px;border-radius:4px}
"""


def rows(items, cache):
    e = html.escape
    out, day = [], None
    for x in items:
        dt = datetime.fromtimestamp(x["ts"], KST)
        if dt.strftime("%m/%d") != day:
            day = dt.strftime("%m/%d")
            out.append(f'<div class="day">{day} ({"월화수목금토일"[dt.weekday()]})</div>')
        name, col = SRC.get(x["s"], (x["s"], "#6b7280"))
        ko = x.get("ko") or cache.get(x["h"])
        tag = f'<span class="tag">{e(x["tag"])}</span>' if x.get("tag") else ""
        org = f'<div class="org">{e(x["h"])}</div>' if ko and ko != x["h"] else ""
        out.append(f'<div class="it{" imp" if x.get("imp") else ""}"><div class="tm">{dt:%H:%M}</div><div>'
                   f'<span class="src" style="background:{col}">{e(name)}</span>{tag}'
                   f'<a class="ko" href="{e(x.get("u") or "#")}">{e(ko or x["h"])}</a>{org}</div></div>')
    return "".join(out)


def build(src, out_dir, cache_path):
    d = json.load(open(src, encoding="utf-8"))
    end = d.get("at") or time.time()
    cut = end - HOURS * 3600
    items = sorted((x for x in d["items"] if x["ts"] >= cut), key=lambda x: -x["ts"])
    if not items:
        print("[flash-pdf] 속보 없음 — 건너뜀")
        return
    try:
        cache = json.load(open(cache_path, encoding="utf-8"))
    except Exception:
        cache = {}
    miss = [x["h"] for x in items if not x.get("ko")]
    if miss:
        translate(miss, cache)
        os.makedirs(os.path.dirname(cache_path) or ".", exist_ok=True)
        json.dump(cache, open(cache_path, "w", encoding="utf-8"), ensure_ascii=False)
    imp = [x for x in items if x.get("imp")]
    cnt = "".join(f'<span style="color:{c}">{n} {sum(1 for x in items if x["s"] == k)}</span>' for k, (n, c) in SRC.items())
    doc = (f'<!doctype html><html lang="ko"><head><meta charset="utf-8"><style>{CSS}</style></head><body>'
           f'<h1>⚡ 실시간 속보 — FinancialJuice · 월스트리트견문 · 시나재경</h1>'
           f'<div class="meta">{datetime.fromtimestamp(cut, KST):%m/%d %H:%M} ~ {datetime.fromtimestamp(end, KST):%m/%d %H:%M} KST (직전 {HOURS}시간) · '
           f'전체 {len(items)}건 · 최신순 · 한국어 기계번역 + 원문 · 시나재경은 A주 회사 공시성 속보 제외'
           f'<div class="cnt">{cnt}<span style="color:#b91c1c">★ 중요 {len(imp)}</span></div></div>'
           + (f'<h2>★ 중요 속보 ({len(imp)}건)</h2>{rows(imp, cache)}' if imp else "")
           + f'<h2>전체 ({len(items)}건)</h2>{rows(items, cache)}</body></html>')
    stamp = datetime.now(KST).strftime("%Y%m%d_%H%M")
    render_pdfs([(os.path.join(out_dir, f"16_미니블벅터_{stamp}_실시간속보_{HOURS}시간.pdf"), doc)])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("src", nargs="?", default="public/briefings/flash.json")
    ap.add_argument("--out", default="reports")
    ap.add_argument("--cache", default=".cache/ko.json")
    a = ap.parse_args()
    build(a.src, a.out, a.cache)
