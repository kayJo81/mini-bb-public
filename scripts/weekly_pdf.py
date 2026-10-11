"""
주간 전략 PDF — 이번 주(월~금) 이벤트를 요일별로 + 주간 전략 (매주 일요일 20:00 KST, weekly.yml)

  python scripts/weekly_pdf.py --out reports_weekly [--json public_lab/weekly.json]

  1. 매크로: 미국·유럽(유로·영국)·일본 경제지표, 중앙은행 이벤트·인사 연설 (Forex Factory 주간 캘린더, 영향 High = **),
     미 국채 입찰 (TreasuryDirect, 10·20·30년물 = **), 옵션 만기 (매월 셋째 금요일 = **)
  2. 정치 / 3. 산업·기업: 이번 주 날짜가 명시된 보도만 골라 요일별로 (scripts/weekly_events.py)
  4. 실적 발표: Nasdaq 실적 캘린더 — 시가총액 200억 달러 이상(S&P 500 대형주) + 미니 블벅터 추적 종목
  5. 주간 전략: 위 일정과 시장 환경(랩 기록)으로 정리한 체크 포인트
"""
import argparse
import html
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
ET, KST = ZoneInfo("America/New_York"), ZoneInfo("Asia/Seoul")
HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
       "Accept": "application/json"}
WD = "월화수목금토일"
CUR = {"USD": "🇺🇸 미국", "EUR": "🇪🇺 유로존", "GBP": "🇬🇧 영국", "JPY": "🇯🇵 일본"}
# 중앙은행 이벤트: 발언·기자회견·금리 결정·의사록·베이지북 (지표 이름의 'Fed'—필라델피아 연은 지수·연방 예산—는 제외)
CB = re.compile(r"Speaks|Testifies|Press Conference|Monetary Policy Statement|Rate Statement|Interest Rate Decision|"
                r"Policy Rate|Cash Rate|Bank Rate|Minutes|Beige Book|Monetary Policy Report", re.I)
STRONG_BOND = re.compile(r"\b(10|20|30)-Year|19-Year|29-Year", re.I)


def week_range(now):
    d = now.date()
    mon = d - timedelta(days=d.weekday()) if d.weekday() < 5 else d + timedelta(days=7 - d.weekday())
    return mon, mon + timedelta(days=4)


def macro(mon, fri):
    out = []
    try:
        ev = requests.get("https://nfs.faireconomy.media/ff_calendar_thisweek.json", headers=HDR, timeout=30).json()
    except Exception as ex:
        print(f"[weekly] ff: {ex}")
        ev = []
    for x in ev:
        if x["country"] not in CUR:
            continue
        t = datetime.fromisoformat(x["date"])
        d = t.astimezone(ET).date()
        if not (mon <= d <= fri + timedelta(days=1)):
            continue
        imp = x.get("impact")
        speech = bool(CB.search(x["title"]))
        if imp not in ("High", "Medium") and not speech:
            continue
        out.append({"day": d.isoformat(), "kst": t.astimezone(KST).strftime("%m/%d %H:%M"), "ts": t.timestamp(),
                    "who": CUR[x["country"]], "title": x["title"], "star": imp == "High",
                    "kind": "중앙은행" if speech else "지표", "fc": x.get("forecast") or "", "prev": x.get("previous") or ""})
    try:
        au = requests.get("https://www.treasurydirect.gov/TA_WS/securities/upcoming?format=json", headers=HDR, timeout=30).json()
    except Exception as ex:
        print(f"[weekly] treasury: {ex}")
        au = []
    for x in au:
        d = date.fromisoformat(x["auctionDate"][:10])
        if mon <= d <= fri:
            t = datetime(d.year, d.month, d.day, 13, 0, tzinfo=ET)
            out.append({"day": d.isoformat(), "kst": t.astimezone(KST).strftime("%m/%d %H:%M"), "ts": t.timestamp(), "who": "🇺🇸 미 국채",
                        "title": f"{x['securityTerm']} {x.get('securityType', '')} 입찰", "star": bool(STRONG_BOND.search(x["securityTerm"])),
                        "kind": "국채 입찰", "fc": "", "prev": ""})
    # 옵션 만기: 매월 셋째 금요일 (월물 만기, 쿼드러플 위칭은 3·6·9·12월)
    d = mon
    while d <= fri:
        if d.weekday() == 4 and 15 <= d.day <= 21:
            t = datetime(d.year, d.month, d.day, 16, 0, tzinfo=ET)
            q = d.month in (3, 6, 9, 12)
            out.append({"day": d.isoformat(), "kst": t.astimezone(KST).strftime("%m/%d %H:%M"), "ts": t.timestamp(), "who": "📑 파생",
                        "title": "쿼드러플 위칭(주가지수·개별 선물·옵션 동시 만기)" if q else "미국 월물 옵션 만기(주식·지수 옵션, 셋째 금요일)",
                        "star": True, "kind": "만기", "fc": "", "prev": ""})
        if d.weekday() == 2 and 15 <= (d + timedelta(days=30)).day <= 21 and (d + timedelta(days=30)).weekday() == 4:
            t = datetime(d.year, d.month, d.day, 9, 30, tzinfo=ET)
            out.append({"day": d.isoformat(), "kst": t.astimezone(KST).strftime("%m/%d %H:%M"), "ts": t.timestamp(), "who": "📑 파생",
                        "title": "VIX 선물·옵션 만기", "star": False, "kind": "만기", "fc": "", "prev": ""})
        d += timedelta(days=1)
    return sorted(out, key=lambda x: x["ts"])


