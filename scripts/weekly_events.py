"""
주간 전략용 정치·산업 일정: 이번 주 '날짜가 명시된' 보도만 골라 요일별로 (weekly_pdf.py 에서 사용)

  · 구글 뉴스를 요일마다 날짜를 넣어 검색 ("Oct. 14" summit OR vote …, "October 14" conference OR keynote …)
  · 제목·요약 속 날짜 표현("Oct. 13", "on Tuesday" — 일주일 안쪽 기사만)으로 요일을 정함
  · 일정 단어(선거·회담·연설·청문회 / 컨퍼런스·키노트·제품 발표·투자자의 날 …)가 제목에 있어야 하고,
    설명형('Why …?')·지역 잡음·스포츠는 제외, 신뢰 등급 있는 매체만
  · 같은 일정을 다룬 기사는 하나로 묶고 보도 매체 수를 표시
  · 산업 일정은 ① 주요 행사 고정 목록(tech_events.json: OCP·GTC·IB 기술 컨퍼런스 …)을 이름으로 검색해 개막일을 찾고
    ② Techmeme 행사 캘린더(techmeme.com/events) ③ 구글 뉴스 날짜 보도로 보충
  · 이번 주~3주 뒤까지 미리 찾아 저장(lab-data 의 events_ahead.json) → 그 주에 기사가 없어도 빠지지 않음
  · 행사별 기조연설·발표 기업(젠슨 황·리사 수 …)과 주요 CEO 의 연설·대담 일정을 따로 찾아 붙임
  · 랩 /weekly 에서 직접 넣은 일정(api/events)도 합침 — '매년 반복'이면 다음 해 같은 주에도 자동으로
  · 번역: 미니 블벅터 번역 AI(/api/translate) → 구글 번역 → 원문
"""
import json
import os
import re
from collections import Counter
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
                   r"\bweek \d+\b|standings|weather|horoscope|lottery|church|festival|concert|game day|\bsing|singer|cricket|"
                   r"medal|olympi|asian games|award|wedding|birthday|anniversary|speakers? join|to miss", re.I)


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


HERE = os.path.dirname(os.path.abspath(__file__))
CURATED = json.load(open(os.path.join(HERE, "tech_events.json"), encoding="utf-8"))

# 주요 기술 기업 CEO → (회사, 한글 이름)
EXECS = {"Jensen Huang": ("Nvidia", "젠슨 황"), "Lisa Su": ("AMD", "리사 수"), "Satya Nadella": ("Microsoft", "사티아 나델라"),
         "Sundar Pichai": ("Google", "순다르 피차이"), "Tim Cook": ("Apple", "팀 쿡"), "Mark Zuckerberg": ("Meta", "마크 저커버그"),
         "Sam Altman": ("OpenAI", "샘 올트먼"), "Dario Amodei": ("Anthropic", "다리오 아모데이"), "Elon Musk": ("Tesla", "일론 머스크"),
         "Andy Jassy": ("Amazon", "앤디 재시"), "Matt Garman": ("AWS", "맷 가먼"), "Hock Tan": ("Broadcom", "혹 탄"),
         "Lip-Bu Tan": ("Intel", "립부 탄"), "C.C. Wei": ("TSMC", "웨이저자"), "Cristiano Amon": ("Qualcomm", "크리스티아노 아몬"),
         "Michael Dell": ("Dell", "마이클 델"), "Larry Ellison": ("Oracle", "래리 엘리슨"), "Marc Benioff": ("Salesforce", "마크 베니오프"),
         "Arvind Krishna": ("IBM", "아르빈드 크리슈나"), "Sanjay Mehrotra": ("Micron", "산제이 메로트라"), "Alex Karp": ("Palantir", "알렉스 카프"),
         "Rene Haas": ("Arm", "르네 하스"), "Chuck Robbins": ("Cisco", "척 로빈스"), "Michael Intrator": ("CoreWeave", "마이클 인트레이터"),
         "Matt Murphy": ("Marvell", "맷 머피"), "Demis Hassabis": ("Google DeepMind", "데미스 허사비스")}
