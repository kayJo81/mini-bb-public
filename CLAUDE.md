# 미니 블벅터 (mini-bb-public) — 작업 규칙

한국어 사용자. 답변·기사·보고는 한국어, 숫자는 원자료 그대로.

## 구성
- 터미널: mini-bbterminal.pages.dev (briefing.yml 로 배포, 뉴스·속보·트렌드포스)
- 랩: mini-bblab.pages.dev (lab.yml 로 배포, `/sim` 가상 매매·포트폴리오, 매크로, 심층 뉴스 뷰어 `lab/deep.html`, Top Picks)
- 심층 뉴스: `lab/deepnews/<id>.html` + `lab/deepnews/index.json` (뷰어가 raw GitHub 의 index.json 을 읽으므로 push 하면 바로 반영)
- GitHub 저장소: `kayJo81/mini-bb-public` (워크플로 dispatch 때 이름을 틀리면 404)
- 절차 상세: `.claude/skills/deepnews`(기사 발행), `.claude/skills/lab-ops`(배포·스케줄·시간)

## 원칙 (어기면 사용자가 바로 지적함)
1. **출처 라벨**: 공식(8-K·공시) / 단일 출처 / 복수 출처 / 계산 / 추정·해석을 본문에 구분해 적는다. 전사 원문이 없으면 "2차 요약 기반, 전사 대조 전"이라고 쓴다.
2. **인용 지어내기 금지**: 영어 원문을 모르면 번역만 적고 "속보 번역"으로 표시한다. 질문자 이름·소속을 모르면 비워 둔다.
3. **질문에 직접 답하는 것이 먼저**: 사용자가 "X 여부"를 물으면 그 답을 맨 앞에 쉬운 말로. 숫자·파트너십 같은 다른 정보로 채우지 않는다.
4. **쉽게 쓴다**: 전문용어 표·8줄 이상 표보다 한 줄 결론 + 6줄 이내 카드. 어렵다는 말이 나오면 먼저 줄이고 다시 보낸다.
5. 확인 못 한 것은 못 했다고 말한다(전사, 10-Q, 정규장 시세 등). "된 것 같다"로 보고하지 않는다.
6. 사용자가 요청하지 않은 PR 은 만들지 않는다. 시킨 일의 범위를 넘는 외부 발행(메일 발송 등)은 규정된 절차(onepager.yml)만 쓴다.

## git
- 커밋 메시지 끝: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` 와 `Claude-Session: <세션 URL>`
- 끝난 파일만 `git add`. 진행 중인 에이전트 파일은 커밋하지 않는다(Stop hook 이 경고해도 그대로 둔다).
- push: `git pull --rebase --autostash origin main && git push origin main`, 실패 시 4회까지 재시도.

## 시간
- 미국 서머타임 종료(11/1) 이후 KST = ET + 14시간(서머타임 중에는 +13). 동부 17:00 콜 = KST 다음 날 07:00.
- 로컬 시계와 사용자 시계가 다를 수 있다. "이미 끝났다"는 사용자 말이 내 시계와 다르면 사용자 말을 먼저 의심하지 말고 자료로 확인한다.
