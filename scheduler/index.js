// 미니 블벅터 스케줄러 (Cloudflare Worker Cron)
// GitHub 예약 실행이 불안정해서, Cloudflare 가 15분마다 briefing 워크플로를 직접 실행시킨다.
// 필요한 Worker 비밀값: GH_TOKEN (Actions: Read and write 권한의 GitHub 토큰)
const REPO = "kayJo81/mini-bb-public";
const WORKFLOW = "briefing.yml";

const REPORT_CRON = "30 20 * * *";   // 매일 05:30 KST: 수집 + 일일 PDF 메일
const LAB_CRON = "0 22 * * TUE-SAT";     // 화~토 07:00 KST(미국 장 마감 뒤): 미니 블벅 랩 갱신

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
    if (event.cron === LAB_CRON) {
      ctx.waitUntil(dispatch(env, false, "lab.yml", { days: "4" }));
    } else {
      ctx.waitUntil(dispatch(env, event.cron === REPORT_CRON));
    }
  },
};
