"""
시장을 움직인 뉴스 — 나스닥·S&P500 움직임을 '구간(다리)'으로 나누고, 같은 시간 다른 자산의 움직임으로
원인 유형을 먼저 좁힌 뒤, 그 유형에 맞는 뉴스와 언론의 사후 설명을 짝짓는다.

1) 구간 찾기: 나스닥100 선물(NQ) 5분봉을 지그재그로 나눠(되돌림 0.35% 이상이면 방향 전환)
   크기 0.45% 이상인 하락·상승 구간 최대 5개. 구간 안에서 15분 낙폭·상승폭이 가장 큰 지점 = '방아쇠'
2) 지문(같은 시간 다른 자산): S&P(ES)·러셀(RTY) 선물, 반도체지수(SOX), 10년 국채 선물(ZN → 금리 bp 환산),
   달러지수, 유가(WTI), 금, VIX, 대형주 11개 → 원인 유형 점수
     · 반도체·AI: SOX 가 나스닥보다 1.3배 이상 크게, 같은 방향
     · 금리·물가: 금리 +3bp 이상 오르며 주식 하락(또는 반대)
     · 유가·지정학: 유가 ±1.2% 이상이 주식과 반대 방향, 금 동반
     · 위험회피: S&P·러셀도 비슷하게 빠지고 VIX 급등
     · 개별 대형주: 한 종목이 다른 대형주보다 훨씬 크게
3) 뉴스 짝짓기: 방아쇠 30분 전 ~ 10분 후 '처음 보도'된 이슈 중 원인 유형과 맞는 토픽·키워드·주도 종목을 담은 것만
   (유형과 무관한 기사는 시간이 맞아도 버림) + 이후 3시간 '주가가 ~때문에 하락' 기사가 원인으로 지목한 이슈 가산
4) 언론 설명: 구간 뒤 시장 반응 기사 중 원인 유형 키워드를 담은 헤드라인을 그대로 보여줌
· 같은 시간대·같은 유형일 뿐 인과관계를 확인한 것은 아니다 (화면에도 그렇게 표시)
"""
import math
import re
from concurrent.futures import ThreadPoolExecutor

import requests

CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=5m&range=2d&includePrePost=true"
HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
REV = 0.35          # 지그재그 방향 전환 되돌림(%)
MIN_LEG = 0.45      # 보여 줄 최소 구간 크기(%)
MAX_EVENTS = 5
ZN_DUR = 6.3        # 10년 국채 선물 듀레이션 근사 → 가격 1% ≈ 금리 16bp
CROSS = {"ES": "ES%3DF", "RTY": "RTY%3DF", "SOX": "SMH", "ZN": "ZN%3DF", "DXY": "DX-Y.NYB",
         "OIL": "CL%3DF", "GOLD": "GC%3DF", "VIX": "%5EVIX"}
MEGA = {"NVDA": "엔비디아", "AVGO": "브로드컴", "AMD": "AMD", "MU": "마이크론", "TSM": "TSMC", "AAPL": "애플",
        "MSFT": "마이크로소프트", "META": "메타", "AMZN": "아마존", "GOOGL": "알파벳", "TSLA": "테슬라",
        "ORCL": "오라클", "CRWV": "코어위브"}
MEGA_WORDS = {"NVDA": "nvidia|nvda|英伟达|엔비디아", "AVGO": "broadcom|avgo|博通|브로드컴", "AMD": r"\bamd\b|超威",
              "MU": "micron|美光|마이크론", "TSM": "tsmc|taiwan semi|台积电|tsm\\b", "AAPL": "apple|aapl|苹果|애플",
              "MSFT": "microsoft|msft|微软", "META": r"\bmeta\b|facebook|instagram", "AMZN": "amazon|amzn|aws|亚马逊",
              "GOOGL": "alphabet|google|gemini|谷歌", "TSLA": "tesla|tsla|musk|特斯拉",
              "ORCL": "oracle|orcl|甲骨文", "CRWV": "coreweave|crwv|neocloud"}
