"""
미니 블벅터 — 미국 당일 테마 (미니 블벅터 KR 처럼) · 15분마다 briefing.yml 에서

  python scripts/us_themes.py --out public/briefings/us_themes.json --cache .cache/ko.json

  · 시세: 야후 파이낸스 시세(장중 약 15분 지연, 장 마감 뒤엔 그날 종가) — 테마 구성 종목 + 야후 상승·하락 상위 종목
  · 테마: 아래 THEMES (한국판 네이버 테마처럼 직접 정한 미국 테마) — 등락률 = 구성 종목 단순 평균, 대장주·약세주
  · 이슈 테마: 시가총액 3억 달러 이상 ±5% 급등락주를 Finviz 종목 뉴스로 '같은 뉴스로 움직인 종목'끼리 묶음 (KR issues.js 와 같은 방식)
  · 강세(약세) 이유: 상승·하락 상위 테마의 대장주 당일 뉴스 중 움직임을 설명하는 기사 (등락 사유 PDF와 같은 기준), 한국어 번역
"""
import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

ET, KST = ZoneInfo("America/New_York"), ZoneInfo("Asia/Seoul")
HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
       "Accept": "application/json"}

# (id, 테마 이름, 설명, 종목들)
THEMES = [
    ("ai_gpu", "AI 반도체", "AI 가속기·GPU·맞춤형 칩", "NVDA AMD AVGO MRVL ARM CBRS ALAB CRDO TSM"),
    ("memory", "메모리·HBM", "D램·낸드·HBM", "MU SNDK SKHY WDC STX"),
    ("semi_equip", "반도체 장비", "전공정·후공정 장비·EDA", "ASML AMAT LRCX KLAC TER ONTO CAMT SNPS CDNS"),
    ("analog", "아날로그·차량 반도체", "아날로그·전력·MCU", "TXN ADI NXPI ON STM MCHP SWKS QRVO MPWR POWI NVTS WOLF"),
    ("optical", "광통신", "광트랜시버·레이저·CPO", "LITE COHR AAOI FN CIEN POET IPGP AXTI MTSI"),
    ("dc_power", "데이터센터 전력·냉각", "전력기기·냉각·배전", "VRT ETN NVT GEV PWR MOD JCI HUBB CEG VST TLN"),
    ("networking", "AI 네트워킹·서버", "스위치·서버·스토리지", "ANET CSCO HPE DELL SMCI NTAP PSTG CLS JBL"),
    ("neocloud", "네오클라우드·AI 호스팅", "GPU 클라우드·AI 데이터센터 전환 채굴사", "CRWV NBIS IREN APLD CIFR WULF CORZ HUT GLXY"),
    ("ai_sw", "AI 소프트웨어", "AI 플랫폼·데이터", "PLTR SNOW DDOG MDB AI SOUN BBAI PATH ESTC"),
    ("cloud_saas", "클라우드·SaaS", "기업용 소프트웨어", "CRM NOW ADBE WDAY ORCL INTU HUBS TEAM ZM DOCU"),
    ("cyber", "사이버보안", "보안 소프트웨어", "CRWD PANW ZS FTNT NET S OKTA CYBR TENB RBRK"),
    ("quantum", "양자컴퓨팅", "양자 하드웨어·소프트웨어", "IONQ RGTI QBTS QUBT ARQQ LAES"),
    ("nuclear", "원자력·SMR", "원전·소형모듈원자로·핵연료", "OKLO SMR NNE CEG CCJ LEU BWXT LTBR UEC UUUU"),
    ("space", "우주항공", "발사체·위성·우주 인프라", "RKLB ASTS LUNR RDW PL SPCE IRDM SATS FLY SPCX"),
    ("drone_defense", "드론·방산", "드론·방산·국방 IT", "AVAV KTOS RCAT UMAC LMT NOC RTX GD LHX PLTR"),
    ("robotics", "로봇·휴머노이드", "산업·수술·휴머노이드 로봇", "TSLA ISRG SYM TER ROK SERV RR PDYN"),
    ("autonomous", "자율주행", "로보택시·자율주행 기술", "TSLA GOOGL MBLY AUR PONY WRD UBER LYFT"),
    ("ev", "전기차", "전기차 완성차", "TSLA RIVN LCID NIO XPEV LI GM F"),
    ("battery_ess", "배터리·ESS", "2차전지·에너지저장·리튬", "QS ENVX FLNC EOSE ALB SLDP STEM TSLA"),
    ("hydrogen", "수소·연료전지", "연료전지·수소", "BE PLUG FCEL BLDP"),
    ("solar", "태양광", "태양광 모듈·인버터", "FSLR ENPH SEDG RUN NXT ARRY CSIQ"),
    ("rare_earth", "희토류·핵심광물", "희토류·리튬·광물", "MP USAR TMC NB ALB LAC CRML"),
    ("gold_silver", "금·은 광산", "금광·은광", "NEM GOLD AEM KGC AG PAAS HL CDE"),
    ("copper", "구리·금속", "구리·철강·알루미늄", "FCX SCCO TECK AA CLF NUE STLD"),
    ("oil_gas", "석유·가스", "원유·가스 생산", "XOM CVX COP OXY EOG DVN FANG"),
    ("lng", "LNG·가스 인프라", "LNG 수출·파이프라인", "LNG TELL NEXT EQT KMI WMB"),
    ("btc", "비트코인 관련주", "비트코인 보유·거래소·채굴", "MSTR COIN HOOD MARA RIOT CLSK BITF HUT"),
    ("stablecoin", "스테이블코인·핀테크", "스테이블코인·결제·디지털 자산", "CRCL COIN HOOD SOFI PYPL XYZ AFRM BLSH FIGR GEMI"),
    ("glp1", "비만치료제", "GLP-1 비만·당뇨 치료제", "LLY NVO VKTX AMGN ALT TERN"),
    ("biotech", "바이오 대형주", "대형 바이오·제약", "LLY ABBV MRK PFE AMGN GILD VRTX REGN BIIB"),
    ("mega_internet", "빅테크 인터넷", "검색·SNS·이커머스", "GOOGL META AMZN NFLX APP RDDT PINS SNAP"),
    ("china_adr", "중국 ADR", "중국 빅테크", "BABA PDD JD BIDU NTES TCEHY BILI"),
    ("airlines_travel", "항공·여행", "항공·크루즈·호텔", "DAL UAL AAL LUV CCL RCL NCLH ABNB BKNG"),
    ("shipping", "해운·조선", "해운·탱커·LNG선", "ZIM MATX FRO STNG DHT TNK INSW"),
    ("banks", "대형 은행", "미국 대형 은행", "JPM BAC WFC C GS MS"),
    ("regional_banks", "지역 은행", "지역 은행", "KRE ZION KEY CFG RF HBAN FITB TFC"),
    ("homebuilders", "주택 건설", "주택 건설·건자재", "DHI LEN PHM TOL NVR BLDR"),
    ("retail", "소매·소비", "대형 소매·소비재", "WMT COST TGT HD LOW AMZN NKE LULU"),
    ("meme", "밈 주식", "개인 투자자 인기주", "GME AMC KOSS BB OPEN"),
    ("cannabis", "대마초", "대마초", "TLRY CGC CRON ACB SNDL"),
]
MIN_CAP_ISSUE = 3e8      # 이슈 테마 대상 최소 시가총액 (야후 상승·하락 상위 + 테마 종목 중)
MIN_CHG = 5.0
MAX_MOVERS = 50
SUFFIX = re.compile(r"\s+(?:Common Stock|Ordinary Shares|Class [A-C].*|American Depositary Shares.*|Inc\.?|Corp(?:oration)?\.?|"
                    r"Holdings?|Ltd\.?|plc|N\.V\.|S\.A\.|Co\.?|Group|Technologies|Technology|Incorporated|Company|"
                    r"\(The\)|Limited)\b.*$", re.I)
