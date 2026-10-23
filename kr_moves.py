"""
미니 블벅터 KR — 장중 변동 원인 추적 (미국판 market_moves.py 의 한국판)

  python kr_moves.py --news public/briefings/kr_news.json --out public/briefings/kr_moves.json

  '오늘 코스피는 왜 올랐나·빠졌나' — 정규장(09:00~15:30) 코스피를 시간대별 구간으로 나누고, 구간마다 원인 유형과 뉴스를 짝짓는다.
  · 기준 지수: 코스피(Yahoo ^KS11, 5분봉, 약 15~20분 지연). 코스닥(^KQ11)은 같은 시간 비교용
  · 구간: ① 갭 = 전일 종가 → 시가 (미국장 영향이 대부분)  ② 정규장 지그재그 구간 (되돌림 0.30% 이상이면 방향 전환)
  · 지문(같은 시간 다른 자산): 대형주 기여(주가 변화 × 코스피 안 비중), 외국인·기관·개인 순매수(다음 금융, 억 원),
    원/달러, 일본·홍콩·중국 증시, 미국 선물·반도체지수·EWY(미국에 상장된 한국 ETF)
  · 원인 유형: 미국 반도체·증시 영향 / 반도체 대형주 / 외국인·기관 수급 / 환율 / 아시아 동반 / 미국 선물 / 개별 종목 / 코스닥 쏠림
  · 뉴스: 구간 직전 '처음 나온' 국내 기사 중 원인 유형과 맞는 것 + 구간 뒤 3시간의 '코스피 ~로 하락' 시황 기사(언론 설명)
  · 같은 시간대·같은 유형일 뿐 인과관계를 확인한 것은 아니다 (화면에도 그렇게 표시)
  · 출력 구조는 미국판 moves 와 비슷(day.issues·segments·attrib·contrib·timeline)
"""
import argparse
import json
import math
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import market_moves as mm  # noqa: E402  (구간 나누기·기사 기억 합치기 재사용)

KST = timezone(timedelta(hours=9))
HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
       "Accept": "application/json"}
YH = ["query1.finance.yahoo.com", "query2.finance.yahoo.com"]
KR_SITE = "https://mini-bbterminal-kr.pages.dev"
REV = 0.30          # 지그재그 방향 전환 되돌림(%)
SEG_MIN = 0.12      # 시간대별 줄에 넣을 최소 구간(%)
MIN_LEG = 0.35      # '자세히' 에 넣을 큰 구간(%)
GAP_MIN = 0.15      # 갭 구간으로 따로 보여 줄 최소 크기(%)
MAX_EVENTS = 5
TOP_SHARE = 0.92    # 시총 상위 200종목이 코스피 전체 시총에서 차지하는 비중(근사) — 지수 기여도 계산용
OPEN_H, CLOSE_H = (9, 0), (15, 30)

# 같은 시간 비교 자산 (야후)
ASSETS = {"KQ": "^KQ11", "FX": "KRW=X", "NQF": "NQ=F", "SOX": "^SOX", "IXIC": "^IXIC", "EWY": "EWY",
          "N225": "^N225", "HSI": "^HSI", "SSEC": "000001.SS"}
# 지도(api/map)를 못 받을 때 쓸 코스피 대형주 (코드, 이름, 대략 비중 %)
FALLBACK_STOCKS = [("005930", "삼성전자", 27.0), ("000660", "SK하이닉스", 20.0), ("402340", "SK스퀘어", 2.2), ("373220", "LG에너지솔루션", 2.0),
                   ("009150", "삼성전기", 1.8), ("207940", "삼성바이오로직스", 1.5), ("005380", "현대차", 1.3), ("105560", "KB금융", 1.2),
                   ("032830", "삼성생명", 1.1), ("028260", "삼성물산", 1.0), ("068270", "셀트리온", 1.0), ("000270", "기아", 0.9)]
SEMIS = {"005930", "000660"}

# 원인 유형 → (하락 때 이름, 상승 때 이름)
NAMES = {"us_semis": ("미국 반도체 급락 여파", "미국 반도체 급등 영향"), "us_tech": ("미국 증시 약세 영향", "미국 증시 강세 영향"),
         "semis": ("반도체 대형주 약세", "반도체 대형주 강세"), "foreign": ("외국인 매도", "외국인 매수"),
         "inst": ("기관 매도", "기관 매수"), "fx": ("환율 상승(원화 약세)", "환율 하락(원화 강세)"),
         "asia": ("아시아 증시 동반 하락", "아시아 증시 동반 상승"), "us_fut": ("미국 선물 약세", "미국 선물 강세"),
         "smallcap": ("코스닥·중소형주 약세", "코스닥·중소형주 강세")}
