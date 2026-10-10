"""
시장을 움직인 뉴스 — 나스닥·S&P500 급변 구간과 같은 시간대 뉴스 짝짓기

· 급변 감지: S&P500 선물(ES)·나스닥100 선물(NQ) 5분봉으로 15분 등락을 계산 (선물은 거의 24시간 거래 →
  한국 낮 시간대 움직임도 잡힘). 창 안에서 15분 등락이 하한선(NQ ±0.20% 또는 ES ±0.15%)을 넘는 구간 중
  가장 큰 것부터 최대 5개
· 뉴스 짝짓기: 급변 구간 시작 10분 전 ~ 끝 5분 후에 '처음 보도된' 이슈 중
  속보 피드(FinancialJuice·월스트리트견문) > 매크로·지정학·실적 > 출처 등급 순으로 최대 2개
· 같은 시간대일 뿐 인과관계를 확인한 것은 아니다 (화면에도 그렇게 표시)
· 상단 지수: S&P500·나스닥 종합 정규장 현재가와 전일 대비 등락
"""
import math
import re

import requests

CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=5m&range=2d"
HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
FLOOR = {"NQ": 0.20, "ES": 0.15}     # 15분 등락 하한선(%)
MAX_EVENTS = 5
# 시장 전체를 움직일 만한 토픽 가중치
TOPIC_WEIGHT = {"fed": 4, "us_macro": 4, "mideast": 4, "us_politics": 4, "earnings": 4, "ecb": 3, "boj": 3,
                "asia_macro": 2, "eu_macro": 2, "nvda": 2, "aapl": 2, "msft": 2, "googl": 2, "amzn": 2, "meta": 2,
                "tesla": 2, "semi_mega": 2, "ai_hw": 1, "ai_sw": 1}
VIA_WEIGHT = {"FinancialJuice": 3, "월스트리트견문(华尔街见闻)": 1}
# 움직임을 '설명'하는 시황 정리·정례 데이터 공지·홍보물은 원인 후보에서 제외
RECAP = re.compile(r"指数(?:跌|涨)|收(?:跌|涨)|盘初|盘中|尾盘|午盘|SOFR|担保隔夜|联邦基金利率报|"
                   r"stocks? (?:fall|rise|slide|rally|drop|gain|climb)|shares? (?:fall|rise|slide)|"
                   r"(?:index|indices|futures|nasdaq|s&p|dow) (?:fall|rise|slide|drop|gain|climb|edge|extend)|"
                   r"press kit|stock market today|market wrap|midday|closing bell|to watch|what to know|explainer|"
                   r"\blaunch(?:es)?\b|pitchfest|webinar|conference|announces? (?:partnership|collaboration)", re.I)


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


def _moves(es, nq, start_ts, end_ts):
    """5분봉 기준 15분 등락이 하한선을 넘는 봉들을 이어 붙여 구간으로 만든다."""
    esd = dict(es)
    flagged = []
    for i in range(3, len(nq)):
        t, c = nq[i]
        t0, c0 = nq[i - 3]
        if not (start_ts <= t <= end_ts) or t - t0 > 20 * 60:
            continue
        nq_ch = (c / c0 - 1) * 100
        es_ch = (esd[t] / esd[t0] - 1) * 100 if t in esd and t0 in esd else None
        if abs(nq_ch) >= FLOOR["NQ"] or (es_ch is not None and abs(es_ch) >= FLOOR["ES"]):
            flagged.append((t0, t, nq_ch))
    events = []
    for t0, t, ch in flagged:   # 겹치거나 맞닿고 방향이 같은 구간은 합친다
        if events and t0 <= events[-1]["end"] and (ch > 0) == (events[-1]["dir"] > 0):
            events[-1]["end"] = t
        else:
            events.append({"start": t0, "end": t, "dir": ch})
    nqd = dict(nq)
    for ev in events:
        ev["nq_pct"] = round((nqd[ev["end"]] / nqd[ev["start"]] - 1) * 100, 2)
        ev["es_pct"] = (round((esd[ev["end"]] / esd[ev["start"]] - 1) * 100, 2)
                        if ev["start"] in esd and ev["end"] in esd else None)
        del ev["dir"]
    events.sort(key=lambda e: -abs(e["nq_pct"]))
    return sorted(events[:MAX_EVENTS], key=lambda e: -e["end"])


# 시장 반응 기사: "주가/지수가 ~ 때문에(after/on/as …) 떨어졌다/올랐다" — 언론이 움직임의 원인으로 지목한 것
REACTION = re.compile(
    r"(?:stocks?|shares|nasdaq|s&p|dow|wall street|equities|futures|tech|chips?|chipmakers|semis|market)s?\b.{0,60}?"
    r"\b(?:drop|fall|sink|slide|tumble|plunge|slump|retreat|sell[- ]?off|rall(?:y|ies)|rise|jump|surge|gain|climb|rebound|soar)\w*\b"
    r".{0,40}?\b(?:after|on|as|amid|following|over|due to)\b"
    r"|(?:下跌|大跌|跳水|走低|重挫|上涨|拉升|大涨|走高).{0,20}?(?:因|受|拖累|提振|影响)|(?:因|受|拖累|提振).{0,30}?(?:下跌|大跌|跳水|走低|重挫|上涨|拉升|大涨|走高)",
    re.I)
