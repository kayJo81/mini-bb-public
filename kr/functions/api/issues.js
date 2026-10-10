// 오늘의 이슈 테마: 급등(?dir=down 이면 급락) 종목을 '같은 뉴스로 움직인 종목'끼리 자동으로 묶는다 (5분 캐시)
//  1) 코스피·코스닥 상승률(하락률) 상위에서 ±5% 이상 보통주 최대 40개 (스팩·우선주 제외)
//  2) 종목마다 네이버 종목 뉴스(3일 이내, 시장 시황 기사 제외) 최근 6개
//  3) 같은 기사를 공유하거나, 움직임 표현이 든 비슷한 제목(한글 두 글자 단위 겹침)끼리 연결 → 2종목 이상 묶음
//     (코스피·증시 시황처럼 시장 전체를 다룬 기사는 연결 근거에서 제외)
//  4) 묶음 이름: 기사 제목의 따옴표 속 핵심어 › 묶음 기사들에 가장 많이 나온 단어
import { cached, naver, num } from "../../lib/naver.js";

const MOVE_UP = /특징주|급등|상한가|강세|수혜|기대감|껑충|폭등|신고가|들썩|↑|뛴다|뛰어|날개|주목|부각|랠리|훨훨|줄줄이|동반|무더기/;
const MOVE_DN = /특징주|급락|하한가|약세|우려|폭락|뚝|추락|곤두박질|↓|하락|쇼크|악재|부진|적자|유상증자|급감|철회|중단|실패|논란|매도|투매|줄줄이|동반|무더기/;
const WRAP = /코스피|코스닥|증시|지수|시황|테마주는|테마주들|주요 종목|오늘의 특징|장 마감 후|투자자별/;   // 시장 전체 기사는 묶는 근거로 쓰지 않음
const MIN_CHG = 5, MAX_STOCKS = 40;
const COMMON = new Set(["특징주", "상한가", "급등", "강세", "주가", "종목", "마감", "주식", "코스닥", "코스피", "증시", "오늘", "관련주",
  "상승", "하락", "급락", "하한가", "약세", "급락세", "기대감", "수혜", "들썩", "무더기", "동반", "줄줄이", "이틀째", "사흘째", "나흘째", "연속", "장중", "개장", "속보", "단독",
  "다시", "연일", "일제히", "불붙은", "불붙", "오름", "급등세", "급등세", "상승세", "오름세", "계열사", "그룹", "관련", "이유", "왜"]);
const JOSA = /(에서|으로|에게|에|의|를|을|이|가|은|는|로|도|와|과|만)$/;
const word = (w) => (w.length > 2 ? w.replace(JOSA, "") : w);

const dec = (s) => String(s || "").replace(/&quot;/g, '"').replace(/&amp;/g, "&").replace(/&#39;|&apos;/g, "'").trim();
const kst = (t) => Date.parse(`${t.slice(0, 4)}-${t.slice(4, 6)}-${t.slice(6, 8)}T${t.slice(8, 10)}:${t.slice(10, 12)}:00+09:00`);
const grams = (s) => {
  const out = new Set();
  for (const w of s.replace(/\[[^\]]*\]/g, " ").match(/[가-힣]{2,}|[A-Za-z0-9]{2,}/g) || []) {
    if (COMMON.has(w)) continue;
    if (/[가-힣]/.test(w)) for (let i = 0; i < w.length - 1; i++) out.add(w.slice(i, i + 2));
    else out.add(w.toLowerCase());
  }
  return out;
};
const sim = (a, b) => {
  let n = 0;
  for (const x of a) if (b.has(x)) n++;
  return n / Math.max(1, Math.min(a.size, b.size));
};

function issueName(titles, members) {
  // 묶음 기사들에 자주 나온 단어 (종목 이름·흔한 단어 제외) 상위 3개
  const cnt = new Map();
  for (const t of titles) {
    for (const w of new Set((t.replace(/\[[^\]]*\]/g, " ").match(/[가-힣A-Za-z0-9]{2,}/g) || []).map(word))) {
      if (COMMON.has(w) || /^\d+[일%]?$/.test(w) || members.some((s) => s.name.includes(w) || w.includes(s.name))) continue;
      cnt.set(w, (cnt.get(w) || 0) + 1);
    }
  }
  const top = [...cnt.entries()].filter((x) => x[1] >= Math.min(2, titles.length))
    .sort((a, b) => b[1] - a[1] || b[0].length - a[0].length).slice(0, 6).map((x) => x[0])
    .sort((a, b) => titles.join(" ").indexOf(a) - titles.join(" ").indexOf(b));   // 기사 속 순서대로
  // 따옴표 속 핵심어(종목 이름이 아닌 것)가 있으면 앞에
  let quoted = "";
  for (const t of titles) {
    for (const m of t.matchAll(/['‘"“]([^'’"”]{2,24})['’"”]/g)) {
      const q = m[1].trim();
      if (!members.some((s) => q.includes(s.name)) && !COMMON.has(q) && q.length >= 3 && !quoted) quoted = q;
    }
  }
  const rest = top.filter((w) => !quoted.includes(w)).slice(0, 3).join(" ");
  return [quoted, rest].filter(Boolean).join(" · ") || "공통 재료";
}