# 원인 유형 → 뉴스 키워드 (core: 2점, ctx: 0.5점 — core 없이는 거의 안 쳐 줌)
KW = {
    "us_semis": {"core": r"엔비디아|마이크론|필라델피아\s*반도체|sox|반도체주|ai\s*(?:버블|거품)|램리서치|브로드컴|amd|tsmc|hbm",
                 "ctx": r"뉴욕|나스닥|미국|공급|실적|가이던스"},
    "us_tech": {"core": r"뉴욕\s*증시|나스닥|s&p|다우|월가|빅테크|미국\s*증시|메타|테슬라|애플|마이크로소프트|아마존|알파벳",
                "ctx": r"ai|실적|금리|연준"},
    "semis": {"core": r"삼성전자|sk\s*하이닉스|하이닉스|반도체|hbm|d램|디램|메모리|파운드리|낸드",
              "ctx": r"엔비디아|ai|공급|가격|수출|실적"},
    "foreign": {"core": r"외국인|순매도|순매수|매도세|매수세|프로그램\s*매매|수급",
                "ctx": r"기관|개인|코스피|코스닥"},
    "inst": {"core": r"기관|연기금|투신|금융투자|프로그램\s*매매|순매도|순매수", "ctx": r"외국인|개인|코스피"},
    "fx": {"core": r"환율|원·달러|원/달러|달러|원화|외환|1,[3-4]\d\d원", "ctx": r"외국인|금리|연준"},
    "asia": {"core": r"닛케이|일본\s*증시|상하이|항셍|홍콩|중국\s*증시|아시아\s*증시|중화권", "ctx": r"중국|일본|엔화|위안"},
    "us_fut": {"core": r"선물|야간", "ctx": r"나스닥|s&p|뉴욕"},
    "smallcap": {"core": r"코스닥|중소형|2차전지|바이오|개인", "ctx": r"외국인|기관"},
}
TOPIC_WEIGHT = {"market": 3, "macro": 3, "policy": 2, "semi": 2, "robot_ai": 1, "battery": 1, "bank": 1, "securities": 1}
# 시황 정리·종목 홍보·정례 공지는 원인 후보에서 제외
RECAP = re.compile(r"특징주|오전장|오후장|장중\s*시황|마감\s*시황|투자자\s*동향|급등\s*테마|상한가|하한가|공시|목표가|리포트|"
                   r"증권사\s*(?:추천|리포트)|종목\s*추천|체크\s*포인트|오늘의\s*증시|증시\s*일정|이시각\s*증시|"
                   r"모닝\s*브리핑|굿모닝|출근길|마감\s*후|라이브|운세|이벤트|광고|보도자료|행사|수급포착|연속\s*순매수행진|연속\s*순매도행진", re.I)
# 시장 반응 기사: '코스피(증시)가 ~하며 하락/상승' — 언론이 움직임의 원인으로 지목한 것
IDX = re.compile(r"코스피|코스닥|증시|지수|kospi|kosdaq")
MOVE = re.compile(r"급락|하락|약세|폭락|내림세|출렁|붕괴|급등|상승|강세|폭등|반등|오름세|랠리|신고가|사이드카|서킷브레이커|매도세|투매")
GENERIC = set("""코스피 코스닥 증시 지수 하락 상승 마감 급락 급등 약세 강세 장중 외국인 기관 개인 순매도 순매수 시장 국내 한국 주가 종목
오늘 이날 전날 오전 오후 뉴스 기자 관련 전망 가능 이상 이하 대비 기준 포인트 전일 등락 거래 투자 투자자 영향 우려 여파""".split())
JOSA = "은는이가을를의에도로과와만"


def kd(t):
    return datetime.fromtimestamp(t, KST)


def hm(t):
    return kd(t).strftime("%H:%M")


# ── 시세 ────────────────────────────────────────────────────────────────────
def yget(sym, rng="5d"):
    err = ""
    for h in YH:
        try:
            r = requests.get(f"https://{h}/v8/finance/chart/{quote(sym)}?interval=5m&range={rng}&includePrePost=false", headers=HDR, timeout=20)
            if r.status_code != 200:
                err = f"{sym} {h[:6]} HTTP {r.status_code}"
                continue
            res = r.json()["chart"]["result"][0]
            q = res["indicators"]["quote"][0]
            ts = res.get("timestamp") or []
            return {"ts": ts, "close": q["close"], "open": q["open"], "meta": res.get("meta", {})}
        except Exception as e:
            err = f"{sym} {h[:6]} {str(e)[:60]}"
    raise RuntimeError(err)