SEMIS = {"NVDA", "AVGO", "AMD", "MU", "TSM"}
AI_INFRA = {"ORCL", "CRWV"}
# 원인 유형 → 관련 토픽 · 핵심 키워드(core, 2점) · 맥락 키워드(ctx, 0.5점 — core 없이는 거의 안 쳐 줌)
DRIVERS = {
    "semis": {"name": "반도체·AI", "topics": {"semi_mega", "semi_other", "ai_hw", "ai_sw", "nvda"},
              "core": r"chip|semiconductor|semis\b|memory|hbm|dram|nand|nvidia|broadcom|micron|tsmc|\bamd\b|hynix|sandisk|asml|"
                      r"lam research|applied materials|kla\b|coreweave|neocloud|ai (?:spend|capex|bubble|trade|stocks|infrastructure|selloff)|"
                      r"data cent|dot-com|芯片|半导体|存储|算力",
              "ctx": r"\bai\b|china|export|huawei|cxmt|ymtc|smic|valuation|debt|bond"},
    "tech": {"name": "대형 기술주", "topics": {"aapl", "msft", "googl", "amzn", "meta", "nvda", "tesla", "ai_sw"},
             "core": r"big tech|megacap|magnificent|tech (?:stocks|selloff|shares)|nasdaq|apple|microsoft|alphabet|google|amazon|meta\b|"
                     r"tesla|nvidia|科技股|大型科技",
             "ctx": r"\bai\b|antitrust|regulat|earnings|guidance"},
    "rates": {"name": "금리·물가", "topics": {"fed", "us_macro"},
              "core": r"yield|treasur|inflation|\bcpi\b|\bppi\b|\bpce\b|\bfed\b|fomc|warsh|rate[- ](?:hike|cut)|payroll|jobs report|"
                      r"jobless|retail sales|\bgdp\b|auction|美债|收益率|通胀|美联储|加息|降息",
              "ctx": r"economy|dollar|data|bond"},
    "oil": {"name": "유가·지정학", "topics": {"mideast", "us_politics"},
            "core": r"\boil\b|crude|brent|\bwti\b|opec|hormuz|pipeline|refiner|tanker|energy price|原油|油价|霍尔木兹",
            "ctx": r"iran|israel|saudi|houthi|yemen|missile|strike|attack|drone|sanction|\bwar\b|伊朗|沙特|胡塞|袭击|导弹"},
    "riskoff": {"name": "위험회피(시장 전반)", "topics": {"us_politics", "fed", "us_macro", "mideast"},
                "core": r"sell-?off|risk-?off|\bvix\b|volatility|\brout\b|plunge|credit|default|tariff|shutdown|margin call|恐慌|抛售",
                "ctx": r"fear|panic|bank|liquidity|safe haven"},
}
TOPIC_WEIGHT = {"fed": 3, "us_macro": 3, "mideast": 3, "us_politics": 2, "earnings": 3, "ecb": 1, "boj": 1,
                "semi_mega": 2, "semi_other": 1, "ai_hw": 1, "ai_sw": 1, "nvda": 2}
VIA_WEIGHT = {"FinancialJuice": 2, "월스트리트견문(华尔街见闻)": 1}
# 시황 정리·정례 데이터 공지·홍보물은 원인 후보에서 제외
RECAP = re.compile(r"指数(?:跌|涨)|收(?:跌|涨)|盘初|盘中|尾盘|午盘|SOFR|担保隔夜|联邦基金利率报|"
                   r"stock market today|market wrap|midday|closing bell|to watch|what to know|explainer|live updates|"
                   r"\blaunch(?:es)?\b|pitchfest|webinar|conference|announces? (?:partnership|collaboration)|"
                   r"jeremy renner|wows|sums up|joked|price target|if you invest|should you buy|is it (?:a|time)|here'?s (?:why|how|what)|"
                   r"price prediction|could make|upside|\breview\b|\bvs\.?\b|which is better|best .{0,20}to buy|deal\b.{0,20}\$|"
                   r"on sale|lowest price|guide\b|how to\b|too late to buy|meet the|stocks? to (?:buy|watch)|motley", re.I)
# 시장 반응 기사: "주가/지수가 ~ 때문에(after/on/as …) 떨어졌다/올랐다" — 언론이 움직임의 원인으로 지목한 것
REACTION = re.compile(
    r"(?:stocks?|shares|nasdaq|s&p|dow|wall street|equities|futures|tech|chips?|chipmakers|semis|market|treasur\w*|yields?|oil|crude)s?\b.{0,60}?"
    r"\b(?:drop|fall|sink|slide|tumble|plunge|slump|retreat|sell[- ]?off|rall(?:y|ies)|rise|jump|surge|gain|climb|rebound|soar|slip|extend|deepen)\w*\b"
    r".{0,60}?\b(?:after|on|as|amid|following|over|due to|with)\b"
    r"|(?:下跌|大跌|跳水|走低|重挫|上涨|拉升|大涨|走高).{0,20}?(?:因|受|拖累|提振|影响)|(?:因|受|拖累|提振).{0,30}?(?:下跌|大跌|跳水|走低|重挫|上涨|拉升|大涨|走高)",
    re.I)