STOP = set("""a an the of to in on for and or with by at from as is are be was were its it this that after amid over into
up down out new says said report reports stock stocks shares share why what how today week year q1 q2 q3 q4 vs inc
corp co ltd its their his her than more most will may could would can about against than just also amid rises falls
jumps drops surges soars plunges gains climbs slides sinks tumbles rallies higher lower""".split())


class Yahoo:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": HDR["User-Agent"]})
        self.s.get("https://fc.yahoo.com", timeout=15)
        self.crumb = self.s.get("https://query2.finance.yahoo.com/v1/test/getcrumb", timeout=15).text.strip()

    @staticmethod
    def row(x):
        chg = x.get("regularMarketChangePercent")
        if chg is None or x.get("regularMarketPrice") is None:
            return None
        name = x.get("shortName") or x.get("longName") or x["symbol"]
        return {"s": x["symbol"], "n": SUFFIX.sub("", name).strip() or x["symbol"], "p": round(x["regularMarketPrice"], 2),
                "chg": round(chg, 2), "cap": float(x.get("marketCap") or 0), "vol": int(x.get("regularMarketVolume") or 0),
                "t": x.get("regularMarketTime")}

    def quotes(self, syms):
        out = {}
        syms = list(dict.fromkeys(syms))
        for i in range(0, len(syms), 150):
            r = self.s.get("https://query2.finance.yahoo.com/v7/finance/quote", timeout=30,
                           params={"symbols": ",".join(syms[i:i + 150]), "crumb": self.crumb})
            for x in r.json()["quoteResponse"]["result"]:
                q = self.row(x)
                if q:
                    out[q["s"]] = q
        return out

    def movers(self, scr):
        r = self.s.get("https://query2.finance.yahoo.com/v1/finance/screener/predefined/saved", timeout=30,
                       params={"scrIds": scr, "count": 100, "crumb": self.crumb})
        return [q for q in (self.row(x) for x in r.json()["finance"]["result"][0]["quotes"]) if q]


