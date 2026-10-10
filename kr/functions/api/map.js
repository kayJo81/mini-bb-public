// 핀비즈 맵처럼: 코스피·코스닥 시가총액 상위 200종목을 업종별로 묶어 (타일 크기 = 시가총액, 색 = 등락률) (30초 캐시)
//  ?m=KOSPI | KOSDAQ — 업종은 lib/sectors.json(kr/tools/build_sectors.py 로 생성), 없는 종목은 '기타'
import { cached, naver, num } from "../../lib/naver.js";
import SECTORS from "../../lib/sectors.json";

const TOP_PAGES = 3, MAX_STOCKS = 200;   // 100개 × 3 받아 ETF·우선주를 빼고 200종목

export async function onRequestGet({ request }) {
  const m = new URL(request.url).searchParams.get("m") === "KOSDAQ" ? "KOSDAQ" : "KOSPI";
  return cached(request, 30, async () => {
    const [idx, ...pages] = await Promise.all([
      naver(`/api/index/${m}/basic`),
      ...Array.from({ length: TOP_PAGES }, (_, i) => naver(`/api/stocks/marketValue/${m}?page=${i + 1}&pageSize=100`)),
    ]);
    const groups = new Map();
    // 보통주만 (ETF·우선주·스팩 제외) 시가총액 순 200종목
    const list = pages.flatMap((p) => p.stocks || [])
      .filter((s) => s.stockEndType === "stock" && !/우$|우B$|우\(.+\)$|\dB$|스팩/.test(s.stockName)).slice(0, MAX_STOCKS);
    for (const s of list) {
      const cap = num(s.marketValue);
      if (!cap) continue;
      const k = SECTORS.codes[s.itemCode];
      const name = k === undefined ? "기타" : SECTORS.names[k];
      if (!groups.has(name)) groups.set(name, []);
      groups.get(name).push({ c: s.itemCode, n: s.stockName, cap, chg: num(s.fluctuationsRatio) ?? 0, p: num(s.closePrice) });
    }
    const sectors = [...groups.entries()].map(([name, stocks]) => {
      const cap = stocks.reduce((a, x) => a + x.cap, 0);
      const chg = stocks.reduce((a, x) => a + x.chg * x.cap, 0) / cap;   // 시총 가중 평균
      return { name, cap, chg: Math.round(chg * 100) / 100, stocks: stocks.sort((a, b) => b.cap - a.cap) };
    }).sort((a, b) => b.cap - a.cap);
    return {
      m, status: pages[0].marketStatus, at: new Date().toISOString(),
      index: { name: idx.stockName, price: idx.closePrice, chg: num(idx.fluctuationsRatio), diff: idx.compareToPreviousClosePrice },
      sectors,
    };
  });
}
