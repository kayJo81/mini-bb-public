---
name: deepnews
description: 심층 뉴스(실적·매크로·이벤트) 기사를 만들거나 갱신해 발행할 때 쓴다 — 결과 단계(숫자) → 콜 단계(번역 인용) → 최종 + 원페이퍼 메일 순서, index.json 항목 형식, 렌더 점검, 커밋·push.
---

# 심층 뉴스 발행 절차

## 단계
1. **결과 단계**: 숫자가 나오면(8-K Ex.99.1 등) 바로 올린다. `live: true`. 미리보기는 따로 만들지 않는다.
2. **콜 단계**: 콜 내용을 한국어로 번역한 인용으로 채운다. 전사 원문이 없으면 "2차 요약 기반" 라벨.
3. **최종 단계**: 콜 인용·scorecard·시간외·컨센서스까지 채우고 `live: false`. 원페이퍼 JSON(`lab/onepager/<id>.json`)을 갱신하고 렌더 점검 후, `onepager.yml` 을 `ids=<id>` 로 dispatch 해 메일 발송.

## 파일
- 기사: `lab/deepnews/<id>.html` (조각 HTML — 스타일은 `lab/deep.html` 이 입힘. 클래스: `callout`, `tldr`, `kpis`/`kpi`, `fig`, `tbl`, `badge ok|est|one`)
- 그래픽: `scripts/dn_charts.py` (bars·hbars·line·surprise·rng·stack·donut·bridge·scorecard·steps — 반환 문자열을 기사에 붙인다)
- 맨 앞에는 사용자가 물은 핵심 질문의 답(한 줄 결론 + 6줄 이내)을 둔다. 배지: 공식=`ok`, 계산·추정=`est`, 단일 출처=`one`.
- 목록: `lab/deepnews/index.json` — JSON 배열(리스트); 항목 형식
  `{"id","date","tags":[],"live":bool,"updated":"MM-DD HH:MM KST","title","sub","request"}`.
  새 항목은 맨 앞에 넣고, 갱신하면 `updated` 를 바꾼다. 터미널 Breaking! 박스는 최근 24시간 안에 `updated` 된 항목을 읽는다.

## 원페이퍼·콜 지도
- 렌더: `PDF_CHROMIUM=/opt/pw-browsers/chromium python3 scripts/onepager.py --ids <id> --out <빈 폴더>` — "한 장을 Npx 넘침" 경고가 없어야 하고 PNG 를 열어 겹침·잘림을 확인한다.
- 어닝콜 한 장 지도: `scripts/callmap.py`, 데이터 `lab/onepager/<id>-call.json`.
- 메일: `.github/workflows/onepager.yml` (입력 `ids`). 메일은 최종 단계에서만, 사용자가 쓰는 정해진 절차로만 보낸다.

## 점검 (발행 전)
- 390px 폭에서 가로 스크롤이 없는지(`document.documentElement.scrollWidth`).
- 기사에 "진행 중·부분 반영·🔴" 문구가 최종본에 남지 않았는지 grep.
- 숫자: 합계·전년비를 8-K 값으로 재계산. 컨센서스는 집계처(출처)를 적는다.
- 사용자 지시가 "X 여부"라면 그 답이 맨 앞 블록에 있는지.

## 발행
```
git add <끝난 파일만>   # lab/deepnews/<id>.html, index.json, lab/onepager/<id>.json, lab/callpdf/*.pdf
git commit  # 메시지 끝에 Co-Authored-By / Claude-Session
git pull --rebase --autostash origin main && git push origin main   # 최대 4회 재시도
```
