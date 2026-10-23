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
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=5m&range=5d&includePrePost=true"
HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
REV = 0.35          # 지그재그 방향 전환 되돌림(%)
MIN_LEG = 0.45      # 보여 줄 최소 구간 크기(%)
MAX_EVENTS = 5
SEG_MIN = 0.15      # 시간대별 원인 줄에 넣을 최소 구간 크기(%) — 이보다 작은 건 '잔 움직임'
# 나스닥100 안 비중(근사, %) — 대형주가 지수를 얼마나 끌었나(기여도 = 주가 변화 × 비중). 지수에 없는 종목(TSMC·오라클·코어위브)은 빠짐
NDX_W = {"NVDA": 9.5, "MSFT": 8.0, "AAPL": 8.0, "GOOGL": 6.0, "AMZN": 5.5, "AVGO": 5.5, "META": 3.5, "TSLA": 3.5,
         "AMD": 1.6, "MU": 1.2}
ZN_DUR = 6.3        # 10년 국채 선물 듀레이션 근사 → 가격 1% ≈ 금리 16bp
CROSS = {"ES": "ES%3DF", "RTY": "RTY%3DF", "SOX": "SMH", "HY": "HYG", "ZN": "ZN%3DF", "DXY": "DX-Y.NYB",
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
    "credit": {"name": "신용 위험(AI 인프라 부채)", "topics": {"ai_hw", "ai_sw", "nvda"},
               "core": r"debt|bond|credit|borrow|financing|loan|spread|\bcds\b|default|leverage|junk|refinanc|\brout\b|downgrade|"
                       r"private credit|cratered|price it to fail|债务|信用|违约",
               "ctx": r"coreweave|oracle|neocloud|nebius|data cent|ai infrastructure|hyperscaler|stargate"},
    "riskoff": {"name": "위험회피(시장 전반)", "topics": {"us_politics", "fed", "us_macro", "mideast"},
                "core": r"sell-?off|risk-?off|\bvix\b|volatility|\brout\b|plunge|credit|default|tariff|shutdown|margin call|恐慌|抛售",
                "ctx": r"fear|panic|bank|liquidity|safe haven"},
}
# 뉴스 흐름: 같은 이야기를 다룬 기사 수를 30분 단위로 세어, 시장이 움직인 구간에 평소보다 몰렸는지 본다
#   subj(주어) 와 pred(서술)가 한 제목에 함께 있어야 그 이야기로 셈 (pred 가 None 이면 subj 만)
#   syms/asset = 그 이야기가 맞다면 같이 움직였어야 할 가격 (확인용)
NARRATIVES = [
    {"key": "ai_credit", "name": "AI 인프라 신용 위험", "driver": "credit", "syms": ["CRWV", "ORCL"],
     "subj": r"coreweave|oracle|neocloud|nebius|\biren\b|crusoe|lambda|ai infrastructure|data cent|hyperscaler|stargate|ai (?:financing|debt|boom)|甲骨文",
     "pred": r"debt|bond|credit|borrow|financ|loan|spread|\bcds\b|default|leverage|junk|refinanc|\brout\b|downgrade|cash out|cratered|price it to fail|债|信用|违约"},
    {"key": "ai_valuation", "name": "AI 버블·밸류에이션 우려", "driver": "semis", "syms": ["NVDA", "AVGO"],
     "subj": r"\bai\b|nvidia|chip|semiconductor|tech|nasdaq|magnificent",
     "pred": r"bubble|dot-?com|cisco|valuation|overvalued|froth|rotation out|casino|reality check|泡沫"},
    {"key": "chip_selloff", "name": "반도체 매도·연속 하락", "driver": "semis", "syms": ["NVDA", "AMD", "AVGO", "MU"],
     "subj": r"chip|semiconductor|semis\b|chipmaker|\bsox\b|芯片|半导体",
     "pred": r"drop|fall|slide|sink|sell-?off|losing streak|tumble|plunge|slump|\brout\b|retreat|weakness|下跌|大跌|连跌|重挫"},
    {"key": "memory", "name": "메모리 가격·중국 경쟁(CXMT)", "driver": "semis", "syms": ["MU"],
     "subj": r"memory|dram|nand|\bhbm\b|micron|hynix|sandisk|cxmt|ymtc|存储|内存", "pred": None},
    {"key": "oil", "name": "유가·중동 공급 위험", "driver": "oil", "asset": "OIL",
     "subj": r"\boil\b|crude|brent|\bwti\b|opec|hormuz|pipeline|refiner|tanker|原油|油价|霍尔木兹", "pred": None},
    {"key": "fed", "name": "연준·금리 경로", "driver": "rates", "asset": "UST10Y_bp",
     "subj": r"\bfed\b|fomc|warsh|rate[- ]hike|rate[- ]cut|yields?\b|treasur|美联储|加息|降息|美债", "pred": None},
    {"key": "inflation", "name": "물가 지표(CPI·PPI)", "driver": "rates", "asset": "UST10Y_bp",
     "subj": r"\bcpi\b|\bppi\b|\bpce\b|inflation|通胀", "pred": None},
    {"key": "trade", "name": "관세·수출 통제", "driver": "riskoff",
     "subj": r"tariff|trade war|export control|entity list|关税|出口管制", "pred": None},
]
BUCKET = 30 * 60

