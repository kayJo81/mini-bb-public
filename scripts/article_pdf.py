"""
심층 뉴스 기사 전문 → A4 PDF(여러 쪽) → 메일 (Gmail SMTP)

  python scripts/article_pdf.py --ids anthropic-s1-2026-10,oracle-ai-world-2026-10 --out reports_articles
  python scripts/article_pdf.py --ids ... --out reports_articles --mail

  · 내용: lab/deepnews/<id>.html (+ lab/deepnews/index.json 의 제목·날짜·태그), 화면 CSS 는 lab/deep.html 의 것을 그대로 씀
  · 메일: GMAIL_USER, GMAIL_APP_PASSWORD, 선택 MAIL_TO (send_mail.py·onepager.py 와 같은 Secrets)
  · 실시간 기사는 실행 시점의 내용(갱신 시각 포함)이 담긴 스냅샷
"""
import argparse
import html
import json
import os
import re
import smtplib
from email.message import EmailMessage

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DN = os.path.join(ROOT, "lab", "deepnews")
e = html.escape

PRINT_CSS = """
@page{size:A4;margin:12mm 11mm}
html,body{background:#fff!important;overflow:visible!important}
body{font-size:11.5px;line-height:1.6;-webkit-print-color-adjust:exact;print-color-adjust:exact}
.wrap{max-width:none;padding:0}
.card{box-shadow:none}
.art h1{font-size:19px}.art h2{font-size:15px;margin:18px 0 6px;padding-top:10px;break-after:avoid}.art h3{font-size:13px;break-after:avoid}
.kpis{grid-template-columns:repeat(4,1fr)}.kpi,.fig,.callout,.tldr,tr,.req{break-inside:avoid}
.tbl{overflow:visible}table{font-size:10.5px}th,td{padding:4px 6px}
.tbl table.w5,.tbl table.w7{min-width:0}
.fig svg{max-height:230px}
a{text-decoration:none;color:#1d4ed8}
.ph{display:flex;justify-content:space-between;font-size:10px;color:#6b7280;border-bottom:1px solid #e5e7eb;padding-bottom:4px;margin-bottom:8px}
.meta{font-size:10.5px;color:#6b7280;margin:2px 0 8px}
"""


def base_css():
    s = open(os.path.join(ROOT, "lab", "deep.html"), encoding="utf-8").read()
    css = re.search(r"<style>(.*?)</style>", s, re.S).group(1)
    # 휴대폰용 @media(max-width:640px) 카드 표는 PDF(A4 폭)에서 쓰이지 않게 그대로 둠(A4 는 640px 보다 넓음)
    return css


def page(meta, body):
    tags = " ".join(f'<span class="tag">{e(t)}</span>' for t in meta.get("tags", []))
    upd = f' · 갱신 {e(meta["updated"])}' if meta.get("updated") else ""
    live = " · 🔴 실시간 기사의 스냅샷" if meta.get("live") else ""
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>{e(meta["title"])}</title>
<style>{base_css()}{PRINT_CSS}</style></head><body><div class="wrap">
<div class="ph"><span>미니 블벅 랩 · 심층 뉴스</span><span>mini-bblab.pages.dev/deep?id={e(meta["id"])}</span></div>
<article class="art"><span class="date">{e(meta["date"])}</span>{upd}{live} {tags}
<h1>{e(meta["title"])}</h1><div class="lede">{e(meta.get("sub", ""))}</div>
{body}
<p class="note" style="margin-top:16px">투자 권유 아님 · 전문과 최신 갱신: https://mini-bblab.pages.dev/deep?id={e(meta["id"])}</p></article></div></body></html>"""


def render(ids, out):
    from playwright.sync_api import sync_playwright
    os.makedirs(out, exist_ok=True)
    index = {x["id"]: x for x in json.load(open(os.path.join(DN, "index.json"), encoding="utf-8"))}
    made = []
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=os.environ.get("PDF_CHROMIUM") or None)
        pg = b.new_page(viewport={"width": 794, "height": 1123})
        for i in ids:
            meta = index[i]
            body = open(os.path.join(DN, i + ".html"), encoding="utf-8").read()
            pg.set_content(page(meta, body), wait_until="load")
            path = os.path.join(out, f"{i}.pdf")
            pg.pdf(path=path, format="A4", print_background=True, prefer_css_page_size=True,
                   display_header_footer=True, header_template="<span></span>",
                   footer_template='<div style="font-size:8px;width:100%;text-align:center;color:#9ca3af"><span class="pageNumber"></span> / <span class="totalPages"></span></div>')
            made.append((meta, path))
            print(f"[article_pdf] {path} ({os.path.getsize(path)//1024} KB)")
        b.close()
    return made


def mail(made):
    user, pw = os.environ.get("GMAIL_USER", ""), os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "")
    if not user or not pw:
        print("::warning::GMAIL_USER / GMAIL_APP_PASSWORD 비밀값이 없어 메일을 보내지 않습니다 (PDF는 실행 결과 Artifacts 에 있음)")
        return
    to = [x.strip() for x in (os.environ.get("MAIL_TO") or user).split(",") if x.strip()]
    msg = EmailMessage()
    msg["Subject"] = "[미니 블벅 랩] 심층 뉴스 PDF — " + " · ".join(re.split(r"\s[—-]\s", m["title"])[0] for m, _ in made)
    msg["From"], msg["To"] = user, ", ".join(to)
    msg.set_content("심층 뉴스 전문 PDF\n\n" + "\n\n".join(f"· {m['title']}\n  {m.get('sub', '')}\n  https://mini-bblab.pages.dev/deep?id={m['id']}" for m, _ in made)
                    + "\n\n실시간 기사는 실행 시점의 스냅샷이에요. 최신 내용은 위 링크에서 확인하세요. 투자 권유 아님.")
    for m, path in made:
        with open(path, "rb") as fh:
            msg.add_attachment(fh.read(), maintype="application", subtype="pdf", filename=f"{m['id']}.pdf")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as s:
        s.login(user, pw)
        s.send_message(msg)
    print(f"[mail] 기사 PDF {len(made)}개 → {', '.join(to)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", required=True, help="쉼표로 구분한 lab/deepnews 기사 id")
    ap.add_argument("--out", default="reports_articles")
    ap.add_argument("--mail", action="store_true")
    a = ap.parse_args()
    made = render([x.strip() for x in a.ids.split(",") if x.strip()], a.out)
    if a.mail:
        mail(made)
