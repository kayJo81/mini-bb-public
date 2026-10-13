// 주요 지표 (환율·달러지수·비트코인·금·금리) — 미국·한국 미니 블벅터 공통, 30초 캐시
//   GET /api/macro → { at, items: [{ k, name, sym, price, chg, pct, time, unit }] }
// · 네이버 증권 시장지표(원/달러·달러지수·금·미 10·30년·한국 10년) + 야후(달러/엔·비트코인)
const NAVER = { "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1",
  Referer: "https://m.stock.naver.com/", Accept: "application/json" };
const ITEMS = [
  { k: "usdjpy", name: "달러/엔", sym: "USD/JPY", y: "JPY=X", d: 2 },
  { k: "usdkrw", name: "달러/원", sym: "USD/KRW", n: "exchange&reutersCode=FX_USDKRW", d: 2 },
  { k: "dxy", name: "달러 지수", sym: "DXY", n: "exchange&reutersCode=.DXY", d: 2 },
  { k: "btc", name: "비트코인", sym: "BTC/USD", y: "BTC-USD", d: 0 },
  { k: "gold", name: "금", sym: "GC", n: "metals&reutersCode=GCcv1", d: 2 },
  { k: "us10", name: "미 10년물", sym: "US10Y", n: "bond&reutersCode=US10YT=RR", d: 3, unit: "%" },
  { k: "us30", name: "미 30년물", sym: "US30Y", n: "bond&reutersCode=US30YT=RR", d: 3, unit: "%" },
  { k: "kr10", name: "한국 10년물", sym: "KR10Y", n: "bond&reutersCode=KR10YT=RR", d: 3, unit: "%" },
];
const num = (s) => { const v = parseFloat(String(s ?? "").replace(/,/g, "")); return Number.isFinite(v) ? v : null; };

async function naver(q) {
  const r = await fetch(`https://m.stock.naver.com/front-api/marketIndex/productDetail?category=${q}`, { headers: NAVER });
  const d = (await r.json()).result || {};
  return { price: num(d.closePrice), chg: num(d.fluctuations), pct: num(d.fluctuationsRatio), time: d.localTradedAt || null };
}

async function yahoo(sym) {
  const r = await fetch(`https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(sym)}?interval=5m&range=1d`,
    { headers: { "User-Agent": "Mozilla/5.0" } });
  const m = (await r.json()).chart.result[0].meta;
  const price = m.regularMarketPrice, prev = m.chartPreviousClose ?? m.previousClose;
  return { price, chg: prev ? price - prev : null, pct: prev ? (price / prev - 1) * 100 : null,
    time: m.regularMarketTime ? new Date(m.regularMarketTime * 1000).toISOString() : null };
}

export async function onRequestGet({ request, waitUntil }) {
  const cache = caches.default;
  const key = new Request(new URL("/api/macro", request.url).toString());
  const hit = await cache.match(key);
  if (hit) return hit;
  const items = await Promise.all(ITEMS.map(async (it) => {
    try {
      const v = it.y ? await yahoo(it.y) : await naver(it.n);
      return { k: it.k, name: it.name, sym: it.sym, d: it.d, unit: it.unit || "", ...v };
    } catch {
      return { k: it.k, name: it.name, sym: it.sym, d: it.d, unit: it.unit || "", price: null };
    }
  }));
  const res = Response.json({ at: new Date().toISOString(), items },
    { headers: { "cache-control": "public, max-age=30", "access-control-allow-origin": "*" } });
  waitUntil(cache.put(key, res.clone()));
  return res;
}
