"""
어닝콜 '한 장 지도' — 녹취록을 한 장으로 시각화(콜 흐름·발언 비중·Q&A 지도·주제·숫자·톤) → PDF·PNG

  python scripts/callmap.py --ids meta-3q26,tesla-3q26 --out reports_onepager

  · 데이터: lab/onepager/<id>-call.json (녹취록에서 뽑은 짧은 요약·숫자·인용 — 녹취록 전문은 저장소에 없음)
  · 테마·회사명은 같은 id의 원페이퍼 JSON(lab/onepager/<id>.json)에서 가져옴
  · onepager.py --mail --callmap 이 이 결과(<file>.png/.pdf)를 메일 본문 이미지·첨부로 씀
"""
import argparse
import json
import os

from onepager import CSS, SRC, art, e

CALL_CSS = """
.flow{display:flex;height:9mm;border-radius:6px;overflow:hidden;margin:1.4mm 0 1mm}
.flow div{display:flex;align-items:center;justify-content:center;color:#fff;font-size:7.6pt;font-weight:800;white-space:nowrap;overflow:hidden}
.lg{display:flex;flex-wrap:wrap;gap:1.2mm 3mm;font-size:7.4pt;color:#374151;margin-top:1mm}
.lg i{display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:3px;vertical-align:0}
.sub{font-size:7.4pt;color:#6b7280;font-weight:700;margin-top:1.6mm}
.nums{display:grid;grid-template-columns:1fr 1fr;gap:1.8mm}
.num{border:1px solid #e5e7eb;border-radius:8px;padding:1.6mm 2.4mm;border-left:3px solid var(--acc)}
.num .v{font-size:12.5pt;font-weight:900;line-height:1.15}.num .l{font-size:7.3pt;color:#374151;line-height:1.3}
table.qa{font-size:7.8pt;table-layout:fixed}table.qa td{padding:1.1mm 1.6mm 1.1mm 0;border-bottom:1px solid #f1f5f9;vertical-align:top}
table.qa td:first-child{width:5mm}
table.qa td.n{width:5mm;color:var(--acc);font-weight:900}
table.qa td.w{width:27mm;font-weight:700;color:#111827}table.qa td.w span{display:block;font-weight:500;color:#6b7280;font-size:6.9pt}
table.qa td.q{width:34%;color:#374151}table.qa td.a{font-weight:600}table.qa td.a span{color:var(--acc);font-weight:800}
.tone{display:grid;grid-template-columns:24mm 1fr 6mm;gap:1.4mm 2mm;align-items:center;font-size:7.6pt;margin-top:1mm}
.tone .bar{height:3mm;border-radius:2px;background:#eef0f3;overflow:hidden}.tone .bar b{display:block;height:100%;background:var(--acc)}
"""


def palette(acc, n):
    """강조색 기준 진하기 단계 + 회색 (발언 비중 막대용)"""
    return [acc] + [f"color-mix(in srgb, {acc} {max(18, 78 - k * 16)}%, #fff)" for k in range(n - 2)] + ["#9ca3af"]


def page(c, d):
    t = d["theme"]
    tot = sum(s["words"] for s in c["share"]) or 1
    cols = palette(t["acc"], max(len(c["share"]), 2))
    pct = lambda s: s["words"] / tot * 100
    share = "".join(f'<div style="width:{pct(s):.1f}%;background:{cols[k]}">{f"{pct(s):.0f}%" if pct(s) >= 6 else ""}</div>'
                    for k, s in enumerate(c["share"]))
    legend = "".join(f'<span><i style="background:{cols[k]}"></i>{e(s["who"])} · {e(s["role"])} {pct(s):.0f}%</span>'
                     for k, s in enumerate(c["share"]))
    pm = sum(p["min"] for p in c["parts"]) or 1
    parts = "".join(f'<div style="width:{p["min"] / pm * 100:.1f}%;background:{t["acc"] if k == 0 else "#111827"}">{e(p["l"])} {p["min"]}분</div>'
                    for k, p in enumerate(c["parts"]))
    nums = "".join(f'<div class="num"><div class="v">{e(n["v"])}</div><div class="l">{e(n["l"])}</div></div>' for n in c["numbers"][:8])
    qa = "".join(f'<tr><td class="n">{k}</td><td class="w">{e(q["who"])}<span>{e(q["firm"])}</span></td><td class="q">{e(q["q"])}</td>'
                 f'<td class="a"><span>{e(q["by"])}</span> {e(q["a"])}</td></tr>' for k, q in enumerate(c["qa"][:8], 1))
    tmax = max(x["n"] for x in c["themes"]) or 1
    themes = "".join(f'<div class="tone" style="grid-template-columns:30mm 1fr 8mm"><span>{e(x["t"])}</span><div class="bar"><b style="width:{x["n"] / tmax * 100:.0f}%"></b></div><b>{x["n"]}</b></div>'
                     for x in c["themes"][:8])
    tone = "".join(f'<div class="tone"><span>{e(x["l"])}</span><div class="bar"><b style="width:{x["v"] / 5 * 100:.0f}%"></b></div><b>{x["v"]}/5</b></div>'
                   for x in c["tone"])
    qs = "".join(f'<div class="qt"><div class="w">{e(q["who"])}</div><div class="en">“{e(q["en"])}”</div><div class="ko">{e(q["ko"])}</div></div>'
                 for q in c["quotes"][:2])
    n_an = len(c["qa"])
    chips = f'<span class="chip">콜 {c["minutes"]}분</span><span class="chip">애널리스트 질문 {n_an}개</span><span class="chip">녹취록 기반</span>'
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>{e(d["name"])} 어닝콜 지도</title><style>
:root{{--acc:{t["acc"]};--acc2:{t["acc2"]};--soft:{t["soft"]};--hero:{t["hero"]}}}{CSS}{CALL_CSS}</style></head><body><div class="pg">
<div class="hero"><div class="art">{art(t["art"])}</div><div class="in">
  <div class="eyebrow">어닝콜 한 장 지도 · {e(c["when"])}</div><h1>{e(d["name"])}<small>{e(d["ticker"])}</small></h1>
  <div class="lede">{c["take"]}</div><div class="chips">{chips}</div></div></div>
