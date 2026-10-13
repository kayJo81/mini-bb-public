// 전체 테마(약 260개) 등락률 + 등락률 상·하위 테마의 구성 종목 (40초 캐시)
// + 급등주가 몰린 테마: 테마 평균이 낮아도 ±5% 넘게 움직인 종목이 2개 이상이면 (예: 남북경협 평균 −1%인데 양지사 +11%·다스코 +7%)
//   g.hot = 그런 종목 수, g.heat = 움직인 상위 3종목 평균 등락률 (하락 쪽은 g.cold / g.chill)
import { cached, naver, num, slimStock } from "../../lib/naver.js";
import MEMBERS from "../../lib/theme_members.json";

const DETAIL_TOP = 15;   // 상위·하위 각각 이만큼은 구성 종목까지
const HOT = 5, HOT_MIN = 2;   // ±5% 종목 2개 이상인 테마
// Cloudflare 무료 요청 한도(외부 요청 50개): 테마 목록 3 + 급등락 4 + 구성 종목 30 + 캐시 2 = 39

export async function onRequestGet({ request }) {
  return cached(request, 40, async () => {
    const pages = await Promise.all([1, 2, 3].map((p) => naver(`/api/stocks/theme?page=${p}&pageSize=100`)));
    const status = pages[0].marketStatus;
    const groups = pages.flatMap((p) => p.groups || []).map((g) => ({
      no: g.no, name: g.name, chg: num(g.changeRate), total: g.totalCount, rise: g.riseCount, fall: g.fallCount,
    }));
    groups.sort((a, b) => b.chg - a.chg);
    // 오늘 ±5% 넘게 움직인 종목(코스피·코스닥 상승·하락률 상위) → 테마별로 모음 (구성 종목을 따로 받지 않아도 됨)
    const movers = await Promise.all(["up", "down"].flatMap((dir) => ["KOSPI", "KOSDAQ"].map((m) =>
      naver(`/api/stocks/${dir}/${m}?page=1&pageSize=100`).then((d) => d.stocks || []).catch(() => []))));
    const hit = { up: new Map(), down: new Map() }, seen = new Set();
    for (const s of movers.flat()) {
      const c = num(s.fluctuationsRatio), dir = c >= HOT ? "up" : c <= -HOT ? "down" : null;
      if (!dir || seen.has(s.itemCode) || /스팩|우$|우B$/.test(s.stockName || "")) continue;
      seen.add(s.itemCode);
      for (const no of MEMBERS.codes[s.itemCode] || []) {
        if (!hit[dir].has(no)) hit[dir].set(no, []);
        hit[dir].get(no).push({ code: s.itemCode, name: s.stockName, chg: c });
      }
    }
    const avg3 = (xs) => Math.round(xs.slice(0, 3).reduce((a, x) => a + x.chg, 0) / Math.min(3, xs.length) * 100) / 100;
    for (const g of groups) {
      const u = (hit.up.get(g.no) || []).sort((a, b) => b.chg - a.chg), d = (hit.down.get(g.no) || []).sort((a, b) => a.chg - b.chg);
      if (u.length >= HOT_MIN) { g.hot = u.length; g.heat = avg3(u); g.hotStocks = u.slice(0, 4); }
      if (d.length >= HOT_MIN) { g.cold = d.length; g.chill = avg3(d); g.coldStocks = d.slice(0, 4); }
    }
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
