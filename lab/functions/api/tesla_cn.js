// 테슬라 중국 주간 보험등록 — 랩에서 직접 넣은 주 (CnEVPost 주간 보도가 끊긴 2025년 10월 이후용)
//   GET    /api/tesla_cn                → { weeks: [{ week_end, tesla, note }], editable }
//   POST   /api/tesla_cn  { rows: [{ week_end:"YYYY-MM-DD", tesla:12345, note? }] }   (헤더 x-edit-key, 같은 주는 덮어씀)
//   DELETE /api/tesla_cn?week_end=YYYY-MM-DD                                         (헤더 x-edit-key)
// 저장: 주간 전략 일정과 같은 KV(EVENTS)의 'tesla_cn' 키 · 편집 키: LAB_EDIT_KEY
const json = (b, status = 200) => Response.json(b, { status, headers: { "cache-control": "no-store" } });
const day = (s) => /^\d{4}-\d{2}-\d{2}$/.test(s || "") && !isNaN(Date.parse(s));

async function load(kv) {
  try { return JSON.parse((await kv.get("tesla_cn")) || "[]"); } catch { return []; }
}
function denied(request, env) {
  if (!env.EVENTS) return json({ error: "저장소(KV)가 연결되지 않았어요" }, 503);
  if (!env.LAB_EDIT_KEY) return json({ error: "편집 키(LAB_EDIT_KEY)가 아직 설정되지 않았어요" }, 403);
  if (request.headers.get("x-edit-key") !== env.LAB_EDIT_KEY) return json({ error: "편집 키가 맞지 않아요" }, 403);
  return null;
}

export async function onRequestGet({ env }) {
  return json({ weeks: env.EVENTS ? await load(env.EVENTS) : [], editable: !!(env.EVENTS && env.LAB_EDIT_KEY) });
}

export async function onRequestPost({ request, env }) {
  const no = denied(request, env);
  if (no) return no;
  let b;
  try { b = await request.json(); } catch { return json({ error: "잘못된 요청" }, 400); }
  const rows = (Array.isArray(b.rows) ? b.rows : [b]).slice(0, 200);
  const all = new Map((await load(env.EVENTS)).map((w) => [w.week_end, w]));
  let n = 0;
  for (const r of rows) {
    const v = Math.round(Number(String(r.tesla ?? "").replace(/,/g, "")));
    if (!day(r.week_end) || !(v > 0 && v < 100000)) continue;
    all.set(r.week_end, { week_end: r.week_end, tesla: v, note: String(r.note || "").slice(0, 80) });
    n++;
  }
  if (!n) return json({ error: "날짜(YYYY-MM-DD)와 대수를 확인해 주세요" }, 400);
  const out = [...all.values()].sort((a, b) => a.week_end.localeCompare(b.week_end));
  await env.EVENTS.put("tesla_cn", JSON.stringify(out));
  return json({ ok: true, saved: n });
}

export async function onRequestDelete({ request, env }) {
  const no = denied(request, env);
  if (no) return no;
  const d = new URL(request.url).searchParams.get("week_end");
  const all = await load(env.EVENTS), left = all.filter((w) => w.week_end !== d);
  await env.EVENTS.put("tesla_cn", JSON.stringify(left));
  return json({ ok: true, removed: all.length - left.length });
}
