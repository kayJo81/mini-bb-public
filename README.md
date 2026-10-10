# 미니 블벅터 · 미니 블벅 랩

- **미니 블벅터** — https://mini-bbterminal.pages.dev
  45분마다 뉴스(구글뉴스·Finnhub·FinancialJuice·월스트리트견문·위불)를 모아 카테고리·팩트체크·한국어 번역으로 보여주는 뉴스 모니터
- **미니 블벅 랩** — https://mini-bblab.pages.dev
  종목 뉴스 급증·논조 신호를 과거 1년 이벤트 스터디로 검증해 롱/숏 후보를 보여주는 통계 페이지 (투자 권유 아님)

자동화: Cloudflare Worker 크론(`scheduler/`) → GitHub Actions(`briefing.yml`, `lab.yml`) → Cloudflare Pages