TOPIC_WEIGHT = {"fed": 3, "us_macro": 3, "mideast": 3, "us_politics": 2, "earnings": 3, "ecb": 1, "boj": 1,
                "semi_mega": 2, "semi_other": 1, "ai_hw": 1, "ai_sw": 1, "nvda": 2}
VIA_WEIGHT = {"FinancialJuice": 2, "월스트리트견문(华尔街见闻)": 1}
# 시황 정리·정례 데이터 공지·홍보물은 원인 후보에서 제외
RECAP = re.compile(r"指数(?:跌|涨)|收(?:跌|涨)|盘初|盘中|尾盘|午盘|SOFR|担保隔夜|联邦基金利率报|"
                   r"stock market today|market wrap|midday|closing bell|to watch|what to know|explainer|live updates|"
                   r"\blaunch(?:es)?\b|pitchfest|webinar|conference|announces? (?:partnership|collaboration)|"
                   r"jeremy renner|wows|sums up|joked|price target|if you invest|should you buy|is it (?:a|time)|here'?s (?:why|how|what)|"
                   r"price prediction|could make|upside|\breview\b|\bvs\.?\b|which is better|best .{0,20}to buy|deal\b.{0,20}\$|"
                   r"on sale|lowest price|guide\b|how to\b|too late to buy|meet the|stocks? to (?:buy|watch)|motley|leveraged notes?|structured notes?|autocall|prediction:|could turn|into a fortune|millionaire", re.I)
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


def session_window(now_ts):
    """화면 기준 창: 정규장 중이면 전일 종가(16:00 ET) 이후 → 지수 전일비와 같은 기준,
       장 마감 뒤(시간외·야간·프리마켓)면 마지막 종가 이후 → 선물 시세 변동.
       나스닥100 지수 5분봉(정규장만 있음)으로 정해 휴장일·서머타임이 저절로 맞음 → (시작, 'regular'|'after') 또는 None"""
    try:
        bars, _ = _chart("%5ENDX")
    except Exception as ex:
        print(f"[moves] session: {ex}")
        return None
    bars = [(t, c) for t, c in bars if t <= now_ts]
    day = lambda t: (t - 4 * 3600) // 86400      # ET 날짜 근사(정규장 9:30~16:00 은 UTC-4·-5 어느 쪽이든 같은 날)
    days = sorted({day(t) for t, _ in bars})
    if len(days) < 2:
        return None
    last = bars[-1][0]
    close_of = lambda d: max(t for t, _ in bars if day(t) == d) + 5 * 60   # 마지막 5분봉(15:55) 끝 = 16:00 종가
    if now_ts - last <= 20 * 60 and datetime.fromtimestamp(last, ET).time() < datetime.strptime("15:55", "%H:%M").time():
        return close_of(days[-2]), "regular"
    return close_of(days[-1]), "after"


def merge_archive(topics, path, keep_h=72):
    """구간 원인 짝짓기용 기사 기억: 이번 수집(14시간) + 지난 실행분을 합쳐 최근 keep_h 시간 유지
       (당일 창은 종가부터라 수집 창(14시간)보다 길 수 있음 — 앞선 구간에도 뉴스를 붙이려고, 주말 넘김 대비 72시간)"""
    import json
    import os
    import time
    old = []
    if path and os.path.exists(path):
        try:
            old = json.load(open(path, encoding="utf-8"))
        except Exception as ex:
            print(f"[moves] archive: {ex}")
    cut = time.time() - keep_h * 3600
    by = {}
    for t in old + [{"id": t["id"], "name": t.get("name", ""), "stories": [
            {k: s.get(k) for k in ("headline", "url", "first_seen", "is_opinion", "verdict")} |
            {"sources": [{k: x.get(k) for k in ("headline", "name", "url", "published", "tier", "via")} for x in s["sources"][:4]]}
            for s in t["stories"]]} for t in topics]:
        d = by.setdefault(t["id"], {"id": t["id"], "name": t["name"], "stories": {}})
        for s in t["stories"]:
            if (s.get("first_seen") or 0) >= cut:
                d["stories"][s["headline"]] = s           # 같은 제목은 최신 것으로
    out = [{**d, "stories": sorted(d["stories"].values(), key=lambda s: s["first_seen"])} for d in by.values()]
    if path:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f"[moves] 기사 기억 {sum(len(d['stories']) for d in out)}건 (지난 실행 {sum(len(d.get('stories', [])) for d in old)}건 포함)")
    return out


