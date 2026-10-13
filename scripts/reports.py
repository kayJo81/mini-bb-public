"""
미니 블벅터 '📑 리포트' 화면 데이터 (briefing.yml 에서 뉴스 수집 다음에 실행) → public/briefings/reports.json

  · IB 매크로·전략: 골드만·모건스탠리·JP모건·BofA·씨티·UBS·바클레이즈·도이체 등 글로벌 투자은행의
    경제·금리·환율·증시 전망을 다룬 보도 (구글 뉴스 검색 + 이번 수집 기사·FinancialJuice 속보에서 골라냄, 직전 72시간)
  · 기업 리포트(투자의견·목표가): 뉴스 카테고리별 추적 종목의 Finviz 투자의견 표 (최근 60일) + IB 이름이 든 종목 투자의견 보도 — 산업군별로
  · 제목은 한국어 번역 (미니 블벅터 번역 AI → 구글 → 원문)

  python scripts/reports.py --live public/briefings/live.json --ratings .cache/finviz_ratings.json --out public/briefings/reports.json
"""
import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import news_scanner as ns  # noqa: E402

IB = [("Goldman Sachs", r"goldman"), ("Morgan Stanley", r"morgan stanley"), ("JPMorgan", r"jpmorgan|jp morgan|j\.p\. morgan"),
      ("BofA", r"bank of america|\bbofa\b"), ("Citi", r"\bciti\b|citigroup|citi research"), ("UBS", r"\bubs\b"),
      ("Barclays", r"barclays"), ("Deutsche Bank", r"deutsche bank"), ("Wells Fargo", r"wells fargo"), ("HSBC", r"\bhsbc\b"),
      ("Nomura", r"nomura"), ("BNP Paribas", r"bnp"), ("Societe Generale", r"societe generale|socgen"), ("Jefferies", r"jefferies"),
      ("Evercore", r"evercore"), ("Bernstein", r"bernstein"), ("BlackRock", r"blackrock"), ("Macquarie", r"macquarie"),
      ("RBC", r"\brbc\b"), ("ING", r"\bing\b economists|\bing\b says|\bing\b expects"), ("Capital Economics", r"capital economics"),
      ("Oxford Economics", r"oxford economics")]
IB_RE = re.compile("|".join(f"(?:{p})" for _, p in IB), re.I)
# 매크로·전략 내용: 연준·금리·물가·성장·환율·원자재·증시 전망
MACRO = re.compile(r"\bfed\b|fomc|rate cut|rate hike|interest rate|yield|treasur|inflation|\bcpi\b|\bpce\b|recession|gdp|growth|"
                   r"economy|economist|jobs|payroll|unemployment|dollar|\byen\b|\beuro\b|yuan|currency|\bfx\b|oil|gold|commodit|"
                   r"s&p 500|s&p500|stocks|equit|strategist|outlook|forecast|target for|year-end|bear market|bull market|"
                   r"\becb\b|\bboj\b|\bboe\b|tariff|deficit|bond", re.I)
VIEW = re.compile(r"says|sees|expects|forecast|predict|warn|raise|cut|lift|lower|boost|outlook|strategist|economist|note|"
                  r"target|view|call|recommend|overweight|underweight|bullish|bearish|probability|odds|estimate", re.I)
NOT = re.compile(r"\bshares? of goldman|goldman sachs (stock|shares)|earnings|quarterly profit|ceo |layoff|lawsuit|fine[sd]?\b|"
                 r"hires|hire[sd]?\b|appoint|job cuts|bonus", re.I)
# 개별 종목 투자의견·목표가 기사 (→ 기업 리포트 쪽으로, 지수 목표는 매크로)
STOCK_CALL = re.compile(r"price target|target (?:price|to|on)\b|\bpt\b|upgrade|downgrade|initiat|overweight|underweight|"
                        r"outperform|underperform|equal[- ]weight|neutral rating|buy rating|sell rating|stock (?:outlook|forecast)|"
                        r"목표주가|목표가|kursziel|einstufung|kaufempfehlung|objectif de cours|stock target|shares? target|target for .{1,40} stock", re.I)
INDEX = re.compile(r"s&p 500|s&p500|nasdaq[- ]?100|\bdow\b|stoxx|nikkei|kospi|msci|index target|year-end target", re.I)
QUERIES = ['"Goldman Sachs" economists OR strategists forecast', '"Morgan Stanley" strategist OR economists outlook',
           'JPMorgan strategists OR economists forecast', '"Bank of America" OR BofA strategists OR economists',
           'Citi OR UBS OR Barclays strategists forecast', '"Deutsche Bank" OR HSBC OR Nomura economists forecast',
           '"S&P 500" target strategist raises OR cuts', 'Fed rate cut forecast Goldman OR JPMorgan OR "Morgan Stanley"']


