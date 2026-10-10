"""
미니 블벅터 일일 PDF — 시황 작성 참고용 (매일 05:30 KST 수집본으로 생성)

  python scripts/daily_pdf.py public/briefings/live.json --out reports

  · 산업: 그룹별 1개 파일 (모빌리티 & 에너지, 메가테크, …) / 매크로 전체 / 지정학 전체
  · 월스트리트견문 18시간 전체 속보는 scripts/wscn_pdf.py
  · 화면과 같은 기준: 수집 시각 직전 12시간, 의견 기사 제외, 최신순, 같은 기사는 한 번만
  · 한국어 제목 + 원문 제목 + 출처, 기사 링크는 PDF 안에서 눌림
"""
import argparse
import html
import json
import os
import re
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
WINDOW_H = 12
VL = {"VERIFIED": "검증됨", "SINGLE": "단독보도", "UNVERIFIED": "미확인"}
TL = {"official": "공식", "tier1": "1급", "tier2": "2급", "tier3": "3급", "opinion": "의견", "social": "SNS"}

CSS = """
@page{size:A4;margin:12mm 11mm 14mm}
body{font-family:'Noto Sans CJK KR','Noto Sans KR','Apple SD Gothic Neo',sans-serif;color:#111827;font-size:9.5pt;margin:0}
h1{font-size:15pt;margin:0 0 2px}
.meta{color:#6b7280;font-size:8.5pt;margin-bottom:10px;border-bottom:1.5px solid #111827;padding-bottom:6px}
.box{border:1px solid #e5e7eb;border-radius:6px;padding:6px 9px;margin-bottom:10px;font-size:9pt;background:#f9fafb}
.it{padding:5px 0 6px;border-bottom:.5px solid #e5e7eb;break-inside:avoid}
.r1{display:flex;gap:5px;align-items:center;flex-wrap:wrap;font-size:7.5pt;color:#4b5563;margin-bottom:2px}
.tm{font-weight:700;color:#fff;background:#111827;border-radius:4px;padding:0 5px;font-family:monospace;font-size:8pt}
.v{border-radius:4px;padding:0 5px;font-weight:700}
.v-VERIFIED{background:#dcfce7;color:#166534}.v-SINGLE{background:#fef3c7;color:#92400e}.v-UNVERIFIED{background:#f3f4f6;color:#4b5563}
.cat{background:#e0e7ff;color:#3730a3;border-radius:4px;padding:0 5px;font-weight:600}
.rumor{background:#ffedd5;color:#c2410c;border-radius:4px;padding:0 5px}
.hl{font-weight:600;font-size:10pt;color:#111827;text-decoration:none;line-height:1.4}
.org{color:#6b7280;font-size:8pt;margin-top:1px}
.src{color:#6b7280;font-size:7.5pt;margin-top:2px}
.empty{color:#6b7280;padding:12px 0}
"""


def _fmt(ts):
    return datetime.fromtimestamp(ts, KST).strftime("%m/%d %H:%M")


def _stories(topics, cut):
    """12시간 이내 보도가 있는 의견 아닌 기사, 같은 기사는 한 번만, 최신순"""
    seen, out = set(), []
    for t in topics:
        for s in t["stories"]:
            if s.get("is_opinion") or s["headline"] in seen:
                continue
            if not any((x.get("published") or s.get("last_seen") or s["first_seen"]) >= cut for x in s["sources"]):
                continue
            seen.add(s["headline"])
            out.append((s, t["name"]))
    out.sort(key=lambda p: -p[0]["first_seen"])
    return out


def _item(s, cat):
    e = html.escape
    ko = s.get("headline_ko")
    srcs = " · ".join(f'{e(x["name"])}({TL.get(x["tier"], "")})' for x in s["sources"][:6])
    more = f" 외 {len(s['sources']) - 6}" if len(s["sources"]) > 6 else ""
    org = f'<div class="org">{e(s["headline"])}</div>' if ko else ""
    return (f'<div class="it"><div class="r1"><span class="tm">{_fmt(s["first_seen"])}</span>'
            f'<span class="v v-{s["verdict"]}">{VL.get(s["verdict"], "")}</span><span class="cat">{e(cat)}</span>'
            f'{"<span class=rumor>소식통/루머</span>" if s.get("is_rumor") else ""}<span>출처 {s["n_sources"]}</span></div>'
            f'<a class="hl" href="{e(s["url"])}">{e(ko or s["headline"])}</a>'
            f'{org}'
            f'<div class="src">{srcs}{more}</div></div>')


