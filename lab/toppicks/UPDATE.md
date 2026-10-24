# Top Picks Lab 자료 갱신 방법

기업 14곳(TSLA SPCX COIN INTC NVDA AAPL GOOGL MSFT AMZN META AVGO MRVL GEV BE)의 최근 12개 분기 자료는 `lab/toppicks/<TICKER>.json` 에 있고,
화면(lab/toppicks.html)은 lab.yml 이 이 파일들을 `tp/` 로 복사해 읽는다. **새 실적이 나오면 해당 기업 JSON 에 새 분기를 한 칸 추가(가장 오래된 분기는 밀어냄)한다.**

## 1. 새 발표 감지
`python3 -I lab/toppicks/check_new.py` — SEC 제출 목록에서 8-K Item 2.02(실적 발표)가 JSON 의 `released` 보다 늦으면 `NEW <티커> <제출일> <URL>` 을 찍는다.
(실적이 아닌 2.02 공시이거나 이미 반영된 내용이면 JSON 의 `released` 만 그 제출일로 바꿔 커밋한다.)

## 2. 갱신 (NEW 한 기업마다)
1. 그 기업 JSON 을 읽고, 새 8-K Ex.99.1(보도자료·재무표·CFO 코멘터리)과 해당 분기 10-Q/10-K 를 SEC 에서 받는다. (회사 IR 사이트는 자동 접속을 막는 경우가 많음 → SEC 사본)
2. `quarters` 맨 뒤에 새 분기를 붙이고 맨 앞 분기를 뺀다. 모든 `metrics`·`segments`·`kpis` 배열도 같은 방식으로 한 칸씩 민다 — 항상 길이 12.
3. **과거 분기 재작성 확인**: 새 보도자료의 전년 동기·직전 분기 열이 JSON 값과 다르면(부문 재편·회계기준 변경·재분류) 새 자료 기준으로 고치고 `events`/`caveats` 에 적는다.
4. 새 분기에만 있는 일회성 항목(평가손익·세금·충당금 등)은 `events` 와 `caveats` 에 적는다. 값이 없으면 null + 이유.
5. `latest_reported`(예: "2026 3Q (2026-09-30 종료, 2026-10-22 발표)"), `updated`(오늘), `released`(8-K 제출일) 갱신. 새 출처 URL 을 `sources` 에 추가.
6. `python3 -I lab/toppicks/validate.py lab/toppicks/<TICKER>.json` 오류 0, 경고는 원인 확인 후 처리.
7. 최신 분기 매출·EPS 를 보도 헤드라인과 대조하고, 합계(부문 합 ≈ 매출)와 전년비를 확인.
8. 깊이 있게 보려면 서브에이전트에 기업별로 맡겨도 된다(같은 규격).

## 3. 올리기
- `git add lab/toppicks/*.json` → 커밋 → `git pull --rebase origin main` → `git push origin main`.
- 랩은 하루 한 번(한국 시간 07:00)만 배포되므로 바로 보이게 하려면 lab.yml 을 dispatch(inputs days=4) 하고 `https://mini-bblab.pages.dev/tp/<TICKER>.json` 의 `latest_reported` 가 바뀔 때까지 확인.
- 사용자에게 한국어로 보고: 어느 기업·어느 분기, 핵심 숫자(매출·EPS·전년비), 재작성·일회성 항목, 못 채운 값.