def firm_of(t):
    for name, p in IB:
        if re.search(p, t, re.I):
            return name
    return ""


def macro_calls(live_path, hours=72):
    """(매크로·전략 보도, 종목 투자의견 보도) — 둘 다 IB 이름이 든 제목만"""
    end = datetime.now(ns.KST)
    start = end - timedelta(hours=hours)
    raw = []
    for q in QUERIES:
        raw += ns.fetch_gnews(q, "US", start, end)
    try:   # 이번 수집 기사(토픽별 기사와 각 보도) — FinancialJuice·RSS·블룸버그 등 포함
        live = json.load(open(live_path, encoding="utf-8"))
        for t in live.get("topics", []):
            for s in t.get("stories", []):
                for x in s.get("sources", []):
                    raw.append({"headline": x.get("headline", ""), "source": x.get("name", ""), "url": x.get("url", ""),
                                "published": x.get("published") or s.get("first_seen") or 0, "summary": ""})
    except Exception as ex:
        print(f"[reports] live.json: {ex}")
    out, calls, seen = [], [], set()
    topic_kw = [(t, re.compile("|".join(t.get("keywords", [])) or r"(?!x)x", re.I)) for t in ns.TOPICS if t.get("tickers")]
    for a in sorted(raw, key=lambda a: -(a.get("published") or 0)):
        h = (a.get("headline") or "").strip()
        if not h or not IB_RE.search(h) or NOT.search(h):
            continue
        if not (start.timestamp() <= (a.get("published") or 0) <= end.timestamp() + 600):
            continue
        k = re.sub(r"\W+", " ", h.lower())[:70]
        if k in seen:
            continue
        seen.add(k)
        it = {"title": h, "firm": firm_of(h), "src": a.get("source", ""), "url": a.get("url", ""), "ts": int(a["published"])}
        if STOCK_CALL.search(h) and not INDEX.search(h):
            t = next((t for t, kw in topic_kw if kw.search(h)), None)   # 뉴스 카테고리(추적 종목) 단어가 든 것만
            if t:
                calls.append({**it, "group": t.get("group", ""), "topic": t["name"]})
            continue
        if MACRO.search(h) and VIEW.search(h):
            out.append(it)
    return out[:80], calls[:80]


def company_reports(ratings_path, days=60):
    try:
        R = json.load(open(ratings_path, encoding="utf-8"))
    except Exception as ex:
        print(f"[reports] ratings: {ex}")
        return []
    owner = {}
    for t in ns.TOPICS:
        for sym in t.get("tickers", []):
            owner.setdefault(sym, (t.get("group", ""), t["name"], t.get("section", "산업")))
    since = (date.today() - timedelta(days=days)).isoformat()
    out = []
    for sym, rows in R.items():
        g = owner.get(sym)
        if not g:
            continue
        for r in rows:
            if r["date"] >= since:
                out.append({"sym": sym, "group": g[0], "topic": g[1], **r})
    out.sort(key=lambda r: (r["date"], r["sym"]), reverse=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", default="public/briefings/live.json")
    ap.add_argument("--ratings", default=".cache/finviz_ratings.json")
    ap.add_argument("--out", default="public/briefings/reports.json")
    ap.add_argument("--cache", default=".cache/reports_ko.json")
    a = ap.parse_args()
    M, N = macro_calls(a.live)
    C = company_reports(a.ratings)
    # 번역 (지난 번역은 캐시에서)
    try:
        cache = json.load(open(a.cache, encoding="utf-8"))
    except Exception:
        cache = {}
    todo = [m["title"] for m in M + N if m["title"] not in cache]
    if todo:
        try:
            from weekly_events import translate_all
            cache.update({k: v for k, v in translate_all(todo).items() if v})
        except Exception as ex:
            print(f"[reports] 번역: {ex}")
    for m in M + N:
        m["ko"] = cache.get(m["title"], "")
    os.makedirs(os.path.dirname(a.cache) or ".", exist_ok=True)
    json.dump(cache, open(a.cache, "w", encoding="utf-8"), ensure_ascii=False)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    json.dump({"at": datetime.now(ns.KST).isoformat(timespec="seconds"), "macro": M, "calls": N, "ratings": C},
              open(a.out, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f"[reports] IB 매크로 {len(M)}건 · 종목 투자의견 보도 {len(N)}건 · 투자의견 표 {len(C)}건 (종목 {len({c['sym'] for c in C})}개)")


if __name__ == "__main__":
    main()
