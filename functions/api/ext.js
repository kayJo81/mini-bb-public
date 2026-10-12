// 미국 주식 장외 시세 (프리마켓·애프터마켓·나이트) — Cloudflare Pages Function
//   GET /api/ext?s=NVDA,AAPL,…  (최대 60개)
//   → { session: "pre"|"regular"|"after"|"night"|"closed", et: "…", quotes: [{ s, price, base, chg, time, src }] }
// · 1순위 로빈후드 24시간 시세(나이트는 Blue Ocean 체결가), 안 되면 토스증권 주간거래 시세
// · base = 직전 정규장 종가, chg = (장외 가격 / base - 1) × 100
// · 같은 요청은 20초 캐시 (Cache API)
const json = (body, ttl = 20) =>
  Response.json(body, { headers: { "cache-control": `public, max-age=${ttl}`, "access-control-allow-origin": "*" } });
const UA = { "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36" };

// 뉴욕 시각으로 지금 세션
function etNow() {
  const p = Object.fromEntries(
    new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hour12: false, weekday: "short", year: "numeric",
      month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).formatToParts(new Date()).map((x) => [x.type, x.value]));
  const hm = (Number(p.hour) % 24) * 60 + Number(p.minute);
  const wd = p.weekday; // Mon … Sun
  const weekday = !["Sat", "Sun"].includes(wd);
  let session = "closed";
  if (weekday && hm >= 240 && hm < 570) session = "pre";
  else if (weekday && hm >= 570 && hm < 960) session = "regular";
  else if (weekday && hm >= 960 && hm < 1200) session = "after";
  // 나이트: 일~목 20:00 → 다음 날 04:00
  else if ((hm >= 1200 && ["Sun", "Mon", "Tue", "Wed", "Thu"].includes(wd)) || (hm < 240 && ["Mon", "Tue", "Wed", "Thu", "Fri"].includes(wd)))
    session = "night";
  // 가장 최근 정규장 날짜 (공휴일은 따지지 않음)
  const d = new Date(Date.UTC(+p.year, +p.month - 1, +p.day));
  const back = (n) => d.setUTCDate(d.getUTCDate() - n);
  if (!(weekday && hm >= 960)) back(1);
  while ([0, 6].includes(d.getUTCDay())) back(1);
  return { session, last: d.toISOString().slice(0, 10), et: `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute}` };
}

const num = (v) => (v == null || v === "" ? null : Number(v));

async function robinhood(syms, last) {
  const r = await fetch(`https://api.robinhood.com/marketdata/quotes/?bounds=24_5&symbols=${syms.join(",")}`, { headers: UA });
  if (!r.ok) throw new Error(`robinhood ${r.status}`);
  const out = [];
  for (const q of (await r.json()).results || []) {
    if (!q) continue;
    // 직전 정규장 종가: previous_close 가 그날 것이면 그대로, 아니면(장 마감 뒤 아직 안 바뀜) 정규장 마지막 체결가
    const base = q.previous_close_date === last ? num(q.previous_close) : num(q.last_trade_price) ?? num(q.previous_close);
    const price = num(q.last_non_reg_trade_price) ?? num(q.last_extended_hours_trade_price);
    out.push({ s: q.symbol, price, base, chg: price && base ? (price / base - 1) * 100 : null,
      time: q.venue_last_non_reg_trade_time || null, bid: num(q.bid_price), ask: num(q.ask_price),
      src: "로빈후드" });
  }
  return out;
}

async function tossCode(sym, cache) {
  const key = new Request(`https://cache.local/toss-code/${sym}`);
  const hit = await cache.match(key);
  if (hit) return hit.text();
  const r = await fetch("https://wts-info-api.tossinvest.com/api/v3/search-all/wts-auto-complete", {
    method: "POST", headers: { ...UA, "content-type": "application/json" },
    body: JSON.stringify({ query: sym, sections: [{ type: "PRODUCT", option: { addIntegratedSearchResult: true } }] }) });
  const items = ((await r.json()).result || []).flatMap((x) => (x.data && x.data.items) || []);
  const it = items.find((x) => (x.symbol || "").toUpperCase() === sym);
  const code = it ? it.productCode : "";
  if (code) await cache.put(key, new Response(code, { headers: { "cache-control": "max-age=2592000" } }));
  return code;
}

async function toss(syms, cache) {
  const codes = {};
  for (const s of syms.slice(0, 30)) {
    try {
      const c = await tossCode(s, cache);
      if (c) codes[c] = s;
    } catch {}
  }
  if (!Object.keys(codes).length) return [];
  const r = await fetch(`https://wts-info-api.tossinvest.com/api/v3/stock-prices?meta=true&productCodes=${Object.keys(codes).join(",")}`,
    { headers: UA });
  return ((await r.json()).result || []).map((q) => {
    const price = num(q.close), base = num(q.base);
    return { s: codes[q.productCode], price, base, chg: price && base ? (price / base - 1) * 100 : null, time: null, src: "토스증권" };
  });
}

export async function onRequestGet({ request, waitUntil }) {
  const url = new URL(request.url);
  const syms = [...new Set((url.searchParams.get("s") || "").toUpperCase().split(",").map((x) => x.trim())
    .filter((x) => /^[A-Z][A-Z.\-]{0,9}$/.test(x)))].slice(0, 60);
  if (!syms.length) return json({ error: "s=티커,티커 를 넣어 주세요" });
  const cache = caches.default;
  const key = new Request(`https://cache.local/ext/${syms.join(",")}`);
  const hit = await cache.match(key);
  if (hit) return hit;
  const now = etNow();
  let quotes = [], err = "";
  try {
    quotes = await robinhood(syms, now.last);
  } catch (e) {
    err = String(e.message || e);
  }
  if (!quotes.some((q) => q.price)) {
    try {
      quotes = await toss(syms, cache);
    } catch (e) {
      err += ` / toss ${e.message || e}`;
    }
  }
  const res = json({ session: now.session, et: now.et, quotes, error: quotes.length ? undefined : err });
  if (quotes.length) waitUntil(cache.put(key, res.clone()));
  return res;
}