def earnings(mon, fri, watch):
    out = []
    d = mon
    while d <= fri:
        try:
            rows = requests.get(f"https://api.nasdaq.com/api/calendar/earnings?date={d}", headers=HDR, timeout=30).json()["data"]["rows"] or []
        except Exception as ex:
            print(f"[weekly] earnings {d}: {ex}")
            rows = []
        for r in rows:
            cap = float(re.sub(r"[^\d.]", "", r.get("marketCap") or "") or 0)
            if cap >= 2e10 or r["symbol"] in watch:
                tm = {"time-pre-market": "장 전", "time-after-hours": "장 마감 후"}.get(r.get("time"), "시간 미정")
                out.append({"day": d.isoformat(), "sym": r["symbol"], "name": r["name"], "cap": cap, "when": tm,
                            "eps": r.get("epsForecast") or "", "star": cap >= 2e11 or r["symbol"] in watch})
        d += timedelta(days=1)
    return sorted(out, key=lambda x: (x["day"], -x["cap"]))


def env_snapshot():
    for p in ("public_lab/env.json", "labdata/env/summary.json"):
        try:
            d = json.load(open(p, encoding="utf-8"))
            return (d.get("summary") or d)[-1]
        except Exception:
            continue
    try:
        return requests.get("https://mini-bblab.pages.dev/env.json", timeout=20).json()["summary"][-1]
    except Exception:
        return {}


def strategy(M, E, env):
    tips = []
    stars = [m for m in M if m["star"]]
    if stars:
        tips.append("이번 주 핵심 매크로(**): " + ", ".join(f"{WD[date.fromisoformat(m['day']).weekday()]} {m['title']}" for m in stars[:8]))
    big = sorted(E, key=lambda e: -e["cap"])[:8]
    if big:
        tips.append("실적 주목: " + ", ".join(f"{e['sym']}({WD[date.fromisoformat(e['day']).weekday()]} {e['when']})" for e in big)
                    + " — 은행·대형주 실적이 시장 방향과 업종 순환을 가를 수 있음")
    if any(m["kind"] == "만기" for m in M):
        tips.append("금요일 옵션 만기: 콜월·풋월 근처에서 가격이 묶이거나(핀닝) 만기 뒤 감마가 풀리며 변동성이 커질 수 있음 — 랩 '시장 환경'의 SPX 콜월/풋월 확인")
    if env:
        v, term, pc = env.get("vix"), env.get("vix_term"), env.get("pc_equity")
        mood = []
        if v is not None:
            mood.append(f"VIX {v:.1f}" + (" (안정)" if v <= 15 else " (경계)" if v >= 22 else ""))
        if term is not None:
            mood.append(f"VIX/VIX3M {term:.2f}" + (" 콘탱고 — 단기 공포 낮음" if term < 0.9 else " 백워데이션 — 단기 공포" if term >= 1 else ""))
        if pc is not None:
            mood.append(f"주식 풋/콜 {pc}" + (" — 콜 쏠림(과열 주의)" if pc <= 0.5 else " — 풋 쏠림(공포)" if pc >= 0.8 else ""))
        if env.get("curve_10y3m") is not None:
            mood.append(f"10년−3개월 금리차 {env['curve_10y3m']:+.2f}%p")
        tips.append("시장 환경(지난 금요일 마감): " + " · ".join(mood))
    cb = [m for m in M if m["kind"] == "중앙은행"]
    if cb:
        tips.append(f"중앙은행 발언 {len(cb)}건 — 금리 경로 기대 변화에 따라 성장주·장기채 민감")
    au = [m for m in M if m["kind"] == "국채 입찰" and m["star"]]
    if au:
        tips.append("장기물 입찰(" + ", ".join(m["title"] for m in au) + ") — 응찰 부진 시 금리 급등·성장주 부담")
    tips.append("대응: 핵심 일정 전후엔 포지션 크기를 줄이고, 실적 발표 종목은 발표 다음 날 반응(랩 신호)으로 추세 확인 후 진입")
    return tips


