"""
미국 장 마감 종목별 등락 사유 PDF — 'M월 D일 등락 사유' (시장 등락 PDF와 같은 종목·같은 순서)

  python scripts/reasons_pdf.py --out reports [--date 2026-10-09] [--cache .cache/ko.json] [--pdf-only]

  · 기사(기간 = 직전 거래일 장 마감 16:00 ET ~ 그날 장 마감 30분 뒤):
    종목별 — Finviz 종목 뉴스, Finnhub 종목 뉴스(FINNHUB_KEY), 위불(Webull) 종목 뉴스,
             (위에서 사유를 못 찾은 종목만) 구글 뉴스 종목 검색
    전체 피드 — 미니 블벅터 수집분(live.json: 구글 뉴스·FinancialJuice·월스트리트견문·동방재부·언론사 RSS 40곳),
             FinancialJuice 누적분·FJ 뉴스 소스(WSJ·CNBC·벤징가·PR뉴스와이어·야후·시킹알파·SCMP …), 블룸버그 최신,
             언론사·기관 RSS, 월스트리트견문·동방재부 7x24 — 제목에 종목 이름(영문·중문)이 있는 것만
  · 고르는 기준(점수): 종목 이름·티커가 제목에 있음 › 'shares jump/fall', 'why … stock', 실적·목표가·등급 같은 움직임 설명
    › 등락 방향과 맞는 표현 › 최신 / 여러 종목을 묶은 시황·'살 만한 종목' 류는 감점
  · 설명 기사가 없으면 '관련 기사 없음' (억지로 채우지 않음) — 업종 ETF 는 업종 전체 흐름이라 대개 없음
  · 제목은 한국어 기계번역 + 원문, 출처·시각(KST)
"""
import argparse
import requests
import html
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from close_pdf import ET, GROUPS, INDICES, KST, WD, fetch, fmt, r2, row  # noqa: E402

UP = re.compile(r"\b(?:jump|jumps|jumped|surge|surges|surged|soar|soars|soared|rall(?:y|ies|ied)|gain|gains|gained|climb|climbs|"
                r"climbed|rise|rises|rose|rising|pop|pops|popped|spike|spikes|spiked|rebound|rebounds|higher|upgrade[sd]?|"
                r"raises?|raised|beats?|tops?|record high|all-time high)\b", re.I)
DN = re.compile(r"\b(?:fall|falls|fell|falling|drop|drops|dropped|slide|slides|slid|plunge|plunges|plunged|tumble|tumbles|"
                r"tumbled|sink|sinks|sank|slump|slumps|slumped|lower|downgrade[sd]?|cuts?|misses|missed|sell-off|selloff|"
                r"retreat|retreats|retreated|slip|slips|slipped|decline|declines|declined)\b", re.I)
WHY = re.compile(r"\bwhy\b.*\b(?:stock|shares)\b|\b(?:stock|shares)\b.*\b(?:today|this week|after|on|as|following)\b|"
                 r"\bprice target\b|\bearnings\b|\bguidance\b|\bresults\b|\bquarter\b|\bdeal\b|\bcontract\b|\bapproval\b|"
                 r"\bacquire|\bacquisition\b|\boffering\b|\bpartnership\b|\banalyst\b|\brating\b", re.I)
ROUNDUP = re.compile(r"stocks? (?:making|to watch|on the move)|biggest (?:moves|movers)|top (?:gainers|losers|\d+)|"
                     r"stock market today|market (?:wrap|recap)|premarket movers|midday movers|movers:|futures|"
                     r"\b(?:dow|s&p 500|nasdaq) (?:rises|falls|gains|drops|closes)|best .* to buy|should you buy|"
                     r"buy (?:now|before)|millionaire|prediction|\d+ (?:stocks|reasons)|if you (?:had )?invested|"
                     r"research reports|what's moving|last (?:month|week|year|quarter)|\d+[- ](?:year|month)|years? ago|"
                     r"looks? (?:stretched|cheap|expensive|attractive)|undervalued|overvalued|valuation|congress|senator|"
                     r"should you|is it time|here's why|here is why|bull case|bear case|long[- ]term|dividend (?:stock|investors)|"
                     r"retire|passive income|vs\.? |versus|better (?:buy|stock)", re.I)
