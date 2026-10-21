"""
실적 원페이퍼 — 심층 뉴스 기사(lab/deepnews)를 A4 한 장 요약(차트·일러스트 포함)으로 → PDF·PNG, 메일

  python scripts/onepager.py --out reports_onepager            # PDF·PNG 만들기
  python scripts/onepager.py --out reports_onepager --mail     # + 메일 (본문에 이미지, PDF 첨부)

  · 내용은 lab/onepager/<id>.json (기사에서 뽑은 숫자·발언 — 기사와 같은 출처 기준)
  · 차트는 이 스크립트가 숫자로 직접 그린 SVG, 머리 그림은 회사별 일러스트(SVG)
  · 메일: GMAIL_USER, GMAIL_APP_PASSWORD, 선택 MAIL_TO (send_mail.py 와 같은 Secrets)
"""
import argparse
import glob
import html
import json
import os
import smtplib
from email.message import EmailMessage

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "lab", "onepager")
e = html.escape


# ───────── 차트 (숫자로 직접 그리는 SVG) ─────────
def bars(d, acc):
    """세로 막대: d['bars'] = [{l, v} | {l, lo, hi}(범위=가이던스)], 선택 d['line'] = [이익률 %…]"""
    items = d["bars"]
    W, left = 340, 8
    H, top, base = (178, 84, 150) if d.get("line") else (138, 20, 112)   # 이익률 꺾은선은 막대 위 별도 띠에
    vmax = max(b.get("hi", b.get("v", 0)) for b in items) * 1.08
    n = len(items)
    slot = (W - left * 2) / n
    bw = slot * 0.56
    y = lambda v: base - (base - top) * v / vmax
    out = [f'<line x1="{left}" y1="{base}" x2="{W - left}" y2="{base}" stroke="#d1d5db" stroke-width="1"/>']
    pts = []
    for i, b in enumerate(items):
        x = left + slot * i + (slot - bw) / 2
        cx = x + bw / 2
        if "hi" in b:   # 가이던스 범위: 빗금 막대 + 위쪽 범위 표시
            out.append(f'<rect x="{x:.1f}" y="{y(b["hi"]):.1f}" width="{bw:.1f}" height="{base - y(b["hi"]):.1f}" rx="4" fill="url(#hatch)" stroke="{acc}" stroke-width="1.2" stroke-dasharray="3 2"/>')
            out.append(f'<rect x="{x:.1f}" y="{y(b["lo"]):.1f}" width="{bw:.1f}" height="{base - y(b["lo"]):.1f}" rx="4" fill="{acc}" opacity=".28"/>')
            lab = f'{b["lo"]:g}~{b["hi"]:g}' if b["lo"] != b["hi"] else f'{b["hi"]:g}'
            ty = y(b["hi"]) - 6
        else:
            hl = b.get("hl")
            out.append(f'<rect x="{x:.1f}" y="{y(b["v"]):.1f}" width="{bw:.1f}" height="{base - y(b["v"]):.1f}" rx="4" fill="{acc if hl else "#cbd5e1"}"/>')
            lab = f'{b["v"]:{d.get("fmt", "g")}}'
            ty = y(b["v"]) - 6
        out.append(f'<text x="{cx:.1f}" y="{ty:.1f}" text-anchor="middle" class="cv">{d.get("pre", "")}{lab}{d.get("unit", "")}</text>')
        out.append(f'<text x="{cx:.1f}" y="{base + 14}" text-anchor="middle" class="cx">{e(b["l"])}</text>')
        if d.get("line") and d["line"][i] is not None:   # None = 그 막대엔 이익률 점 없음(예: 가이던스 미제시)
            pts.append((cx, d["line"][i], i))
    if pts:   # 이익률 꺾은선 (오른쪽 축 없이 점 위에 값)
        lo, hi = min(p[1] for p in pts) - 4, max(p[1] for p in pts) + 3
        ly = lambda v: 18 + (top - 40) * (hi - v) / (hi - lo)
        path = " ".join(f'{"M" if i == 0 else "L"}{x:.1f},{ly(v):.1f}' for i, (x, v, _) in enumerate(pts))
        out.append(f'<path d="{path}" fill="none" stroke="#111827" stroke-width="1.6"/>')
        labs = d.get("line_lab") or [None] * len(items)
        for x, v, k in pts:
            out.append(f'<circle cx="{x:.1f}" cy="{ly(v):.1f}" r="3" fill="#fff" stroke="#111827" stroke-width="1.5"/>'
                       f'<text x="{x:.1f}" y="{ly(v) - 7:.1f}" text-anchor="middle" class="cl">{labs[k] or f"{v:.1f}%"}</text>')
    defs = (f'<defs><pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
            f'<line x1="0" y1="0" x2="0" y2="6" stroke="{acc}" stroke-width="2" opacity=".45"/></pattern></defs>')
    return f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">{defs}{"".join(out)}</svg>'


