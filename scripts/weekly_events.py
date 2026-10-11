"""
주간 전략용 정치·산업 일정: 이번 주 '날짜가 명시된' 보도만 골라 요일별로 (weekly_pdf.py 에서 사용)

  · 구글 뉴스를 요일마다 날짜를 넣어 검색 ("Oct. 14" summit OR vote …, "October 14" conference OR keynote …)
  · 제목·요약 속 날짜 표현("Oct. 13", "on Tuesday" — 일주일 안쪽 기사만)으로 요일을 정함
  · 일정 단어(선거·회담·연설·청문회 / 컨퍼런스·키노트·제품 발표·투자자의 날 …)가 제목에 있어야 하고,
    설명형('Why …?')·지역 잡음·스포츠는 제외, 신뢰 등급 있는 매체만
  · 같은 일정을 다룬 기사는 하나로 묶고 보도 매체 수를 표시
  · 번역: 미니 블벅터 번역 AI(/api/translate) → 구글 번역 → 원문
"""
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests

ET = ZoneInfo("America/New_York")
WDE = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
POL_K = re.compile(r"election|summit|talks|meeting|meets|vote|speech|speak|address|testif|hearing|visit|referendum|debate|"
                   r"\bg7\b|\bg20\b|nato|\bimf\b|world bank|apec|asean|parliament|congress|senate|shutdown|state visit|"
                   r"inaugurat|primary|ballot|ceasefire|negotiat|trade deal|deadline|campaign|rally|trip|travel|attend|host|sign|"
                   r"announce|call with|to meet|press conference|briefing|sanction", re.I)
IND_K = re.compile(r"conference|summit|keynote|launch|unveil|event|investor day|analyst day|expo|developer|showcase|forum|"
                   r"symposium|\bipo\b|reveal|demo|hackathon|\bai day\b", re.I)
NOISE = re.compile(r"^(why|how|what is|who is|when is)\b|\?$|road work|recall|obituar|high school|football|soccer|basketball|"
                   r"\bweek \d+\b|standings|weather|horoscope|lottery|church|festival|concert|game day", re.I)


# 정치: 주요 국가·기구·인물이 들어간 일정만 (지역 이사회·파업·지방 청문회 제외)
POL_MAJOR = ["Trump", "White House", "Congress", "Senate", "House Speaker", "Supreme Court", "President", "Prime Minister",
             "Vance", "Rubio", "Bessent", "Lutnick", "Greer", "Xi", "Putin", "Zelensky", "Netanyahu", "Modi", "Erdogan", "Macron",
             "Merz", "Starmer", "Meloni", "Takaichi", "Ishiba", "Lee Jae", "Carney", "Lula", "Sheinbaum", "von der Leyen", "Lagarde",
             "EU", "European Union", "European Commission", "European Parliament", "G7", "G20", "NATO", "United Nations", "UN General",
             "IMF", "World Bank", "OPEC", "APEC", "ASEAN", "BRICS", "WTO", "Iran", "Israel", "Gaza", "Ukraine", "Russia", "China",
             "Taiwan", "North Korea", "Pentagon", "Treasury", "Fed ", "tariff", "trade deal", "trade talks", "shutdown",
             "presidential", "general election", "parliamentary election", "national election", "referendum", "midterm"]
# 산업·기업: 대형 기술기업·추적 종목·IB 컨퍼런스·주요 기술 행사
IND_MAJOR = ["Apple", "Google", "Alphabet", "Microsoft", "Amazon", "AWS", "Meta", "Nvidia", "Tesla", "AMD", "Intel", "TSMC",
             "Samsung", "SK Hynix", "OpenAI", "Anthropic", "xAI", "SpaceX", "Broadcom", "Oracle", "Qualcomm", "Arm ", "ASML",
             "Micron", "Dell", "Salesforce", "Adobe", "IBM", "Cisco", "Palantir", "Netflix", "Uber", "Snowflake", "CoreWeave",
             "Nebius", "Coinbase", "Robinhood", "Sony", "Huawei", "Xiaomi", "Alibaba", "Tencent", "Baidu", "ByteDance", "TikTok",
             "Goldman Sachs", "Morgan Stanley", "JPMorgan", "J.P. Morgan", "Bank of America", "BofA", "Barclays", "UBS", "Citi ",
             "Jefferies", "Wells Fargo", "Bernstein", "Evercore", "Deutsche Bank", "Nomura",
             "CES", "Computex", "GTC", "OCP", "Open Compute", "SEMICON", "Dreamforce", "re:Invent", "Ignite", "WWDC", "Google I/O",
             "Money20/20", "Web Summit", "Hot Chips", "Snapdragon Summit", "Adobe MAX", "DevDay", "IPO", "CME", "Nasdaq", "NYSE",
             "Amazon Web Services", "Nintendo", "Spotify", "Shopify", "Intuit", "ServiceNow", "Workday"]


