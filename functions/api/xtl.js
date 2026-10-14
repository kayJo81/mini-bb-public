// X(트위터) 공개 임베드 타임라인 읽기 — 허용한 계정만 (랩 테슬라 중국 주간 보험등록용), 30분 캐시
//   GET /api/xtl?u=Tslachan → { tweets: [{ id, at, text }] }
const ALLOW = new Set(["tslachan"]);

export async function onRequestGet({ request, waitUntil }) {
  const u = (new URL(request.url).searchParams.get("u") || "").toLowerCase();
  if (!ALLOW.has(u)) return new Response("not allowed", { status: 403 });
  const cache = caches.default, key = new Request(`https://cache.local/xtl/${u}`);
  const hit = await cache.match(key);
  if (hit) return hit;
  const r = await fetch(`https://syndication.twitter.com/srv/timeline-profile/screen-name/${u}`, {
    headers: { "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36" } });
  const s = await r.text();
  const m = s.match(/<script id="__NEXT_DATA__" type="application\/json">([\s\S]*?)<\/script>/);
  if (!m) return Response.json({ error: `x ${r.status}`, tweets: [] }, { status: 502 });
  const E = JSON.parse(m[1]).props?.pageProps?.timeline?.entries || [];
  const tweets = E.map((e) => e.content?.tweet).filter(Boolean)
    .map((t) => ({ id: t.id_str, at: t.created_at, text: t.full_text || t.text || "" }));
  const res = Response.json({ tweets }, { headers: { "cache-control": "public, max-age=1800" } });
  waitUntil(cache.put(key, res.clone()));
  return res;
}