def series(raw, inject_open=False):
    """[(끝 시각, 종가)] — 5분봉의 종가는 봉이 끝난 시각의 값. inject_open: 날짜마다 첫 봉의 시가를 그 봉 시작 시각에 넣음(장 시작 값)"""
    out, seen = [], set()
    for t, o, c in zip(raw["ts"], raw["open"], raw["close"]):
        if inject_open and o and kd(t).date() not in seen:
            seen.add(kd(t).date())
            out.append((t, o))
        if c:
            out.append((t + 300, c))
    return out


def val_at(s, t):
    """t 이전(포함) 마지막 값 — 없으면 None"""
    best = None
    for bt, c in s:
        if bt <= t + 1:
            best = c
        else:
            break
    return best


def chg(s, t0, t1):
    a, b = val_at(s, t0), val_at(s, t1)
    return (b / a - 1) * 100 if a and b else None


# ── 종목 비중 · 수급 ──────────────────────────────────────────────────────────
def load_stocks():
    """코스피 시총 상위 종목(코드·이름·지수 안 비중 0~1). 지도 API → 실패하면 고정 목록"""
    try:
        d = requests.get(f"{KR_SITE}/api/map?m=KOSPI", headers=HDR, timeout=20).json()
        st = [x for s in d["sectors"] for x in s["stocks"]]
        tot = sum(x["cap"] for x in st) / TOP_SHARE
        top = sorted(st, key=lambda x: -x["cap"])[:14]
        return [{"code": x["c"], "name": x["n"], "w": x["cap"] / tot} for x in top]
    except Exception as e:
        print(f"[kr_moves] map: {e} — 고정 목록 사용")
        return [{"code": c, "name": n, "w": w / 100} for c, n, w in FALLBACK_STOCKS]


def load_flow():
    """다음 금융 시간대별 누적 순매수(억 원) → {'KOSPI': [(ts, 개인, 외국인, 기관)], 'at': ...}"""
    try:
        d = requests.get(f"{KR_SITE}/api/flow", headers=HDR, timeout=20).json()
        k = d["KOSPI"]
        day = k["at"][:10]
        out = []
        for hh, ind, frn, inst in k["series"]:
            t = int(datetime.strptime(f"{day} {hh}", "%Y-%m-%d %H:%M").replace(tzinfo=KST).timestamp())
            out.append((t, ind or 0, frn or 0, inst or 0))
        return {"day": day, "s": out, "now": (k["ind"], k["frn"], k["inst"])}
    except Exception as e:
        print(f"[kr_moves] flow: {e}")
        return None


def flow_delta(F, t0, t1):
    """구간 안 순매수 변화 (개인, 외국인, 기관) — 장 시작 전 값은 0"""
    if not F:
        return None

    def at(t):
        v = (0, 0, 0)
        for r in F["s"]:
            if r[0] <= t + 60:
                v = r[1:]
            else:
                break
        return v
    a, b = at(t0), at(t1)
    return tuple(b[i] - a[i] for i in range(3))