# '오늘 왜 올랐나/내렸나' 류는 의견 매체라도 사유 설명으로 인정
WHY_TODAY = re.compile(r"\bwhy\b.*\b(?:stock|shares)\b.*\b(?:up|down|higher|lower|soar|jump|surg|fall|drop|sink|slid|plung|"
                       r"rall|climb|ris|gain|tumbl|slump|today|friday|monday|tuesday|wednesday|thursday)", re.I)
PAST = re.compile(r"last (?:month|week|year|quarter)|in (?:\d+|five|ten) (?:days|weeks|months)|this (?:month|year)|"
                  r"years? ago|worth buying|buy (?:the|this) (?:dip|stock)|you should buy", re.I)
OPINION_SRC = re.compile(r"motley fool|fool\.com|simply wall|zacks|investorplace|24/7|gurufocus|insider monkey|marketbeat|"
                         r"tikr|ainvest|seeking alpha", re.I)
# 중문 속보(월스트리트견문·동방재부)용 종목 이름
CN = {"NVDA": ["英伟达"], "AAPL": ["苹果"], "MSFT": ["微软"], "AMZN": ["亚马逊"], "GOOGL": ["谷歌", "字母表"], "META": ["Meta", "脸书"],
      "TSLA": ["特斯拉"], "MU": ["美光"], "TSM": ["台积电"], "INTC": ["英特尔"], "AVGO": ["博通"], "QCOM": ["高通"],
      "ASML": ["阿斯麦"], "ORCL": ["甲骨文"], "SMCI": ["超微电脑"], "SNDK": ["闪迪"], "SKHY": ["海力士"], "AMD": ["AMD", "超威"],
      "ARM": ["安谋", "Arm"], "WDC": ["西部数据"], "STX": ["希捷"], "CSCO": ["思科"], "DELL": ["戴尔"], "PLTR": ["Palantir"],
      "COIN": ["Coinbase"], "MSTR": ["Strategy", "微策略"], "CRWV": ["CoreWeave"], "LITE": ["Lumentum"], "COHR": ["Coherent"],
      "SPCX": ["SpaceX", "太空探索"], "CRCL": ["Circle"], "HOOD": ["Robinhood"], "IBM": ["IBM"], "CRM": ["Salesforce"],
      "ADBE": ["Adobe", "奥多比"], "NOW": ["ServiceNow"], "GEV": ["GE Vernova"], "CEG": ["Constellation"], "OKLO": ["Oklo"]}
CN_UP = re.compile(r"涨|飙升|走高|拉升|上扬|新高|大涨|涨超|涨逾")
CN_DN = re.compile(r"跌|下挫|走低|跳水|重挫|大跌|跌超|跌逾|下滑")
GENERIC = {"global", "applied", "analog", "texas", "advanced", "intuitive", "power", "planet", "bloom", "general", "iShares",
           "ishares", "procure", "international", "the", "palo", "super", "rocket", "nano"}


def aliases(sym, name):
    n = name.lower()
    words = [w for w in re.findall(r"[a-z][a-z0-9&'.-]+", n) if w not in GENERIC and len(w) > 2]
    al = {n} | ({words[0]} if words else set())
    if sym not in ("ON", "NOW", "BE", "PL", "APP", "ARM", "UFO", "ITA", "LEU", "SMR"):   # 흔한 영어 단어 티커는 이름만
        al.add(sym.lower())
    return [re.compile(rf"(?<![a-z0-9]){re.escape(a)}(?![a-z0-9])", re.I) for a in al] + \
        [re.compile(re.escape(c)) for c in CN.get(sym, []) if not c.isascii()]