def hbars(d, acc):
    """가로 막대: d['rows'] = [{l, v, note}]"""
    rows = d["rows"]
    W, rh, lw = 340, 32, 128
    H = rh * len(rows) + 6
    vmax = max(r["v"] for r in rows) * 1.02
    out = []
    for i, r in enumerate(rows):
        y0 = 4 + i * rh
        w = (W - lw - 70) * r["v"] / vmax
        op = 1 - i * 0.22
        out.append(f'<text x="0" y="{y0 + 13}" class="hl">{e(r["l"])}</text>'
                   f'<text x="0" y="{y0 + 26}" class="hn">{e(r.get("note", ""))}</text>'
                   f'<rect x="{lw}" y="{y0 + 4}" width="{max(w, 3):.1f}" height="18" rx="4" fill="{acc}" opacity="{op:.2f}"/>'
                   f'<text x="{lw + w + 6:.1f}" y="{y0 + 17}" class="cv">{d.get("pre", "")}{r["v"]:{d.get("fmt", "g")}}{d.get("unit", "")}</text>')
    return f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">{"".join(out)}</svg>'


CHARTS = {"bars": bars, "hbars": hbars}

# ───────── 머리 그림 (회사별 일러스트) ─────────
ART = {
    # 별·행성 곡선·궤도 위성·로켓
    "space": """<svg viewBox="0 0 260 150" xmlns="http://www.w3.org/2000/svg">
  <defs><linearGradient id="pl" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#38bdf8"/><stop offset="1" stop-color="#1e3a8a"/></linearGradient>
  <linearGradient id="fl" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fde68a"/><stop offset="1" stop-color="#f97316" stop-opacity="0"/></linearGradient></defs>
  <g fill="#fff">{stars}</g>
  <circle cx="210" cy="190" r="120" fill="url(#pl)" opacity=".85"/>
  <ellipse cx="150" cy="78" rx="118" ry="30" fill="none" stroke="#7dd3fc" stroke-width="1" stroke-dasharray="3 4" opacity=".7" transform="rotate(-14 150 78)"/>
  <g transform="translate(48 70) rotate(-14)"><rect x="-7" y="-3" width="14" height="6" fill="#e5e7eb"/><rect x="-19" y="-5" width="10" height="10" fill="#38bdf8"/><rect x="9" y="-5" width="10" height="10" fill="#38bdf8"/></g>
  <g transform="translate(236 64) rotate(-14)"><rect x="-6" y="-2.5" width="12" height="5" fill="#e5e7eb"/><rect x="-16" y="-4" width="8" height="8" fill="#38bdf8"/><rect x="8" y="-4" width="8" height="8" fill="#38bdf8"/></g>
  <g transform="translate(132 72) rotate(32)">
    <path d="M0,-52 C9,-40 11,-22 11,0 L11,34 L-11,34 L-11,0 C-11,-22 -9,-40 0,-52Z" fill="#f8fafc"/>
    <path d="M0,-52 C5,-45 7,-38 8,-32 L-8,-32 C-7,-38 -5,-45 0,-52Z" fill="#94a3b8"/>
    <circle cx="0" cy="-12" r="4.2" fill="#1e3a8a" stroke="#cbd5e1" stroke-width="1.4"/>
    <path d="M-11,14 L-22,36 L-11,32Z M11,14 L22,36 L11,32Z" fill="#94a3b8"/>
    <path d="M-8,34 L8,34 L4,80 L0,92 L-4,80Z" fill="url(#fl)"/>
  </g></svg>""",
    # 서버 랙·LED·GPU 칩·올라가는 화살표
    "server": """<svg viewBox="0 0 260 150" xmlns="http://www.w3.org/2000/svg">
  <defs><linearGradient id="ch" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#34d399"/><stop offset="1" stop-color="#047857"/></linearGradient></defs>
  <g stroke="#10b981" stroke-width="1" opacity=".22" fill="none">{grid}</g>
  <rect x="44" y="14" width="96" height="124" rx="8" fill="#0f172a" stroke="#334155" stroke-width="2"/>
  {units}
  <g transform="translate(190 92)">
    <g stroke="#6ee7b7" stroke-width="2" opacity=".8">{pins}</g>
    <rect x="-30" y="-30" width="60" height="60" rx="8" fill="url(#ch)"/>
    <rect x="-17" y="-17" width="34" height="34" rx="4" fill="#064e3b" stroke="#a7f3d0" stroke-width="1"/>
    <text x="0" y="5" text-anchor="middle" font-size="12" font-weight="800" fill="#d1fae5" font-family="sans-serif">AI</text>
  </g>
  <path d="M156,46 L188,22 M174,20 L188,22 L186,36" stroke="#34d399" stroke-width="3" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
</svg>""",
    # 화면·재생 버튼·필름 구멍·내려가는 주가선
    "stream": """<svg viewBox="0 0 260 150" xmlns="http://www.w3.org/2000/svg">
  <g fill="#fff" opacity=".08">{film}</g>
  <rect x="58" y="22" width="150" height="92" rx="10" fill="#18181b" stroke="#3f3f46" stroke-width="2"/>
  <rect x="66" y="30" width="134" height="76" rx="5" fill="#27272a"/>
  <path d="M70,52 L98,60 L120,48 L146,70 L170,74 L196,96" fill="none" stroke="#f87171" stroke-width="2.4" stroke-linejoin="round"/>
  <circle cx="196" cy="96" r="3.6" fill="#f87171"/>
  <circle cx="133" cy="68" r="20" fill="#e50914"/>
  <path d="M127,58 L143,68 L127,78Z" fill="#fff"/>
  <rect x="112" y="114" width="42" height="6" rx="2" fill="#3f3f46"/><rect x="98" y="120" width="70" height="5" rx="2.5" fill="#3f3f46"/>
  <g transform="translate(222 36)"><rect x="-14" y="-10" width="28" height="20" rx="4" fill="#e50914"/><text x="0" y="4.5" text-anchor="middle" font-size="12" font-weight="800" fill="#fff" font-family="sans-serif">AD</text></g>
  <path d="M222,52 L222,70 M215,63 L222,70 L229,63" stroke="#fca5a5" stroke-width="2.4" fill="none" stroke-linecap="round"/>
</svg>""",
}


