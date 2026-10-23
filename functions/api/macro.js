// 주요 지표 (환율·달러지수·비트코인·금·금리) — 미국·한국 미니 블벅터 공통, 30초 캐시
//   GET /api/macro → { at, items: [{ k, name, sym, price, chg, pct, time, unit, s }] }   (s = 오늘 추세선용 가격 배열, 최대 36점)
// · 네이버 증권 시장지표(원/달러·달러지수·금·미 10·30년·한국 10년) + 야후(달러/엔·비트코인)
const NAVER = { "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1",
  Referer: "https://m.stock.naver.com/", Accept: "application/json" };
const ITEMS = [
  { k: "usdjpy", name: "달러/엔", sym: "USD/JPY", y: "JPY=X", d: 2 },
  { k: "usdkrw", name: "달러/원", sym: "USD/KRW", n: "exchange&reutersCode=FX_USDKRW", ys: "KRW=X", d: 2 },
  { k: "dxy", name: "달러 지수", sym: "DXY", n: "exchange&reutersCode=.DXY", ys: ["DX-Y.NYB", "DX=F"], d: 2 },
  { k: "btc", name: "비트코인", sym: "BTC/USD", y: "BTC-USD", d: 0 },
  { k: "gold", name: "금", sym: "GC", n: "metals&reutersCode=GCcv1", ys: ["GC=F", "XAUUSD=X"], d: 2 },
  { k: "us10", name: "미 10년물", sym: "US10Y", n: "bond&reutersCode=US10YT=RR", ys: "^TNX", d: 3, unit: "%" },
  { k: "us30", name: "미 30년물", sym: "US30Y", n: "bond&reutersCode=US30YT=RR", ys: "^TYX", d: 3, unit: "%" },
  { k: "kr10", name: "한국 10년물", sym: "KR10Y", n: "bond&reutersCode=KR10YT=RR", d: 3, unit: "%" },
];
const num = (s) => { const v = parseFloat(String(s ?? "").replace(/,/g, "")); return Number.isFinite(v) ? v : null; };

async function naver(q) {
  const r = await fetch(`https://m.stock.naver.com/front-api/marketIndex/productDetail?category=${q}`, { headers: NAVER });
  const d = (await r.json()).result || {};
  return { price: num(d.closePrice), chg: num(d.fluctuations), pct: num(d.fluctuationsRatio), time: d.localTradedAt || null };
}

// 추세선: 오늘 가격 흐름을 최대 36점으로 줄임 (모양만 보여 주는 용도 — 표시 가격은 위 시세를 씀)
function thin(closes) {
  const v = closes.filter((x) => Number.isFinite(x));
  if (v.length < 4) return null;
  const step = Math.max(1, Math.ceil(v.length / 36)), out = [];
  for (let i = v.length - 1; i >= 0; i -= step) out.unshift(v[i]);   // 마지막(현재) 값은 항상 포함
  return out.map((x) => +x.toPrecision(6));
}
// 서버(클라우드플레어)에서 야후가 일부 종목·주소를 막는 일이 있어, 주소 두 곳을 차례로 시도하고 실패 이유를 se 로 남김
const YH = ["query1.finance.yahoo.com", "query2.finance.yahoo.com"];
async function yahooChart(sym) {
  let err = "";
  for (const h of YH) {
    try {
      const r = await fetch(`https://${h}/v8/finance/chart/${encodeURIComponent(sym)}?interval=15m&range=1d`,
        { headers: { "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36", Accept: "application/json" } });
      if (!r.ok) { err = `${sym} ${h.slice(0, 6)} HTTP ${r.status}`; continue; }
      const res = (await r.json()).chart.result[0];
      return { meta: res.meta, s: thin(res.indicators.quote[0].close || []), err: "" };
    } catch (e) { err = `${sym} ${h.slice(0, 6)} ${String(e).slice(0, 60)}`; }
  }
  throw new Error(err);
}

async function yahoo(sym) {
  const { meta: m, s } = await yahooChart(sym);
  const price = m.regularMarketPrice, prev = m.chartPreviousClose ?? m.previousClose;
  return { price, chg: prev ? price - prev : null, pct: prev ? (price / prev - 1) * 100 : null,
    time: m.regularMarketTime ? new Date(m.regularMarketTime * 1000).toISOString() : null, s };
}

export async function onRequestGet({ request, waitUntil }) {
  const cache = caches.default;
  const key = new Request(new URL("/api/macro", request.url).toString());
  const hit = await cache.match(key);
  if (hit) return hit;
  const items = await Promise.all(ITEMS.map(async (it) => {
    try {
      let se = "";
      const series = async (ys) => {
        for (const y of [].concat(ys)) {
          try { const x = await yahooChart(y); if (x.s) return x.s; se = `${y} 점 부족`; } catch (e) { se = String(e.message || e); }
        }
        return null;
      };
      const [v, s] = await Promise.all([it.y ? yahoo(it.y) : naver(it.n), it.ys ? series(it.ys) : null]);
      return { k: it.k, name: it.name, sym: it.sym, d: it.d, unit: it.unit || "", ...v, ...(s ? { s } : it.ys ? { se } : {}) };
    } catch {
      return { k: it.k, name: it.name, sym: it.sym, d: it.d, unit: it.unit || "", price: null };
    }
  }));
  const res = Response.json({ at: new Date().toISOString(), items },
    { headers: { "cache-control": "public, max-age=30", "access-control-allow-origin": "*" } });
  waitUntil(cache.put(key, res.clone()));
  return res;
}