def shared_pool(start, end):
    """종목 구분 없는 피드 전부 (미니 블벅터 수집분 + 속보·언론사 피드) → [{headline, source, url, published}]"""
    import news_scanner as ns
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    pool = []
    # 미니 블벅터 수집분 (구글 뉴스·FJ·월스트리트견문·동방재부·RSS·Finnhub·위불 — 토픽에 들어간 기사)
    for path in ("public/briefings/live.json",):
        try:
            for t in json.load(open(path, encoding="utf-8"))["topics"]:
                for st in t["stories"]:
                    for x in st["sources"]:
                        pool.append({"headline": x["headline"], "source": x["name"], "url": x["url"], "published": x["published"]})
        except Exception as ex:
            print(f"[reasons] live.json: {ex}")
    jobs = []
    with ThreadPoolExecutor(8) as ex:
        # FinancialJuice 누적분 + FJ 뉴스 소스 피드
        try:
            from fj_pdf import FEEDS, fetch_feed, fetch_fj
            jobs += [ex.submit(fetch_feed, *f) for f in FEEDS] + [ex.submit(fetch_fj)]
            try:
                pool += [{"headline": x["headline"], "source": x["source"], "url": x["url"], "published": x["ts"]}
                         for x in json.load(open(".cache/fj.json", encoding="utf-8"))]
            except Exception:
                pass
        except Exception as e:
            print(f"[reasons] fj: {e}")
        try:
            from bbg_pdf import fetch as fetch_bbg
            jobs.append(ex.submit(fetch_bbg))
        except Exception as e:
            print(f"[reasons] bbg: {e}")
        rss = [ex.submit(ns.fetch_rss, *f) for f in ns.RSS_FEEDS]
        cn = [ex.submit(ns.fetch_wallstreetcn, start.astimezone(KST)), ex.submit(ns.fetch_eastmoney, start.astimezone(KST))]
        for f in jobs:
            try:
                pool += [{"headline": x["headline"], "source": ("블룸버그 " + x["source"]) if "bloomberg" in x["url"] else x["source"],
                          "url": x["url"], "published": x["ts"]} for x in f.result()]
            except Exception as e:
                print(f"[reasons] feed: {e}")
        for f in rss + cn:
            try:
                pool += [{"headline": x["headline"], "source": x["source"], "url": x["url"], "published": x["published"]}
                         for x in f.result()]
            except Exception as e:
                print(f"[reasons] feed: {e}")
    s, e = start.timestamp(), end.timestamp()
    seen, out = set(), []
    for x in pool:
        k = re.sub(r"\W+", " ", (x["headline"] or "").lower()).strip()
        if k and k not in seen and s <= (x["published"] or 0) <= e:
            seen.add(k)
            out.append(x)
    srcs = {}
    for x in out:
        srcs[x["source"]] = srcs.get(x["source"], 0) + 1
    print(f"[reasons] 전체 피드 {len(out)}건 · 출처 {len(srcs)}곳")
    return out


def gnews(sym, name, start, end):
    import news_scanner as ns
    q = f'"{name}" stock' if len(name) > 3 else f'"{name}" {sym} stock'
    try:
        return [{"headline": x["headline"], "source": x["source"], "url": x["url"], "published": x["published"]}
                for x in ns.fetch_gnews(q, "US", start, end) if start.timestamp() <= x["published"] <= end.timestamp()]
    except Exception as ex:
        print(f"[reasons] gnews {sym}: {ex}")
        return []


def news(sym, start, end):
    """Finnhub + 위불 종목 뉴스 (기간 안, 제목 중복 제거)"""
    import news_scanner as ns
    items = []
    tok = os.environ.get("FINNHUB_KEY", "")
    if tok:
        items += ns.fetch_finnhub_company(sym, start, end, tok)
    try:
        items += ns.fetch_webull(sym)
    except Exception as ex:
        print(f"[reasons] webull {sym}: {ex}")
    items += [x | {"via": "Finviz"} for x in ns.fetch_finviz(sym)]
    s, e = start.timestamp(), end.timestamp()
    seen, out = set(), []
    for x in items:
        k = re.sub(r"\W+", " ", x["headline"].lower()).strip()
        if not k or k in seen or not (s <= x["published"] <= e):
            continue
        seen.add(k)
        out.append(x)
    return out


DAY_OPEN = {}   # pick() 이 쓰는 '그날 새벽 4시(ET)' — 그 전 기사는 전날 장 정리일 가능성이 커 감점


