"""
미니 블벅터 KR — 한국 증시 뉴스 (구글 뉴스 한국판, 직전 24시간, 15분마다 briefing.yml 에서 실행)

  python kr_news.py --out public/briefings/kr_news.json

  · 미국판과 같은 엔진(news_scanner): 카테고리별 검색 → 같은 사건 기사 묶기 → 출처 등급으로 팩트체크
  · 국내 매체 등급: 1급(연합·한경·매경·조선·중앙·동아·서경·머투·이데일리·방송 3사 등) / 2급(전자신문·아시아경제 등)
    정부 부처·한국은행·거래소·금감원 사이트는 '공식'
  · 화면(mini-bbterminal-kr)의 /api/krnews 가 이 파일을 가져다 보여준다
"""
import argparse
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import news_scanner as ns

HOURS = 24

ns.SOURCE_TIERS["tier1"] += ["연합뉴스", "yna.co.kr", "연합인포맥스", "einfomax", "한국경제", "매일경제", "조선일보", "chosun",
                             "중앙일보", "동아일보", "donga", "서울경제", "머니투데이", "mt.co.kr", "이데일리", "edaily",
                             "kbs", "mbc", "sbs", "ytn", "뉴스1", "news1", "뉴시스", "newsis", "조선비즈", "biz.chosun",
                             "한경", "매경", "jtbc"]
ns.SOURCE_TIERS["tier2"] += ["파이낸셜뉴스", "fnnews", "헤럴드경제", "아시아경제", "asiae", "디지털타임스", "dt.co.kr",
                             "전자신문", "etnews", "아이뉴스24", "inews24", "비즈니스포스트", "더벨", "thebell", "딜사이트",
                             "한국일보", "한겨레", "경향신문", "국민일보", "세계일보", "문화일보", "zdnet korea", "zdnet.co.kr",
                             "블로터", "bloter", "비즈워치", "bizwatch", "이투데이", "etoday", "데일리안", "머니s", "moneys",
                             "인베스트조선", "한경비즈니스", "매경이코노미", "연합인포맥스", "the elec", "디일렉",
                             "서울신문", "노컷뉴스", "오마이뉴스", "프레시안", "뉴데일리", "아주경제", "ajunews", "이코노미스트",
                             "조세일보", "택스워치", "약업신문", "메디파나", "히트뉴스", "더바이오", "팜이데일리", "에너지경제",
                             "ekn.kr", "전기신문", "투데이에너지", "에너지신문", "국방일보", "디펜스", "코리아헤럴드",
                             "한국해운신문", "ksg.co.kr", "메디팜스투데이", "pharmstoday", "바이오스펙테이터", "biospectator",
                             "스틸앤메탈뉴스", "snmnews", "이코노미21", "economy21"]
ns.SOURCE_TIERS["opinion"] += ["팍스넷", "증권플러스", "인포스탁", "매일경제tv", "한국경제tv", "wowtv", "sbs biz 칼럼"]
ns.OFFICIAL_DOMAINS |= {"go.kr", "bok.or.kr", "krx.co.kr", "fss.or.kr", "fsc.go.kr", "moef.go.kr", "motie.go.kr",
                        "kiwoom.com", "kind.krx.co.kr", "dart.fss.or.kr", "korea.kr", "kcmi.re.kr", "kif.re.kr", "keei.re.kr",
                        "kdb.co.kr", "ksia.or.kr"}

