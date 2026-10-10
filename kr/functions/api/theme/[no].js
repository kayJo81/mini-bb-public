// 테마 상세: 설명 + 구성 종목 시세 + 종목별 편입 사유 (40초 캐시)
import { cached, naver, num, slimStock } from "../../../lib/naver.js";

export async function onRequestGet({ request, params }) {
  const no = String(params.no).replace(/\D/g, "");
  return cached(request, 40, async () => {
    const d = await naver(`/api/stocks/theme/${no}?page=1&pageSize=100`);
    const info = d.themeItemInfoMap || {};
    const stocks = (d.stocks || []).map((s) => slimStock(s, info[s.itemCode])).sort((a, b) => b.chg - a.chg);
    const g = d.groupInfo || {};
    return { no: g.no, name: g.name, chg: num(g.changeRate), rise: g.riseCount, fall: g.fallCount, total: g.totalCount,
             desc: d.themeDescription || "", status: d.marketStatus, stocks };
  });
}
