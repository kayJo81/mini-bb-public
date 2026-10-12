// 투자자별 수급 (코스피·코스닥): 개인 / 외국인 / 기관계 (= 순수기관 + 금융투자) — 다음 금융 시간대별 순매수, 30초 캐시
//   GET /api/flow → { KOSPI: { at, ind, frn, inst, pure, fin, detail:{…}, series:[[hhmm, ind, frn, inst], …] }, KOSDAQ: … }
//   단위: 억 원 · 순수기관 = 기관계 − 금융투자 (투신·사모·연기금·보험·은행·기타금융)
import { cached } from "../../lib/naver.js";

const HDR = {
  "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
  Referer: "https://finance.daum.net/domestic/investors",
  Accept: "application/json",
};
const eok = (v) => (v == null ? null : Math.round(Number(v) / 1e8));

async function daum(path) {
  const r = await fetch("https://finance.daum.net" + path, { headers: HDR, cf: { cacheTtl: 0 } });
  if (!r.ok) throw new Error(`daum ${r.status} ${path}`);
  return r.json();
}

async function market(m) {
  const [now, chart] = await Promise.all([
    daum(`/api/investor/${m}/times?market=${m}&page=1&perPage=1&details=true&pagination=true`),
    daum(`/api/charts/investors/${m}/times?limit=400&adjusted=true`).catch(() => ({ data: [] })),
  ]);
  const x = (now.data || [])[0];
  if (!x) throw new Error(`daum ${m}: 데이터 없음`);
  const d = x.details || {};
  const inst = eok(x.institutionStraightPurchasePrice), fin = eok(d.FINANCIAL_INVESTOR);
  return {
    at: x.date,
    ind: eok(x.individualStraightPurchasePrice),
    frn: eok(x.foreignStraightPurchasePrice),
    inst,
    fin,
    pure: inst != null && fin != null ? inst - fin : null,
    corp: eok(d.ETC_CORPORATION),
    detail: { 투신: eok(d.MUTUAL_FUND), 사모: eok(d.PRIVATE_EQUITY_FUND), 연기금: eok(d.PENSION_FUND), 보험: eok(d.INSURANCE_COMPANIES),
      은행: eok(d.BANK), 기타금융: eok(d.ETC_FINANCIAL_INSTITUTION) },
    // 오늘(마지막 날짜) 분 단위 흐름만
    series: (chart.data || []).filter((r) => r.date.slice(0, 10) === x.date.slice(0, 10))
      .map((r) => [r.time.slice(0, 5), eok(r.individualStraightPurchasePrice), eok(r.foreignStraightPurchasePrice), eok(r.institutionStraightPurchasePrice)]),
  };
}

export async function onRequestGet({ request }) {
  return cached(request, 30, async () => {
    const [KOSPI, KOSDAQ] = await Promise.all([market("KOSPI"), market("KOSDAQ")]);
    return { KOSPI, KOSDAQ };
  });
}