def pick(sym, name, pct, items, n=1):
    """움직임을 설명하는 기사 고르기 → [(점수, 기사)]"""
    al = aliases(sym, name)
    scored = []
    for x in items:
        h = x["headline"]
        rel = any(p.search(h) for p in al)
        if not rel:
            continue
        sc = 3
        up, dn = bool(UP.search(h) or CN_UP.search(h)), bool(DN.search(h) or CN_DN.search(h))
        if WHY.search(h):
            sc += 2
        if up or dn:
            sc += 2
            if (pct > 0 and up and not dn) or (pct < 0 and dn and not up):
                sc += 2
            elif (pct > 0 and dn and not up) or (pct < 0 and up and not dn):
                sc -= 3   # 반대 방향 기사(전날 움직임 등)
        if ROUNDUP.search(h) and not WHY_TODAY.search(h):
            sc -= 5
        if DAY_OPEN.get("ts") and x["published"] < DAY_OPEN["ts"]:
            sc -= 2   # 전날 장 마감 직후 기사(전날 움직임 정리일 때가 많음)
        if PAST.search(h):
            sc -= 6   # 지난 기간 움직임·매수 추천 (당일 사유 아님)
        if OPINION_SRC.search(x.get("source") or "") and not WHY_TODAY.search(h):
            sc -= 2   # 의견·분석 매체는 '오늘 왜' 기사일 때만
        scored.append((sc, x["published"], x))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [(sc, x) for sc, _, x in scored if sc >= 5][:n]