CSS = """
@page{size:A4}
body{font-family:'Noto Sans CJK KR','Noto Sans KR',sans-serif;color:#111827;font-size:9.5pt}
h1{font-size:20pt;margin:0 0 4px}.sub{color:#6b7280;font-size:9pt;margin-bottom:10px}
h2{font-size:13pt;margin:16px 0 6px;padding-bottom:3px;border-bottom:2px solid #111b33;break-after:avoid}
h3{font-size:10.5pt;margin:10px 0 4px;color:#1d4ed8;break-after:avoid}
table{width:100%;border-collapse:collapse}tr{break-inside:avoid}
td{padding:3px 6px;border-bottom:.5px solid #e5e7eb;vertical-align:top}
td.t{width:72px;color:#6b7280;white-space:nowrap}td.w{width:78px;white-space:nowrap}
.star{color:#dc2626;font-weight:800}.k{font-size:8pt;color:#6b7280}
.n{color:#6b7280;font-size:8.5pt}a{color:inherit;text-decoration:none}
ul{margin:4px 0 0 16px;padding:0}li{margin:3px 0}
.none{color:#9ca3af}
.card{border:1px solid #e5e7eb;border-radius:8px;padding:6px 10px 8px;margin:0 0 10px;break-inside:avoid}
.ch{font-weight:800;font-size:11.5pt;color:#1d4ed8;margin:2px 0 4px}
.more{font-size:8pt;color:#6b7280;margin-top:5px;line-height:1.45}
.day{font-weight:800;font-size:11.5pt;margin:12px 0 4px;break-after:avoid}
.ln{margin:3px 0 3px 4px;line-height:1.55;break-inside:avoid}
"""


def fcprev(m):
    if not (m["fc"] or m["prev"]):
        return ""
    return f" <span class='n'>예상 {html.escape(m['fc'] or '–')} · 이전 {html.escape(m['prev'] or '–')}</span>"


ICON = {"지표": "📊", "중앙은행": "🏦", "국채 입찰": "💵", "만기": "📑"}