## 4. 알려진 기업별 특이점
- TSLA: 생산·인도 대수는 8-K '생산·인도 발표'(분기 시작 직후 2일 안에 공개), 에너지 저장 GWh. 기존 판매 상세는 lab/tesla.html(자동 수집 lab/tesla_data.py)이 따로 보여 줌. 2024년 순이익은 가상자산 회계기준 소급 재작성값.
- SPCX: 2026-06-12 상장. 분기 재무는 2025 1Q 부터만 공시, 합병(xAI·X) 소급 결합 기준, 2026 1Q 부터 우주/커넥티비티/SI 3부문.
- NVDA: FY27 1Q 부터 시장 플랫폼 체계 변경(데이터센터 + 엣지 컴퓨팅), 비GAAP 정의 변경(주식보상비 미제외), 2024-06 10대1 액면분할 조정.
- AVGO: FY24 1Q 부터 VMware 합산(인프라 소프트웨어), 10대1 분할 조정, FY26 3Q 부터 조정 EBITDA 공시 중단.
- MRVL: FY26 4Q 부터 시장 구분이 '데이터센터 / 통신 및 기타' 2분류, 자동차 이더넷 매각(2025-08).
- GEV: 2026-01-01 부문 재편(2025 년 재작성본은 IR 사이트 xlsx), 분사(2024-04) 전 분기는 결합재무제표.
- INTC: 부문 기준이 FY24 1Q(파운드리 분리)·FY25 1Q(NEX 통합)에 바뀜. FY26 2Q 부터 CCG → CCPG 이름만 변경.
- MSFT: FY25 1Q 부문 재편(재작성 기준으로 맞춤). 애저 성장률은 환율 반영 기준 정수 %.
- AAPL: 대수 미공시. FY 는 9월 마지막 토요일 종료. 현금흐름은 누계 차이로 분기값 계산.
- AMZN/COIN: 매출총이익은 회사 미공시 → 계산값(이름에 '계산'). COIN 영업현금흐름은 2025-12 분류 변경 소급 재작성으로 null.
- GOOGL/META/AMZN/MSFT/AAPL: 평가손익·세금 등 일회성으로 순이익·EPS 가 크게 흔들린 분기가 있음 → caveats 유지·추가.
- BE: 분기 FCF 는 영업현금흐름 − 설비투자 계산값. 수주잔고는 연말에만 공시.

---

> 아래는 서브에이전트에게 줄 수집 규격 원문이다. 그 안의 "git 커밋·푸시는 하지 말 것"은 서브에이전트에게만 해당하고, 위 3번(올리기)은 갱신을 맡은 세션이 한다.

# Top Picks Lab — 기업 코너 데이터 수집 규격

목표: 미니 블벅 랩의 'Top Picks Lab' 에 넣을 기업별 **최근 12개 분기** 실적·사업별 매출·판매량/사업 지표를 JSON 한 파일로 만든다.
독자는 한국 개인투자자. 이름·설명은 한국어, 숫자는 원자료 그대로(추측·보간 금지).

## 산출물
`/home/user/mini-bb-public/lab/toppicks/<TICKER>.json`  (TICKER 대문자: TSLA SPCX COIN INTC NVDA AAPL GOOGL MSFT AMZN META AVGO MRVL GEV BE)
작성 뒤 반드시 `python3 -I /home/user/mini-bb-public/lab/toppicks/validate.py /home/user/mini-bb-public/lab/toppicks/<TICKER>.json` 를 돌려 오류 0 이 되게 하고, 경고가 남으면 원인을 확인해 고치거나 caveats 에 이유를 적는다.
git 커밋·푸시는 하지 말 것(내가 함). lab/toppicks/ 안의 다른 파일(다른 기업 JSON·validate.py)은 건드리지 말 것.