# (id, 그룹, 카테고리, 검색어들, 제목/요약에 있어야 할 단어) — 그룹이 없으면("") 섹션 바로 아래
KR_TOPICS = [
    # ── 산업 ──
    ("semi", "K 테크", "반도체", ["삼성전자 반도체 OR SK하이닉스 OR HBM", "반도체 수출 OR 파운드리 OR D램 가격 OR 낸드"],
     r"반도체|hbm|하이닉스|삼성전자|파운드리|d램|디램|낸드|메모리|웨이퍼|칩"),
    ("battery", "K 테크", "배터리", ["2차전지 OR 배터리 LG에너지솔루션 OR 삼성SDI OR SK온", "에코프로 OR 포스코퓨처엠 OR 엘앤에프 OR 양극재 OR 전고체"],
     r"2차전지|이차전지|배터리|양극재|음극재|리튬|에코프로|엘앤에프|포스코퓨처엠|에너지솔루션|삼성sdi|sk온|전고체|ess"),
    ("sobujang", "K 테크", "소부장", ["반도체 장비 OR 반도체 소재 OR 소부장 수주", "한미반도체 OR 원익IPS OR 이오테크닉스 OR 리노공업 OR 반도체 후공정"],
     r"소부장|장비|소재|부품|수주|국산화|증착|식각|노광|후공정|패키징|본더|한미반도체|원익|이오테크닉스|리노공업|프로브"),
    ("robot_ai", "K 테크", "AI·로봇", ["휴머노이드 OR 로봇 레인보우로보틱스 OR 두산로보틱스", "AI 데이터센터 OR 인공지능 네이버 OR 카카오 OR LG AI"],
     r"로봇|휴머노이드|ai|인공지능|데이터센터|gpu|네이버|카카오|llm|소버린|피지컬"),
    ("ship", "K 조방원", "조선", ["조선 수주 OR HD현대중공업 OR 한화오션 OR 삼성중공업 OR HD한국조선해양", "LNG선 수주 OR 미 해군 MRO OR 마스가 조선"],
     r"조선|수주|lng선|선박|컨테이너선|hd현대|한화오션|삼성중공업|한국조선해양|mro|마스가|해양"),
    ("defense", "K 조방원", "방산", ["방산 수출 OR 한화에어로스페이스 OR 현대로템 OR LIG넥스원 OR 한국항공우주", "K9 OR K2 전차 OR 천무 수출 계약"],
     r"방산|방위|한화에어로|현대로템|lig넥스원|넥스원|kai|한국항공우주|k9|k2|천무|무기|국방"),
    ("nuclear", "K 조방원", "원자력", ["원전 수출 OR 두산에너빌리티 OR SMR OR 한수원", "체코 원전 OR 소형모듈원자로 OR 한전기술 OR 원전 수주"],
     r"원전|원자력|두산에너빌리티|smr|한수원|체코|소형모듈|한전기술|한전kps|우라늄"),
    ("bio", "K 바이오", "바이오·제약", ["바이오 기술이전 OR 임상 결과 OR 신약 허가", "삼성바이오로직스 OR 셀트리온 OR 알테오젠 OR 유한양행 OR 한미약품"],
     r"바이오|제약|임상|기술이전|신약|fda|허가|셀트리온|삼성바이오|알테오젠|유한양행|한미약품|비만|항암"),
    ("auto", "K 모빌리티", "자동차", ["현대차 OR 기아 판매 OR 관세 OR 실적", "현대모비스 OR 자동차 부품 OR 전기차 판매"],
     r"현대차|현대자동차|기아|모비스|자동차|전기차|완성차|관세"),
    ("bank", "K 금융", "금융지주·은행", ["KB금융 OR 신한지주 OR 하나금융 OR 우리금융 실적", "은행 대출 금리 OR 은행권 순이익 OR 예대마진"],
     r"금융지주|kb금융|신한|하나금융|우리금융|은행|대출|예대|기업은행|bnk|jb금융"),
    ("securities", "K 금융", "증권", ["증권사 실적 OR 증권주 OR 거래대금 증가", "미래에셋증권 OR 키움증권 OR 삼성증권 OR 한국금융지주 OR NH투자증권"],
     r"증권|증권사|미래에셋|키움|한국금융지주|한국투자|nh투자|거래대금|ib|브로커리지"),
    ("insurance", "K 금융", "보험", ["보험사 실적 OR 보험주 OR 삼성생명 OR 삼성화재", "생명보험 OR 손해보험 OR 킥스 비율"],
     r"보험|생명|화재|손보|생보|킥스|db손해|현대해상|한화생명"),
    ("valueup", "K 정책", "밸류업·주주환원", ["밸류업 OR 자사주 소각 OR 주주환원 OR 배당 확대", "기업가치 제고 계획 OR 밸류업 지수 OR 자사주 매입"],
     r"밸류업|자사주|주주환원|배당|기업가치 제고|소각|매입"),
    ("market_rule", "K 정책", "증시 제도", ["공매도 OR 상법 개정 OR 배당소득 분리과세", "금융투자소득세 OR 코리아 디스카운트 OR 자본시장 제도 개선 OR 거래시간 연장"],
     r"공매도|상법|분리과세|금투세|금융투자소득세|코리아 디스카운트|자본시장|거래시간|증시 제도|대체거래소|nxt"),
    # ── 매크로 ──
    ("market", "", "증시·수급", ["코스피 마감 OR 코스닥 마감", "외국인 순매수 OR 기관 순매도 코스피", "코스피 급등 OR 코스피 급락 OR 증시 특징주"],
     r"코스피|코스닥|외국인|기관|순매수|순매도|공매도|증시|지수|특징주|상한가|하한가|시가총액"),
    ("macro", "", "매크로", ["한국은행 기준금리 OR 금통위 OR 이창용", "원달러 환율 OR 외환시장 OR 환율 급등", "수출 증가 OR 무역수지 OR 소비자물가 OR 경제성장률 한국",
                         "국고채 금리 OR 채권시장 OR 가계부채"],
     r"한국은행|한은|기준금리|금통위|이창용|환율|원·달러|원달러|원화|외환|수출|무역수지|물가|cpi|성장률|gdp|국고채|가계부채|경기"),
    # ── 정치·정책 ──
    ("policy", "", "정책·정치", ["대통령 경제 OR 국회 법안 기업 OR 정부 예산", "금융위원회 OR 금감원 OR 기재부 정책", "산업부 OR 공정위 규제"],
     r"금융위|금감원|금융감독원|세법|세제|정부|대통령|국회|법안|정책|규제|예산|공정위|기재부|산업부"),
]


