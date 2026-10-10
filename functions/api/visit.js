// 미니 블벅터 방문자 수 (Cloudflare Pages Function + KV)
//   GET  /api/visit → { today, total }
//   POST /api/visit → 1 증가 후 { today, total }  (화면에서 브라우저당 하루 1번만 호출)
// KV 무료 한도: 쓰기 1,000회/일 → 방문 1회에 쓰기 2회 = 하루 약 500명까지 집계
const kstDate = () => new Date(Date.now() + 9 * 3600e3).toISOString().slice(0, 10);
const json = (body) => Response.json(body, { headers: { "cache-control": "no-store" } });

async function read(kv, day) {
  const [today, total] = await Promise.all([kv.get(`day:${day}`), kv.get("total")]);
  return { today: Number(today) || 0, total: Number(total) || 0 };
}

export async function onRequestGet({ env }) {
  if (!env.VISITS) return json({ today: null, total: null });
  return json(await read(env.VISITS, kstDate()));
}

export async function onRequestPost({ env }) {
  if (!env.VISITS) return json({ today: null, total: null });
  const day = kstDate();
  const c = await read(env.VISITS, day);
  c.today += 1;
  c.total += 1;
  await Promise.all([
    env.VISITS.put(`day:${day}`, String(c.today), { expirationTtl: 60 * 60 * 24 * 40 }),
    env.VISITS.put("total", String(c.total)),
  ]);
  return json(c);
}