def _index_summary(sym, name):
    try:
        bars, meta = _chart(sym)
    except Exception as ex:
        print(f"[moves] {sym}: {ex}")
        return None
    px = meta.get("regularMarketPrice") or (bars[-1][1] if bars else None)
    prev = meta.get("previousClose") or meta.get("chartPreviousClose")   # chartPreviousClose 는 조회 범위(5일) 시작 전 종가라 전일비가 아님
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


def _legs(nq, start_ts, end_ts, min_leg=MIN_LEG, bounds=None, phase=None, rev=None):
    """지그재그: 고점·저점에서 REV% 이상 되돌리면 방향 전환 → 구간 목록, 시간순 (창 시작~첫 꼭짓점·마지막 진행 중 구간 포함)"""
    bounds, phase, rev = bounds or _bounds, phase or _phase, rev or REV
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
            if c >= lo[1] * (1 + rev / 100):
                d, piv, ext = 1, lo, (t, c)
            elif c <= hi[1] * (1 - rev / 100):
                d, piv, ext = -1, hi, (t, c)
            continue
        if d * (c - ext[1]) > 0:
            ext = (t, c)
        elif (d > 0 and c <= ext[1] * (1 - rev / 100)) or (d < 0 and c >= ext[1] * (1 + rev / 100)):
            raw.append((piv, ext))
            piv, ext, d = ext, (t, c), -d
    if d:
        raw.append((piv, ext))
        if pts[-1][0] > ext[0]:
            raw.append((ext, pts[-1]))      # 마지막 고점·저점 뒤 진행 중인 움직임
        if raw[0][0][0] > pts[0][0]:
            raw.insert(0, (pts[0], raw[0][0]))   # 창 시작 ~ 첫 꼭짓점
    # 장 구분 경계(정규장 개장·마감, 시간외 끝, 프리마켓 시작)에서 한 번 더 자름 — 장 마감 후 실적 반응이 야간 움직임과 섞이지 않게
    cuts = []
    for (t0, c0), (t1, c1) in raw:
        b = [t for t in bounds(t0, t1) if t0 < t < t1]
        knots = [(t0, c0)] + [(t, c) for t, c in pts if t in b] + [(t1, c1)]
        cuts += list(zip(knots, knots[1:]))
    out = []
    for (t0, c0), (t1, c1) in cuts:
        ch = (c1 / c0 - 1) * 100
        if abs(ch) < min_leg or t1 <= t0:
            continue
        sg = 1 if ch > 0 else -1
        seg = [(t, c) for t, c in pts if t0 <= t <= t1]
        trig, best = (t0, t1), None
        for k in range(3, len(seg)):
            x = (seg[k][1] / seg[k - 3][1] - 1) * 100
            if best is None or x * sg > best * sg:
                best, trig = x, (seg[k - 3][0], seg[k][0])
        out.append({"start": t0, "end": t1, "nq_pct": round(ch, 2), "trig": list(trig), "trig_pct": round(best or ch, 2),
                    "phase": phase(t0)})
    return out


ET = ZoneInfo("America/New_York")
# 미국 동부 시각 경계 → 장 구분 (시작 시각, 이름)
PHASES = [((4, 0), "프리마켓"), ((9, 30), "정규장"), ((16, 0), "시간외"), ((20, 0), "야간")]


def _bounds(t0, t1):
    """t0~t1 사이의 장 구분 경계 시각(유닉스초)"""
    out, d = [], datetime.fromtimestamp(t0, ET).date()
    while True:
        for (h, m), _ in PHASES:
            ts = int(datetime(d.year, d.month, d.day, h, m, tzinfo=ET).timestamp())
            if ts > t1:
                return out
            if d.weekday() < 5 and ts > t0:
                out.append(ts)
        d += timedelta(days=1)


def _phase(t):
    x = datetime.fromtimestamp(t + 60, ET)
    hm_ = (x.hour, x.minute)
    name = "야간"
    for st_, n in PHASES:
        if hm_ >= st_:
            name = n
    return name


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
    semi = [mega[s] for s in SEMIS if s in mega]
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
    # 신용 위험: AI 인프라(코어위브·오라클)가 반도체보다 훨씬 크게 같은 방향, 또는 하이일드 채권 ETF 하락
    infra = [mega[s] for s in AI_INFRA if s in mega]
    ia = sum(infra) / len(infra) if infra else None
    hy = f.get("HY")
    ref = abs(sox) if sox is not None else base
    cs = 0.0
    if ia is not None and ia * sgn > 0 and abs(ia) >= 2 and abs(ia) >= 1.8 * max(ref, 0.3):
        cs += min(2.5, abs(ia) / max(ref, 0.3) / 1.5)
    if hy is not None and sgn < 0 and hy <= -0.2:
        cs += min(1.5, abs(hy) / 0.2 * 0.5)
    if cs:
        sc["credit"] = round(cs, 2)
        why["credit"] = " · ".join(x for x in (
            (f"AI 인프라 {', '.join(f'{MEGA[s_]} {mega[s_]:+.1f}%' for s_ in AI_INFRA if s_ in mega)}" if ia is not None and abs(ia) >= 2 else ""),
            (f"하이일드 채권 ETF {hy:+.2f}%" if hy is not None and hy <= -0.2 else "")) if x)
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