def day_cards(M, E, P, I, mon):
    """요일 카드: 한국 시간 날짜별로 매크로·정치·산업·실적을 시간 순으로 합침.
    ** 중요 일정을 위로, 중요하지 않은 중앙은행 인사 발언·단기 국채 입찰은 '그 밖에'로 접음"""
    cards = {}

    def add(d, item):
        cards.setdefault(d, []).append(item)
    # 같은 시각·같은 나라 지표는 한 줄로 (예: CPI 월별·연간·코어)
    merged = {}
    for m in M:
        k = (m["ts"], m["who"], m["kind"])
        if k in merged and m["kind"] == "지표":
            g = merged[k]
            g["title"] += " / " + m["title"]
            g["ko"] = (g.get("ko") or g["title"]) + " · " + (m.get("ko") or m["title"])
            g["star"] = g["star"] or m["star"]
            if m["fc"] or m["prev"]:
                g["fc"] = (g["fc"] + " / " if g["fc"] else "") + (m["fc"] or "–")
                g["prev"] = (g["prev"] + " / " if g["prev"] else "") + (m["prev"] or "–")
        else:
            merged[k] = dict(m)
    for m in merged.values():
        t = datetime.fromtimestamp(m["ts"], KST)
        text = f"{m['who'].split(' ', 1)[-1]} · {m.get('ko') or m['title']}"
        sub = f"예상 {m['fc'] or '–'} · 이전 {m['prev'] or '–'}" if (m["fc"] or m["prev"]) else ""
        add(t.date(), {"sort": t.strftime("%H:%M"), "time": t.strftime("%H:%M"), "cat": "매크로", "icon": ICON.get(m["kind"], "📊"),
                       "star": m["star"], "text": text, "sub": sub, "url": "",
                       "more": m["kind"] in ("중앙은행", "국채 입찰") and not m["star"]})
    # 실적: 같은 날 같은 시간대(장 전/장 마감 후)는 한 줄로 — 장 전 = 그날 밤, 장 마감 후 = 다음 날 새벽 (한국 시간)
    groups = {}
    for x in E:
        groups.setdefault((x["day"], x["when"]), []).append(x)
    for (day, when), xs in groups.items():
        d = date.fromisoformat(day)
        if when == "장 마감 후":
            kd, tm, srt = d + timedelta(days=1), "05:00경", "05:00"
        elif when == "장 전":
            kd, tm, srt = d, "21:00경", "21:00"
        else:
            kd, tm, srt = d, "시간 미정", "23:59"
        xs.sort(key=lambda x: -x["cap"])
        add(kd, {"sort": srt, "time": tm, "cat": "실적", "icon": "💰", "star": any(x["star"] for x in xs),
                 "text": f"실적 ({when}) · " + ", ".join(("**" if x["star"] else "") + x["sym"] for x in xs),
                 "sub": ", ".join(x["name"] for x in xs[:6]) + (f" 외 {len(xs) - 6}" if len(xs) > 6 else ""), "url": "", "more": False})
    for L, cat, icon in ((P, "정치", "🏛"), (I, "산업", "💻")):
        for x in L:
            kd = "week" if x["day"] == "week" else date.fromisoformat(x["day"])
            more = f" 외 {x['n'] - 1}곳" if x["n"] > 1 else ""
            add(kd, {"sort": "99", "time": "일정", "cat": cat, "icon": icon, "star": x["n"] >= 8,
                     "text": x.get("ko") or x["title"], "sub": (x["title"] + " · " if x.get("ko") else "") + f"{x['src']}{more} 보도",
                     "url": x["url"], "more": False})
    out = []
    for i in range(6):   # 월~토 (토요일은 미국 금요일 밤 일정이 한국 새벽으로 넘어올 때만)
        d = mon + timedelta(days=i)
        items = sorted(cards.get(d, []), key=lambda x: (not x["star"], x["sort"]))
        if i == 5 and not items:
            continue
        out.append({"date": d.isoformat(), "label": f"{d.month}/{d.day} ({WD[d.weekday()]})" + (" 새벽" if i == 5 else ""),
                    "main": [x for x in items if not x["more"]], "more": sorted([x for x in items if x["more"]], key=lambda x: x["sort"])})
    if cards.get("week"):
        out.append({"date": "week", "label": "이번 주 중 (요일 미정)", "main": cards["week"], "more": []})
    return out


CB_NAME = [("FOMC|Fed |Federal Reserve|Powell|Warsh", "연준"), ("MPC|BOE|Bailey", "영란은행"), ("ECB|Lagarde", "ECB"),
           ("BOJ|Ueda", "일본은행")]
STRIP = re.compile(r"FOMC|MPC|ECB|BOE|BOJ|위원|총재|의장|부의장|이사|발언|연설|연준|영란은행|일본은행|중앙은행|·|\s+")


def _cb(m):
    for pat, name in CB_NAME:
        if re.search(pat, m["title"]):
            return name
    return m["who"].split(" ", 1)[-1]


VARIANT = re.compile(r"\b(?:m/m|y/y|q/q)\b|월별|연간|전월비|전월 대비|전년비|전년 대비|분기별|예비치|확정치|속보치|"
                     r"코어|핵심|근원|헤드라인", re.I)