KO_CO = {"Nvidia": "엔비디아", "AMD": "AMD", "Microsoft": "마이크로소프트", "Google": "구글", "Alphabet": "구글", "Apple": "애플",
         "Meta": "메타", "OpenAI": "오픈AI", "Anthropic": "앤트로픽", "Tesla": "테슬라", "Amazon": "아마존", "AWS": "AWS",
         "Broadcom": "브로드컴", "Intel": "인텔", "TSMC": "TSMC", "Qualcomm": "퀄컴", "Dell": "델", "Oracle": "오라클",
         "Salesforce": "세일즈포스", "IBM": "IBM", "Micron": "마이크론", "Palantir": "팔란티어", "Arm": "Arm", "Cisco": "시스코",
         "CoreWeave": "코어위브", "Marvell": "마벨", "Samsung": "삼성전자", "SK Hynix": "SK하이닉스", "Arista": "아리스타",
         "Credo": "크레도", "Astera Labs": "아스테라랩스", "Lumentum": "루멘텀", "Coherent": "코히런트", "Supermicro": "슈퍼마이크로",
         "Super Micro": "슈퍼마이크로", "Vertiv": "버티브", "Celestica": "셀레스티카", "Kioxia": "키오시아", "SanDisk": "샌디스크",
         "Western Digital": "웨스턴디지털", "Seagate": "시게이트", "xAI": "xAI", "Google DeepMind": "구글 딥마인드", "HPE": "HPE"}
NOW = lambda: datetime.now(ET)  # noqa: E731


def _q(ev):
    return ev.get("q") or f'"{ev["name"]}"'


def _peak_dates(q, days):
    """'<행사명>' + "October 13" 처럼 날짜를 붙여 검색 → 언급이 가장 많은 날 = 개막일, 그 뒤 4일 안에 꽤 많은 날 = 폐막일
    (그날·전날 나온 기사는 기사 날짜 표기와 겹치므로 미리 나온 기사만 셈)"""
    import news_scanner as ns
    now = NOW()
    cnt = {}
    for d in days:
        arts = ns.fetch_gnews(f'({q}) "{d:%B} {d.day}"', "US", now - timedelta(days=60), now)
        cnt[d] = sum(datetime.fromtimestamp(a["published"], ET).date() < d - timedelta(days=1) for a in arts)
    best = max(cnt, key=lambda d: (cnt[d], -d.toordinal()))
    if cnt[best] < 8:
        return None, None
    end = best
    for i in range(1, 5):
        d = best + timedelta(days=i)
        if cnt.get(d, 0) >= max(5, 0.4 * cnt[best]):
            end = d
    return best, end


def load_store(path):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return {}


def curated_events(mon, fri, store, extra=(), weeks=3):
    """주요 행사 고정 목록 중 이번 주~(weeks-1)주 뒤 달에 열리는 행사: 최근 2주 보도가 있으면(임박) 이름+날짜 검색으로 날짜를 찾음.
    찾은 날짜는 store(lab-data/events_ahead.json)에 저장해 다음 주에도 그대로 씀"""
    import news_scanner as ns
    last = fri + timedelta(days=7 * (weeks - 1))
    months = {(mon + timedelta(days=i)).month for i in range((last - mon).days + 1)}
    now = NOW()
    out = []
    for ev in list(CURATED) + list(extra):
        if not set(ev.get("months", [])) & months:
            continue
        k = ev["name"]
        hit = store.get(k)
        if hit and hit.get("year") == mon.year and date.fromisoformat(hit["end"]) >= mon - timedelta(days=1):
            s0, s1 = date.fromisoformat(hit["start"]), date.fromisoformat(hit["end"])
        else:
            if len(ns.fetch_gnews(_q(ev), "US", now - timedelta(days=14), now)) < 5:
                continue
            days = [mon - timedelta(days=1) + timedelta(days=i) for i in range((last - mon).days + 2)]
            s0, s1 = _peak_dates(_q(ev), days)
            if not s0:
                continue
            store[k] = {"start": s0.isoformat(), "end": s1.isoformat(), "year": mon.year, "found": now.date().isoformat()}
            print(f"[weekly] 행사 날짜 찾음: {k} {s0}~{s1}")
        if s1 < mon or s0 > fri:
            continue
        out.append(_ev_item(mon, s0, s1, ev["name"], ev.get("ko") or ev["name"], "주요 행사 목록", "",
                            8 if ev.get("star") else 4, q=_q(ev), multi=True))
    print(f"[weekly] 주요 행사 목록: 이번 주 {len(out)}")
    return out


