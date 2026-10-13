// 증권사 리포트 (네이버 증권 리서치, 10분 캐시)
//   GET /api/reports → { at, macro: [...], industry: [...], company: [...] }
//   · 매크로: 경제분석·투자전략·채권분석·데일리(시황)
//   · 산업·기업: 산업분석 + 종목분석을 뉴스 화면과 같은 산업군(K 테크·K 조방원·K 바이오·K 모빌리티·K 금융 …)으로 나눔
//     (업종 이름·종목명·제목의 단어로 분류, 해당 없으면 '기타')
import { cached, naver } from "../../lib/naver.js";
import SECTORS from "../../lib/sectors.json";

// [산업군, 카테고리, 단어] — kr_news.py 의 산업 카테고리와 같은 이름 (위에서부터 먼저 맞는 것)
const TOPICS = [
  ["K 테크", "반도체", /반도체|hbm|하이닉스|삼성전자|파운드리|d램|디램|낸드|메모리|웨이퍼/i],
  ["K 테크", "소부장", /장비|소재|부품|후공정|패키징|한미반도체|원익|이오테크닉스|리노공업|디스플레이/i],
  ["K 테크", "배터리", /2차전지|이차전지|배터리|양극재|음극재|리튬|에코프로|엘앤에프|포스코퓨처엠|에너지솔루션|삼성sdi|전고체|ess/i],
  ["K 테크", "AI·로봇", /로봇|휴머노이드|\bai\b|인공지능|데이터센터|소프트웨어|it서비스|인터넷|게임|플랫폼|네이버|카카오/i],
  ["K 조방원", "조선", /조선|lng선|선박|해운|hd현대|한화오션|삼성중공업|한국조선해양/i],
  ["K 조방원", "방산", /방산|방위|국방|우주항공|한화에어로|현대로템|lig넥스원|한국항공우주|k9|k2/i],
  ["K 조방원", "원자력·전력", /원전|원자력|smr|두산에너빌리티|한전|전력|유틸리티|에너지/i],
  ["K 바이오", "바이오·제약", /바이오|제약|임상|신약|생물공학|건강관리|의료|셀트리온|삼성바이오|알테오젠|유한양행|한미약품/i],
  ["K 모빌리티", "자동차", /자동차|현대차|기아|모비스|타이어|전기차/i],
  ["K 금융", "금융지주·은행", /은행|금융지주|kb금융|신한|하나금융|우리금융|카드/i],
  ["K 금융", "증권", /증권|창업투자|브로커리지/i],
  ["K 금융", "보험", /보험|생명|손해|화재/i],
  ["소비·기타", "소비재", /화장품|음식료|식품|음료|유통|소매|의류|섬유|미디어|엔터|방송|호텔|레저|여행|항공|면세|담배/i],
  ["소비·기타", "소재·산업재", /화학|석유|정유|철강|비철|금속|건설|건축|기계|운송|물류|종이|포장|시멘트/i],
];
const classify = (...texts) => {
  const t = texts.filter(Boolean).join(" ");
  const hit = TOPICS.find(([, , re]) => re.test(t));
  return hit ? { group: hit[0], topic: hit[1] } : { group: "소비·기타", topic: "기타" };
};
const slim = (r) => ({ id: r.researchId, title: r.title, broker: r.brokerName, date: r.writeDate, url: r.endUrl,
  views: Number(r.readCount) || 0, item: r.itemName || "", code: r.itemCode || "", cat: r.researchCategory });

async function list(category, pages = 1) {
  const all = await Promise.all(Array.from({ length: pages }, (_, i) =>
    naver(`/front-api/research/list?category=${category}&pageSize=50&page=${i + 1}`).then((d) => d.result || []).catch(() => [])));
  return all.flat();
}

export async function onRequestGet({ request }) {
  return cached(request, 600, async () => {
    const [eco, inv, deb, daily, ind, com] = await Promise.all([list("economy"), list("invest"), list("debenture"),
      list("daily"), list("industry"), list("company", 2)]);
    const macro = [...eco, ...inv, ...deb, ...daily].map(slim).sort((a, b) => b.date.localeCompare(a.date) || b.id - a.id);
    const industry = ind.map((r) => ({ ...slim(r), sector: r.category !== "기타" ? r.category : "", ...classify(r.category !== "기타" ? r.category : "", r.title) }));
    const company = com.map((r) => {
      const k = SECTORS.codes[r.itemCode], sector = k === undefined ? "" : SECTORS.names[k];
      return { ...slim(r), sector, ...classify(sector, r.itemName, r.title) };
    });
    return { at: new Date().toISOString(), macro, industry, company };
  });
}
