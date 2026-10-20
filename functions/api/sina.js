// 시나재경(新浪财经) 7x24 실시간 속보 최근 60건 — '⚡ 속보' 화면이 30초마다 읽음 (시나는 다른 사이트에서 직접 읽기를 막아 서버가 대신 받음), 30초 캐시
//   GET /api/sina → { at, items: [{ ts, h, u, s: "sina", imp?, tag }] }
// · A주 '회사' 태그만 붙은 공시성 속보는 뺌, '焦点' 태그는 중요 표시 (scripts/flash_feed.py 와 같은 기준)
const URL_ = "https://zhibo.sina.com.cn/api/zhibo/feed?page=1&page_size=60&zhibo_id=152&tag_id=0&dire=f&dpc=1&type=0";
const HDR = { "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
  Referer: "https://finance.sina.com.cn/7x24/" };
const TAG_KO = { "焦点": "포커스", "国际": "국제", "宏观": "거시", "市场": "시장", "央行": "중앙은행", "数据": "지표", "观点": "의견",
  "行业": "산업", "A股": "A주", "其他": "기타" };
const clean = (s) => String(s || "").replace(/<[^>]+>/g, " ").replace(/&nbsp;/g, " ").replace(/&amp;/g, "&").replace(/\s+/g, " ").trim();

export async function onRequestGet({ request, waitUntil }) {
  const cache = caches.default;
  const key = new Request(new URL("/api/sina", request.url).toString());
  const hit = await cache.match(key);
  if (hit) return hit;
  let items = [];
  try {
    const list = (await (await fetch(URL_, { headers: HDR })).json()).result.data.feed.list || [];
    for (const it of list) {
      const tags = (it.tag || []).map((t) => t.name);
      if (tags.length && tags.every((t) => t === "公司")) continue;
      const text = clean(it.rich_text).slice(0, 400);
      const m = text.match(/^【([^】]{4,})】/);
      const h = (m ? m[1] : text).slice(0, 160);
      // create_time 은 베이징 시각(UTC+8)
      const ts = Math.floor(Date.parse(String(it.create_time).replace(" ", "T") + "+08:00") / 1000);
      if (!h || !Number.isFinite(ts)) continue;
      const x = { ts, h, u: it.docurl || "https://finance.sina.com.cn/7x24/", s: "sina", tag: TAG_KO[tags.find((t) => t !== "公司")] || "" };
      if (tags.includes("焦点")) x.imp = true;
      items.push(x);
    }
  } catch {
    items = [];
  }
  const res = Response.json({ at: Math.floor(Date.now() / 1000), items },
    { headers: { "cache-control": items.length ? "public, max-age=30" : "no-store" } });
  if (items.length) waitUntil(cache.put(key, res.clone()));
  return res;
}
