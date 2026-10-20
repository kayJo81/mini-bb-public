// FinancialJuice 실시간 헤드라인 (공개 RSS, 최근 100건) — '⚡ 속보' 화면이 1분마다 읽음, 60초 캐시
//   GET /api/fj → { at, items: [{ ts, h, u }] }
// · 15분 수집본(briefings/fj.json, 30시간·한국어 번역 포함)과 화면에서 합쳐 최신분을 바로 보여 준다
const FJ = "https://www.financialjuice.com/feed.ashx?xy=1";
const HDR = { "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
  Accept: "application/rss+xml, application/xml, text/xml" };
const unent = (s) => s.replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, "$1").replace(/&lt;/g, "<").replace(/&gt;/g, ">")
  .replace(/&quot;/g, '"').replace(/&#39;|&apos;/g, "'").replace(/&amp;/g, "&").trim();
const tag = (x, t) => unent((x.match(new RegExp(`<${t}[^>]*>([\\s\\S]*?)</${t}>`)) || [])[1] || "");

export async function onRequestGet({ request, waitUntil }) {
  const cache = caches.default;
  const key = new Request(new URL("/api/fj", request.url).toString());
  const hit = await cache.match(key);
  if (hit) return hit;
  let items = [];
  try {
    const xml = await (await fetch(FJ, { headers: HDR })).text();
    items = [...xml.matchAll(/<item>([\s\S]*?)<\/item>/g)].map((m) => ({
      ts: Math.floor(Date.parse(tag(m[1], "pubDate")) / 1000),
      h: tag(m[1], "title").replace(/^FinancialJuice:\s*/, ""),
      u: tag(m[1], "link"),
    })).filter((x) => x.h && Number.isFinite(x.ts));
  } catch {
    items = [];
  }
  const res = Response.json({ at: Math.floor(Date.now() / 1000), items },
    { headers: { "cache-control": items.length ? "public, max-age=60" : "no-store" } });
  if (items.length) waitUntil(cache.put(key, res.clone()));
  return res;
}