GENERIC = set("""stock stocks share shares market markets nasdaq s&p dow wall street equities future futures tech chip chips
chipmaker chipmakers semis drop drops fall falls sink sinks slide slides tumble plunge slump retreat rally rallies rise rises
jump jumps surge gain gains climb rebound soar after on as amid following over due to the a an of in for and or with by at from
report reports reported says said new over less more than than us u.s. ai other others point points index others
data center centre operator investor investors company companies firm firms deal deals billion million bln mln trillion
year week day today amid may could""".split())


def _toks(text):
    out = set()
    for w in re.findall(r"[a-z0-9$]+", text.lower()):
        w = w[:-1] if len(w) > 3 and w.endswith("s") else w
        if len(w) > 2 and w not in GENERIC:
            out.add(w)
    return out


def _reactions(ev, topics):
    """급변 구간 시작 ~ 끝 3시간 후에 나온 '시장 반응' 헤드라인들의 단어 집합"""
    lo, hi = ev["start"], ev["end"] + 3 * 3600
    out = []
    for t in topics:
        for s in t["stories"]:
            for x in s["sources"]:
                if lo <= x.get("published", s["first_seen"]) <= hi and REACTION.search(x["headline"]):
                    out.append(_toks(x["headline"]))
    return out


def _idf(topics):
    """전체 헤드라인 기준 단어 희소도 — 'data', 'stock'처럼 흔한 단어로 엮이는 것을 막기 위함"""
    df, n = {}, 0
    for t in topics:
        for s in t["stories"]:
            n += 1
            for w in _toks(s["headline"]):
                df[w] = df.get(w, 0) + 1
    return {w: math.log((n + 1) / (c + 1)) for w, c in df.items()}, math.log(n + 1)


def _cited(tk, reactions, idf):
    """반응 기사 중 이 이슈와 '드문 핵심 단어'를 2개 이상 공유(희소도 합 ≥ 7)하는 것의 수"""
    w, dflt = idf
    return sum(1 for r in reactions if len(tk & r) >= 2 and sum(w.get(x, dflt) for x in tk & r) >= 7)


def _match_news(ev, topics):
    lo, hi = ev["start"] - 10 * 60, ev["end"] + 5 * 60
    # 보도자료(official)는 개별 기업 홍보가 많아 시장 전반 원인 후보로는 낮게
    tier_rank = {"official": 1, "tier1": 3, "tier2": 2, "tier3": 1}
    reactions = _reactions(ev, topics)
    idf = _idf(topics)
    cands = []
    for t in topics:
        for s in t["stories"]:
            if s.get("is_opinion") or not (lo <= s["first_seen"] <= hi) or RECAP.search(s["headline"]) \
                    or REACTION.search(s["headline"]):     # 반응 기사는 근거로만 쓰고 원인 후보에서는 제외
                continue
            via = max((VIA_WEIGHT.get(x["via"], 0) for x in s["sources"]), default=0)
            best_tier = max((tier_rank.get(x["tier"], 0) for x in s["sources"]), default=0)
            score = TOPIC_WEIGHT.get(t["id"], 0) + via + best_tier + (1 if s["verdict"] == "VERIFIED" else 0)
            # 언론이 이 움직임의 원인으로 지목한 이슈(반응 기사와 핵심 단어 2개 이상 공유)면 크게 가산
            support = _cited(_toks(s["headline"]), reactions, idf)
            score += 4 * min(support, 3)
            if score < 4:      # 시장 전반과 관련성이 낮은 개별 기사는 후보에서 제외
                continue
            cands.append((score, s["first_seen"], t, s, support))
    cands.sort(key=lambda x: (-x[0], x[1]))
    out, seen = [], set()
    for _, _, t, s, support in cands:
        if s["headline"] in seen:
            continue
        seen.add(s["headline"])
        out.append({"headline": s["headline"], "url": s["url"], "first_seen": s["first_seen"],
                    "topic": t["name"], "source": s["sources"][0]["name"], "cited": support})
        if len(out) == 2:
            break
    return out


def build_moves(briefing, start_ts, end_ts):
    try:
        es, _ = _chart("ES%3DF")
        nq, _ = _chart("NQ%3DF")
    except Exception as ex:
        print(f"[moves] futures: {ex}")
        return None
    events = _moves(es, nq, start_ts, end_ts)
    for ev in events:
        ev["news"] = _match_news(ev, briefing.get("topics", []))
    span = [c for t, c in nq if start_ts <= t <= end_ts], [c for t, c in es if start_ts <= t <= end_ts]
    print(f"[moves] 급변 구간 {len(events)}개")
    return {
        "indices": [x for x in (_index_summary("%5EGSPC", "S&P 500"), _index_summary("%5EIXIC", "나스닥")) if x],
        "futures_window": {"nq_pct": round((span[0][-1] / span[0][0] - 1) * 100, 2) if len(span[0]) > 1 else None,
                           "es_pct": round((span[1][-1] / span[1][0] - 1) * 100, 2) if len(span[1]) > 1 else None},
        "events": events,
    }