<div class="row"><div class="card"><h2>콜 흐름 · 누가 얼마나 말했나<span>녹취록 단어 수 기준</span></h2>
  <div class="sub">시간 배분</div><div class="flow">{parts}</div>
  <div class="sub">발언 비중</div><div class="flow" style="height:7mm">{share}</div><div class="lg">{legend}</div>
  <div class="sub" style="margin-top:2.4mm">톤 (추정)</div>{tone}</div>
  <div class="card"><h2>콜에서 나온 숫자</h2><div class="nums">{nums}</div></div></div>
<div class="card"><h2>Q&amp;A 지도<span>질문 순서 · 답변자</span></h2><table class="qa"><colgroup><col style="width:5mm"><col style="width:30mm"><col style="width:34%"><col></colgroup>{qa}</table></div>
<div class="two"><div class="card"><h2>많이 나온 주제<span>언급 횟수</span></h2>{themes}</div>
  <div class="card"><h2>가장 중요한 두 마디</h2><div style="display:grid;gap:2mm">{qs}</div></div></div>
<div class="ft">녹취록: {e(c.get("source", ""))} · 비중·횟수는 녹취록에서 직접 센 값, 톤은 미니 블벅 랩의 판단(추정) · 투자 권유 아님 · 전문: mini-bblab.pages.dev/deep?id={e(d["article"])}</div>
</div></body></html>"""


def render(ids, out):
    from playwright.sync_api import sync_playwright
    os.makedirs(out, exist_ok=True)
    made = []
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=os.environ.get("PDF_CHROMIUM") or None)
        pg = b.new_page(viewport={"width": 794, "height": 1123}, device_scale_factor=2)
        for i in ids:
            c = json.load(open(os.path.join(SRC, f"{i}-call.json"), encoding="utf-8"))
            d = json.load(open(os.path.join(SRC, f"{c.get('base', i)}.json"), encoding="utf-8"))
            if not c.get("source"):
                tr = json.load(open(os.path.join(SRC, "transcripts.json"), encoding="utf-8")).get(i) or {}
                c["source"] = tr.get("title", "")
            doc = page(c, d)
            base = os.path.join(out, c["file"])
            open(base + ".html", "w", encoding="utf-8").write(doc)
            pg.set_content(doc, wait_until="load")
            over = pg.evaluate("document.querySelector('.pg').scrollHeight - document.querySelector('.pg').clientHeight")
            if over > 0:
                print(f"::warning::{i}: 한 장을 {over}px 넘침 — 내용 줄이기 필요")
            pg.pdf(path=base + ".pdf", width="210mm", height="297mm", print_background=True, page_ranges="1")
            pg.screenshot(path=base + ".png", full_page=False)
            made.append((d, base))
            print(f"[callmap] {base}.pdf / .png")
        b.close()
    return made


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", required=True)
    ap.add_argument("--out", default="reports_onepager")
    ap.add_argument("--mail", action="store_true", help="콜 지도(본문 이미지·PDF) + 녹취록 한국어 번역 PDF를 메일로")
    a = ap.parse_args()
    ids = [x for x in a.ids.split(",") if x]
    made = render(ids, a.out)
    if a.mail and made:
        from onepager import mail
        from transcript_pdf import build as build_transcript
        tr = {}
        for i, (d, base) in zip(ids, made):
            try:
                f = build_transcript(i, a.out, ko=True)
                if f:
                    tr[base] = f
            except Exception as ex:
                print(f"::warning::{i}: 녹취록 번역 PDF 실패 — {ex}")
        mail(made, tr=tr, title="[미니 블벅 랩] 어닝콜 지도·녹취록 번역 — ",
             tr_label="어닝콜 녹취록 한국어 번역 PDF(문단마다 영어 원문 병기, 기계 번역)")