def art(kind):
    import random
    rnd = random.Random(7)
    stars = "".join(f'<circle cx="{rnd.uniform(0, 260):.0f}" cy="{rnd.uniform(0, 150):.0f}" r="{rnd.choice([0.6, 0.8, 1.1, 1.5])}" opacity="{rnd.uniform(.35, .95):.2f}"/>' for _ in range(60))
    film = "".join(f'<rect x="{x}" y="{y}" width="9" height="7" rx="1.5"/>' for x in range(4, 260, 16) for y in (4, 139))
    grid = "".join(f'<path d="M{x},0 V150"/>' for x in range(0, 261, 20)) + "".join(f'<path d="M0,{y} H260"/>' for y in range(0, 151, 20))
    units = "".join(f'<rect x="52" y="{22 + i * 22}" width="80" height="16" rx="3" fill="#1e293b"/>'
                    + "".join(f'<rect x="{60 + j * 7}" y="{28 + i * 22}" width="4" height="4" rx="1" fill="#334155"/>' for j in range(6))
                    + f'<circle cx="114" cy="{30 + i * 22}" r="2.2" fill="#34d399" opacity="{rnd.uniform(.5, 1):.2f}"/>'
                    + f'<circle cx="122" cy="{30 + i * 22}" r="2.2" fill="{rnd.choice(["#34d399", "#fbbf24"])}"/>' for i in range(5))
    pins = "".join(f'<path d="M{a},-38 V-30 M{a},30 V38 M-38,{a} H-30 M30,{a} H38"/>' for a in range(-21, 22, 10))
    return (ART[kind].replace("{stars}", stars).replace("{film}", film).replace("{grid}", grid)
            .replace("{units}", units).replace("{pins}", pins))


