// 테마 강세(약세) 이유: 대장주들의 최신 뉴스 중 움직임을 설명하는 기사 (3분 캐시)
//  ?c=종목코드,종목코드,... (최대 16개) → { 코드: [{title, office, t, url, score}] }
import { cached, naver } from "../../lib/naver.js";

const MOVE = /특징주|급등|급락|상한가|하한가|강세|약세|수혜|기대감|우려|껑충|폭등|폭락|신고가|↑|↓|오름|내림|뛴다|뛰어|날아|주목|부각/;

export async function onRequestGet({ request }) {
  const codes = [...new Set((new URL(request.url).searchParams.get("c") || "").split(",")
    .map((x) => x.replace(/\D/g, "").slice(0, 6)).filter((x) => x.length === 6))].slice(0, 16);
  return cached(request, 180, async () => {
    const now = Date.now();
    const rows = await Promise.all(codes.map((code) =>
      naver(`/api/news/stock/${code}?pageSize=10&page=1`).then((d) => [code, d]).catch(() => [code, []])));
    const out = {};
    for (const [code, d] of rows) {
      const items = (Array.isArray(d) ? d : []).flatMap((x) => x.items || []).map((n) => {
        const t = n.datetime || "";   // YYYYMMDDHHmm (한국시간)
        const ts = Date.parse(`${t.slice(0, 4)}-${t.slice(4, 6)}-${t.slice(6, 8)}T${t.slice(8, 10)}:${t.slice(10, 12)}:00+09:00`);
        const title = (n.title || "").replace(/&quot;/g, '"').replace(/&amp;/g, "&").replace(/&#39;/g, "'");
        const age = (now - ts) / 3600e3;
        const score = (MOVE.test(title) ? 3 : 0) + (age < 12 ? 2 : age < 36 ? 1 : 0);
        return { title, office: n.officeName, t, url: `https://n.news.naver.com/mnews/article/${n.officeId}/${n.articleId}`, score, age };
      }).filter((n) => n.age < 72);
      items.sort((a, b) => b.score - a.score || a.age - b.age);
      out[code] = items.slice(0, 6).map(({ age, ...n }) => n);
    }
    return out;
  });
}