def _articles(topics):
    """모든 출처 기사 (같은 제목 재전송은 하나로): [(시각, 제목 소문자, 제목, url, 매체)]"""
    out, seen = [], set()
    for t in topics:
        for s in t["stories"]:
            for x in s["sources"]:
                h = x.get("headline") or ""
                key = re.sub(r"[^a-z가-힣一-龥]", "", h.lower())[:40]
                if not key or key in seen:
                    continue
                seen.add(key)
                out.append((x.get("published", s["first_seen"]), h.lower(), h, x.get("url") or s["url"], x.get("name", "")))
    return out


def _narr_hits(arts, start_ts, end_ts):
    """이야기마다 해당 기사 목록 (창 안)"""
    N = {}
    for n in NARRATIVES:
        sj, pd = re.compile(n["subj"], re.I), (re.compile(n["pred"], re.I) if n["pred"] else None)
        N[n["key"]] = [a for a in arts if start_ts - 2 * 3600 <= a[0] <= end_ts and sj.search(a[1]) and (pd is None or pd.search(a[1]))]
    return N


def _narr_rank(ev, N, f, mega, start_ts, end_ts, nq, arts_t):
    """구간(시작 90분 전 ~ 끝)에 이야기 기사가 평소보다 몰렸는지 × 가격이 그 이야기대로 움직였는지
       · 몰림 = 구간 안 '전체 기사 중 이 이야기 비중' ÷ 구간 밖 비중 (미국 장중엔 기사 자체가 많아지는 것을 보정)
       · 급증 시작 = 구간 안에서 이 이야기 30분 기사 수가 평소 30분 평균의 2배(최소 2건)를 처음 넘은 때
         → 가장 가파른 15분보다 먼저면 '원인 쪽', 한참 뒤면 '해설 쪽'"""
    lo, hi = ev["start"] - 90 * 60, ev["end"]
    w0 = start_ts - 2 * 3600
    tot_in = sum(1 for t in arts_t if lo <= t <= hi) or 1
    tot_out = sum(1 for t in arts_t if w0 <= t <= end_ts and not lo <= t <= hi) or 1
    dout = max((end_ts - w0) - max(hi - lo, 3600), 3600)
    sgn = 1 if nq > 0 else -1
    out = []
    for n in NARRATIVES:
        hits = N.get(n["key"], [])
        k_in = sum(1 for a in hits if lo <= a[0] <= hi)
        if k_in < 3:
            continue
        k_out = len(hits) - k_in
        ratio = ((k_in + 0.5) / tot_in) / ((k_out + 1) / tot_out)
        per_b = (k_out + 1) / dout * BUCKET                     # 평소 30분당 기사 수
        # 급증한 30분들(평소보다 많고 직전 1시간보다도 늘어남) 중 가장 가파른 15분에 가장 가까운 '앞쪽' 것, 없으면 첫 뒤쪽 것
        bursts, b = [], lo
        while b <= hi:
            c_ = sum(1 for a in hits if b <= a[0] < b + BUCKET)
            prev = sum(1 for a in hits if b - 2 * BUCKET <= a[0] < b) / 2
            if c_ >= max(2, 2 * per_b, 1.5 * prev + 1):
                bursts.append(b)
            b += BUCKET // 2
        tg = ev["trig"][0]
        before = [x for x in bursts if x <= tg + 15 * 60]
        onset = before[-1] if before else (bursts[0] if bursts else None)
        # 가격 확인: 관련 종목이 나스닥보다 크게 같은 방향 / 유가는 반대 방향 / 금리는 주식과 반대
        conf, cw = 1.0, ""
        if n.get("syms"):
            mv = [mega[x] for x in n["syms"] if x in mega]
            if mv:
                a = sum(mv) / len(mv)
                conf = 1 + min(2, abs(a) / max(abs(nq), 0.3)) / 1.5 if a * sgn > 0 else 0.6
                cw = ", ".join(f"{MEGA[x]} {mega[x]:+.1f}%" for x in n["syms"] if x in mega)
        elif n.get("asset"):
            v = f.get(n["asset"])
            if v is not None:
                th = 3 if n["asset"] == "UST10Y_bp" else 1.2
                conf = 1 + min(2, abs(v) / th) / 1.5 if v * sgn < 0 and abs(v) >= th * 0.5 else 0.7
                cw = f"{'10년 금리' if n['asset'] == 'UST10Y_bp' else '유가'} {v:+.1f}{'bp' if n['asset'] == 'UST10Y_bp' else '%'}"
        score = math.log1p(k_in) * min(ratio, 5) ** 0.5 * conf
        if ratio < 1.2 and k_in < 8:
            continue
        top = sorted((a for a in hits if lo <= a[0] <= hi and not RECAP.search(a[2])), key=lambda a: a[0])
        lead = None if onset is None else round((onset - ev["trig"][0]) / 60)
        if lead is not None and lead <= 15:
            score *= 1.25                                        # 가격보다 먼저 몰린 이야기 가산
        out.append({"key": n["key"], "name": n["name"], "driver": n["driver"], "n": k_in, "ratio": round(ratio, 1),
                    "confirm": round(conf, 2), "price": cw, "score": round(score, 2), "onset": onset, "lead_min": lead,
                    "first": top[0][0] if top else None,
                    "heads": [{"headline": a[2], "url": a[3], "source": a[4], "time": a[0]} for a in top[:1] + top[-2:]][:3]})
    out.sort(key=lambda x: -x["score"])
    return out[:4]


