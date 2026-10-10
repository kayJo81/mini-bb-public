// 종목 뉴스 (네이버 증권 종목 뉴스, 3분 캐시)
import { cached, naver } from "../../../lib/naver.js";

export async function onRequestGet({ request, params }) {
  const code = String(params.code).replace(/\D/g, "").slice(0, 6);
  return cached(request, 180, async () => {
    const d = await naver(`/api/news/stock/${code}?pageSize=8&page=1`);
    const items = (Array.isArray(d) ? d : []).flatMap((x) => x.items || []);
    return items.slice(0, 8).map((n) => ({
      title: (n.title || "").replace(/&quot;/g, '"').replace(/&amp;/g, "&").replace(/&#39;/g, "'"),
      office: n.officeName, t: n.datetime, // YYYYMMDDHHmm
      url: `https://n.news.naver.com/mnews/article/${n.officeId}/${n.articleId}`,
    }));
  });
}
