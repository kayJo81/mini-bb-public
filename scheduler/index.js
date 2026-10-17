// 미니 블벅터 스케줄러 (Cloudflare Worker Cron)
// GitHub 예약 실행이 불안정해서, Cloudflare 가 15분마다 briefing 워크플로를 직접 실행시킨다.
// 필요한 Worker 비밀값: GH_TOKEN (Actions: Read and write 권한의 GitHub 토큰)
const REPO = "kayJo81/mini-bb-public";
const WORKFLOW = "briefing.yml";

// 일일 PDF 메일: 미국 장 마감 30분 뒤 — 서머타임(EDT) 20:30 UTC = 05:30 KST, 겨울(EST) 21:30 UTC = 06:30 KST
// (종가 PDF가 들어가서 장 마감 뒤여야 함). 두 시각 중 맞지 않는 쪽은 평소 수집으로 실행
const REPORT_CRON = "30 20,21 * * *";   // 20:30 UTC(서머타임) / 21:30 UTC(겨울) — 맞는 쪽만 메일, 다른 쪽은 평소 수집
// 크론의 요일 이름(MON-FRI·SUN)은 실행되지 않는 일이 있어(10/13~16 랩 미실행) 매일 실행시키고 요일은 코드에서 확인
const LAB_CRON = "0 22 * * *";          // UTC 월~금 22:00 = KST 화~토 07:00 (미국 장 마감 뒤): 미니 블벅 랩 갱신
const WEEKLY_CRON = "0 11 * * *";       // UTC 일요일 11:00 = KST 일요일 20:00: 주간 전략

async function dispatch(env, report, workflow = WORKFLOW, inputs = null) {
  const r = await fetch(`https://api.github.com/repos/${REPO}/actions/workflows/${workflow}/dispatches`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GH_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "mini-bbterminal-scheduler",
    },
    body: JSON.stringify(inputs ? { ref: "main", inputs } : report ? { ref: "main", inputs: { report: "true" } } : { ref: "main" }),
  });
  if (!r.ok) console.log(`dispatch failed: ${r.status} ${await r.text()}`);
  return r.status;
}

export default {
  async scheduled(event, env, ctx) {
    const now = new Date(event.scheduledTime || Date.now());
    const dow = now.getUTCDay();             // 0=일 … 6=토 (UTC)
    if (event.cron === WEEKLY_CRON) {
      if (dow === 0) ctx.waitUntil(dispatch(env, false, "weekly.yml", {}));
      return;
    }
    if (event.cron === LAB_CRON) {
      if (dow >= 1 && dow <= 5) ctx.waitUntil(dispatch(env, false, "lab.yml", { days: "4" }));
      return;
    }
    const edt = /EDT/.test(now.toLocaleString("en-US", { timeZone: "America/New_York", timeZoneName: "short" }));
    const h = now.getUTCHours();
    const report = event.cron === REPORT_CRON && ((h === 20 && edt) || (h === 21 && !edt));
    ctx.waitUntil(dispatch(env, report));
  },
};