CSS = """
@page{size:A4}
body{font-family:'Noto Sans CJK KR','Noto Sans KR',sans-serif;color:#111827;font-size:9pt}
h1{font-size:20pt;margin:0 0 4px}.sub{color:#6b7280;font-size:9pt;margin-bottom:12px;line-height:1.5}
h2{font-size:12.5pt;margin:14px 0 5px;break-after:avoid}
table{width:100%;border-collapse:collapse}thead{display:table-header-group}tr{break-inside:avoid}
th{background:#111b33;color:#fff;font-weight:700;text-align:left;padding:4px 7px;font-size:8.5pt}
td{padding:5px 7px;border-bottom:.5px solid #e5e7eb;vertical-align:top}
td.tk{font-weight:700;width:46px}td.p{text-align:right;width:58px;font-weight:700;white-space:nowrap}
.nm{display:block;font-weight:400;color:#6b7280;font-size:7.5pt}
.up{color:#dc2626}.dn{color:#2563eb}
.r{margin:0 0 4px}.r:last-child{margin:0}.ko{font-weight:600}.org{color:#6b7280;font-size:7.8pt}
.src{color:#9ca3af;font-size:7.5pt}.none{color:#9ca3af}
a{color:inherit;text-decoration:none}
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="reports")
    ap.add_argument("--date")
    ap.add_argument("--cache", default=".cache/ko.json")
    ap.add_argument("--pdf-only", action="store_true")
    a = ap.parse_args()

    stocks = list(dict.fromkeys(s for _, g in GROUPS for s, _ in g))
    names = {s: n for _, g in GROUPS for s, n in g}
    syms = [s for s, _ in INDICES] + stocks
    with ThreadPoolExecutor(8) as pool:
        data = dict(zip(syms, pool.map(fetch, syms)))
    if a.date:
        day = datetime.strptime(a.date, "%Y-%m-%d").date()
    else:
        days = set.intersection(*[set(data[s]) for s, _ in INDICES if data[s]])
        now = datetime.now(ET)
        day = max(d for d in days if d < now.date() or now.hour >= 16)
        if os.environ.get("CLOSE_REQUIRE_TODAY") and day != now.date():
            print(f"[reasons] {now:%m/%d} 미국 휴장 — PDF 만들지 않음")
            return
    prev = max(d for d in data["^GSPC"] if d < day)
    start = datetime(prev.year, prev.month, prev.day, 16, 0, tzinfo=ET)
    end = datetime(day.year, day.month, day.day, 16, 30, tzinfo=ET)

    moves = {s: row(s, data[s], day) for s in stocks}
    DAY_OPEN["ts"] = datetime(day.year, day.month, day.day, 4, 0, tzinfo=ET).timestamp()
    # 뉴스: 위불은 병렬, Finnhub 은 news_scanner 가 호출 간격(분당 60회)을 지켜 준다
    with ThreadPoolExecutor(4) as pool:
        got = dict(zip(stocks, pool.map(lambda s: news(s, start, end), stocks)))
    common = shared_pool(start, end)
    for s in stocks:
        al = aliases(s, names[s])
        have = {re.sub(r"\W+", " ", x["headline"].lower()).strip() for x in got[s]}
        got[s] += [x for x in common if any(p.search(x["headline"]) for p in al)
                   and re.sub(r"\W+", " ", x["headline"].lower()).strip() not in have]
    # 그래도 사유가 없는 종목은 구글 뉴스 종목 검색으로 한 번 더 (등락 ±1% 이상만)
    need = [s for s in stocks if moves[s] and abs(float(moves[s]["pct"])) >= 1
            and not pick(s, names[s], float(moves[s]["pct"]), got[s])]
    for s in need:
        got[s] += gnews(s, names[s], start, end)
        time.sleep(1)
    print(f"[reasons] 구글 뉴스 추가 검색 {len(need)}종목")
    picks = {}
    for s in stocks:
        d = moves[s]
        if not d:
            continue
        pct = float(d["pct"])
        picks[s] = pick(s, names[s], pct, got[s], n=2 if abs(pct) >= 3 else 1)
    found = sum(1 for v in picks.values() if v)
    print(f"[reasons] {day} · 종목 {len(stocks)}개 · 기사 {sum(len(v) for v in got.values())}건 · 사유 찾음 {found}개")

    # 번역 (캐시 공유)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from wscn_pdf import translate
    try:
        cache = json.load(open(a.cache, encoding="utf-8"))
    except Exception:
        cache = {}
    heads = [x["headline"] for v in picks.values() for _, x in v]
    if heads:
        translate(heads, cache)
        os.makedirs(os.path.dirname(a.cache) or ".", exist_ok=True)
        json.dump(cache, open(a.cache, "w", encoding="utf-8"), ensure_ascii=False)

    e = html.escape
    cls = lambda v: "up" if r2(v) > 0 else "dn" if r2(v) < 0 else ""  # noqa: E731
    parts = []
    for title, items in GROUPS:
        rows = sorted(items, key=lambda t: -(float(moves[t[0]]["pct"]) if moves[t[0]] else -1e9))
        body = ""
        for s, n in rows:
            d = moves[s]
            pct = f"<td class='p {cls(d['pct'])}'>{fmt(d['pct'], True, True)}</td>" if d else "<td class='p'>—</td>"
            rs = ""
            for _, x in picks.get(s, []):
                t = datetime.fromtimestamp(x["published"], KST)
                ko = cache.get(x["headline"])
                rs += (f"<div class='r'><a href='{e(x['url'] or '#')}'><span class='ko'>{e(ko or x['headline'])}</span></a>"
                       + (f"<div class='org'>{e(x['headline'])}</div>" if ko else "")
                       + f"<div class='src'>{e(x['source'])} · {t:%m/%d %H:%M}</div></div>")
            body += (f"<tr><td class='tk'>{e(s)}<span class='nm'>{e(n)}</span></td>{pct}"
                     f"<td>{rs or '<span class=none>관련 기사 없음</span>'}</td></tr>")
        parts.append(f"<h2>{e(title)}</h2><table><thead><tr><th>티커</th><th style='text-align:right'>등락률</th>"
                     f"<th>등락 사유 (당일 기사)</th></tr></thead><tbody>{body}</tbody></table>")

    kday = day + timedelta(days=1)
    title = f"{kday.month}월 {kday.day}일 등락 사유"
    sub = (f"미국 {day.month}/{day.day}({WD[day.weekday()]}) 마감 기준 · 시장 등락 PDF와 같은 종목·순서 · "
           f"기사 기간 {start.astimezone(KST):%m/%d %H:%M} ~ {end.astimezone(KST):%m/%d %H:%M} KST<br>"
           f"종목 이름이 제목에 들어간 당일 기사 중 움직임을 설명하는 것 (±3% 이상은 최대 2건) · 한국어 기계번역 + 원문 · "
           f"사유 찾음 {found}/{len(stocks)}종목")
    doc = (f"<!doctype html><html lang='ko'><head><meta charset='utf-8'><style>{CSS}</style></head><body>"
           f"<h1>{title}</h1><div class='sub'>{sub}</div>{''.join(parts)}</body></html>")
    from daily_pdf import render_pdfs
    render_pdfs([(os.path.join(a.out, f"{kday.month}.{kday.day:02d} 등락 사유.pdf"), doc)])
    if not a.pdf_only:
        open(os.path.join(a.out, "summary.txt"), "w", encoding="utf-8").write(f"미니 블벅터 · {title} · 미국 {day.month}/{day.day} 마감")
        open(os.path.join(a.out, "subject.txt"), "w", encoding="utf-8").write(f"[미니 블벅터] {title} (미국 {day.month}/{day.day} 마감)")


if __name__ == "__main__":
    main()
