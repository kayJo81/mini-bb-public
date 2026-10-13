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
// 네이버 업종(sectors.json) → [산업군, 카테고리]: 업종이 있으면 이것이 먼저 (단어 분류는 업종이 없을 때만)
const T = { semi: ["K 테크", "반도체"], hw: ["K 테크", "소부장"], bat: ["K 테크", "배터리"], ai: ["K 테크", "AI·로봇"],
  ship: ["K 조방원", "조선"], def: ["K 조방원", "방산"], pow: ["K 조방원", "원자력·전력"], bio: ["K 바이오", "바이오·제약"],
  car: ["K 모빌리티", "자동차"], bank: ["K 금융", "금융지주·은행"], sec: ["K 금융", "증권"], ins: ["K 금융", "보험"],
  con: ["소비·기타", "소비재"], ind: ["소비·기타", "소재·산업재"] };
const SECTOR = {
  "반도체와반도체장비": T.semi, "디스플레이장비및부품": T.hw, "디스플레이패널": T.hw, "전자장비와기기": T.hw, "전자제품": T.hw,
  "컴퓨터와주변기기": T.hw, "통신장비": T.hw, "핸드셋": T.hw, "사무용전자제품": T.hw, "전기제품": T.bat,
  "소프트웨어": T.ai, "IT서비스": T.ai, "양방향미디어와서비스": T.ai, "게임엔터테인먼트": T.ai,
  "조선": T.ship, "해운사": T.ship, "우주항공과국방": T.def, "전기유틸리티": T.pow, "복합유틸리티": T.pow, "가스유틸리티": T.pow,
  "전기장비": T.pow, "제약": T.bio, "생물공학": T.bio, "생명과학도구및서비스": T.bio, "건강관리기술": T.bio,
  "건강관리업체및서비스": T.bio, "건강관리장비와용품": T.bio, "자동차": T.car, "자동차부품": T.car,
  "은행": T.bank, "기타금융": T.bank, "카드": T.bank, "증권": T.sec, "창업투자": T.sec, "생명보험": T.ins, "손해보험": T.ins,
  "화학": T.ind, "철강": T.ind, "비철금속": T.ind, "기계": T.ind, "건설": T.ind, "건축자재": T.ind, "건축제품": T.ind,
  "항공화물운송과물류": T.ind, "도로와철도운송": T.ind, "운송인프라": T.ind, "석유와가스": T.ind, "에너지장비및서비스": T.ind,
  "종이와목재": T.ind, "포장재": T.ind, "복합기업": T.ind, "무역회사와판매업체": T.ind, "상업서비스와공급품": T.ind,
};
const classify = (sector, ...texts) => {
  if (SECTOR[sector]) return { group: SECTOR[sector][0], topic: SECTOR[sector][1] };
  for (const t of [sector, texts.filter(Boolean).join(" ")]) {   // 산업분석 분류(반도체·자동차 …) → 제목·종목명
    const hit = t && TOPICS.find(([, , re]) => re.test(t));
    if (hit) return { group: hit[0], topic: hit[1] };
  }
  return { group: "소비·기타", topic: "기타" };
};
const slim = (r) => ({ id: r.researchId, title: r.title, broker: r.brokerName, date: r.writeDate, url: r.endUrl,
  views: Number(r.readCount) || 0, item: r.itemName || "", code: r.itemCode || "", cat: r.researchCategory });

async function list(category, pages = 1) {
  const all = await Promise.all(Array.from({ length: pages }, (_, i) =>
    naver(`/front-api/research/list?category=${category}&pageSize=50&page=${i + 1}`).then((d) => d.result || []).catch(() => [])));
  return all.flat();
}

const RECENT_DAYS = 14;   // 최근 2주 리포트만

export async function onRequestGet({ request }) {
  return cached(request, 600, async () => {
    const since = new Date(Date.now() + 9 * 3600e3 - RECENT_DAYS * 86400e3).toISOString().slice(0, 10);
    const recent = (r) => (r.writeDate || "") >= since;
    const [eco, inv, deb, daily, ind, com] = await Promise.all([list("economy"), list("invest"), list("debenture"),
      list("daily"), list("industry"), list("company", 2)]);
    const macro = [...eco, ...inv, ...deb, ...daily].filter(recent).map(slim).sort((a, b) => b.date.localeCompare(a.date) || b.id - a.id);
    const industry = ind.filter(recent).map((r) => ({ ...slim(r), sector: r.category !== "기타" ? r.category : "", ...classify(r.category !== "기타" ? r.category : "", r.title) }));
    const company = com.filter(recent).map((r) => {
      const k = SECTORS.codes[r.itemCode], sector = k === undefined ? "" : SECTORS.names[k];
      return { ...slim(r), sector, ...classify(sector, r.itemName, r.title) };
    });
    return { at: new Date().toISOString(), macro, industry, company };
  });
}