GENERIC = set("""stock stocks share shares market markets nasdaq s&p dow wall street equities future futures tech chip chips
chipmaker chipmakers semis drop drops fall falls sink sinks slide slides tumble plunge slump retreat rally rallies rise rises
jump jumps surge gain gains climb rebound soar after on as amid following over due to the a an of in for and or with by at from
report reports reported says said new over less more than than us u.s. ai other others point points index others
data center centre operator investor investors company companies firm firms deal deals billion million bln mln trillion
year week day today amid may could""".split())


def _chart(sym):
    r = requests.get(CHART.format(sym=sym), headers=HDR, timeout=15).json()["chart"]["result"][0]
    q = r["indicators"]["quote"][0]["close"]
    bars = [(t, c) for t, c in zip(r.get("timestamp") or [], q) if c]
    return bars, r.get("meta", {})


def _index_summary(sym, name):
    try:
        bars, meta = _chart(sym)
    except Exception as ex:
        print(f"[moves] {sym}: {ex}")
        return None
    px = meta.get("regularMarketPrice") or (bars[-1][1] if bars else None)
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    if not px or not prev:
        return None
    return {"name": name, "price": round(px, 2), "change_pct": round((px / prev - 1) * 100, 2),
            "time": meta.get("regularMarketTime")}


def _at(bars, t, tol=10 * 60):
    """t 시각에 가장 가까운(앞쪽 우선) 종가 — tol 안에 봉이 없으면 None"""
    best = None
    for bt, c in bars:
        if bt <= t + 60:
            if t - bt <= tol:
                best = c
        else:
            break
    return best


def _first_after(bars, t, within=3 * 3600):
    for bt, c in bars:
        if t <= bt <= t + within:
            return c
    return None


def _chg(bars, t0, t1):
    a, b = _at(bars, t0) or _first_after(bars, t0), _at(bars, t1)   # 시작에 봉이 없으면(장 시작 전) 첫 봉부터
    return (b / a - 1) * 100 if a and b else None


def _legs(nq, start_ts, end_ts):
    """지그재그: 고점·저점에서 REV% 이상 되돌리면 방향 전환 → 구간 목록 (마지막 진행 중 구간 포함)"""
    pts = [(t, c) for t, c in nq if start_ts <= t <= end_ts]
    if len(pts) < 4:
        return []
    hi = lo = pts[0]
    d, piv, ext, raw = 0, None, None, []
    for t, c in pts[1:]:
        if d == 0:
            if c > hi[1]:
                hi = (t, c)
            if c < lo[1]:
                lo = (t, c)
            if c >= lo[1] * (1 + REV / 100):
                d, piv, ext = 1, lo, (t, c)
            elif c <= hi[1] * (1 - REV / 100):
                d, piv, ext = -1, hi, (t, c)
            continue
        if d * (c - ext[1]) > 0:
            ext = (t, c)
        elif (d > 0 and c <= ext[1] * (1 - REV / 100)) or (d < 0 and c >= ext[1] * (1 + REV / 100)):
            raw.append((piv, ext))
            piv, ext, d = ext, (t, c), -d
    if d:
        raw.append((piv, ext))
        if pts[-1][0] > ext[0]:
            raw.append((ext, pts[-1]))      # 마지막 고점·저점 뒤 진행 중인 움직임
    out = []
    for (t0, c0), (t1, c1) in raw:
        ch = (c1 / c0 - 1) * 100
        if abs(ch) < MIN_LEG or t1 <= t0:
            continue
        sg = 1 if ch > 0 else -1
        seg = [(t, c) for t, c in pts if t0 <= t <= t1]
        trig, best = (t0, t1), None
        for k in range(3, len(seg)):
            x = (seg[k][1] / seg[k - 3][1] - 1) * 100
            if best is None or x * sg > best * sg:
                best, trig = x, (seg[k - 3][0], seg[k][0])
        out.append({"start": t0, "end": t1, "nq_pct": round(ch, 2), "trig": list(trig), "trig_pct": round(best or ch, 2)})
    out.sort(key=lambda e: -abs(e["nq_pct"]))
    return out[:MAX_EVENTS]