def calendar(M, E, P, I, mon):
    """요일별 한 줄 요약 캘린더 (미국 날짜 기준): 요일마다 @매크로 / @정치 / @산업 및 기업 / @실적 발표
    · 항목은 ' / ' 로 잇고 중요(**) 일정을 앞에 · 중앙은행 인사 발언은 은행별로 이름을 묶고, 단기 국채 입찰도 한 줄로"""
    days = [mon + timedelta(days=i) for i in range(5)]
    out = []
    for d in days:
        ds = d.isoformat()
        ms = [m for m in M if m["day"] == ds]
        star, plain = [], []
        # 지표: 같은 시각·같은 나라는 하나로 — 월별/연간/근원 같은 변형은 한 이름으로, 다른 지표는 '·'로 이어서
        seen = {}
        for m in ms:
            if m["kind"] != "지표":
                continue
            k = (m["ts"], m["who"])
            g = seen.setdefault(k, {"star": False, "bases": [], "fc": [], "country": m["who"].split(" ", 1)[-1]})
            g["star"] = g["star"] or m["star"]
            base = re.sub(r"\s+", " ", VARIANT.sub("", m.get("ko") or m["title"])).strip(" ·") or (m.get("ko") or m["title"])
            if base not in g["bases"]:
                g["bases"].append(base)
            if m["fc"] and m["star"] and len(g["fc"]) < 2:
                g["fc"].append(m["fc"])
        for g in seen.values():
            fc = f"(예상 {'·'.join(g['fc'])})" if g["fc"] else ""
            text = f"{g['country']} {'·'.join(g['bases'])}{fc}"
            (star if g["star"] else plain).append(("** " if g["star"] else "") + text)
        # 중앙은행 발언: 중요(**)는 따로, 나머지는 은행별로 이름을 묶음
        cb = {}
        for m in ms:
            if m["kind"] != "중앙은행":
                continue
            if re.search(r"Beige Book", m["title"], re.I):
                star.append("** 연준 베이지북")
                continue
            if re.search(r"Minutes", m["title"], re.I):
                star.append(f"** {_cb(m)} 의사록")
                continue
            if m["star"]:
                star.append("** " + (m.get("ko") or m["title"]))
                continue
            nm = re.sub(r"\s+", " ", STRIP.sub(" ", m.get("ko") or m["title"])).strip() or m["title"]
            cb.setdefault(_cb(m), []).append(nm)
        for bank, names in cb.items():
            plain.append(f"{bank} {'·'.join(dict.fromkeys(names))} 발언")
        # 국채 입찰·만기
        short = []
        for m in ms:
            if m["kind"] == "국채 입찰":
                term = re.sub(r"\s*(Bill|Note|Bond|TIPS|FRN|CMB)?\s*입찰$", "", m["title"]).replace("-Week", "주").replace("-Year", "년")
                (star.append(f"** 미 국채 {term} 입찰") if m["star"] else short.append(term))
            elif m["kind"] == "만기":
                star.append("** " + (m.get("ko") or m["title"]))
        if short:
            plain.append("미 국채 " + "·".join(short) + " 입찰")
        lines = []
        macro = star + plain
        if macro:
            lines.append(("매크로", " / ".join(macro)))
        for L, cat in ((P, "정치"), (I, "산업 및 기업")):
            xs = sorted([x for x in L if x["day"] == ds], key=lambda x: -x["n"])
            if xs:
                lines.append((cat, " / ".join(("** " if x["n"] >= 8 else "") + _short(x.get("ko") or x["title"]) for x in xs)))
        es = [x for x in E if x["day"] == ds]
        if es:
            parts = []
            for when in ("장 전", "장 마감 후", "시간 미정"):
                xs = sorted([x for x in es if x["when"] == when], key=lambda x: -x["cap"])
                if xs:
                    st = any(x["star"] for x in xs)
                    parts.append(("** " if st else "") + "·".join(x["sym"] for x in xs) + " " + when.replace(" ", ""))
            lines.append(("실적 발표", " / ".join(parts)))
        out.append({"date": ds, "label": f"{WD[d.weekday()]}요일 ({d.month}/{d.day})", "lines": lines})
    week = [x for x in P + I if x["day"] == "week"]
    if week:
        out.append({"date": "week", "label": "이번 주 중 (요일 미정)", "lines": [("정치·산업", " / ".join(_short(x.get("ko") or x["title"]) for x in week))]})
    return out


