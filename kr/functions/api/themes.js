// 전체 테마(약 260개) 등락률 + 등락률 상·하위 테마의 구성 종목 (40초 캐시)
import { cached, naver, num, slimStock } from "../../lib/naver.js";

const DETAIL_TOP = 15;   // 상위·하위 각각 이만큼은 구성 종목까지

export async function onRequestGet({ request }) {
  return cached(request, 40, async () => {
    const pages = await Promise.all([1, 2, 3].map((p) => naver(`/api/stocks/theme?page=${p}&pageSize=100`)));
    const status = pages[0].marketStatus;
    const groups = pages.flatMap((p) => p.groups || []).map((g) => ({
      no: g.no, name: g.name, chg: num(g.changeRate), total: g.totalCount, rise: g.riseCount, fall: g.fallCount,
    }));
    groups.sort((a, b) => b.chg - a.chg);
    const want = [...groups.slice(0, DETAIL_TOP), ...groups.slice(-DETAIL_TOP)];
    const details = await Promise.all(want.map((g) =>
      naver(`/api/stocks/theme/${g.no}?page=1&pageSize=60`).then((d) => [g.no, d]).catch(() => [g.no, null])));
    const map = Object.fromEntries(details);
    for (const g of groups) {
      const d = map[g.no];
      if (!d) continue;
      const stocks = (d.stocks || []).map((s) => slimStock(s)).sort((a, b) => b.chg - a.chg);
      g.leaders = stocks.slice(0, 3).map(({ code, name, chg }) => ({ code, name, chg }));
      g.laggards = stocks.slice(-2).map(({ code, name, chg }) => ({ code, name, chg }));
      g.value = Math.round(stocks.reduce((a, s) => a + (s.value || 0), 0));   // 테마 거래대금 합(백만원)
    }
    return { status, at: new Date().toISOString(), count: groups.length, groups };
  });
}