# ── 구간 지문 → 원인 유형 ─────────────────────────────────────────────────────
def fingerprint(leg, K, X, S, stocks, F):
    """K: 코스피 시계열, X: 비교 자산 시계열, S: 종목 시계열 {code: s}, F: 수급"""
    t0, t1, kp = leg["start"], leg["end"], leg["pct"]
    gap = leg.get("gap")
    sgn = 1 if kp > 0 else -1
    f = {}
    for k, s in X.items():
        f[k] = chg(s, t0, t1)
    f = {k: (round(v, 2) if v is not None else None) for k, v in f.items()}
    # 종목별 변화 · 지수 기여(%p = 변화 × 비중)
    mega, contrib = {}, {}
    for st in stocks:
        v = chg(S.get(st["code"], []), t0, t1)
        if v is not None:
            mega[st["code"]] = round(v, 2)
            contrib[st["code"]] = v * st["w"]
    flow = flow_delta(F, t0, t1) if not gap else None
    mins = max(5, (t1 - t0) / 60)
    sc, why = {}, {}

    def put(key, score, text):
        sc[key] = round(score, 2)
        why[key] = text

    # (갭) 미국 반도체·증시 — 간밤 미국 정규장 + 선물
    sox, ixic, nqf, ewy = f.get("SOX"), f.get("IXIC"), f.get("NQF"), f.get("EWY")
    if gap:
        if sox is not None and sox * sgn > 0 and abs(sox) >= 1.0 and (ixic is None or abs(sox) >= 1.2 * abs(ixic)):
            put("us_semis", min(3, abs(sox) / 1.5), f"간밤 미국 반도체지수(SOX) {sox:+.2f}%" + (f" · 나스닥 {ixic:+.2f}%" if ixic is not None else "")
                + (f" · 미국 상장 한국 ETF(EWY) {ewy:+.2f}%" if ewy is not None else ""))
        elif ixic is not None and ixic * sgn > 0 and abs(ixic) >= 0.7:
            put("us_tech", min(3, abs(ixic) / 1.0), f"간밤 나스닥 {ixic:+.2f}%" + (f" · 선물 {nqf:+.2f}%" if nqf is not None else "")
                + (f" · EWY {ewy:+.2f}%" if ewy is not None else ""))
        elif ewy is not None and ewy * sgn > 0 and abs(ewy) >= 1.5:
            put("us_tech", min(3, abs(ewy) / 2.0), f"미국 상장 한국 ETF(EWY) {ewy:+.2f}%")
    else:
        if nqf is not None and nqf * sgn > 0 and abs(nqf) >= 0.6:
            put("us_fut", min(3, abs(nqf) / 0.8), f"미국 나스닥 선물 {nqf:+.2f}% (같은 시간)")
    # 반도체 대형주(삼성전자·SK하이닉스) 기여
    semi_pp = sum(contrib.get(c, 0) for c in SEMIS)
    if abs(kp) >= 0.05 and semi_pp * sgn > 0 and semi_pp / kp >= 0.45 and abs(semi_pp) >= 0.12:
        parts = [f"{st['name']} {mega[st['code']]:+.2f}%" for st in stocks if st["code"] in SEMIS and st["code"] in mega]
        put("semis", min(3, semi_pp / kp * 2), " · ".join(parts) + f" (지수 기여 {semi_pp:+.2f}%p)")
    # 외국인·기관 수급 (장중 구간)
    if flow:
        ind, frn, inst = flow
        for key, v, nm in (("foreign", frn, "외국인"), ("inst", inst, "기관")):
            if v * sgn > 0 and (abs(v) >= 2500 or (abs(v) >= 700 and abs(v) / mins >= 40)):   # 억 원: 크거나, 짧은 시간에 몰림
                put(key, min(3, abs(v) / 2000 + abs(v) / mins / 80), f"{nm} 순{'매수' if v > 0 else '매도'} {v:+,}억 (이 구간)" + (f" · 개인 {ind:+,}억" if abs(ind) >= 1000 else ""))
    # 환율: 원화 약세(환율↑)는 증시 하락 쪽
    fx = f.get("FX")
    if fx is not None and fx * sgn < 0 and abs(fx) >= (0.3 if gap else 0.2):
        put("fx", min(3, abs(fx) / 0.4), f"원/달러 {fx:+.2f}%")
    # 아시아 증시 동반 (장중)
    asia = [f[k] for k in ("N225", "HSI", "SSEC") if f.get(k) is not None]
    if not gap and len(asia) >= 2:
        a = sum(asia) / len(asia)
        if a * sgn > 0 and abs(a) >= 0.7 and abs(a) >= 0.7 * abs(kp):
            put("asia", min(3, abs(a) / 0.9), " · ".join(f"{n} {f[k]:+.2f}%" for k, n in (("N225", "닛케이"), ("HSI", "항셍"), ("SSEC", "상하이")) if f.get(k) is not None))
    # 코스닥 쏠림
    kq = f.get("KQ")
    if kq is not None and kq * sgn > 0 and abs(kq) >= 0.6 and abs(kq) >= 1.5 * abs(kp):
        put("smallcap", min(3, abs(kq) / abs(kp) / 1.5), f"코스닥 {kq:+.2f}% (코스피 {kp:+.2f}%)")
    # 개별 종목 한 곳이 크게
    leaders = sorted(((c, contrib[c]) for c in contrib if contrib[c] * sgn > 0), key=lambda x: -abs(x[1]))[:3]
    single = None
    if leaders and abs(kp) >= 0.05:
        c0, pp0 = leaders[0]
        nm = next(st["name"] for st in stocks if st["code"] == c0)
        if c0 not in SEMIS and pp0 / kp >= 0.45 and abs(mega[c0]) >= 1.5:
            single = nm
            put("single", min(3, pp0 / kp * 2), f"{nm} {mega[c0]:+.2f}% (지수 기여 {pp0:+.2f}%p)")
    drivers = []
    for k in sorted(sc, key=lambda k: -sc[k]):
        nm = f"{single} {'급등' if kp > 0 else '급락'}" if k == "single" else NAMES[k][kp > 0]
        drivers.append({"key": k, "name": nm, "why": why[k], "score": sc[k]})
    movers = [{"code": c, "name": next(st["name"] for st in stocks if st["code"] == c), "pct": mega[c], "pp": round(contrib[c], 2)}
              for c, _ in sorted(((c, contrib[c]) for c in contrib if contrib[c] * sgn > 0), key=lambda x: -abs(x[1]))[:3] if abs(contrib[c]) >= 0.03]
    return f, drivers, movers, flow