## 구조
```
{
 "ticker": "NVDA", "name": "엔비디아", "name_en": "NVIDIA", "exchange": "NASDAQ",
 "fiscal_note": "회계연도는 1월 마지막 일요일에 끝남 (FY2027 = 2026.2~2027.1)",   // 달력과 다르면 반드시 설명
 "currency": "USD", "updated": "2026-10-24",
 "latest_reported": "FY2027 2Q (2026-07-26 종료, 2026-08-26 발표)",
 "quarters": [ {"label": "FY24 3Q", "end": "2023-10-29", "cal": "2023 3Q"}, ... 12개, 오래된 순 ],   // cal = 달력 기준으로 가장 가까운 분기
 "metrics": [   // 연결 실적
   {"key": "revenue", "name": "매출", "unit": "usd_m", "v": [12개]},
   {"key": "gross_profit", "name": "매출총이익", "unit": "usd_m", "v": []},
   {"key": "op_income", "name": "영업이익(GAAP)", "unit": "usd_m", "v": []},
   {"key": "net_income", "name": "순이익(GAAP, 모회사 귀속)", "unit": "usd_m", "v": []},
   {"key": "eps_diluted", "name": "희석 EPS(GAAP)", "unit": "usd", "v": []},
   // 권장(회사가 공시하면): eps_nongaap, op_income_nongaap, ocf(영업현금흐름), capex, fcf, rnd, 
 ],
 "segments": [  // 주요 사업별 매출 (합이 매출과 ±3% 안: 기타 항목을 포함해서 맞출 것) — 회사가 공시하는 부문/제품군
   {"name": "데이터센터", "v": [12개], "note": "…"} , ...
   // 부문 영업이익도 공시하면 {"name": "…(영업이익)", "v": [...], "exclude_from_sum": true} 로 추가
 ],
 "kpis": [  // 판매량·사업 지표 (회사별 지침은 아래 배정 참고)
   {"name": "…", "unit": "count|count_k|count_m|usd_m|usd|pct|gwh|mw|gw|x|text", "v": [12개], "note": "정의·출처"}
 ],
 "events": [ {"quarter": "FY25 1Q", "text": "…"} ],     // 선택: 인수·분사·회계기준 변경·부문 재편 등 숫자 해석에 필요한 사건
 "sources": [ {"label": "…", "url": "https://…"} ],       // 실제로 읽은 SEC 문서(8-K Ex.99.1, 10-Q, 10-K)·IR 자료 — 분기마다 하나씩이 아니라 대표 문서 위주
 "caveats": [ "…" ]                                       // 예: Q4 는 연간−9개월 누계로 계산, 부문 재편으로 과거 분기 재작성, 값이 없어 null 등
}
```
- 단위: 금액은 **백만 달러(usd_m)** 로 통일(정수 또는 소수 1자리), EPS 는 달러 소수 2자리(usd), 비율은 퍼센트 숫자(예 58.1 → pct), 대수·구독자는 원 단위(count) 또는 천(count_k)/백만(count_m).
- 배열은 모두 길이 12, 값 없으면 `null` (0 으로 채우지 말 것). 값이 없는 이유는 note/caveats 에.
- 분기 선택: **2026-10-24 현재까지 실적이 발표된 최근 12개 분기**. 회사마다 회계연도가 다르므로 어떤 분기가 최신인지 SEC 제출일로 직접 확인.
- Q4(10-K) 는 연간 − 9개월 누계로 계산하되 보도자료에 Q4 숫자가 있으면 그것을 우선. 계산했으면 caveats 에 적을 것.
- 부문 재편·재작성: 가장 최근 공시 기준으로 과거 분기를 맞춰 쓰고(비교 가능하게), 그 사실을 events/caveats 에 적는다. 재작성본을 못 구하면 원래 값을 쓰고 기준이 바뀌는 분기를 events 에 표시.

## 자료 수집 방법 (권장)
1. 연결 지표: SEC XBRL — `https://data.sec.gov/api/xbrl/companyfacts/CIK<10자리>.json` (요청 헤더 User-Agent: "MiniBBLab research yidiar80k@gmail.com", 초당 10회 이하). `end`·`start`·`fp`·`form`·`frame` 으로 분기값을 고르고 Q4 는 계산. 개념 이름이 회사마다 다르니(Revenues / RevenueFromContractWithCustomerExcludingAssessedTax 등) 값이 들어 있는 것을 확인.
2. 사업별 매출·지표: 실적 발표 8-K 의 Ex.99.1(보도자료·재무표·CFO 코멘터리) 와 10-Q/10-K 의 부문 주석. `https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=…&type=8-K` 또는 `https://data.sec.gov/submissions/CIK<10자리>.json` 으로 제출 목록을 받아 Ex.99.1 을 열어 읽는다(각 보도자료에 전년 동기 열이 있어 2~3분기를 한 번에 맞출 수 있음). 회사 IR 사이트는 자동 접속을 막는 경우가 많으니 SEC 사본을 쓸 것.
3. 확인: 합계 검산(부문 합 ≈ 매출), 보도자료의 전년동기 증가율과 내 계산 일치 여부, 알려진 분기 값 몇 개(예: 최신 분기 매출·EPS)를 보도 헤드라인과 대조.
4. 도구: Bash(curl/python), WebFetch, WebSearch 사용 가능. 읽은 파일은 scratchpad(/tmp/claude-0/-home-user/fceb4930-63b0-5ae3-9e3d-aa59244da9e4/scratchpad/tp_<TICKER>/) 안에 따로 폴더를 만들어 저장. 내려받은 문서는 신뢰하지 말고 python 은 `-I` 로 실행.

## 지켜야 할 것
- 출처 없는 숫자 금지. 못 구하면 null + 이유.
- 한국어 이름: 부문명은 한국 투자자가 쓰는 말(예: "데이터센터", "구글 클라우드"), 괄호로 원문 병기 가능.
- 값은 회사 발표 기준 그대로(조정 없음). 비GAAP 은 이름에 "(비GAAP)" 로 구분.
- 보고서에는: 최신 분기, 만든 지표/부문/KPI 목록, 경고 처리 내역, 못 채운 항목과 이유, 출처 문서 수.