def _fingerprint(ev, X, M):
    """같은 시간 다른 자산 움직임 → 지문 + 원인 유형 점수"""
    t0, t1, nq = ev["start"], ev["end"], ev["nq_pct"]
    f = {k: _chg(X.get(k, []), t0, t1) for k in CROSS}
    zn = f.pop("ZN")
    f["UST10Y_bp"] = None if zn is None else -zn / ZN_DUR * 100   # 선물 가격 하락 = 금리 상승
    for k in f:
        if f[k] is not None:
            f[k] = round(f[k], 1 if k == "UST10Y_bp" else 2)
    mega = {s: _chg(M.get(s, []), t0, t1) for s in MEGA}
    mega = {s: round(v, 2) for s, v in mega.items() if v is not None}
    sgn = 1 if nq > 0 else -1
    sc, why = {}, {}
    sox = f.get("SOX")
    semi = [mega[s] for s in SEMIS | AI_INFRA if s in mega]
    semi_avg = sum(semi) / len(semi) if semi else None
    lead = sox if sox is not None else semi_avg
    es0 = f.get("ES")
    base = abs(es0) if es0 is not None and abs(es0) > 0.05 else abs(nq) / 1.5
    if lead is not None and lead * sgn > 0 and abs(lead) >= 1.0 and abs(lead) >= 1.5 * base:
        sc["semis"] = min(3, abs(lead) / base)
        why["semis"] = f"{'반도체 ETF(SMH)' if sox is not None else '반도체·AI 대형주'} {lead:+.2f}% (S&P의 {abs(lead)/base:.1f}배)"
    if es0 is not None and (nq - es0) * sgn >= 0.5 and "semis" not in sc:
        sc["tech"] = min(3, abs(nq - es0) / 0.5)
        why["tech"] = f"나스닥이 S&P보다 {abs(nq - es0):.2f}%p 더 {'하락' if sgn < 0 else '상승'}"
    y = f.get("UST10Y_bp")
    if y is not None and abs(y) >= 3 and y * sgn < 0:
        sc["rates"] = min(3, abs(y) / 3)
        why["rates"] = f"10년 금리 {y:+.1f}bp (추정)"
    o, g = f.get("OIL"), f.get("GOLD")
    if o is not None and abs(o) >= 1.2 and o * sgn < 0:
        sc["oil"] = min(3, abs(o) / 1.2) + (0.5 if g is not None and g * o > 0 else 0)
        why["oil"] = f"유가 {o:+.2f}%" + (f" · 금 {g:+.2f}%" if g is not None else "")
    es, rty, vix = f.get("ES"), f.get("RTY"), f.get("VIX")
    if es is not None and es * sgn > 0 and abs(es) >= 0.6 * abs(nq) and (vix is None or vix * sgn < 0):
        s_ = abs(es) / abs(nq) + (0.5 if rty is not None and abs(rty) >= abs(es) else 0) + (min(1, abs(vix) / 10) if vix else 0)
        if sgn < 0:
            sc["riskoff"] = s_
            why["riskoff"] = f"S&P {es:+.2f}%" + (f" · 러셀 {rty:+.2f}%" if rty is not None else "") + (f" · VIX {vix:+.1f}%" if vix is not None else "")
    leaders = sorted(((s, v) for s, v in mega.items() if v * sgn > 0), key=lambda x: -abs(x[1]))[:3]
    single = None
    if len(mega) >= 5 and leaders:
        rest = sorted(abs(v) for s, v in mega.items() if s != leaders[0][0])
        med = rest[len(rest) // 2] if rest else 0
        if abs(leaders[0][1]) >= max(1.0, 3 * med):
            single = leaders[0][0]
            sc["single"] = min(3, abs(leaders[0][1]) / max(0.3, med))
            why["single"] = f"{MEGA[single]} {leaders[0][1]:+.2f}% (다른 대형주 중간값 {med:.2f}%)"
    drivers = sorted(sc, key=lambda k: -sc[k])
    return f, mega, [{"key": k, "name": (DRIVERS.get(k) or {"name": f"개별 종목({MEGA.get(single, '')})"})["name"],
                      "why": why[k], "score": round(sc[k], 2)} for k in drivers], leaders, single


def _toks(text):
    out = set()
    for w in re.findall(r"[a-z0-9$]+", text.lower()):
        w = w[:-1] if len(w) > 3 and w.endswith("s") else w
        if len(w) > 2 and w not in GENERIC:
            out.add(w)
    return out


def _idf(topics):
    df, n = {}, 0
    for t in topics:
        for s in t["stories"]:
            n += 1
            for w in _toks(s["headline"]):
                df[w] = df.get(w, 0) + 1
    return {w: math.log((n + 1) / (c + 1)) for w, c in df.items()}, math.log(n + 1)


def _reactions(ev, topics):
    """구간 시작 ~ 끝 3시간 후 '시장 반응' 헤드라인 (언론의 사후 설명)"""
    lo, hi = ev["start"] - 30 * 60, ev["end"] + 3 * 3600
    out, seen = [], set()
    for t in topics:
        for s in t["stories"]:
            for x in s["sources"]:
                h = x["headline"]
                key = re.sub(r"[^a-z가-힣一-龥]", "", h.lower())[:32]   # 같은 기사 재전송(제목 꼬리만 다름) 하나로
                if lo <= x.get("published", s["first_seen"]) <= hi and REACTION.search(h) and key not in seen:
                    seen.add(key)
                    out.append({"headline": h, "url": x.get("url") or s["url"], "source": x.get("name", ""),
                                "time": x.get("published", s["first_seen"]), "toks": _toks(h)})
    return out


def _cited(tk, reactions, idf):
    w, dflt = idf
    return sum(1 for r in reactions if len(tk & r["toks"]) >= 2 and sum(w.get(x, dflt) for x in tk & r["toks"]) >= 7)


def _relevance(t, s, drivers, single, leaders):
    """기사가 원인 유형과 얼마나 맞는지 (0 = 무관)"""
    h = " ".join([s["headline"]] + [x["headline"] for x in s["sources"][:3]]).lower()
    rel, hit = 0.0, []
    for i, d in enumerate(drivers):
        if d["key"] == "single":
            if single and re.search(MEGA_WORDS[single], h, re.I):
                rel += 4; hit.append(MEGA[single])
            continue
        D = DRIVERS[d["key"]]
        wgt = 1.0 if i == 0 else 0.6
        core = len(set(m.group(0).lower() for m in re.finditer(D["core"], h, re.I)))
        ctx = len(set(m.group(0).lower() for m in re.finditer(D["ctx"], h, re.I)))
        if not core:
            continue   # 핵심 키워드가 없으면 토픽이 같아도 이 유형 근거로 안 씀
        rel += (2 * min(3, core) + 0.5 * min(2, ctx) + (1 if t["id"] in D["topics"] else 0)) * wgt
        hit.append(D["name"])
    for sym, _ in leaders:
        if re.search(MEGA_WORDS[sym], h, re.I):
            rel += 1.5; hit.append(MEGA[sym])
    return rel, hit


def _match_news(ev, topics, drivers, single, leaders, idf):
    a, b = ev["trig"]
    # 후보 시간: 방아쇠(가장 가파른 15분) 45분 전 ~ 10분 후, 또는 구간 시작 30분 전 ~ 15분 후
    win = [(a - 45 * 60, b + 10 * 60), (ev["start"] - 30 * 60, ev["start"] + 15 * 60)]
    tier_rank = {"official": 0, "tier1": 2, "tier2": 1, "tier3": 0}
    reactions = _reactions(ev, topics)
    cands = []
    for t in topics:
        for s in t["stories"]:
            if s.get("is_opinion") or not any(x <= s["first_seen"] <= y for x, y in win) or RECAP.search(s["headline"]) or REACTION.search(s["headline"]):
                continue
            rel, hit = _relevance(t, s, drivers or [{"key": k} for k in ("rates", "oil", "semis", "riskoff")], single, leaders)
            if rel <= 0:
                continue   # 원인 유형과 무관하면 시간이 맞아도 버림
            via = max((VIA_WEIGHT.get(x.get("via"), 0) for x in s["sources"]), default=0)
            tier = max((tier_rank.get(x.get("tier"), 0) for x in s["sources"]), default=0)
            support = _cited(_toks(s["headline"]), reactions, idf)
            # 방아쇠 직전(−30분~시작)에 처음 나온 기사일수록 가산
            near = 4 if a - 30 * 60 <= s["first_seen"] <= a + 5 * 60 else 1.5 if win[1][0] <= s["first_seen"] <= win[1][1] else 0
            score = 2 * rel + TOPIC_WEIGHT.get(t["id"], 0) + via + tier + near + (1 if s["verdict"] == "VERIFIED" else 0) + 3 * min(support, 3)
            if score < 6:
                continue
            cands.append((score, s["first_seen"], t, s, support, hit))
    cands.sort(key=lambda x: (-x[0], x[1]))
    out, seen = [], set()
    for score, _, t, s, support, hit in cands:
        if s["headline"] in seen:
            continue
        seen.add(s["headline"])
        out.append({"headline": s["headline"], "url": s["url"], "first_seen": s["first_seen"], "topic": t["name"],
                    "source": s["sources"][0]["name"], "cited": support, "match": sorted(set(hit))[:3], "score": round(score, 1)})
        if len(out) == 3:
            break
    # 언론 설명: 반응 기사 중 원인 유형 키워드를 담은 것 (가장 이른 것부터 2개)
    # 반응 기사마다 원인 유형 핵심 키워드 일치 정도 → 많이 맞는 것, 같은 점수면 이른 것 순으로 최대 3개
    def fit(r):
        sc_ = 0.0
        for i, d in enumerate(drivers):
            D = DRIVERS.get(d["key"])
            if D:
                sc_ += (1.0 if i == 0 else 0.6) * len(set(m.group(0).lower() for m in re.finditer(D["core"], r["headline"], re.I)))
            elif single:
                sc_ += 1.0 if re.search(MEGA_WORDS[single], r["headline"], re.I) else 0
        return sc_
    exp = sorted(((fit(r), r) for r in reactions), key=lambda x: (-x[0], x[1]["time"]))
    exp = [r for f_, r in exp if f_ > 0 or not drivers][:3]
    return out, [{k: r[k] for k in ("headline", "url", "source", "time")} for r in exp]


def build_moves(briefing, start_ts, end_ts):
    syms = {"NQ": "NQ%3DF", **CROSS, **{s: s for s in MEGA}}
    with ThreadPoolExecutor(8) as ex:
        fut = {k: ex.submit(_chart, v) for k, v in syms.items()}
    bars = {}
    for k, f in fut.items():
        try:
            bars[k] = f.result()[0]
        except Exception as e:
            print(f"[moves] {k}: {e}")
    if "NQ" not in bars:
        return None
    nq = bars["NQ"]
    topics = briefing.get("topics", [])
    idf = _idf(topics)
    events = _legs(nq, start_ts, end_ts)
    X = {k: bars.get(k, []) for k in CROSS}
    M = {s: bars.get(s, []) for s in MEGA}
    for ev in events:
        f, mega, drivers, leaders, single = _fingerprint(ev, X, M)
        ev["es_pct"] = f.get("ES")
        ev["cross"] = f
        ev["leaders"] = [{"sym": s, "name": MEGA[s], "pct": v} for s, v in leaders]
        ev["drivers"] = drivers
        ev["news"], ev["explain"] = _match_news(ev, topics, drivers, single, leaders, idf)
    events.sort(key=lambda e: -e["end"])
    # 창 전체 요약 (오늘 무엇이 시장을 끌었나)
    whole = {"start": max(start_ts, nq[0][0]), "end": min(end_ts, nq[-1][0])}
    pts = [c for t, c in nq if start_ts <= t <= end_ts]
    whole["nq_pct"] = round((pts[-1] / pts[0] - 1) * 100, 2) if len(pts) > 1 else 0
    summary = None
    if abs(whole["nq_pct"]) >= MIN_LEG:
        wf, wm, wd, wl, _ = _fingerprint(whole, X, M)
        summary = {"nq_pct": whole["nq_pct"], "es_pct": wf.get("ES"), "cross": wf, "drivers": wd,
                   "leaders": [{"sym": s, "name": MEGA[s], "pct": v} for s, v in wl]}
    span = [c for t, c in bars.get("ES", []) if start_ts <= t <= end_ts]
    print(f"[moves] 구간 {len(events)}개 · 원인 유형 " + ", ".join(d["name"] for e in events for d in e["drivers"][:1]))
    return {
        "indices": [x for x in (_index_summary("%5EGSPC", "S&P 500"), _index_summary("%5EIXIC", "나스닥")) if x],
        "futures_window": {"nq_pct": whole["nq_pct"], "es_pct": round((span[-1] / span[0] - 1) * 100, 2) if len(span) > 1 else None},
        "summary": summary,
        "events": events,
    }