SECTION = {"market": "매크로", "macro": "매크로", "policy": "정치·정책"}   # 나머지는 '산업'


def topics():
    out = []
    for tid, group, name, queries, kw in KR_TOPICS:
        out.append({"id": tid, "section": SECTION.get(tid, "산업"), "group": group, "name": name, "queries": [(q, "KR") for q in queries],
                    "tickers": [], "_kw": re.compile(kw, re.I), "_feed": None, "_must": None, "_exclude": None})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="public/briefings/kr_news.json")
    a = ap.parse_args()
    end = datetime.now(ns.KST)
    start = end - timedelta(hours=HOURS)
    T = topics()
    with ThreadPoolExecutor(max_workers=6) as fetch_pool, ThreadPoolExecutor(max_workers=len(T)) as topic_pool:
        res = list(topic_pool.map(lambda t: ns.scan_topic(t, start, end, "", fetch_pool), T))
    tail = re.compile(r"\s+[-|]\s+[^-|]{1,25}$")   # 제목 끝 '- 머니투데이' 같은 언론사 표기
    for t in res:   # 화면에 필요 없는 필드 정리
        for s in t["stories"]:
            s.pop("summary", None)
            s["headline"] = tail.sub("", s["headline"])
            for x in s["sources"]:
                x["headline"] = tail.sub("", x["headline"])
    total = sum(len(t["stories"]) for t in res)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    json.dump({"generated_at": end.isoformat(timespec="seconds"), "window_hours": HOURS, "total": total, "topics": res},
              open(a.out, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f"[kr_news] {total}건 · " + ", ".join(f"{t['name']} {t['count']}" for t in res))


if __name__ == "__main__":
    main()