# ── 뉴스 짝짓기 ──────────────────────────────────────────────────────────────
def toks(text):
    out = set()
    for w in re.findall(r"[가-힣]{2,}|[A-Za-z]{3,}|\d+", text):
        w = w.lower()
        if len(w) > 2 and w[-1] in JOSA and re.match(r"[가-힣]", w):
            w = w[:-1]
        if len(w) >= 2 and w not in GENERIC:
            out.add(w)
    return out


def idf_of(topics):
    df, n = {}, 0
    for t in topics:
        for s in t["stories"]:
            n += 1
            for w in toks(s["headline"]):
                df[w] = df.get(w, 0) + 1
    return {w: math.log((n + 1) / (c + 1)) for w, c in df.items()}, math.log(n + 1)


def is_reaction(h):
    return bool(IDX.search(h.lower()) and MOVE.search(h))


def reactions(leg, topics):
    lo, hi = leg["start"] - 30 * 60, leg["end"] + 3 * 3600
    out, seen = [], set()
    for t in topics:
        for s in t["stories"]:
            for x in s.get("sources") or [s]:
                h = x.get("headline") or s["headline"]
                ts = x.get("published") or s["first_seen"]
                key = re.sub(r"[^가-힣a-z0-9]", "", h.lower())[:30]
                if lo <= ts <= hi and is_reaction(h) and not RECAP.search(h) and key not in seen:
                    seen.add(key)
                    out.append({"headline": h, "url": x.get("url") or s["url"], "source": x.get("name", ""), "time": ts, "toks": toks(h)})
    return out


def cited(tk, rs, idf):
    w, dflt = idf
    return sum(1 for r in rs if len(tk & r["toks"]) >= 2 and sum(w.get(x, dflt) for x in tk & r["toks"]) >= 7)


def relevance(t, s, drivers, leaders):
    h = " ".join([s["headline"]] + [x.get("headline", "") for x in (s.get("sources") or [])[:3]]).lower()
    rel, hit = 0.0, []
    for i, d in enumerate(drivers):
        K = KW.get(d["key"])
        if not K:
            if d["key"] == "single":
                nm = d["name"].split(" ")[0]
                if nm in h:
                    rel += 4
                    hit.append(nm)
            continue
        wgt = 1.0 if i == 0 else 0.6
        core = len({m.group(0) for m in re.finditer(K["core"], h, re.I)})
        ctx = len({m.group(0) for m in re.finditer(K["ctx"], h, re.I)})
        if not core:
            continue
        rel += (2 * min(3, core) + 0.5 * min(2, ctx)) * wgt
        hit.append(d["name"])
    for m in leaders:
        if m["name"] in h:
            rel += 1.5
            hit.append(m["name"])
    return rel, hit


def match_news(leg, topics, drivers, movers, idf):
    a, b = leg["trig"]
    wins = [(a - 45 * 60, b + 10 * 60), (leg["start"] - 30 * 60, leg["start"] + 15 * 60)]
    if leg.get("gap"):                                   # 갭: 전일 마감 후 ~ 시가 직후의 간밤 뉴스
        wins = [(leg["start"], leg["end"] + 10 * 60)]
    tier_rank = {"official": 0, "tier1": 2, "tier2": 1, "tier3": 0}
    rs = reactions(leg, topics)
    cands = []
    for t in topics:
        for s in t["stories"]:
            if s.get("is_opinion") or not any(x <= s["first_seen"] <= y for x, y in wins) or RECAP.search(s["headline"]) or is_reaction(s["headline"]):
                continue
            rel, hit = relevance(t, s, drivers or [{"key": k, "name": k} for k in ("semis", "foreign", "us_tech", "fx")], movers)
            if rel <= 0:
                continue
            tier = max((tier_rank.get(x.get("tier"), 0) for x in (s.get("sources") or [])), default=0)
            support = cited(toks(s["headline"]), rs, idf)
            near = 4 if a - 30 * 60 <= s["first_seen"] <= a + 5 * 60 else 1.5
            score = 2 * rel + TOPIC_WEIGHT.get(t["id"], 0) + tier + near + (1 if s.get("verdict") == "VERIFIED" else 0) + 3 * min(support, 3)
            if score < 6:
                continue
            cands.append((score, s["first_seen"], t, s, support, hit))
    cands.sort(key=lambda x: (-x[0], x[1]))
    news, seen = [], set()
    for score, _, t, s, support, hit in cands:
        if s["headline"] in seen:
            continue
        seen.add(s["headline"])
        src = (s.get("sources") or [{}])[0]
        news.append({"headline": s["headline"], "url": s["url"], "first_seen": s["first_seen"], "source": src.get("name", ""),
                     "cited": support, "match": sorted(set(hit))[:3], "score": round(score, 1)})
        if len(news) == 3:
            break

    def fit(r):
        sc_ = 0.0
        for i, d in enumerate(drivers):
            K = KW.get(d["key"])
            if K:
                sc_ += (1.0 if i == 0 else 0.6) * len({m.group(0) for m in re.finditer(K["core"], r["headline"].lower(), re.I)})
            elif d["key"] == "single" and d["name"].split(" ")[0] in r["headline"]:
                sc_ += 1.0
        return sc_
    ex = sorted(((fit(r), r) for r in rs), key=lambda x: (-x[0], x[1]["time"]))
    ex = [r for f_, r in ex if f_ > 0 or not drivers][:3]
    return news, [{k: r[k] for k in ("headline", "url", "source", "time")} for r in ex]