def _narr_day(N, f, mega, nq, start_ts, end_ts, n_all):
    """12시간 전체의 이야기 순위: 기사 수(전체 기사 중 비중) × 가격 확인 — 하루 내내 이어진 이야기용"""
    sgn = 1 if nq > 0 else -1
    out = []
    for n in NARRATIVES:
        hits = [a for a in N.get(n["key"], []) if start_ts <= a[0] <= end_ts]
        if len(hits) < 4:
            continue
        conf, cw = 1.0, ""
        if n.get("syms"):
            mv = [mega[x] for x in n["syms"] if x in mega]
            if mv:
                a = sum(mv) / len(mv)
                conf = 1 + min(2, abs(a) / max(abs(nq), 0.3)) / 1.5 if a * sgn > 0 else 0.6
                cw = ", ".join(f"{MEGA[x]} {mega[x]:+.1f}%" for x in n["syms"] if x in mega)
        elif n.get("asset") and f.get(n["asset"]) is not None:
            v = f[n["asset"]]
            th = 3 if n["asset"] == "UST10Y_bp" else 1.2
            conf = 1 + min(2, abs(v) / th) / 1.5 if v * sgn < 0 and abs(v) >= th * 0.5 else 0.7
            cw = f"{'10년 금리' if n['asset'] == 'UST10Y_bp' else '유가'} {v:+.1f}{'bp' if n['asset'] == 'UST10Y_bp' else '%'}"
        top = sorted((a for a in hits if not RECAP.search(a[2])), key=lambda a: a[0])
        out.append({"key": n["key"], "name": n["name"], "driver": n["driver"], "n": len(hits),
                    "share": round(len(hits) / max(1, n_all) * 100, 1), "confirm": round(conf, 2), "price": cw,
                    "score": round(math.log1p(len(hits)) * conf, 2), "onset": None, "lead_min": None,
                    "heads": [{"headline": a[2], "url": a[3], "source": a[4], "time": a[0]} for a in (top[:1] + top[-1:])][:2]})
    out.sort(key=lambda x: -x["score"])
    return out[:4]