ANALYST_LIST = re.compile(r"analyst calls|top analyst|analyst (?:picks|moves)|upgrades? (?:and|&) downgrades?|"
                          r"stocks? (?:moving|movers)|midday|premarket|after[- ]hours movers|\d+ (?:stocks|companies)", re.I)


def tokens(t):
    return {w for w in re.findall(r"[a-z][a-z0-9'-]{2,}", t.lower()) if w not in STOP}


def sim(a, b):
    return len(a & b) / max(1, min(len(a), len(b)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="public/briefings/us_themes.json")
    ap.add_argument("--cache", default=".cache/ko.json")
    a = ap.parse_args()
    import news_scanner as ns
    from reasons_pdf import DN, UP, ROUNDUP, PAST, pick
    t0 = time.time()
    Y = Yahoo()
    Q = Y.quotes([x for _, _, _, syms in THEMES for x in syms.split()])
    mv_up, mv_dn = Y.movers("day_gainers"), Y.movers("day_losers")
    for x in mv_up + mv_dn:
        Q.setdefault(x["s"], x)
    now = datetime.now(ET)
    open_ = now.weekday() < 5 and (now.hour, now.minute) >= (9, 30) and now.hour < 16

    # ── 테마 등락 ──
    themes = []
    for tid, name, desc, syms in THEMES:
        st = [Q[s] for s in dict.fromkeys(syms.split()) if s in Q]
        if len(st) < 2:
            continue
        st.sort(key=lambda x: -x["chg"])
        avg = round(sum(x["chg"] for x in st) / len(st), 2)
        themes.append({"id": tid, "name": name, "desc": desc, "chg": avg, "n": len(st),
                       "rise": sum(x["chg"] > 0 for x in st), "fall": sum(x["chg"] < 0 for x in st),
                       "stocks": [{k: x[k] for k in ("s", "n", "p", "chg", "cap")} for x in st]})
    themes.sort(key=lambda t: -t["chg"])

    # ── 이슈 테마: 급등락주를 같은 뉴스로 묶기 ──
    def movers(up):
        c = [x for x in Q.values() if x["cap"] >= MIN_CAP_ISSUE and (x["chg"] >= MIN_CHG if up else x["chg"] <= -MIN_CHG)
             and not re.search(r"[\^/.\s=-]", x["s"])]
        return sorted(c, key=lambda x: -abs(x["chg"]))[:MAX_MOVERS]
    ups, dns = movers(True), movers(False)
    need = {x["s"] for x in ups + dns}
    top_t = [t for t in themes[:5] + themes[-5:]]
    for t in top_t:   # 테마 이유용: 상승 테마 대장주 3, 하락 테마 약세주 3
        for x in (t["stocks"][:3] if t["chg"] >= 0 else t["stocks"][-3:]):
            need.add(x["s"])
    # 기사 창: 마지막 거래일 장 마감(16:00 ET) 기준 30시간 전부터 (주말·장 전에도 그 거래일 기사)
    last = now.date() if now.weekday() < 5 and (now.hour, now.minute) >= (9, 30) else now.date() - timedelta(days=1)
    while last.weekday() >= 5:
        last -= timedelta(days=1)
    since = datetime(last.year, last.month, last.day, 16, 0, tzinfo=ET).timestamp() - 30 * 3600
    with ThreadPoolExecutor(6) as pool:
        news = dict(zip(need, pool.map(lambda s: [n for n in ns.fetch_finviz(s) if n["published"] >= since], need)))

    def cluster(ms, up):
        MOVE = UP if up else DN
        heads = {}
        for x in ms:
            hs = []
            for n in news.get(x["s"], [])[:8]:
                if ROUNDUP.search(n["headline"]) or PAST.search(n["headline"]) or ANALYST_LIST.search(n["headline"]):
                    continue
                hs.append(n | {"g": tokens(n["headline"]), "mv": bool(MOVE.search(n["headline"]))})
            heads[x["s"]] = hs[:6]
        par = {x["s"]: x["s"] for x in ms}

        def find(s):
            while par[s] != s:
                par[s] = par[par[s]]
                s = par[s]
            return s
        why = {x["s"]: {} for x in ms}
        for i, x in enumerate(ms):
            for y in ms[i + 1:]:
                hit = None
                for p in heads[x["s"]]:
                    for q in heads[y["s"]]:
                        if p["url"] == q["url"] or p["headline"] == q["headline"] or (p["mv"] and q["mv"] and sim(p["g"], q["g"]) >= 0.6):
                            hit = p if p["published"] >= q["published"] else q
                            break
                    if hit:
                        break
                if hit:
                    par[find(x["s"])] = find(y["s"])
                    why[x["s"]][hit["headline"]] = hit
                    why[y["s"]][hit["headline"]] = hit
        groups = {}
        for x in ms:
            groups.setdefault(find(x["s"]), []).append(x)
        out = []
        for g in groups.values():
            if len(g) < 2:
                continue
            arts = {}
            for x in g:
                arts.update(why[x["s"]])
            arts = sorted(arts.values(), key=lambda n: (n["mv"], sum(m["s"] in n["headline"] or m["n"].split()[0] in n["headline"] for m in g), n["published"]), reverse=True)
            avg = round(sum(m["chg"] for m in g) / len(g), 2)
            # 이름은 대표 기사 제목 (번역 뒤 한국어로 바뀜)
            out.append({"name": arts[0]["headline"] if arts else "공통 재료", "chg": avg, "score": round(abs(avg) * len(g) ** 0.5, 2),
                        "stocks": [{k: m[k] for k in ("s", "n", "p", "chg", "cap")} for m in sorted(g, key=lambda m: -abs(m["chg"]))],
                        "articles": [{"title": n["headline"], "src": n["source"], "url": n["url"], "ts": n["published"]} for n in arts[:3]]})
        return sorted(out, key=lambda i: -i["score"])
    issues = {"up": cluster(ups, True), "down": cluster(dns, False)}

    # ── 테마 이유 (상승·하락 상위 5) ──
    for t in top_t:
        cand = t["stocks"][:3] if t["chg"] >= 0 else t["stocks"][::-1][:3]
        best = None
        for x in cand:
            items = [n for n in news.get(x["s"], [])]
            p = pick(x["s"], x["n"], x["chg"], items)
            if p and (best is None or p[0][0] > best[0]):
                best = (p[0][0], x["s"], p[0][1])
        if best:
            n = best[2]
            t["why"] = {"stock": best[1], "title": n["headline"], "src": n["source"], "url": n["url"], "ts": n["published"]}

    # 번역
    try:
        from wscn_pdf import translate
        try:
            cache = json.load(open(a.cache, encoding="utf-8"))
        except Exception:
            cache = {}
        heads = [t["why"]["title"] for t in themes if t.get("why")] + [x["title"] for k in issues for i in issues[k] for x in i["articles"]]
        translate(heads, cache)
        for t in themes:
            if t.get("why"):
                t["why"]["ko"] = cache.get(t["why"]["title"])
        for k in issues:
            for i in issues[k]:
                for x in i["articles"]:
                    x["ko"] = cache.get(x["title"])
                if i["articles"] and i["articles"][0].get("ko"):
                    i["name"] = i["articles"][0]["ko"]
        os.makedirs(os.path.dirname(a.cache) or ".", exist_ok=True)
        json.dump(cache, open(a.cache, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception as ex:
        print(f"[us_themes] 번역: {ex}")

    qt = max((x.get("t") or 0) for x in Q.values())
    res = {"at": datetime.now(KST).isoformat(timespec="seconds"), "market_open": open_, "n_stocks": len(Q),
           "quote_time": datetime.fromtimestamp(qt, KST).isoformat(timespec="minutes") if qt else None,
           "themes": themes, "issues": issues,
           "gainers": [{k: x[k] for k in ("s", "n", "p", "chg", "cap")} for x in ups[:20]],
           "losers": [{k: x[k] for k in ("s", "n", "p", "chg", "cap")} for x in dns[:20]]}
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    json.dump(res, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f"[us_themes] 종목 {len(Q)} · 테마 {len(themes)} · 이슈 상승 {len(issues['up'])}/하락 {len(issues['down'])} · "
          f"뉴스 {len(need)}종목 · {time.time() - t0:.0f}초 · 상위 {[(t['name'], t['chg']) for t in themes[:3]]}")


if __name__ == "__main__":
    main()
