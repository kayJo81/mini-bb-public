"""
어닝콜 녹취록 PDF — 녹취록 원문(공개 URL)을 받아 한국어 표지(콜 핵심 요약)와 함께 PDF로 (원페이퍼 메일 첨부용)

  python scripts/transcript_pdf.py --ids meta-3q26,tesla-3q26 --out reports_onepager

  · 녹취록 위치는 lab/onepager/transcripts.json ({원페이퍼id: {url, kind: pdf|html, title, when}})
    — 저장소가 공개라 녹취록 원문은 저장소에 넣지 않고, 실행할 때마다 원 출처에서 받아 온다
  · 표지: 원페이퍼 JSON(lab/onepager/<id>.json)의 요약·핵심 발언·체크포인트를 한국어로
  · 본문: PDF 원문이면 그대로 뒤에 붙이고, 웹페이지면 본문만 뽑아 화자별로 정리해 그린다
  · 결과: <out>/<원페이퍼 file>_녹취록.pdf (onepager.py --mail 이 같이 첨부)
"""
import argparse
import html
import io
import json
import os
import re

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "lab", "onepager")
HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
       "Accept-Language": "en-US,en;q=0.9"}
e = html.escape

CSS = """
@page{size:A4;margin:14mm 13mm 14mm}
body{font-family:'Noto Sans CJK KR','Noto Sans KR',sans-serif;color:#111827;font-size:9.2pt;line-height:1.5;margin:0}
.cover{page-break-after:always}
.band{border-radius:12px;padding:6mm 7mm;color:#fff;background:var(--hero)}
.band .k{font-size:8pt;font-weight:700;letter-spacing:.08em;color:var(--acc2)}
.band h1{font-size:20pt;margin:1mm 0 1.5mm;font-weight:900}
.band .lede{font-size:10pt;color:#e5e7eb}
.meta{font-size:8pt;color:#6b7280;margin:3mm 0 1mm}
h2{font-size:11pt;margin:5mm 0 2mm;padding-left:2.5mm;border-left:4px solid var(--acc)}
.q{background:var(--soft);border-left:3px solid var(--acc);border-radius:6px;padding:2mm 3mm;margin:1.6mm 0}
.q .w{font-size:7.6pt;font-weight:800;color:var(--acc)}.q .en{font-style:italic;color:#374151;font-size:8.4pt}.q .ko{font-weight:700}
ul{margin:0;padding-left:5mm}li{margin:.8mm 0}
.take{background:#111827;color:#f9fafb;border-radius:8px;padding:3mm 4mm;font-weight:700;margin-top:4mm}
.tr h2{page-break-after:avoid}
.sp{font-weight:800;color:var(--acc);margin:3.2mm 0 .6mm;page-break-after:avoid}
.sp span{color:#6b7280;font-weight:600}
.p{margin:0 0 1.6mm;text-align:justify}
.note{font-size:7.6pt;color:#9ca3af;margin-top:4mm;border-top:1px solid #e5e7eb;padding-top:2mm}
"""


def load(i):
    d = json.load(open(os.path.join(SRC, f"{i}.json"), encoding="utf-8"))
    t = json.load(open(os.path.join(SRC, "transcripts.json"), encoding="utf-8")).get(i)
    return d, t


def fetch(url):
    r = requests.get(url, headers=HDR, timeout=60)
    r.raise_for_status()
    return r


