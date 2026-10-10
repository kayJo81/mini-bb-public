// 한국 증시 뉴스: 15분마다 수집되는 결과(미니 블벅터 사이트의 briefings/kr_news.json)를 가져와 전달 (1분 캐시)
import { cached } from "../../lib/naver.js";

export async function onRequestGet({ request }) {
  return cached(request, 60, async () => {
    const r = await fetch("https://mini-bbterminal.pages.dev/briefings/kr_news.json", { cf: { cacheTtl: 0 } });
    if (!r.ok) throw new Error(`kr_news ${r.status}`);
    return r.json();
  });
}
