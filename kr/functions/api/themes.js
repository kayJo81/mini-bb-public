// 전체 테마(약 260개) 등락률 + 등락률 상·하위 테마의 구성 종목 (40초 캐시)
// + 급등주가 몰린 테마: 테마 평균이 낮아도 ±5% 넘게 움직인 종목이 2개 이상이면 (예: 남북경협 평균 −1%인데 양지사 +11%·다스코 +7%)
//   g.hot = 그런 종목 수, g.heat = 움직인 상위 3종목 평균 등락률 (하락 쪽은 g.cold / g.chill)
import { cached, naver, num, slimStock } from "../../lib/naver.js";
import MEMBERS from "../../lib/theme_members.json";

const DETAIL_TOP = 15;   // 상위·하위 각각 이만큼은 구성 종목까지
const HOT = 5, HOT_MIN = 2, HOT_MAX = 12;   // ±5% 종목 2개 이상인 테마, 방향별 최대 12개까지 구성 종목을 받아 봄

export async function onRequestGet({ request }) {
  return cached(request, 40, async () => {
    const pages = await Promise.all([1, 2, 3].map((p) => naver(`/api/stocks/theme?page=${p}&pageSize=100`)));
    const status = pages[0].marketStatus;
    const groups = pages.flatMap((p) => p.groups || []).map((g) => ({
      no: g.no, name: g.name, chg: num(g.changeRate), total: g.totalCount, rise: g.riseCount, fall: g.fallCount,
    }));
    groups.sort((a, b) => b.chg - a.chg);
    // 오늘 ±5% 넘게 움직인 종목(코스피·코스닥 상승·하락률 상위) → 테마별로 세기
    const movers = await Promise.all(["up", "down"].flatMap((dir) => ["KOSPI", "KOSDAQ"].map((m) =>
      naver(`/api/stocks/${dir}/${m}?page=1&pageSize=100`).then((d) => d.stocks || []).catch(() => []))));
    const cnt = { up: new Map(), down: new Map() };
    for (const s of movers.flat()) {
      const c = num(s.fluctuationsRatio), dir = c >= HOT ? "up" : c <= -HOT ? "down" : null;
      if (!dir || /스팩|우$|우B$/.test(s.stockName || "")) continue;
      for (const no of MEMBERS.codes[s.itemCode] || []) cnt[dir].set(no, (cnt[dir].get(no) || 0) + 1);
    }
    const hotNos = (dir) => [...cnt[dir].entries()].filter((x) => x[1] >= HOT_MIN).sort((a, b) => b[1] - a[1]).slice(0, HOT_MAX).map((x) => x[0]);
    const hotUp = new Set(hotNos("up")), hotDn = new Set(hotNos("down"));
    const want = [...new Set([...groups.slice(0, DETAIL_TOP), ...groups.slice(-DETAIL_TOP),
      ...groups.filter((g) => hotUp.has(g.no) || hotDn.has(g.no))])];
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
      const avg3 = (xs) => Math.round(xs.reduce((a, x) => a + x.chg, 0) / Math.max(1, xs.length) * 100) / 100;
      if (hotUp.has(g.no)) {
        const hs = stocks.filter((x) => x.chg >= HOT);
        if (hs.length >= HOT_MIN) { g.hot = hs.length; g.heat = avg3(hs.slice(0, 3)); g.leaders = stocks.slice(0, 4).map(({ code, name, chg }) => ({ code, name, chg })); }
      }
      if (hotDn.has(g.no)) {
        const cs = stocks.filter((x) => x.chg <= -HOT).reverse();
        if (cs.length >= HOT_MIN) { g.cold = cs.length; g.chill = avg3(cs.slice(0, 3)); }
      }
    }
    return { status, at: new Date().toISOString(), count: groups.length, groups };
  });
}
