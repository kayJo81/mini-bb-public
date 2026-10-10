// 테마가 왜 오르(내리)는지: 테마별로 한 줄 (5분 캐시)
//  1) 대장주 3개(약세 테마는 가장 많이 내린 3개)의 네이버 종목 뉴스에서
//     A: 종목명·테마어 + 움직임 표현  B: 종목명·테마어  C: 움직임 표현(지수 시황 기사 제외)
//  2) 없으면 구글 뉴스 한국판에서 '대장주 이름 + 특징주/급등/상한가…' 최근 2일
//  3) 그래도 없으면 대장주 최신 기사(관련 뉴스) → 테마 설명·편입 사유(개요)
import { cached, naver, num } from "../../../lib/naver.js";

const MOVE = /특징주|급등|급락|상한가|하한가|강세|약세|수혜|기대감|우려|껑충|폭등|폭락|신고가|들썩|↑|↓|오름세|뛴다|뛰어|날개|주목|부각|랠리|훨훨|줄줄이|동반/;
const WRAP = /코스피|코스닥|증시|지수|시황|마감|장중|외국인|기관|개장|출발/;
const STOP = new Set(["관련주", "관련", "테마", "수혜", "등", "및", "기타"]);

const dec = (s) => String(s || "").replace(/<!\[CDATA\[|\]\]>/g, "").replace(/&quot;/g, '"').replace(/&amp;/g, "&")
  .replace(/&#39;|&apos;/g, "'").replace(/&lt;/g, "<").replace(/&gt;/g, ">").trim();

function kst(t) {   // YYYYMMDDHHmm → ms
  return Date.parse(`${t.slice(0, 4)}-${t.slice(4, 6)}-${t.slice(6, 8)}T${t.slice(8, 10)}:${t.slice(10, 12)}:00+09:00`);
}

async function googleNews(names) {
  const q = `(${names.map((n) => `"${n}"`).join(" OR ")}) (특징주 OR 급등 OR 상한가 OR 강세 OR 급락 OR 약세 OR 수혜) when:2d`;
  const r = await fetch(`https://news.google.com/rss/search?q=${encodeURIComponent(q)}&hl=ko&gl=KR&ceid=KR:ko`,
    { headers: { "User-Agent": "Mozilla/5.0 (compatible; MiniBBTerminal/1.0)" } });
  if (!r.ok) return [];
  const xml = await r.text();
  return [...xml.matchAll(/<item>([\s\S]*?)<\/item>/g)].map((m) => {
    const g = (tag) => dec((m[1].match(new RegExp(`<${tag}[^>]*>([\\s\\S]*?)</${tag}>`)) || [])[1]);
    const office = g("source");
    return { title: g("title").replace(new RegExp(`\\s+-\\s+${office.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}$`), ""),
             url: g("link"), office, ts: Date.parse(g("pubDate")) };
  });
}

export async function onRequestGet({ request, params }) {
  const no = String(params.no).replace(/\D/g, "");
  return cached(request, 300, async () => {
    const d = await naver(`/api/stocks/theme/${no}?page=1&pageSize=100`);
    const g = d.groupInfo || {};
    const chg = num(g.changeRate) || 0;
    const stocks = (d.stocks || []).map((s) => ({ code: s.itemCode, name: s.stockName, chg: num(s.fluctuationsRatio) || 0 }))
      .sort((a, b) => (chg >= 0 ? b.chg - a.chg : a.chg - b.chg));
    const movers = stocks.slice(0, 3);
    const words = (g.name || "").replace(/[()\/·,]/g, " ").split(/\s+/).filter((w) => w.length >= 2 && !STOP.has(w));
    const now = Date.now();
    const pick = { best: null };
    const consider = (n, stock, src) => {
      const age = (now - n.ts) / 3600e3;
      if (!(age >= -1 && age < 72) || !n.title) return;
      const hasName = movers.some((m) => n.title.includes(m.name)), hasWord = words.some((w) => n.title.includes(w));
      const move = MOVE.test(n.title), wrap = WRAP.test(n.title) && !hasName;
      let tier = 0;
      if ((hasName || hasWord) && move) tier = 3;
      else if (hasName || hasWord) tier = 2;
      else if (move && !wrap) tier = 1;
      const sc = tier * 10 + (age < 12 ? 3 : age < 36 ? 1 : 0) + (hasName ? 1 : 0);
      const item = { ...n, stock, src, tier, sc };
      if (!pick.best || sc > pick.best.sc) pick.best = item;
      if (!pick.latest || n.ts > pick.latest.ts) pick.latest = item;
    };
    const feeds = await Promise.all(movers.map((m) =>
      naver(`/api/news/stock/${m.code}?pageSize=10&page=1`).then((x) => [m, x]).catch(() => [m, []])));
    for (const [m, x] of feeds) {
      for (const n of (Array.isArray(x) ? x : []).flatMap((y) => y.items || [])) {
        consider({ title: dec(n.title), office: n.officeName, ts: kst(n.datetime || ""),
                   url: `https://n.news.naver.com/mnews/article/${n.officeId}/${n.articleId}` }, m.name, "naver");
      }
    }
    if (!pick.best || pick.best.tier < 2) {
      try {
        for (const n of await googleNews(movers.map((m) => m.name))) consider(n, "", "google");
      } catch (ex) { /* 구글 뉴스 실패는 무시 */ }
    }
    const base = { no: g.no, name: g.name, chg, movers: movers.map(({ name, chg: c }) => ({ name, chg: c })) };
    const b = pick.best;
    if (b && b.tier >= 1) return { ...base, kind: "reason", title: b.title, url: b.url, office: b.office, ts: b.ts, stock: b.stock };
    if (pick.latest) return { ...base, kind: "related", title: pick.latest.title, url: pick.latest.url, office: pick.latest.office,
                              ts: pick.latest.ts, stock: pick.latest.stock };
    const info = (d.themeItemInfoMap || {})[movers[0]?.code] || "";
    const desc = (d.themeDescription || "").split(/(?<=[.다])\s/)[0];
    return { ...base, kind: "info", desc, info: info.slice(0, 160), stock: movers[0]?.name || "" };
  });
}
