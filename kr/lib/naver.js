// 네이버 증권 모바일 API 공통: 브라우저처럼 요청하고, 결과는 Cloudflare 엣지 캐시에 잠깐 보관
// (방문자가 많아도 네이버에는 캐시 시간당 한 번만 요청)
const HDR = {
  "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
  Referer: "https://m.stock.naver.com/",
  Accept: "application/json",
};

export async function naver(path) {
  const r = await fetch("https://m.stock.naver.com" + path, { headers: HDR, cf: { cacheTtl: 0 } });
  if (!r.ok) throw new Error(`naver ${r.status} ${path}`);
  return r.json();
}

export async function cached(request, ttl, build) {
  const cache = caches.default;
  const key = new Request(new URL(request.url).toString(), { method: "GET" });
  const hit = await cache.match(key);
  if (hit) return hit;
  let body, status = 200;
  try {
    body = JSON.stringify(await build());
  } catch (ex) {
    body = JSON.stringify({ error: String(ex) });
    status = 502;
  }
  const res = new Response(body, {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": `public, max-age=${ttl}` },
  });
  if (status === 200) await cache.put(key, res.clone());
  return res;
}

export const num = (s) => {
  const v = parseFloat(String(s ?? "").replace(/,/g, ""));
  return Number.isFinite(v) ? v : null;
};

export function slimStock(s, info) {
  return {
    code: s.itemCode, name: s.stockName, market: s.sosok === "0" ? "코스피" : "코스닥",
    price: num(s.closePrice), chg: num(s.fluctuationsRatio), value: num(s.accumulatedTradingValue), // 거래대금(백만원)
    cap: num(s.marketValue), // 시가총액(억원)
    up: s.compareToPreviousPrice?.name, info: info || "",
  };
}
