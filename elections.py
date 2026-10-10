"""
미국 중간선거 현황 — 하원 정당 지지도(generic ballot) 여론조사 평균 + 예측시장 다수당 확률

· 여론조사: 위키백과 '2026 United States House of Representatives elections' 문서의
  'Generic congressional ballot aggregate polls' 표 (RealClearPolitics·Silver Bulletin·NBC 등 집계 기관별 + 평균)
  → 문서 원문(action=raw, CDN 캐시)을 읽어 표만 파싱. 형식이 바뀌면 None (화면에 '갱신 실패')
· 예측시장: 폴리마켓 'Which party will win the House/Senate in 2026?' 의 민주당 Yes 가격(=확률)
  → 여론조사가 아니라 베팅 시장의 확률이므로 화면에서 구분해 표시
"""
import json
import re

import requests

UA = {"User-Agent": "MiniBBTerminal/1.0 (personal news dashboard; github.com/kayJo81/mini-bb-public)"}
WIKI_RAW = "https://en.wikipedia.org/w/index.php?title=2026_United_States_House_of_Representatives_elections&action=raw"
WIKI_URL = "https://en.wikipedia.org/wiki/2026_United_States_House_of_Representatives_elections#Generic_congressional_ballot_aggregate_polls"
PM_SEARCH = "https://gamma-api.polymarket.com/public-search"


def _clean(cell):
    cell = re.sub(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>", "", cell, flags=re.S)          # 각주 제거
    cell = re.sub(r"\{\{Party shading/[^}]*\}\}\s*\|", "", cell)                       # 셀 배경색 지정 제거
    cell = re.sub(r"\{\{[^}]*\}\}", "", cell)
    cell = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", cell)                       # [[링크|이름]] → 이름
    return re.sub(r"'''|''|<br\s*/?>", " ", cell).strip(" |\n")


def _pct(s):
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*%", s)
    return float(m.group(1)) if m else None


def generic_ballot():
    try:
        t = requests.get(WIKI_RAW, headers=UA, timeout=20).text
        b = re.search(r'<section begin="GenericBallotAgg"\s*/>', t)
        e = re.search(r'<section end="GenericBallotAgg"\s*/>', t)
        body = t[b.end():e.start()]
    except Exception as ex:
        print(f"[elections] generic ballot: {ex}")
        return None
    rows = []
    for raw in body.split("\n|-")[1:]:
        cells = [_clean(c) for c in re.split(r"\n\|(?!-)", "\n" + raw.strip()) if c.strip() and not c.startswith("}")]
        cells = [c for c in cells if c]
        if len(cells) < 6:
            continue
        name = re.sub(r"colspan=\d+\s*\|?", "", cells[0]).strip()
        r, d = _pct(cells[-4]), _pct(cells[-3])
        if r is None or d is None:
            continue
        rows.append({"source": name, "updated": cells[-5] if len(cells) >= 7 else cells[1],
                     "rep": r, "dem": d, "margin": round(d - r, 1)})
    if not rows:
        print("[elections] generic ballot: 표 형식을 읽지 못함")
        return None
    avg = next((x for x in rows if x["source"].lower().startswith("average")), None)
    sources = [x for x in rows if x is not avg]
    if avg is None:
        avg = {"source": "Average", "updated": sources[0]["updated"],
               "rep": round(sum(x["rep"] for x in sources) / len(sources), 1),
               "dem": round(sum(x["dem"] for x in sources) / len(sources), 1)}
        avg["margin"] = round(avg["dem"] - avg["rep"], 1)
    return {"average": avg, "sources": sources, "url": WIKI_URL}


def _pm_prob(query, must):
    """폴리마켓에서 질문에 must 문구가 들어간 시장의 Yes 확률(%)"""
    r = requests.get(PM_SEARCH, params={"q": query, "limit_per_type": 5}, headers=UA, timeout=15).json()
    for ev in r.get("events") or []:
        for m in ev.get("markets") or []:
            if must.lower() in (m.get("question") or "").lower():
                prices = m.get("outcomePrices")
                prices = json.loads(prices) if isinstance(prices, str) else prices
                return round(float(prices[0]) * 100, 1), f"https://polymarket.com/event/{ev.get('slug', '')}"
    return None, None


def prediction_markets():
    try:
        house, hurl = _pm_prob("Which party will win the House in 2026", "Democratic Party control the House")
        senate, surl = _pm_prob("Which party will win the Senate in 2026", "Democratic Party control the Senate")
    except Exception as ex:
        print(f"[elections] polymarket: {ex}")
        return None
    if house is None and senate is None:
        return None
    return {"house_dem": house, "senate_dem": senate, "house_url": hurl, "senate_url": surl}


def build_midterms():
    out = {"generic": generic_ballot(), "markets": prediction_markets()}
    g, m = out["generic"], out["markets"]
    print(f"[elections] generic {'D%+.1f' % g['average']['margin'] if g else '실패'}, "
          f"polymarket {'H %.0f%% / S %.0f%%' % (m['house_dem'] or 0, m['senate_dem'] or 0) if m else '실패'}")
    return out