def _series(N, start_ts, end_ts):
    """화면 시간축용: 이야기별 30분 기사 수"""
    nb = int((end_ts - start_ts) // BUCKET) + 1
    out = {}
    for n in NARRATIVES:
        c = [0] * nb
        for a in N.get(n["key"], []):
            i = int((a[0] - start_ts) // BUCKET)
            if 0 <= i < nb:
                c[i] += 1
        out[n["key"]] = c
    return out


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


def _merge_drivers(drivers, narr):
    """가격 지문으로 찾은 원인 유형 + 뉴스 흐름(기사 급증 × 가격 확인) → 합친 순위"""
    by = {d["key"]: dict(d) for d in drivers}
    for n in narr:
        if n["confirm"] < 1 or n["score"] < 1.5 or n["ratio"] < 1.2 or n["driver"] not in DRIVERS:
            continue
        note = f"뉴스 흐름 '{n['name']}' 기사 {n['n']}건(비중 평소의 {n['ratio']:g}배)"
        d = by.get(n["driver"])
        if d:
            d["why"] += " · " + note
            d["score"] = round(d["score"] + 0.5 * n["score"], 2)
        else:
            by[n["driver"]] = {"key": n["driver"], "name": DRIVERS[n["driver"]]["name"],
                               "why": note + (f" · {n['price']}" if n["price"] else ""), "score": round(0.8 * n["score"], 2), "from_news": True}
    return sorted(by.values(), key=lambda d: -d["score"])


def _match_news(ev, topics, drivers, single, leaders, idf, narr=()):
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
            # 이 구간에 기사가 몰린 이야기(뉴스 흐름)에 속하면 가산
            hl = s["headline"].lower()
            for n in narr[:2]:
                N_ = next(x for x in NARRATIVES if x["key"] == n["key"])
                if re.search(N_["subj"], hl, re.I) and (not N_["pred"] or re.search(N_["pred"], hl, re.I)):
                    rel += 3; hit.append(n["name"])
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


# 원인 유형을 움직인 방향에 맞춰 부르는 이름 (하락 구간, 상승 구간)
CAUSE_NAME = {"semis": ("반도체·AI 매도", "반도체·AI 반등"), "tech": ("대형 기술주 약세", "대형 기술주 강세"),
              "rates": ("금리 상승 부담", "금리 하락"), "oil": ("유가 급등·지정학", "유가 하락"),
              "credit": ("AI 인프라 신용 위험", "신용 우려 완화"), "riskoff": ("위험회피", "위험선호 회복")}


def _cause(ev):
    """구간 한 줄 원인: 맨 앞 원인 유형(방향 반영) + 대표 뉴스(원인 후보 → 없으면 언론 설명)"""
    d = (ev.get("drivers") or [None])[0]
    up = ev["nq_pct"] > 0
    if d:
        if d["key"] in CAUSE_NAME:
            d = {**d, "name": CAUSE_NAME[d["key"]][up]}
        elif d["key"] == "single":
            d = {**d, "name": d["name"].replace("개별 종목(", "").rstrip(")") + (" 급등" if up else " 급락")}
    n = (ev.get("news") or [None])[0]
    x = (ev.get("explain") or [None])[0]
    head = None
    if n:
        head = {"headline": n["headline"], "url": n["url"], "source": n["source"], "time": n["first_seen"], "kind": "news"}
    elif x:
        head = {"headline": x["headline"], "url": x["url"], "source": x["source"], "time": x["time"], "kind": "explain"}
    return ((d["key"] if d else "none") + ("+" if up else "-")), (d["name"] if d else ("반등(원인 불분명)" if up else "하락(원인 불분명)")), \
        (d["why"] if d else ""), head


def _day(segs, nq, M, whole, start_ts, end_ts):
    """하루(창 전체) 방향은 무엇이 정했나
       · 시간대별: 모든 구간(SEG_MIN 이상)을 시간순으로, 구간마다 원인 유형 + 대표 뉴스
       · 원인별 기여: 구간 등락을 원인 유형별로 더함 (합 + 잔 움직임 = 창 전체 등락)
       · 방향 확정: 나스닥 선물이 창 시작 수준을 마지막으로 건너 다시 돌아오지 않은 때
       · 방향 계기: 창 전체 방향과 같은 쪽 구간 중 기여가 가장 큰 것
       · 대형주 기여: 주가 변화 × 나스닥100 비중(근사)"""
    pts = [(t, c) for t, c in nq if start_ts <= t <= end_ts]
    if len(pts) < 4:
        return None
    net = whole["nq_pct"]
    sgn = 1 if net > 0 else -1 if net < 0 else 0
    rows = []
    for e in sorted(segs, key=lambda e: e["start"]):
        key, name, why, head = _cause(e)
        # 이 구간에 지수를 가장 많이 끈 대형주 (주가 변화 × 나스닥100 비중, 구간 방향과 같은 쪽)
        pp = []
        for s_, w in NDX_W.items():
            v = _chg(M.get(s_, []), e["start"], e["end"])
            if v is not None and v * e["nq_pct"] > 0:
                pp.append((v * w / 100, s_, v))
        pp.sort(key=lambda x: -abs(x[0]))
        rows.append({"start": e["start"], "end": e["end"], "nq_pct": e["nq_pct"], "es_pct": e.get("es_pct"), "phase": e.get("phase"),
                     "cause_key": key, "cause": name, "why": why, "head": head, "detail": abs(e["nq_pct"]) >= MIN_LEG,
                     "movers": [{"name": MEGA[s_], "pct": round(v, 1), "pp": round(x, 2)} for x, s_, v in pp[:3] if abs(x) >= 0.02]})
    by = {}
    for r in rows:
        b = by.setdefault(r["cause_key"], {"key": r["cause_key"], "name": r["cause"], "pct": 0.0, "n": 0})
        b["pct"] += r["nq_pct"]
        b["n"] += 1
    # 구간 등락의 단순 합은 복리 때문에 창 전체와 조금 다름 → 남는 것은 '잔 움직임'
    rest = net - sum(b["pct"] for b in by.values())
    if abs(rest) >= 0.05:
        by["rest"] = {"key": "rest", "name": "잔 움직임", "pct": rest, "n": 0}
    attrib = sorted(({**b, "pct": round(b["pct"], 2)} for b in by.values() if abs(b["pct"]) >= 0.03), key=lambda b: -abs(b["pct"]))
    # 방향 확정: 시작 수준을 마지막으로 건넌 뒤의 첫 봉
    c0, settle = pts[0][1], None
    if sgn and abs(net) >= 0.2:
        last = None
        for t, c in pts:
            if (c - c0) * sgn <= 0:
                last = t
        nxt = [t for t, c in pts if last is None or t > last]
        settle = nxt[0] if nxt else None
    trig = None
    if sgn and abs(net) >= 0.2:
        same = [r for r in rows if r["nq_pct"] * sgn > 0]
        if same:
            t_ = max(same, key=lambda r: abs(r["nq_pct"]))
            # 같은 원인이 이어진 구간 묶음이면 함께 (예: 반도체 매도 두 번)
            grp = [r for r in same if r["cause_key"] == t_["cause_key"] and not t_["cause_key"].startswith("none")]
            gp = sum(r["nq_pct"] for r in grp) if grp else t_["nq_pct"]
            gross = sum(r["nq_pct"] for r in same)
            ev = next((e for e in segs if e["start"] == t_["start"]), {})
            trig = {"start": t_["start"], "end": t_["end"], "nq_pct": t_["nq_pct"], "cause_key": t_["cause_key"], "cause": t_["cause"],
                    "phase": t_["phase"], "movers": t_["movers"],
                    "why": t_["why"], "share": round(gp / gross * 100), "gross": round(gross, 2), "group_n": len(grp) or 1, "group_pct": round(gp, 2),
                    "news": (ev.get("news") or [])[:2], "explain": (ev.get("explain") or [])[:2],
                    "trig": ev.get("trig"), "trig_pct": ev.get("trig_pct")}
    contrib = []
    for s_, w in NDX_W.items():
        v = _chg(M.get(s_, []), whole["start"], whole["end"])
        if v is not None:
            contrib.append({"sym": s_, "name": MEGA[s_], "pct": round(v, 2), "pp": round(v * w / 100, 3)})
    contrib.sort(key=lambda x: -abs(x["pp"]))
    return {"start": pts[0][0], "end": pts[-1][0], "nq_pct": net, "settle": settle, "trigger": trig,
            "attrib": attrib, "segments": rows, "contrib": contrib[:5]}


def _issues(rows, k):
    """같은 원인 구간을 묶어 '이슈' 목록: 이슈 이름 · 맨 위 숫자 기준 몫(%p) · 대표 기사 · 언제(장 구분)"""
    by = {}
    for i, r in enumerate(rows, 1):
        b = by.setdefault(r["cause_key"], {"key": r["cause_key"], "name": r["cause"], "pct": 0.0, "nos": [], "phases": [], "top": r})
        b["pct"] += r["nq_pct"]
        b["nos"].append(i)
        if r.get("phase") and r["phase"] not in b["phases"]:
            b["phases"].append(r["phase"])
        if abs(r["nq_pct"]) > abs(b["top"]["nq_pct"]) or (not b["top"].get("head") and r.get("head")):
            b["top"] = r
    out = []
    for b in by.values():
        t = b.pop("top")
        if abs(b["pct"]) < 0.1:
            continue
        out.append({**b, "pct": round(b["pct"], 2), "pp": round(b["pct"] * k, 2), "head": t.get("head"), "why": t.get("why", ""),
                    "start": t["start"], "end": t["end"], "movers": t.get("movers", [])})
    out.sort(key=lambda b: -abs(b["pct"]))
    return out


def build_moves(briefing, start_ts, end_ts, topics=None, mode="window"):
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
    topics = topics if topics is not None else briefing.get("topics", [])
    idf = _idf(topics)
    arts = _articles(topics)
    arts_t = [a[0] for a in arts]
    NH = _narr_hits(arts, start_ts, end_ts)
    segs = _legs(nq, start_ts, end_ts, SEG_MIN)
    X = {k: bars.get(k, []) for k in CROSS}
    M = {s: bars.get(s, []) for s in MEGA}
    for ev in segs:
        f, mega, drivers, leaders, single = _fingerprint(ev, X, M)
        narr = _narr_rank(ev, NH, f, mega, start_ts, end_ts, ev["nq_pct"], arts_t)
        drivers = _merge_drivers(drivers, narr)
        ev["es_pct"] = f.get("ES")
        ev["cross"] = f
        ev["leaders"] = [{"sym": s, "name": MEGA[s], "pct": v} for s, v in leaders]
        ev["drivers"] = drivers
        ev["narratives"] = narr
        ev["news"], ev["explain"] = _match_news(ev, topics, drivers, single, leaders, idf, narr)
    events = sorted((e for e in segs if abs(e["nq_pct"]) >= MIN_LEG), key=lambda e: -abs(e["nq_pct"]))[:MAX_EVENTS]
    events.sort(key=lambda e: -e["end"])
    # 창 전체 요약 (오늘 무엇이 시장을 끌었나)
    whole = {"start": max(start_ts, nq[0][0]), "end": min(end_ts, nq[-1][0])}
    pts = [c for t, c in nq if start_ts <= t <= end_ts]
    whole["nq_pct"] = round((pts[-1] / pts[0] - 1) * 100, 2) if len(pts) > 1 else 0
    summary = None
    if abs(whole["nq_pct"]) >= MIN_LEG:
        wf, wm, wd, wl, _ = _fingerprint(whole, X, M)
        n_all = sum(1 for t in arts_t if start_ts <= t <= end_ts)
        dn = _narr_day(NH, wf, wm, whole["nq_pct"], start_ts, end_ts, n_all)
        summary = {"nq_pct": whole["nq_pct"], "es_pct": wf.get("ES"), "cross": wf, "drivers": wd,
                   "leaders": [{"sym": s, "name": MEGA[s], "pct": v} for s, v in wl], "narratives": dn}
    day = _day(segs, nq, M, whole, start_ts, end_ts)
    idx = [x for x in (_index_summary("%5EGSPC", "S&P 500"), _index_summary("%5ENDX", "나스닥100"),
                       _index_summary("%5EIXIC", "나스닥 종합")) if x]
    if day:
        es = [c for t, c in bars.get("ES", []) if start_ts <= t <= end_ts]
        day["mode"] = mode
        day["es_pct"] = round((es[-1] / es[0] - 1) * 100, 2) if len(es) > 1 else None
        ndx = next((x for x in idx if x["name"] == "나스닥100"), None)
        # 맨 위 숫자: 정규장 중엔 지수 전일비, 장 마감 뒤엔 선물 — 이슈별 몫(%p)도 그 숫자에 맞춰 환산
        ref = ndx["change_pct"] if mode == "regular" and ndx else day["nq_pct"]
        day["ref_pct"] = ref
        tot = sum(r["nq_pct"] for r in day["segments"])           # 구간 합에 맞춰 환산 → 이슈 몫을 더하면 맨 위 숫자와 거의 같음
        k = ref / tot if tot and ref * tot > 0 and 0.5 <= ref / tot <= 2 else 1
        day["issues"] = _issues(day["segments"], k)
    # 시간축: 나스닥 선물(15분) + 구간 + 이야기별 30분 기사 수 (기사가 4건 이상인 이야기, 많은 순 6개)
    ser = _series(NH, start_ts, end_ts)
    nb = len(next(iter(ser.values()))) if ser else 0
    tot = [0] * nb
    for t in arts_t:
        i = int((t - start_ts) // BUCKET)
        if 0 <= i < nb:
            tot[i] += 1
    rets = []
    for i in range(nb):
        a, b = _at(nq, start_ts + i * BUCKET), _at(nq, start_ts + (i + 1) * BUCKET)
        rets.append((b / a - 1) * 100 if a and b else None)

    def corr(x, y):
        pr = [(p, q) for p, q in zip(x, y) if p is not None and q is not None]
        if len(pr) < 8:
            return None
        mx, my = sum(p for p, _ in pr) / len(pr), sum(q for _, q in pr) / len(pr)
        sx = sum((p - mx) ** 2 for p, _ in pr) ** 0.5
        sy = sum((q - my) ** 2 for _, q in pr) ** 0.5
        return round(sum((p - mx) * (q - my) for p, q in pr) / (sx * sy), 2) if sx and sy else None
    tl_n = []
    for n in NARRATIVES:
        c = ser[n["key"]]
        if sum(c) < 4:
            continue
        share = [c[i] / tot[i] if tot[i] else None for i in range(nb)]          # 전체 기사 중 비중 (시간대별 기사량 보정)
        r0 = corr(share, rets)                                                  # 같은 30분
        r1 = corr(share[:-1], rets[1:])                                         # 기사 뒤 30분 시장
        tl_n.append({"key": n["key"], "name": n["name"], "c": c, "n": sum(c), "r_same": r0, "r_next": r1})
    tl_n = sorted(tl_n, key=lambda x: -x["n"])[:6]
    timeline = {"start": start_ts, "end": end_ts, "bucket": BUCKET,
                "nq": [[t, round(c, 2)] for t, c in nq if start_ts <= t <= end_ts and (t - start_ts) % 900 < 300],
                "legs": [[e["start"], e["end"], e["nq_pct"]] for e in segs], "narr": tl_n,
                "phases": [[a, b, _phase(a)] for a, b in zip([start_ts] + _bounds(start_ts, end_ts), _bounds(start_ts, end_ts) + [end_ts]) if b > a]}
    span = [c for t, c in bars.get("ES", []) if start_ts <= t <= end_ts]
    print(f"[moves] 구간 {len(segs)}개(자세히 {len(events)}) · 원인 " + ", ".join(f'{x["nq_pct"]:+.2f} {x["cause"]}' for x in (day or {}).get("segments", [])))
    return {
        "indices": idx,
        "window": {"start": start_ts, "end": end_ts},
        "futures_window": {"nq_pct": whole["nq_pct"], "es_pct": round((span[-1] / span[0] - 1) * 100, 2) if len(span) > 1 else None},
        "summary": summary,
        "day": day,
        "timeline": timeline,
        "events": events,
    }
