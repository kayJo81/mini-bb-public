// 미니 블벅터 헤드라인 한국어 번역 (Cloudflare Workers AI + Cache API)
//   POST /api/translate  { texts: [영문, …] }  →  { ko: [한국어 | null, …] }
// · 금융 용어 정확도를 위해 LLM 에 용어집과 함께 한 번에 묶어 번역 (계정에서 쓸 수 있는 첫 모델 사용)
//   → 실패하면 번역 전용 모델(m2m100)로 한 줄씩 대체
// · 화면에 보이는 카드만 20개씩 요청 → Workers AI 무료 한도(하루 1만 뉴런) 안에서 사용
// · 같은 문장은 30일간 캐시해서 다시 번역하지 않음 (KV 쓰기 한도를 쓰지 않도록 Cache API 사용)
// · 하루 무료 한도(1만 뉴런)를 다 쓰면 그날(UTC)은 LLM을 건너뛰고 구글 번역으로 바로 번역 (한도 소진 표시는 Cache API)
// · 그래도 실패하면 null → 화면은 원문을 보여 주고 잠시 뒤 다시 요청
// 앞에서부터 시도해 처음 성공한 모델을 쓴다 (gemma 는 이 계정에서 사용 불가 5018)
const LLMS = [
  "@cf/mistralai/mistral-small-3.1-24b-instruct",
  "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
  "@cf/meta/llama-3.1-8b-instruct-fast",
];
const GT = "https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=ko&dt=t";
// 구글 기계번역에서 자주 틀리는 금융 용어 보정 (scripts/translate_ko.py 의 FIXES 와 같게)
const FIXES = [["시장 매출", "시가총액"], ["시가 총액", "시가총액"], ["모든 시간 최고", "사상 최고치"], ["사상 최고", "사상 최고치"],
  ["사상 최고치치", "사상 최고치"], ["머스크 씨", "머스크"], ["마스크", "머스크"], ["연방 준비 은행", "연준"],
  ["연방준비제도이사회", "연준"], ["연방 준비 제도", "연준"], ["중간고사", "중간선거"], ["Fed의 ", "연준 "], ["Fed ", "연준 "]];
const fix = (s) => FIXES.reduce((t, [a, b]) => t.split(a).join(b), s).trim();
const MAX_BATCH = 25;
const CACHE_VER = "ko8";

const SYSTEM = `You translate English or Chinese financial news headlines into natural Korean news-headline style.
Rules:
- Keep numbers, currencies, percentages and tickers exactly (e.g. $6 trillion → 6조 달러, 4% → 4%).
- Keep company/product names in their common Korean form or in English if unsure (Nvidia→엔비디아, Tesla→테슬라, TSMC, OpenAI→오픈AI, Anthropic→앤트로픽).
- Chinese names: 马斯克→머스크, 特朗普→트럼프, 黄仁勋→젠슨 황, 苏姿丰→리사 수, 奥特曼→올트먼, 英伟达→엔비디아, 台积电→TSMC,
  特斯拉→테슬라, 微软→마이크로소프트, 谷歌→구글, 苹果→애플, 亚马逊→아마존, 美光→마이크론, 海力士→SK하이닉스, 三星→삼성,
  英特尔→인텔, 比特币→비트코인, 以太坊→이더리움, 币安→바이낸스, 稳定币→스테이블코인, 美联储→연준.
- Glossary: market cap→시가총액, all-time high/record high→사상 최고치, shares/stock→주가 or 주식, earnings→실적, guidance→가이던스,
  CPI→소비자물가지수(CPI), nonfarm payrolls→비농업 고용, jobless claims→신규 실업수당 청구, rate cut/hike→금리 인하/인상,
  hawkish/dovish→매파/비둘기파, FOMC minutes→FOMC 의사록, Treasury yields→미 국채 금리, basis points→bp, Fed→연준,
  BOJ→일본은행, ECB→유럽중앙은행, gilts→영국 국채, Houthi→후티, Strait of Hormuz→호르무즈 해협, White House→백악관,
  beats estimates→예상치 상회, misses→예상치 하회, upgrade/downgrade→투자의견 상향/하향, price target→목표주가, IPO→기업공개(IPO),
  stake→지분, acquisition→인수, layoffs→감원, tariff→관세, data center→데이터센터, chip→반도체/칩, stablecoin→스테이블코인.
- "raises at $X" / "at a $X valuation" means the company's VALUATION is $X, not the amount raised:
  "OKX Raises at $25 Billion" → "OKX, 기업가치 250억 달러로 투자 유치". Only "raises $X" means the amount raised.
- Analyst ratings: Outperform→시장수익률 상회, Overweight→비중확대, Buy→매수, Neutral/Hold→중립, Underperform→시장수익률 하회.
- Translate ONLY what is written. Never add titles, roles, reasons or context that are not in the headline
  (e.g. do not add "테슬라 CEO" if the headline only says "Musk").
- Keep EVERY person, company and number that appears in the headline, and nothing else.
  Example: "Apple slips after Buffett trims stake" → "버핏 지분 축소에 애플 하락" (not "애플 최대주주…").
- Do not explain.
Return ONLY a JSON array of Korean strings, same order and same length as the input array.`;