CSS = """
@page{size:A4;margin:0}
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{width:210mm;height:297mm;overflow:hidden;font-family:'Noto Sans CJK KR','Noto Sans KR',sans-serif;color:#111827;
  font-size:8.3pt;line-height:1.4;background:#fff;-webkit-print-color-adjust:exact;print-color-adjust:exact}
.pg{padding:8mm 10mm 6mm;height:297mm;display:flex;flex-direction:column;gap:2.5mm}
.pg>*{flex-shrink:0}
.num,.k .v,.cv{font-variant-numeric:tabular-nums}
.hero{position:relative;border-radius:14px;overflow:hidden;color:#fff;padding:5mm 7mm 4.6mm;min-height:40mm;background:var(--hero)}
.hero .art{position:absolute;right:-2mm;top:0;height:100%;width:40%;opacity:.95}
.hero .art svg{width:100%;height:100%}
.hero .in{position:relative;width:66%}
.eyebrow{font-size:7.6pt;font-weight:700;letter-spacing:.08em;color:var(--acc2);text-transform:uppercase}
.hero h1{font-size:18pt;line-height:1.18;margin:1.2mm 0 1.4mm;font-weight:900;letter-spacing:-.01em}
.hero h1 small{font-size:11pt;font-weight:600;opacity:.8;margin-left:4px}
.hero .lede{font-size:9.2pt;line-height:1.45;color:#e5e7eb;font-weight:500}
.chips{display:flex;gap:5px;margin-top:2.6mm;flex-wrap:wrap}
.chip{font-size:8pt;font-weight:700;border-radius:999px;padding:2px 9px;background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.22)}
.chip.up{background:#16a34a;border-color:#16a34a}.chip.dn{background:#dc2626;border-color:#dc2626}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:2.6mm}
.k{border:1px solid #e5e7eb;border-radius:10px;padding:2mm 3mm;border-top:3px solid var(--acc)}
.k .l{font-size:7.6pt;color:#6b7280;font-weight:700}
.k .v{font-size:15pt;font-weight:900;line-height:1.15;margin:.6mm 0 .4mm;letter-spacing:-.01em}
.k .d{font-size:7.4pt;color:#374151;line-height:1.35}
.k .d b{color:var(--acc)}
.row{display:grid;grid-template-columns:1fr 1fr;gap:3mm}
.card{border:1px solid #e5e7eb;border-radius:10px;padding:2.2mm 3.2mm}
.card h2{font-size:9.6pt;margin:0 0 1.4mm;display:flex;align-items:center;gap:5px;font-weight:800}
.card h2:before{content:"";width:4px;height:11px;border-radius:2px;background:var(--acc)}
.card h2 span{font-size:7.2pt;color:#6b7280;font-weight:500;margin-left:auto}
.card svg{width:100%;height:auto;display:block}
.cv{font-size:10px;font-weight:800;fill:#111827}.cx{font-size:9.5px;fill:#6b7280;font-weight:600}
.cl{font-size:9px;font-weight:700;fill:#111827}.hl{font-size:10.5px;font-weight:800;fill:#111827}.hn{font-size:8.8px;fill:#6b7280;font-weight:600}
.cap{font-size:7.2pt;color:#6b7280;margin-top:1mm;line-height:1.35}
table{width:100%;border-collapse:collapse;font-size:8pt}
td{padding:.9mm 0;border-bottom:1px solid #f1f5f9;vertical-align:top}
td:first-child{color:#374151;font-weight:600;width:33%}
td.v{font-weight:800;white-space:nowrap;padding-right:6px}
td.c{color:#6b7280;font-size:7.4pt}
.q{display:grid;grid-template-columns:1fr 1fr;gap:2.2mm}
.qt{background:var(--soft);border-radius:8px;padding:1.8mm 3mm;border-left:3px solid var(--acc)}
.qt .w{font-size:7.2pt;font-weight:800;color:var(--acc)}
.qt .en{font-size:7.6pt;font-style:italic;color:#374151;margin:.5mm 0 .4mm;line-height:1.35}
.qt .ko{font-size:8.2pt;font-weight:700;line-height:1.38}
.two{display:grid;grid-template-columns:1.15fr .85fr;gap:3mm}
ul{margin:0;padding-left:4.2mm}li{margin:.5mm 0;line-height:1.36}
li b{color:#111827}
.warn li::marker{color:#dc2626}
.bl{border-radius:10px;padding:3mm 4mm;background:#111827;color:#f9fafb;font-size:9.4pt;font-weight:700;line-height:1.45}
.bl span{color:var(--acc2);font-size:7.6pt;letter-spacing:.06em;display:block;margin-bottom:.6mm}
.ft{margin-top:auto;font-size:6.8pt;color:#9ca3af;line-height:1.4;border-top:1px solid #e5e7eb;padding-top:1.6mm}
.tag{display:inline-block;font-size:6.6pt;font-weight:700;border-radius:4px;padding:0 4px;background:#fef3c7;color:#92400e;vertical-align:1px;margin-left:3px;font-style:normal}
"""


