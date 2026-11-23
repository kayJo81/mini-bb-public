---
name: lab-ops
description: 랩·터미널 배포, 워크플로 dispatch, 스케줄러(Cloudflare Worker), Top Picks 새 실적 점검, 트렌드포스·가상 포트폴리오를 만질 때 쓴다.
---

# 랩·터미널 운영 메모

## 배포·dispatch (GitHub: `kayJo81/mini-bb-public`, ref `main`)
- `lab.yml` (입력 `days`, 보통 4): 랩 전체 재배포. 이 워크플로가 `lab/site.html → sim.html` 로 복사하고 portfolio·paper·gpu_rent·prefill 등을 만든다. 상태는 `lab-data` 브랜치(labdata/)에 쌓임.
- `briefing.yml`: 터미널. `onepager.yml`(입력 `ids`): 원페이퍼 메일. `trendforce.yml`: 트렌드포스(30분). `scheduler.yml`: Worker 배포(수동 전용, 실행 저장소가 바뀌는 배포).
- 스케줄은 GitHub schedule 이 불안정해 Cloudflare Worker(`scheduler/index.js`, `wrangler.toml` 크론)가 workflow_dispatch 로 깨운다. 변경하면 wrangler.toml 크론 문자열과 index.js 상수를 같이 맞춘다.
- 배포 후 확인: `https://mini-bblab.pages.dev/<파일>.json` 의 날짜·`latest_reported` 등이 바뀐 것까지 본다(배포는 몇 분 걸림).

## Top Picks Lab
- 매주 화~토 새 실적 점검: `cd /home/user/mini-bb-public && git pull -q --rebase origin main && python3 -I lab/toppicks/check_new.py`. 출력이 `새 실적 발표 없음`이면 사용자에게 아무 말 하지 않고 끝낸다.
- `NEW <티커>` 가 나오면 `lab/toppicks/UPDATE.md` 를 끝까지 읽고 그 기업만 갱신 → `validate.py` 오류 0 → 커밋·push → `lab.yml` dispatch(days=4) → `tp/<TICKER>.json` 의 `latest_reported` 변경 확인.

## 가상 포트폴리오 (`lab/portfolio.py`)
- 기본: 숏 포함. `--long-only`: 숏 테마·숏 신호 제외, 그 몫은 현금(별도 상태 `portfolio_long.json`, `/sim` 에 카드).
- 로컬 시험: `LAB_DATA=<lab-data 브랜치 복사본> python3 -I lab/portfolio.py --out <public_lab 사본> [--long-only]` (실제 상태 파일을 건드리지 않게 복사본으로).

## 데이터 소스 메모
- SEC: `data.sec.gov` submissions JSON, EFTS 전문 검색(당일 제출은 지연). EDGAR 는 미국 공휴일에 닫힌다. User-Agent 필수, 초당 10회 이하.
- 회사 IR·야후 등은 자동 접속을 막는 경우가 많다. 403 우회 금지 — SEC 사본이나 다른 출처를 쓴다.
- 트렌드포스: 점의 날짜는 페이지의 Last Update. 값이 안 바뀌면 커밋이 없다(실행 기록으로 확인).
- 외국인 공매도 비중 등 KRX 통계는 로그인이 필요해 직접 접근 불가 — 기사(톱스타뉴스) 환산치를 쓰고 환산치임을 표시.