def _ev_item(mon, s0, s1, title, ko, src, url, n, q="", place="", multi=False):
    day = max(s0, mon)
    tag = (" 개막" if s0 >= mon else " 진행 중") if s1 > s0 or multi else ""
    until = f"(~{s1.month}/{s1.day})" if s1 > s0 else ""
    return {"day": day.isoformat(), "title": title, "ko": ko, "suffix": tag + until, "place": place, "src": src, "url": url,
            "n": n, "ts": 0, "key": title, "tm": True, "q": q or f'"{title}"'}


def merge_events(primary, more):
    """같은 행사(이름 단어가 겹침)는 하나로: 앞쪽(primary) 이름·별표를 쓰고 날짜는 Techmeme 쪽을 우선"""
    tok = lambda t: set(re.findall(r"[a-z0-9]{3,}", t.lower())) - {"the", "and", "conference", "summit", "global", "event"}  # noqa: E731
    out = list(primary)
    for x in more:
        same = next((p for p in out if tok(p["title"]) and tok(x["title"]) and
                     (tok(p["title"]) <= tok(x["title"]) or tok(x["title"]) <= tok(p["title"]))), None)
        if same:
            if x.get("src") == "Techmeme" or (same.get("guess") and not x.get("manual")):
                same.update(day=x["day"], suffix=x["suffix"], url=x["url"], place=x.get("place", ""))
            continue
        out.append(x)
    return out


def speakers(events, days_back=21):
    """이번 주 행사마다 '<행사> keynote/CEO/연설' 기사 → 나오는 CEO·주요 기업을 붙임 (예: 젠슨 황·리사 수 연설 / 참가: 브로드컴·마벨)"""
    import news_scanner as ns
    now = NOW()
    cos = sorted(KO_CO, key=len, reverse=True)
    for e in events:
        arts = ns.fetch_gnews(f'({e["q"]}) (keynote OR CEO OR fireside OR speak OR speech OR showcase OR unveil)', "US",
                              now - timedelta(days=days_back), now)
        ceo, co = Counter(), Counter()
        for a in arts:
            h = a["headline"]
            for name, (c, ko) in EXECS.items():
                if name.lower() in h.lower():
                    ceo[ko] += 1
            for c in cos:
                if c.lower() in e["title"].lower():   # 행사 이름에 이미 있는 회사(애플 이벤트의 애플)는 빼고
                    continue
                if re.search(r"(?<![A-Za-z])" + re.escape(c) + r"(?![A-Za-z])", h):
                    co[KO_CO[c]] += 1
        e["ceo"] = [k for k, _ in ceo.most_common(3)]
        e["cos"] = [k for k, v in co.most_common(4) if v >= 2]
        bits = []
        if e["ceo"]:
            bits.append("·".join(e["ceo"]) + " 연설")
        rest = [c for c in e["cos"] if c not in (e.get("ko") or "") and c.lower() not in e["title"].lower() and not any(c == KO_CO.get(EXECS[n][0]) for n in EXECS if EXECS[n][1] in e["ceo"])]
        if rest:
            bits.append("참가: " + "·".join(rest[:3]))
        e["who"] = " / ".join(bits)
    return events


def exec_events(mon, fri):
    """주요 CEO 의 이번 주 연설·대담·청문회 (날짜가 나온 보도만)"""
    import news_scanner as ns
    now = NOW()
    start = datetime.combine(mon - timedelta(days=10), datetime.min.time(), tzinfo=ET)
    out = []
    for name, (c, ko) in EXECS.items():
        arts = ns.fetch_gnews(f'"{name}" (keynote OR "fireside chat" OR speak OR speech OR testify OR interview OR conference)',
                              "US", start, now)
        best = {}
        for a in arts:
            h = a["headline"]
            if name.split()[-1].lower() not in h.lower() or NOISE.search(h):
                continue
            if not re.search(r"keynote|fireside|to speak|will speak|speech|testif|to address|conference|summit|interview|appear", h, re.I):
                continue
            d = event_day(a, mon, fri)
            if d:
                best.setdefault(d, []).append(a)
        for d, L in best.items():
            L.sort(key=lambda a: -ns.TIER_RANK[ns.source_tier(a["source"], a.get("domain", ""))])
            out.append({"day": d.isoformat(), "title": L[0]["headline"], "src": L[0]["source"], "url": L[0]["url"],
                        "n": 8 if len({a["source"] for a in L}) >= 2 else 4, "ts": L[0]["published"], "key": c,
                        "prefix": f"{ko}({KO_CO.get(c, c)}) "})
    print(f"[weekly] CEO 연설 일정 {len(out)}")
    return out