def _short(t, n=48):
    t = re.sub(r"\s+", " ", t).strip()
    return t if len(t) <= n else t[:n - 1] + "…"


def card_row(x):
    e = html.escape
    star = "<span class='star'>** </span>" if x["star"] else ""
    text = f"<a href='{e(x['url'])}'>{e(x['text'])}</a>" if x.get("url") else e(x["text"])
    sub = f"<div class='n'>{e(x['sub'])}</div>" if x.get("sub") else ""
    return f"<tr><td class='t'>{e(x['time'])}</td><td>{star}{x['icon']} {text}{sub}</td></tr>"


def build(out_dir, json_out=None):
    now = datetime.now(KST)
    mon, fri = week_range(now.astimezone(ET))
    try:
        from close_pdf import GROUPS
        watch = {s for _, g in GROUPS for s, _ in g}
    except Exception:
        watch = set()
    M = macro(mon, fri)
    E = earnings(mon, fri, watch)
    nstart = datetime.combine(mon - timedelta(days=4), datetime.min.time(), tzinfo=ET)
    nend = datetime.now(ET)
    from weekly_events import dated_events, translate_all
    P = dated_events(mon, fri, "pol")
    names = []
    try:
        from close_pdf import GROUPS
        names = [n for _, g in GROUPS for _, n in g if len(n) > 3]
    except Exception:
        pass
    I = dated_events(mon, fri, "ind", extra_names=names)
    KO = translate_all([m["title"] for m in M] + [x["title"] for x in P + I])
    for x in M + P + I:
        x["ko"] = KO.get(x["title"])
    env = env_snapshot()
    S = strategy(M, E, env)
    cards = day_cards(M, E, P, I, mon)
    e = html.escape
    body = "<h2>주간 전략</h2><ul>" + "".join(f"<li>{e(t)}</li>" for t in S) + "</ul>"
    cal = calendar(M, E, P, I, mon)
    body += "<h2>주간 주요 일정 <span class='k'>미국 날짜 기준 · <span class='star'>**</span> = 중요</span></h2>"
    for c in cal:
        body += f"<div class='day'>• {e(c['label'])}</div>"
        for cat, text in c["lines"]:
            body += f"<p class='ln'>@ {e(cat)}: {e(text).replace('** ', '<span class=star>**</span> ')}</p>"
        if not c["lines"]:
            body += "<p class='ln none'>주요 일정 없음</p>"
    title = f"주간 전략 · {mon.month}/{mon.day}~{fri.month}/{fri.day}"
    sub = f"{now:%Y-%m-%d %H:%M} KST 작성 · 시각은 한국 시간 · 자료: Forex Factory, TreasuryDirect, Nasdaq 실적 캘린더, 구글 뉴스, 미니 블벅 랩"
    doc = (f"<!doctype html><html lang='ko'><head><meta charset='utf-8'><style>{CSS}</style></head><body>"
           f"<h1>{e(title)}</h1><div class='sub'>{e(sub)}</div>{body}</body></html>")
    os.makedirs(out_dir, exist_ok=True)
    from daily_pdf import render_pdfs
    render_pdfs([(os.path.join(out_dir, f"주간전략_{mon:%m%d}-{fri:%m%d}.pdf"), doc)])
    open(os.path.join(out_dir, "summary.txt"), "w", encoding="utf-8").write(f"미니 블벅터 · {title} · 매크로 {len(M)} · 실적 {len(E)}")
    open(os.path.join(out_dir, "subject.txt"), "w", encoding="utf-8").write(f"[미니 블벅터] {title}")
    if json_out:
        os.makedirs(os.path.dirname(json_out) or ".", exist_ok=True)
        json.dump({"at": now.isoformat(timespec="minutes"), "mon": mon.isoformat(), "fri": fri.isoformat(), "macro": M, "earnings": E,
                   "politics": P, "industry": I, "strategy": S, "env": env, "cards": cards, "calendar": cal}, open(json_out, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"[weekly] {title} · 매크로 {len(M)}(**{sum(m['star'] for m in M)}) · 실적 {len(E)} · 정치 {len(P)} · 산업 {len(I)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="reports_weekly")
    ap.add_argument("--json")
    a = ap.parse_args()
    build(a.out, a.json)
