// 구글 뉴스 RSS 예비 경로 (GitHub Actions 서버가 구글 뉴스에 막힐 때 수집기가 이 경로로 다시 요청)
//   GET /api/gnews?q=…&hl=…&gl=…&ceid=…  → news.google.com/rss/search 결과(XML) 그대로, 5분 캐시
// · 구글 뉴스 검색 RSS 만 대신 받아 줌 (다른 주소는 받지 않음)
const OK = /^[A-Za-z]{2}(-[A-Za-z]{2})?$|^[A-Z]{2}:[a-z]{2}$/;

export async function onRequestGet({ request, waitUntil }) {
  const u = new URL(request.url);
  const q = u.searchParams.get("q") || "";
  const hl = u.searchParams.get("hl") || "en-US", gl = u.searchParams.get("gl") || "US", ceid = u.searchParams.get("ceid") || "US:en";
  if (!q || q.length > 600 || ![hl, gl, ceid].every((x) => OK.test(x))) return new Response("bad request", { status: 400 });
  const cache = caches.default;
  const key = new Request(`https://cache.local/gnews?${new URLSearchParams({ q, hl, gl, ceid })}`);
  const hit = await cache.match(key);
  if (hit) return hit;
  const r = await fetch(`https://news.google.com/rss/search?${new URLSearchParams({ q, hl, gl, ceid })}`, {
    headers: { "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
      "accept-language": hl },
  });
  const body = await r.text();
  if (!r.ok || !body.trimStart().startsWith("<?xml")) return new Response(`upstream ${r.status}`, { status: 502 });
  const res = new Response(body, { headers: { "content-type": "application/xml; charset=utf-8", "cache-control": "public, max-age=300" } });
  waitUntil(cache.put(key, res.clone()));
  return res;
}