def manual_events(url="https://mini-bblab.pages.dev/api/events"):
    """랩 /weekly 에서 직접 넣은 일정"""
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        return r.json().get("events", [])
    except Exception as ex:
        print(f"[weekly] 직접 넣은 일정: {ex}")
        return []


def manual_for_week(manual, mon, fri):
    """이번 주에 해당하는 직접 입력 일정 + '매년 반복' 일정은 작년 같은 요일(52주 뒤)로 옮겨서"""
    out = []
    for m in manual:
        try:
            s0 = date.fromisoformat(m["date"])
            s1 = date.fromisoformat(m.get("end") or m["date"])
        except Exception:
            continue
        guess = ""
        if m.get("recur") and s1 < mon - timedelta(days=200):
            while s1 < mon - timedelta(days=3):
                s0, s1 = s0 + timedelta(days=364), s1 + timedelta(days=364)
            guess = " (작년 일정 기준)"
        if s1 < mon or s0 > fri:
            continue
        it = _ev_item(mon, s0, s1, m.get("en") or m["text"], m["text"], "직접 입력", m.get("url", ""), 8 if m.get("star") else 4)
        it["suffix"] += guess
        it["guess"] = bool(guess)
        it["cat"] = m.get("cat") or "산업 및 기업"
        it["manual"] = True
        it["mid"] = m.get("id", "")
        out.append(it)
    return out


def recur_curated(manual):
    """'매년 반복' 직접 입력 일정 중 영문 이름이 있는 것 → 주요 행사 목록처럼 이름으로 날짜를 찾음"""
    out = []
    for m in manual:
        if m.get("recur") and m.get("en") and (m.get("cat") or "산업 및 기업") == "산업 및 기업":
            try:
                mo = date.fromisoformat(m["date"]).month
            except Exception:
                continue
            out.append({"name": m["en"], "ko": m["text"], "months": [mo], "star": bool(m.get("star"))})
    return out


def techmeme_events(mon, fri, extra_names=()):
    """Techmeme 기술 행사 캘린더: 이번 주(월~금)에 열리거나 진행 중인 행사. 광고(featured)·실적 항목은 제외"""
    import html as H
    try:
        r = requests.get("https://www.techmeme.com/events", headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        r.raise_for_status()
    except Exception as ex:
        print(f"[weekly] techmeme: {ex}")
        return []
    sat = fri + timedelta(days=1)
    words = [w.strip().lower() for w in IND_MAJOR + list(extra_names)]
    out = []
    for cls, href, when, name, place in re.findall(
            r'<div class="([^"]*)"><div class="rhov"><a href="([^"]+)"><div>([^<]+)</div><div>(.*?)</div><div>(.*?)</div></a>', r.text):
        if "featured" in cls:
            continue
        name = re.sub(r"\s+", " ", H.unescape(re.sub(r"<[^>]+>", "", name)).replace("HYBRID:", "")).strip()
        if not name or name.lower().startswith("earnings:"):
            continue
        m = re.match(r"([A-Z][a-z]{2}) (\d+)(?:-(?:([A-Z][a-z]{2}) )?(\d+))?$", when.strip())
        if not m or m.group(1).lower() not in MONTHS:
            continue
        def mk(mo, d):
            y = mon.year + (1 if MONTHS[mo.lower()] < mon.month - 6 else -1 if MONTHS[mo.lower()] > mon.month + 6 else 0)
            return date(y, MONTHS[mo.lower()], int(d))
        try:
            s0 = mk(m.group(1), m.group(2))
            s1 = mk(m.group(3) or m.group(1), m.group(4)) if m.group(4) else s0
        except ValueError:
            continue
        if s1 < mon or s0 >= sat:
            continue
        day = max(s0, mon)
        tag = (" 개막" if s0 >= mon else " 진행 중") if s1 > s0 else ""
        until = f"(~{s1.month}/{s1.day})" if s1 > s0 else ""
        key = next((w for w in words if re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", name.lower())), "")
        out.append({"day": day.isoformat(), "title": name, "suffix": tag + until, "place": H.unescape(place).strip(),
                    "src": "Techmeme", "url": "https://www.techmeme.com" + href, "n": 8 if key else 3,
                    "ts": 0, "key": key or name, "tm": True, "q": f'"{name}"'})
    print(f"[weekly] techmeme: 이번 주 행사 {len(out)}")
    return out


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