# ── 하루 요약 ────────────────────────────────────────────────────────────────
def phase_of(t):
    h = kd(t).hour + kd(t).minute / 60
    return "장 초반" if h < 10 else "오전" if h < 12 else "오후" if h < 14 else "장 마감 무렵"


def cause_of(ev):
    d = (ev.get("drivers") or [None])[0]
    n = (ev.get("news") or [None])[0]
    x = (ev.get("explain") or [None])[0]
    head = None
    if n:
        head = {"headline": n["headline"], "url": n["url"], "source": n["source"], "time": n["first_seen"], "kind": "news"}
    elif x:
        head = {"headline": x["headline"], "url": x["url"], "source": x["source"], "time": x["time"], "kind": "explain"}
    up = ev["pct"] > 0
    key = "none" + ("+" if up else "-") if not d else ("single:" + d["name"].split(" ")[0] if d["key"] == "single" else d["key"])
    return key, (d["name"] if d else ("반등(원인 불분명)" if up else "하락(원인 불분명)")), (d["why"] if d else ""), head


def issues_of(rows, k):
    by = {}
    for i, r in enumerate(rows, 1):
        b = by.setdefault(r["cause_key"], {"key": r["cause_key"], "name": r["cause"], "pct": 0.0, "nos": [], "phases": [], "top": r})
        b["pct"] += r["pct"]
        b["nos"].append(i)
        if r.get("phase") and r["phase"] not in b["phases"]:
            b["phases"].append(r["phase"])
        if abs(r["pct"]) > abs(b["top"]["pct"]) or (not b["top"].get("head") and r.get("head")):
            b["top"] = r
    out = []
    for b in by.values():
        t = b.pop("top")
        if abs(b["pct"]) < 0.1:
            continue
        up = b["pct"] > 0                                   # 같은 원인의 오름·내림을 합친 하루 순효과 → 이름은 순효과 방향으로
        if b["key"] in NAMES:
            b["name"] = NAMES[b["key"]][up]
        elif b["key"].startswith("single:"):
            b["name"] = b["key"][7:] + (" 급등" if up else " 급락")
        out.append({**b, "pct": round(b["pct"], 2), "pp": round(b["pct"] * k, 2), "head": t.get("head"), "why": t.get("why", ""),
                    "start": t["start"], "end": t["end"], "movers": t.get("movers", [])})
    out.sort(key=lambda b: -abs(b["pct"]))
    return out