export async function onRequestGet({ request }) {
  const down = new URL(request.url).searchParams.get("dir") === "down";   // ?dir=down → 급락주 묶음
  const MOVE = down ? MOVE_DN : MOVE_UP;
  return cached(request, 300, async () => {
    const lists = await Promise.all(["KOSPI", "KOSDAQ"].map((m) => naver(`/api/stocks/${down ? "down" : "up"}/${m}?page=1&pageSize=100`)));
    const all = lists.flatMap((l, i) => (l.stocks || []).map((s) => ({
      code: s.itemCode, name: s.stockName, market: i ? "코스닥" : "코스피",
      chg: num(s.fluctuationsRatio), value: num(s.accumulatedTradingValue), type: s.stockEndType,
    })));
    const picks = all.filter((s) => s.type === "stock" && (down ? s.chg <= -MIN_CHG : s.chg >= MIN_CHG) && !/스팩|우$|우B$|\dB?$/.test(s.name))
      .sort((a, b) => (down ? a.chg - b.chg : b.chg - a.chg) || b.value - a.value).slice(0, MAX_STOCKS);
    const now = Date.now();
    await Promise.all(picks.map(async (s) => {
      try {
        const d = await naver(`/api/news/stock/${s.code}?pageSize=10&page=1`);
        s.heads = (Array.isArray(d) ? d : []).flatMap((x) => x.items || []).map((n) => ({
          title: dec(n.title), office: n.officeName, ts: kst(n.datetime || ""),
          url: `https://n.news.naver.com/mnews/article/${n.officeId}/${n.articleId}`,
        })).filter((n) => (now - n.ts) / 3600e3 < 72 && !WRAP.test(n.title)).slice(0, 6);
      } catch (ex) { s.heads = []; }
      for (const h of s.heads) { h.g = grams(h.title); h.move = MOVE.test(h.title); }
    }));
    // 연결: 같은 기사이거나 제목 비슷(≥0.5) → 묶음(유니온 파인드)
    const parent = picks.map((_, i) => i);
    const find = (i) => (parent[i] === i ? i : (parent[i] = find(parent[i])));
    const why = picks.map(() => new Map());   // 연결 근거 기사
    for (let i = 0; i < picks.length; i++) {
      for (let j = i + 1; j < picks.length; j++) {
        let hit = null;
        for (const a of picks[i].heads) for (const b of picks[j].heads) {
          // 같은 기사면 바로 연결, 비슷한 제목은 둘 다 움직임 표현이 있을 때만
          if (a.title === b.title) { hit = a; break; }
          if (a.move && b.move && sim(a.g, b.g) >= 0.5) { hit = a.ts >= b.ts ? a : b; break; }
        }
        if (hit) {
          parent[find(i)] = find(j);
          why[i].set(hit.title, hit); why[j].set(hit.title, hit);
        }
      }
    }
    const groups = new Map();
    picks.forEach((s, i) => { const r = find(i); if (!groups.has(r)) groups.set(r, []); groups.get(r).push(i); });
    const issues = [];
    for (const idx of groups.values()) {
      if (idx.length < 2) continue;
      const members = idx.map((i) => picks[i]).sort((a, b) => (down ? a.chg - b.chg : b.chg - a.chg));
      const arts = new Map();
      for (const i of idx) for (const [t, a] of why[i]) arts.set(t, a);
      // 대표 기사: 움직임 표현 있음 › 묶음 종목을 여러 개 언급 › 최신
      const sorted = [...arts.values()].sort((a, b) => {
        const ca = members.filter((m) => a.title.includes(m.name)).length, cb = members.filter((m) => b.title.includes(m.name)).length;
        return (b.move - a.move) || cb - ca || b.ts - a.ts;
      });
      issues.push({
        name: issueName(sorted.map((a) => a.title), members),
        avg: Math.round(members.reduce((x, m) => x + m.chg, 0) / members.length * 100) / 100,
        stocks: members.map(({ code, name, market, chg, value }) => ({ code, name, market, chg, value })),
        articles: sorted.slice(0, 3).map(({ title, office, ts, url }) => ({ title, office, ts, url })),
      });
    }
    // 순위: 평균 등락률 크기 × √종목 수 (여러 종목이 함께 크게 움직인 재료일수록 위)
    for (const i of issues) i.score = Math.round(Math.abs(i.avg) * Math.sqrt(i.stocks.length) * 100) / 100;
    issues.sort((a, b) => b.score - a.score);
    return { status: lists[0].marketStatus, dir: down ? "down" : "up", scanned: picks.length, issues };
  });
}