def major(h, words):
    for w in words:
        if re.search(r"(?<![A-Za-z])" + re.escape(w.strip()) + r"(?![A-Za-z])", h, re.I if w.islower() else 0):
            return w.strip()
    return None


# 뜻이 넓은 주체(다른 나라 의회·지방 선거와 겹침)는 주요 매체 보도일 때만
WEAK_KEYS = {"Senate", "Congress", "general election", "midterm", "EU", "referendum", "President", "Prime Minister",
             "presidential", "parliamentary election", "national election", "Treasury", "shutdown", "tariff"}


def event_day(x, mon, fri):
    """제목(없으면 요약) 속 날짜 표현 → 이번 주 날짜 (없거나 이번 주 밖이면 None)"""
    d = _day_in(x["headline"].lower(), x, mon, fri)
    return d or _day_in((x.get("summary") or "").lower(), x, mon, fri)


def _day_in(t, x, mon, fri):
    for m in re.finditer(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b", t):
        try:
            d = date(mon.year, MONTHS[m.group(1)], int(m.group(2)))
        except ValueError:
            continue
        if mon <= d <= fri:
            return d
    m = re.search(r"\b(?:on|this|next)\s+(monday|tuesday|wednesday|thursday|friday)\b", t)
    if m:
        d = mon + timedelta(days=WDE.index(m.group(1)))
        pub = datetime.fromtimestamp(x["published"], ET).date()
        if 0 <= (d - pub).days <= 9:
            return d
    return None


def dated_events(mon, fri, kind, per_day=6, extra_names=()):
    import news_scanner as ns
    extra_names = list(extra_names)
    days = [mon + timedelta(days=i) for i in range(5)]
    if kind == "pol":
        K = POL_K
        words = ('(Trump OR "White House" OR Congress OR Senate OR summit OR G20 OR G7 OR IMF OR NATO OR EU OR Xi OR Putin '
                 'OR election OR tariff OR talks)')
        extra = ['Trump this week summit OR visit OR speech OR meeting', '"this week" summit leaders', 'IMF "World Bank" annual meetings',
                 'Congress Senate vote this week', '"week ahead" Washington', 'election this week national OR presidential OR parliamentary',
                 'tariff deadline this week', 'Xi OR Putin OR Zelensky meeting this week']
    else:
        K = IND_K
        words = '(conference OR keynote OR launch OR "investor day" OR event OR summit OR unveil)'
        extra = ['tech conference this week', '"developer conference" OR "product launch" OR keynote this week',
                 'technology conference Goldman OR "Morgan Stanley" OR JPMorgan OR Barclays OR UBS OR Citi',
                 'AI summit OR chip conference this week']
    qs = [f'"{d:%b}. {d.day}" {words}' for d in days] + [f'"{d:%B} {d.day}" {words}' for d in days] + extra
    start = datetime.combine(mon - timedelta(days=10), datetime.min.time(), tzinfo=ET)
    end = datetime.now(ET)
    raw = []
    for q in qs:
        try:
            raw += ns.fetch_gnews(q, "US", start, end)
        except Exception as ex:
            print(f"[weekly] news {q}: {ex}")
    arts, seen = [], set()
    for x in raw:
        h = x["headline"]
        k = re.sub(r"\W+", " ", h.lower())[:80]
        if k in seen or NOISE.search(h) or not K.search(h):
            continue
        seen.add(k)
        tier = ns.source_tier(x["source"], x.get("domain", ""))
        # 산업은 신뢰 등급 매체만, 정치는 주요 인물·기구가 들어가면 지역 언론도 허용 (대통령 지방 일정 등)
        if tier in ("social", "opinion") or (kind != "pol" and tier == "tier3"):
            continue
        key = major(h, POL_MAJOR if kind == "pol" else IND_MAJOR + extra_names)
        if not key or (key in WEAK_KEYS and tier not in ("tier1", "tier2", "official")):
            continue
        d = event_day(x, mon, fri)
        if d:
            arts.append(x | {"day": d.isoformat(), "key": key})
        elif re.search(r"\bthis week\b|\bweek ahead\b", h, re.I) and x["published"] >= start.timestamp() + 6 * 86400:
            arts.append(x | {"day": "week", "key": key})   # 이번 주 중 (요일 미정)
    res = []
    for d in days + ["week"]:
        dk = d if d == "week" else d.isoformat()
        group = [a for a in arts if a["day"] == dk]
        evs = []
        # 같은 날 같은 주체(회사·인물·기구) 기사는 한 일정으로
        by_key = {}
        for a in group:
            by_key.setdefault(a["key"].lower(), []).append(a)
        for members in by_key.values():
            members.sort(key=lambda m: (-ns.TIER_RANK[ns.source_tier(m["source"], m.get("domain", ""))], -m["published"]))
            lead = members[0]
            evs.append({"day": dk, "title": lead["headline"], "src": lead["source"], "url": lead["url"],
                        "n": len({m["source"] for m in members}), "ts": lead["published"], "key": lead["key"]})
        res += sorted(evs, key=lambda e: -e["n"])[:per_day]
    # 요일이 달라도 같은 주체의 비슷한 기사(같은 일정)는 보도 많은 쪽 하나만
    tok = lambda t: set(re.findall(r"[a-z]{4,}", t.lower()))  # noqa: E731
    keep = []
    for e in sorted(res, key=lambda e: -e["n"]):
        if any(k["key"] == e["key"] and len(tok(k["title"]) & tok(e["title"])) / max(1, min(len(tok(k["title"])), len(tok(e["title"])))) >= 0.5
               for k in keep):
            continue
        keep.append(e)
    order = {d.isoformat(): i for i, d in enumerate(days)} | {"week": 9}
    res = sorted(keep, key=lambda e: (order[e["day"]], -e["n"]))
    print(f"[weekly] {kind}: 기사 {len(raw)} → 날짜 확인 {len(arts)} → 일정 {len(res)}")
    return res


def _nums(t):
    return set(re.findall(r"\d+", t))


def _ok(src, ko):
    """번역이 다른 문장과 뒤섞이지 않았는지: 원문의 숫자(날짜·금액)가 번역에 그대로 있어야 함"""
    return bool(ko) and _nums(src) <= _nums(ko)


def _ai(batch):
    try:
        r = requests.post("https://mini-bbterminal.pages.dev/api/translate", json={"texts": batch}, timeout=90).json()
        return r.get("ko") or []
    except Exception as ex:
        print(f"[weekly] translate: {ex}")
        return []


def translate_all(texts):
    """한국어 번역: 미니 블벅터 번역 AI(묶음 → 숫자가 안 맞으면 한 건씩) → 구글 번역 → 원문"""
    ko = {}
    todo = [t for t in dict.fromkeys(texts) if t]
    for i in range(0, len(todo), 10):
        b = todo[i:i + 10]
        for t, k in zip(b, _ai(b)):
            if _ok(t, k):
                ko[t] = k
    for t in [t for t in todo if t not in ko]:   # 묶음 번역이 어긋난 것은 한 건씩
        k = (_ai([t]) or [None])[0]
        if _ok(t, k):
            ko[t] = k
    rest = [t for t in todo if t not in ko]
    if rest:
        try:
            from wscn_pdf import translate
            cache = {}
            translate(rest, cache)
            ko.update({t: cache[t] for t in rest if _ok(t, cache.get(t))})
        except Exception:
            pass
    bad = len(todo) - len(ko)
    print(f"[weekly] 번역 {len(ko)}/{len(todo)}" + (f" (원문 유지 {bad})" if bad else ""))
    return ko