def _midterms_box(m):
    if not m:
        return ""
    parts = []
    g = (m.get("generic") or {}).get("average")
    if g:
        lead = f"민주 +{g['margin']:.1f}%p" if g["margin"] > 0 else f"공화 +{-g['margin']:.1f}%p"
        parts.append(f"하원 정당 지지도(여론조사 평균) 민주 {g['dem']:.1f} / 공화 {g['rep']:.1f} → {lead} ({html.escape(g.get('updated', ''))})")
    k = m.get("markets")
    if k and (k.get("house_dem") is not None or k.get("senate_dem") is not None):
        f = lambda v: "–" if v is None else f"{v:.0f}%"
        parts.append(f"민주당 다수당 확률(폴리마켓·예측시장, 여론조사 아님) 하원 {f(k.get('house_dem'))} · 상원 {f(k.get('senate_dem'))}")
    return f'<div class="box">🗳️ 미국 중간선거 — {"<br>".join(parts)}</div>' if parts else ""


def page(title, sub, items, extra=""):
    body = "".join(_item(s, c) for s, c in items) or '<div class="empty">해당 시간대 뉴스가 없습니다</div>'
    return (f'<!doctype html><html lang="ko"><head><meta charset="utf-8"><style>{CSS}</style></head><body>'
            f'<h1>{html.escape(title)}</h1><div class="meta">{html.escape(sub)} · {len(items)}건 · 의견 기사 제외 · 최신순</div>'
            f'{extra}{body}</body></html>')


def render_pdfs(docs):
    """[(경로, html)] → A4 PDF (쪽 번호 포함)"""
    from playwright.sync_api import sync_playwright
    paths = []
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=os.environ.get("PDF_CHROMIUM") or None)
        pg = b.new_page()
        for path, doc in docs:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            pg.set_content(doc, wait_until="load")
            pg.pdf(path=path, format="A4", print_background=True,
                   display_header_footer=True, header_template="<span></span>",
                   footer_template='<div style="font-size:7pt;color:#9ca3af;width:100%;text-align:center">'
                                   '<span class="pageNumber"></span> / <span class="totalPages"></span></div>',
                   margin={"top": "12mm", "bottom": "14mm", "left": "11mm", "right": "11mm"})
            paths.append(path)
            print(f"[pdf] {path} ({os.path.getsize(path) // 1024} KB)")
        b.close()
    return paths


def build(live_path, out_dir):
    d = json.load(open(live_path, encoding="utf-8"))
    end = datetime.fromisoformat(d["generated_at"]).astimezone(KST)
    cut = end.timestamp() - WINDOW_H * 3600
    sub = f"미니 블벅터 · {_fmt(cut)} ~ {_fmt(end.timestamp())} KST (직전 {WINDOW_H}시간)"
    topics = d["topics"]
    docs = []  # (파일 이름, html)
    groups = []
    for t in topics:
        if t.get("section", "산업") == "산업" and t["group"] not in groups:
            groups.append(t["group"])
    for g in groups:
        docs.append((f"산업_{g}", page(f"산업 · {g}", sub, _stories([t for t in topics if t["group"] == g and t.get("section", "산업") == "산업"], cut))))
    for sec in ("매크로", "지정학"):
        extra = _midterms_box(d.get("midterms")) if sec == "지정학" else ""
        docs.append((f"{sec}_전체", page(f"{sec} 전체", sub, _stories([t for t in topics if t.get("section") == sec], cut), extra)))

    os.makedirs(out_dir, exist_ok=True)
    stamp = end.strftime("%Y%m%d_%H%M")
    safe = lambda n: re.sub(r"[^\w가-힣]+", "", n)  # noqa: E731
    paths = render_pdfs([(os.path.join(out_dir, f"{i:02d}_미니블벅터_{stamp}_{safe(name)}.pdf"), doc)
                         for i, (name, doc) in enumerate(docs, 1)])
    with open(os.path.join(out_dir, "summary.txt"), "w", encoding="utf-8") as f:
        f.write(sub + "\n")
    return paths


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("live")
    ap.add_argument("--out", default="reports")
    a = ap.parse_args()
    build(a.live, a.out)