def page(d):
    t = d["theme"]
    kp = "".join(f'<div class="k"><div class="l">{e(k["l"])}</div><div class="v">{e(k["v"])}</div><div class="d">{k["d"]}</div></div>' for k in d["kpis"])
    cap = lambda c: f'<div class="cap">{c["cap"]}</div>' if c.get("cap") else ""
    charts = "".join(f'<div class="card"><h2>{e(c["t"])}<span>{e(c.get("s", ""))}</span></h2>{CHARTS[c["type"]](c, t["acc"])}{cap(c)}</div>'
                     for c in d["charts"])
    gd = "".join(f'<tr><td>{e(r[0])}</td><td class="v">{e(r[1])}</td><td class="c">{r[2]}</td></tr>' for r in d["guide"]["rows"])
    qs = "".join(f'<div class="qt"><div class="w">{e(q["who"])}{"<i class=tag>" + e(q["tag"]) + "</i>" if q.get("tag") else ""}</div>'
                 f'<div class="en">“{e(q["en"])}”</div><div class="ko">{e(q["ko"])}</div></div>' for q in d["quotes"])
    pos = "".join(f"<li>{x}</li>" for x in d["good"])
    neg = "".join(f"<li>{x}</li>" for x in d["bad"])
    chk = "".join(f"<li>{x}</li>" for x in d["watch"])
    chips = "".join(f'<span class="chip {c.get("k", "")}">{e(c["t"])}</span>' for c in d["chips"])
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>{e(d["name"])} 원페이퍼</title><style>
:root{{--acc:{t["acc"]};--acc2:{t["acc2"]};--soft:{t["soft"]};--hero:{t["hero"]}}}{CSS}</style></head><body><div class="pg">
<div class="hero"><div class="art">{art(t["art"])}</div><div class="in">
  <div class="eyebrow">{e(d["eyebrow"])}</div><h1>{e(d["name"])}<small>{e(d["ticker"])}</small></h1>
  <div class="lede">{d["lede"]}</div><div class="chips">{chips}</div></div></div>
<div class="kpis">{kp}</div>
<div class="row">{charts}</div>
<div class="two"><div class="card"><h2>{e(d["guide"]["t"])}<span>{e(d["guide"].get("s", ""))}</span></h2><table>{gd}</table></div>
  <div class="card"><h2>좋았던 점 · 약한 고리</h2><ul>{pos}</ul><ul class="warn" style="margin-top:1.2mm">{neg}</ul></div></div>
<div class="card"><h2>{e(d.get("quote_t", "어닝콜 핵심 발언"))}<span>{e(d["quote_src"])}</span></h2><div class="q">{qs}</div></div>
<div class="two"><div class="card"><h2>다음 체크포인트</h2><ul>{chk}</ul></div>
  <div class="bl"><span>ONE-LINE TAKE</span>{d["take"]}</div></div>