def build(news_topics, now=None):
    now = now or time.time()
    syms = {"KOSPI": "^KS11", **ASSETS}
    stocks = load_stocks()
    for st in stocks:
        syms["S" + st["code"]] = st["code"] + ".KS"
    with ThreadPoolExecutor(8) as ex:
        fut = {k: ex.submit(yget, v) for k, v in syms.items()}
        flow_f = ex.submit(load_flow)
    raw = {}
    for k, f in fut.items():
        try:
            raw[k] = f.result()
        except Exception as e:
            print(f"[kr_moves] {k}: {e}")
    if "KOSPI" not in raw:
        return None
    F = flow_f.result()
    K = series(raw["KOSPI"], inject_open=True)
    days = sorted({kd(t).date() for t in raw["KOSPI"]["ts"]})
    if len(days) < 2:
        return None
    day, pday = days[-1], days[-2]
    cur = [(t, c) for t, c in K if kd(t).date() == day]
    prevs = [(t, c) for t, c in K if kd(t).date() == pday]
    open_ts, open_px = cur[0]                              # series(inject_open) 의 첫 점 = 장 시작 값(시가)
    at_hm = lambda d_, hm_: int(datetime.combine(d_, datetime.min.time(), KST).replace(hour=hm_[0], minute=hm_[1]).timestamp())
    prev_close_ts, prev_close = at_hm(pday, CLOSE_H), prevs[-1][1]   # 야후 5분봉은 15:00 에서 끊겨 있어 전일 종가 시각은 15:30 으로
    meta = raw["KOSPI"]["meta"]
    pc_meta = meta.get("previousClose")
    if pc_meta and abs(pc_meta / prev_close - 1) < 0.005:
        prev_close = pc_meta                               # 공식 전일 종가
    open_end = at_hm(day, CLOSE_H)
    rt, rm = meta.get("regularMarketTime"), meta.get("regularMarketPrice")
    if rt and rm and kd(rt).date() == day and rt > cur[-1][0]:      # 봉보다 새로운 체결가(또는 15:30 종가)가 있으면 마지막 점으로
        cur.append((min(rt, open_end), rm))
    last_ts = cur[-1][0]
    kst_now = kd(now)
    live = kst_now.date() == day and kst_now.weekday() < 5 and now < open_end + 10 * 60
    mode = "live" if live else "closed"
    start_ts, end_ts = prev_close_ts, last_ts
    px_now = cur[-1][1]
    ref_pct = round((px_now / prev_close - 1) * 100, 2)

    X = {k: series(raw[k]) for k in ASSETS if k in raw}
    S = {st["code"]: series(raw["S" + st["code"]], inject_open=True) for st in stocks if "S" + st["code"] in raw}
    X["KQ"] = series(raw["KQ"], inject_open=True) if "KQ" in raw else []

    # 구간: 갭 + 정규장 지그재그
    legs = []
    gap_pct = (open_px / prev_close - 1) * 100
    if abs(gap_pct) >= GAP_MIN:
        legs.append({"start": prev_close_ts, "end": open_ts, "pct": round(gap_pct, 2), "trig": [prev_close_ts, open_ts], "trig_pct": round(gap_pct, 2),
                     "phase": "갭(시가)", "gap": True})
    rng = (max(c for _, c in cur) - min(c for _, c in cur)) / prev_close * 100        # 그날 고저 폭(%)
    rev = max(REV, min(1.0, rng / 4))
    seg_min, min_leg = max(SEG_MIN, rev * 0.4), max(MIN_LEG, rev * 1.2)
    zz = mm._legs(cur, open_ts, last_ts, seg_min, bounds=lambda a, b: [], phase=phase_of, rev=rev)
    for z in zz:
        legs.append({"start": z["start"], "end": z["end"], "pct": z["nq_pct"], "trig": z["trig"], "trig_pct": z["trig_pct"], "phase": z["phase"]})
    topics = news_topics
    idf = idf_of(topics)
    for ev in legs:
        f, drivers, movers, flow = fingerprint(ev, K, X, S, stocks, F)
        ev.update(cross=f, drivers=drivers, movers=movers)
        if flow:
            ev["flow"] = {"ind": flow[0], "frn": flow[1], "inst": flow[2]}
        ev["news"], ev["explain"] = match_news(ev, topics, drivers, movers, idf)
    # 같은 기사가 여러 구간의 대표 기사가 되지 않게: 큰 구간부터 정하고, 이미 쓴 기사는 뒤로 미룸
    used = set()
    for ev in sorted(legs, key=lambda e: -abs(e["pct"])):
        for lst in ("news", "explain"):
            L = ev[lst]
            ev[lst] = [x for x in L if x["headline"] not in used] + [x for x in L if x["headline"] in used]
        h = (ev["news"] or ev["explain"] or [None])[0]
        if h:
            used.add(h["headline"])
    # 행(시간순) · 이슈 · 기여
    rows = []
    for ev in sorted(legs, key=lambda e: e["start"]):
        key, name, why, head = cause_of(ev)
        rows.append({"start": ev["start"], "end": ev["end"], "pct": ev["pct"], "phase": ev["phase"], "gap": bool(ev.get("gap")), "cause_key": key,
                     "cause": name, "why": why, "head": head, "detail": abs(ev["pct"]) >= min_leg, "movers": ev["movers"], "flow": ev.get("flow")})
    total = sum(r["pct"] for r in rows)
    k = ref_pct / total if total and ref_pct * total > 0 and 0.5 <= ref_pct / total <= 2 else 1
    issues = issues_of(rows, k)
    # 방향 확정: 전일 종가 수준을 마지막으로 건넌 뒤 (한 번도 안 건넜으면 시가부터)
    sgn = 1 if ref_pct > 0 else -1 if ref_pct < 0 else 0
    settle = None
    if sgn and abs(ref_pct) >= 0.2:
        lastc = None
        for t, c in cur:
            if (c - prev_close) * sgn <= 0:
                lastc = t
        nxt = [t for t, c in cur if lastc is None or t > lastc]
        settle = open_ts if lastc is None else (nxt[0] if nxt else None)
    trigger = None
    if sgn and abs(ref_pct) >= 0.2:
        same = [r for r in rows if r["pct"] * sgn > 0]
        if same:
            t_ = max(same, key=lambda r: abs(r["pct"]))
            ev = next((e for e in legs if e["start"] == t_["start"]), {})
            grp = [r for r in same if r["cause_key"] == t_["cause_key"] and not t_["cause_key"].startswith("none")]
            gp = sum(r["pct"] for r in grp) if grp else t_["pct"]
            gross = sum(r["pct"] for r in same)
            trigger = {"start": t_["start"], "end": t_["end"], "pct": t_["pct"], "cause_key": t_["cause_key"], "cause": t_["cause"], "why": t_["why"],
                       "phase": t_["phase"], "gap": t_["gap"], "movers": t_["movers"], "share": round(gp / gross * 100), "gross": round(gross, 2),
                       "group_n": len(grp) or 1, "group_pct": round(gp, 2), "news": (ev.get("news") or [])[:2], "explain": (ev.get("explain") or [])[:2],
                       "trig": ev.get("trig"), "trig_pct": ev.get("trig_pct")}
    # 하루 대형주 기여 (전일 종가 대비)
    contrib = []
    for st in stocks:
        s = S.get(st["code"])
        if not s:
            continue
        pcl = raw["S" + st["code"]]["meta"].get("previousClose")
        px = s[-1][1]
        if pcl and px:
            v = (px / pcl - 1) * 100
            contrib.append({"code": st["code"], "name": st["name"], "pct": round(v, 2), "pp": round(v * st["w"], 3)})
    contrib.sort(key=lambda x: -abs(x["pp"]))
    kq_raw = raw.get("KQ")
    kq_pct = None
    if kq_raw:
        kq_pc = kq_raw["meta"].get("previousClose")
        kq_px = X["KQ"][-1][1] if X["KQ"] else None
        kq_pct = round((kq_px / kq_pc - 1) * 100, 2) if kq_pc and kq_px else None
    day_obj = {"mode": mode, "pct": ref_pct, "kq_pct": kq_pct, "start": prev_close_ts, "end": last_ts, "open": open_ts, "price": round(px_now, 2),
               "prev_close": round(prev_close, 2), "settle": settle, "trigger": trigger, "segments": rows, "issues": issues, "contrib": contrib[:5],
               "flow": ({"ind": F["now"][0], "frn": F["now"][1], "inst": F["now"][2]} if F and F["day"] == str(day) else None)}
    legs_small = [[e["start"], e["end"], e["pct"]] for e in legs]
    timeline = {"start": prev_close_ts, "end": last_ts, "open": open_ts,
                "kp": [[t, round(c, 2)] for t, c in [(prev_close_ts, prev_close)] + cur if (t - open_ts) % 900 < 300 or t == last_ts],
                "legs": legs_small}
    events = sorted((e for e in legs if abs(e["pct"]) >= min_leg), key=lambda e: -abs(e["pct"]))[:MAX_EVENTS]
    events.sort(key=lambda e: e["start"])
    print(f"[kr_moves] {mode} {day} 코스피 {ref_pct:+.2f}% 구간 {len(legs)}개 · " + ", ".join(f'{r["pct"]:+.2f} {r["cause"]}' for r in rows))
    return {
        "generated_at": datetime.fromtimestamp(now, KST).isoformat(timespec="seconds"),
        "mode": mode, "date": str(day),
        "indices": [{"name": "코스피", "price": round(px_now, 2), "change_pct": ref_pct},
                    {"name": "코스닥", "price": round(X["KQ"][-1][1], 2) if X["KQ"] else None, "change_pct": kq_pct}],
        "day": day_obj, "timeline": timeline, "events": events,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--news", required=True, help="kr_news.py 가 만든 briefings/kr_news.json")
    ap.add_argument("--out", required=True)
    ap.add_argument("--archive", default=os.environ.get("KR_MOVES_NEWS", ""), help="지난 실행 기사 기억(캐시 파일) — 비우면 이번 수집분만")
    a = ap.parse_args()
    d = json.load(open(a.news, encoding="utf-8"))
    topics = mm.merge_archive(d.get("topics", []), a.archive, keep_h=72) if a.archive else d.get("topics", [])
    res = build(topics)
    if not res:
        print("[kr_moves] 시세를 받지 못해 건너뜀")
        return 1
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, separators=(",", ":"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