def extract(page_html):
    """웹페이지 → [(kind, text)] — 본문은 <p> 글자의 85% 이상을 담은 가장 안쪽 덩어리"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(page_html, "lxml")
    for t in soup(["script", "style", "nav", "header", "footer", "aside", "noscript", "figure"]):   # form은 남김(ASP.NET 페이지는 본문 전체가 form 안)
        t.decompose()
    plen = lambda el: sum(len(p.get_text(" ", strip=True)) for p in el.find_all("p"))
    root = soup.body or soup
    total = plen(root)
    best = root
    for el in root.find_all(["article", "div", "section", "main"]):
        if plen(el) >= total * 0.85 and len(list(el.parents)) > len(list(best.parents)):
            best = el
    out = []
    is_spk = lambda el: el.name == "div" and "speaker" in " ".join(el.get("class") or [])
    for el in best.find_all(["h2", "h3", "p", "div"]):
        if is_spk(el):                                   # 마켓비트 형식: <div class="…speaker…">이름<div>직함</div></div>
            for t in el.find_all("time"):
                t.decompose()
            sub = el.find(class_=re.compile("title|role"))
            role = sub.get_text(" ", strip=True) if sub else ""
            if sub:
                sub.extract()
            name = re.sub(r"\s+", " ", el.get_text(" ", strip=True))
            if name:
                out.append(("sp", f"{name} -- {role}" if role else name))
            continue
        if el.name == "div" or el.find(["p", "h2", "h3"]):
            continue
        txt = re.sub(r"\s+", " ", el.get_text(" ", strip=True))
        if not txt:
            continue
        first = el.find(["strong", "b", "span"])
        bold = first is not None and (first.name in ("strong", "b") or re.search(r"bold|semibold", " ".join(first.get("class") or [])))
        if el.name in ("h2", "h3"):
            out.append(("h", txt))
        elif bold and len(txt) < 120 and txt.startswith(first.get_text(strip=True)):
            name = first.get_text(" ", strip=True)       # 화자 줄: 굵은 이름 + 직함 (모틀리풀·글룸 형식)
            role = re.sub(r"^\s*(--|—|-|,)\s*", "", txt[len(name):].strip())
            out.append(("sp", f"{name} -- {role}" if role else name))
        else:
            out.append(("p", txt))
    return out


SPEAKER = re.compile(r"^([A-Z][\w.'\- ]{2,60}?)(?:\s*(?:--|—|,)\s*([^:]{2,90}))?:\s+(.+)$")


def body_html(items):
    rows = []
    for kind, txt in items:
        if kind == "h":
            rows.append(f"<h2>{e(txt)}</h2>")
        elif kind == "sp":
            name, role = (re.split(r"\s*(?:--|—)\s*", txt, maxsplit=1) + [""])[:2]
            rows.append(f'<div class="sp">{e(name)} <span>{e(role)}</span></div>')
        else:
            m = SPEAKER.match(txt)
            if m and len(m.group(1).split()) <= 5:   # 'Name, Title: 발언' 형식이면 화자와 발언을 나눔
                rows.append(f'<div class="sp">{e(m.group(1))} <span>{e(m.group(2) or "")}</span></div><p class="p">{e(m.group(3))}</p>')
            else:
                rows.append(f'<p class="p">{e(txt)}</p>')
    return "".join(rows)


def cover_html(d, t):
    qs = "".join(f'<div class="q"><div class="w">{e(q["who"])}</div><div class="en">“{e(q["en"])}”</div><div class="ko">{e(q["ko"])}</div></div>'
                 for q in d["quotes"])
    li = lambda xs: "".join(f"<li>{x}</li>" for x in xs)
    return (f'<div class="cover"><div class="band"><div class="k">어닝콜 녹취록 · {e(t.get("when", ""))}</div>'
            f'<h1>{e(d["name"])} <small style="font-size:11pt;opacity:.8">{e(d["ticker"])}</small></h1><div class="lede">{d["lede"]}</div></div>'
            f'<div class="meta">녹취록 출처: {e(t["url"])} · 원문(영어)은 다음 쪽부터 · 한국어 요약은 미니 블벅 랩 정리</div>'
            f'<h2>어닝콜 핵심 발언</h2>{qs}'
            f'<h2>좋았던 점 · 약한 고리</h2><ul>{li(d["good"])}{li(d["bad"])}</ul>'
            f'<h2>다음 체크포인트</h2><ul>{li(d["watch"])}</ul>'
            f'<div class="take">{d["take"]}</div>'
            f'<div class="note">투자 권유 아님 · 녹취록 저작권은 원 출처에 있으며 개인 참고용으로만 · 심층 뉴스 전문: mini-bblab.pages.dev/deep?id={e(d["article"])}</div></div>')


def render(doc, path):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=os.environ.get("PDF_CHROMIUM") or None)
        pg = b.new_page()
        pg.set_content(doc, wait_until="load")
        pg.pdf(path=path, format="A4", print_background=True, display_header_footer=True, header_template="<span></span>",
               footer_template='<div style="font-size:7pt;color:#9ca3af;width:100%;text-align:center"><span class="pageNumber"></span> / <span class="totalPages"></span></div>')
        b.close()


def build(i, out):
    d, t = load(i)
    if not t:
        print(f"[transcript] {i}: transcripts.json 에 없음 — 건너뜀")
        return None
    th = d["theme"]
    head = (f'<!doctype html><html lang="ko"><head><meta charset="utf-8"><style>:root{{--acc:{th["acc"]};--acc2:{th["acc2"]};'
            f'--soft:{th["soft"]};--hero:{th["hero"]}}}{CSS}</style></head><body>')
    path = os.path.join(out, d["file"] + "_녹취록.pdf")
    r = fetch(t["url"])
    if t.get("kind") == "pdf" or "pdf" in r.headers.get("content-type", ""):
        from pypdf import PdfReader, PdfWriter
        tmp = path + ".cover.pdf"
        render(head + cover_html(d, t) + "</body></html>", tmp)
        w = PdfWriter()
        for src in (open(tmp, "rb"), io.BytesIO(r.content)):
            for pg in PdfReader(src).pages:
                w.add_page(pg)
        with open(path, "wb") as fh:
            w.write(fh)
        os.remove(tmp)
    else:
        items = extract(r.text)
        ren = t.get("speakers") or {}      # 출처가 'Speaker 3'처럼만 적은 경우 이름으로 바꿈
        items = [(k, ren.get(x.split(" -- ")[0], x) if k == "sp" else x) for k, x in items]
        words = sum(len(x[1].split()) for x in items)
        if words < 3000:     # 녹취록 전체가 아니면(차단·요약 페이지) 만들지 않음
            print(f"::warning::{i}: 녹취록 본문이 너무 짧음({words}단어) — 건너뜀")
            return None
        render(head + cover_html(d, t) + f'<div class="tr"><h2>녹취록 원문 — {e(t.get("title", ""))}</h2>{body_html(items)}</div></body></html>', path)
    print(f"[transcript] {path} ({os.path.getsize(path) // 1024} KB)")
    return path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", required=True)
    ap.add_argument("--out", default="reports_onepager")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    for i in [x for x in a.ids.split(",") if x]:
        try:
            build(i, a.out)
        except Exception as ex:
            print(f"::warning::{i}: 녹취록 PDF 실패 — {ex}")