<div class="ft">{d["foot"]} · 투자 권유 아님 · 미니 블벅 랩 심층 뉴스 전문: mini-bblab.pages.dev/deep?id={e(d["article"])}</div>
</div></body></html>"""


def render(ids, out):
    from playwright.sync_api import sync_playwright
    os.makedirs(out, exist_ok=True)
    made = []
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=os.environ.get("PDF_CHROMIUM") or None)
        pg = b.new_page(viewport={"width": 794, "height": 1123}, device_scale_factor=2)
        for i in ids:
            d = json.load(open(os.path.join(SRC, f"{i}.json"), encoding="utf-8"))
            doc = page(d)
            base = os.path.join(out, d["file"])
            open(base + ".html", "w", encoding="utf-8").write(doc)
            pg.set_content(doc, wait_until="load")
            over = pg.evaluate("document.querySelector('.pg').scrollHeight - document.querySelector('.pg').clientHeight")
            if over > 0:
                print(f"::warning::{i}: 한 장을 {over}px 넘침 — 내용 줄이기 필요")
            pg.pdf(path=base + ".pdf", width="210mm", height="297mm", print_background=True, page_ranges="1")
            pg.screenshot(path=base + ".png", full_page=False)
            made.append((d, base))
            print(f"[onepager] {base}.pdf / .png")
        b.close()
    return made


def mail(made):
    user, pw = os.environ.get("GMAIL_USER", ""), os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "")
    if not user or not pw:
        print("::warning::GMAIL_USER / GMAIL_APP_PASSWORD 비밀값이 없어 메일을 보내지 않습니다")
        return
    to = [x.strip() for x in (os.environ.get("MAIL_TO") or user).split(",") if x.strip()]
    msg = EmailMessage()
    tr = {base: base + "_녹취록.pdf" for _, base in made if os.path.exists(base + "_녹취록.pdf")}   # transcript_pdf.py 결과
    msg["Subject"] = ("[미니 블벅 랩] 어닝콜 원페이퍼·녹취록 — " if tr else "[미니 블벅 랩] 실적 원페이퍼 — ") + " · ".join(d["name"] for d, _ in made)
    msg["From"], msg["To"] = user, ", ".join(to)
    msg.set_content("실적 원페이퍼\n\n" + "\n".join(f"· {d['name']}: {d['plain']}" for d, _ in made)
                    + f"\n\n첨부: 원페이퍼 PDF (A4 한 장){' · 어닝콜 녹취록 PDF ' + str(len(tr)) + '개' if tr else ''}\n심층 뉴스 전문: https://mini-bblab.pages.dev/deep")
    body = ['<div style="font-family:Apple SD Gothic Neo,Malgun Gothic,sans-serif;max-width:760px;margin:0 auto;color:#111827">',
            '<h2 style="font-size:18px;margin:8px 0">📄 실적 원페이퍼</h2>']
    for n, (d, _) in enumerate(made):
        body.append(f'<p style="font-size:14px;margin:14px 0 6px"><b>{e(d["name"])}</b> — {e(d["plain"])}</p>'
                    f'<a href="https://mini-bblab.pages.dev/deep?id={e(d["article"])}"><img src="cid:op{n}" alt="{e(d["name"])} 원페이퍼" '
                    f'style="width:100%;max-width:760px;border:1px solid #e5e7eb;border-radius:8px"></a>')
    body.append(f'<p style="font-size:12px;color:#6b7280">A4 원페이퍼 PDF{" · 어닝콜 녹취록 PDF(한국어 요약 표지 + 영어 원문)" if tr else ""} 첨부 · 이미지를 누르면 심층 뉴스 전문 · 투자 권유 아님</p></div>')
    msg.add_alternative("".join(body), subtype="html")
    htmlpart = msg.get_payload()[1]
    for n, (d, base) in enumerate(made):
        with open(base + ".png", "rb") as fh:
            htmlpart.add_related(fh.read(), maintype="image", subtype="png", cid=f"<op{n}>", filename=os.path.basename(base) + ".png")
    for d, base in made:
        for f in [base + ".pdf"] + ([tr[base]] if base in tr else []):
            with open(f, "rb") as fh:
                msg.add_attachment(fh.read(), maintype="application", subtype="pdf", filename=os.path.basename(f))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as s:
        s.login(user, pw)
        s.send_message(msg)
    print(f"[mail] 원페이퍼 {len(made)}개·녹취록 {len(tr)}개 → {', '.join(to)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", default="", help="쉼표로 구분 (비우면 lab/onepager/*.json 전부)")
    ap.add_argument("--out", default="reports_onepager")
    ap.add_argument("--mail", action="store_true")
    ap.add_argument("--transcripts", action="store_true", help="transcripts.json 의 녹취록 PDF도 만들어 함께 첨부")
    a = ap.parse_args()
    ids = [x for x in a.ids.split(",") if x] or sorted(os.path.basename(f)[:-5] for f in glob.glob(os.path.join(SRC, "*.json")))
    made = render(ids, a.out)
    if a.transcripts:
        from transcript_pdf import build as build_transcript
        for i in ids:
            try:
                build_transcript(i, a.out)
            except Exception as ex:
                print(f"::warning::{i}: 녹취록 PDF 실패 — {ex}")
    if a.mail and made:
        mail(made)