async function cacheKey(text) {
  const buf = await crypto.subtle.digest("SHA-1", new TextEncoder().encode(text));
  const hex = [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
  return new Request(`https://translate.cache/${CACHE_VER}/${hex}`);
}

async function llmBatch(env, texts) {
  const errs = [];
  for (const model of LLMS) {
    try {
      return { model, ko: await llmTry(env, model, texts) };
    } catch (e) {
      errs.push(`${model.split("/").pop()}: ${String(e && e.message || e).slice(0, 120)}`);
    }
  }
  throw new Error(errs.join(" | "));
}

async function llmTry(env, model, texts) {
  const r = await env.AI.run(model, {
    messages: [
      { role: "system", content: SYSTEM },
      { role: "user", content: JSON.stringify(texts) },
    ],
    max_tokens: 3000,
    temperature: 0,
  });
  let out = (r && (r.response ?? r.result?.response)) || "";
  if (typeof out !== "string") out = JSON.stringify(out);
  out = out.replace(/^```(?:json)?\s*/i, "").replace(/```\s*$/, "");
  const arr = JSON.parse(out.slice(out.indexOf("["), out.lastIndexOf("]") + 1));
  if (!Array.isArray(arr) || arr.length !== texts.length) throw new Error("length mismatch");
  return arr.map((s) => (typeof s === "string" && s.trim()) || null);
}

// 구글 번역: 줄바꿈으로 묶어 한 번에 — 줄 수가 어긋나면 한 줄씩
async function gtBatch(texts) {
  const one = async (q) => {
    const r = await fetch(GT, { method: "POST", headers: { "content-type": "application/x-www-form-urlencoded" },
      body: "q=" + encodeURIComponent(q) });
    if (!r.ok) throw new Error("google " + r.status);
    return ((await r.json())[0] || []).map((seg) => (seg && seg[0]) || "").join("");
  };
  try {
    const parts = (await one(texts.map((t) => t.replace(/\n/g, " ")).join("\n"))).split("\n");
    if (parts.length === texts.length) return parts.map((p) => (p.trim() ? fix(p) : null));
  } catch { /* 아래에서 한 줄씩 */ }
  return Promise.all(texts.map((t) => one(t).then((x) => fix(x) || null).catch(() => null)));
}

const dayKey = () => new Request(`https://translate.cache/quota/${new Date().toISOString().slice(0, 10)}`);

export async function onRequestPost({ request, env, waitUntil }) {
  let body;
  try { body = await request.json(); } catch { body = {}; }
  if (!Array.isArray(body.texts)) return Response.json({ ko: [] }, { status: 400 });
  const texts = body.texts.slice(0, MAX_BATCH).map((t) => String(t).slice(0, 300));
  const cache = caches.default;

  const keys = await Promise.all(texts.map(cacheKey));
  const ko = await Promise.all(keys.map(async (k) => { const h = await cache.match(k); return h ? h.text() : null; }));
  const miss = texts.map((_, i) => i).filter((i) => ko[i] == null);

  let err = null, model = null;
  if (miss.length) {
    let res = [];
    const spent = !env.AI || await cache.match(dayKey());       // 오늘 LLM 한도를 이미 다 썼으면 바로 구글로
    if (!spent) {
      try { ({ model, ko: res } = await llmBatch(env, miss.map((i) => texts[i]))); }
      catch (e) {
        err = String(e && e.message || e).slice(0, 500);
        if (/4006|daily free allocation/.test(err))
          waitUntil(cache.put(dayKey(), new Response("1", { headers: { "cache-control": "max-age=86400" } })));
      }
    }
    const left = miss.filter((i, j) => !res[j]);
    const g = left.length ? await gtBatch(left.map((i) => texts[i])) : [];
    if (left.length) model = model ? model + "+google" : "google";
    miss.forEach((i, j) => { ko[i] = res[j] || g[left.indexOf(i)] || null; });
    for (const i of miss) if (ko[i]) waitUntil(cache.put(keys[i], new Response(ko[i], { headers: { "cache-control": "max-age=2592000" } })));
  }
  // err: LLM 번역이 실패해 대체 모델을 쓴 경우 그 이유 (문제 확인용)
  return Response.json({ ko, ...(model && { model }), ...(err && { err }) }, { headers: { "cache-control": "no-store" } });
}
