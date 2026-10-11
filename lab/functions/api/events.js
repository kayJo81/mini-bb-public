// 미니 블벅 랩 · 주간 전략에 직접 넣는 일정 (Cloudflare Pages Function + KV)
//   GET    /api/events           → { events: [...], editable }
//   POST   /api/events  {date, end?, cat, text, en?, star?, recur?}  (헤더 x-edit-key 필요)
//   DELETE /api/events?id=…      (헤더 x-edit-key 필요)
// 주간 전략(scripts/weekly_pdf.py)이 매주 GET 으로 읽어 PDF·랩 캘린더에 넣음
// 편집 키: Pages 비밀 값 LAB_EDIT_KEY (GitHub Secrets 의 LAB_EDIT_KEY 를 lab.yml 이 넣어 줌)
const CATS = ["매크로", "정치", "산업 및 기업", "실적 발표"];
const json = (body, status = 200) =>
  Response.json(body, { status, headers: { "cache-control": "no-store", "access-control-allow-origin": "*" } });

async function load(kv) {
  try {
    return JSON.parse((await kv.get("events")) || "[]");
  } catch {
    return [];
  }
}

function denied(request, env) {
  if (!env.EVENTS) return json({ error: "저장소(KV)가 연결되지 않았어요" }, 503);
  if (!env.LAB_EDIT_KEY) return json({ error: "편집 키(LAB_EDIT_KEY)가 아직 설정되지 않았어요" }, 403);
  if (request.headers.get("x-edit-key") !== env.LAB_EDIT_KEY) return json({ error: "편집 키가 맞지 않아요" }, 403);
  return null;
}

const day = (s) => /^\d{4}-\d{2}-\d{2}$/.test(s || "");

export async function onRequestGet({ env }) {
  const events = env.EVENTS ? await load(env.EVENTS) : [];
  return json({ events, editable: !!(env.EVENTS && env.LAB_EDIT_KEY) });
}

export async function onRequestPost({ request, env }) {
  const no = denied(request, env);
  if (no) return no;
  let b;
  try {
    b = await request.json();
  } catch {
    return json({ error: "잘못된 요청" }, 400);
  }
  const text = String(b.text || "").trim().slice(0, 120);
  if (!day(b.date) || !text) return json({ error: "날짜와 내용을 넣어 주세요" }, 400);
  const end = day(b.end) && b.end > b.date ? b.end : "";
  const ev = {
    id: crypto.randomUUID().slice(0, 8),
    date: b.date,
    end,
    cat: CATS.includes(b.cat) ? b.cat : "산업 및 기업",
    text,
    en: String(b.en || "").trim().slice(0, 120),
    star: !!b.star,
    recur: !!b.recur,
    added: new Date().toISOString().slice(0, 10),
  };
  const all = await load(env.EVENTS);
  all.push(ev);
  all.sort((a, b) => a.date.localeCompare(b.date));
  await env.EVENTS.put("events", JSON.stringify(all.slice(-500)));
  return json({ ok: true, event: ev });
}

export async function onRequestDelete({ request, env }) {
  const no = denied(request, env);
  if (no) return no;
  const id = new URL(request.url).searchParams.get("id");
  const all = await load(env.EVENTS);
  const left = all.filter((e) => e.id !== id);
  await env.EVENTS.put("events", JSON.stringify(left));
  return json({ ok: true, removed: all.length - left.length });
}
