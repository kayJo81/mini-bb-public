"""GPU 임대료 시세
  python lab/gpu_rent.py --out public_lab/gpu_rent.json

· Vast.ai 공개 가격 피드 (https://vast.ai/pricing.md 안내, 매시간 갱신)
    GPU 종류별 현재 최저가·하위 10%·중간값·매물 수 + 최근 90일 일별 중간값
    ※ 피드 조건: 출처 표시하면 '표시·인용'은 무료, 대량 수집·재배포·지수/벤치마크 제작은 별도 라이선스
      → 여기서는 받은 그대로 화면에 보여 주기만 하고, Vast 숫자는 따로 쌓아 두지 않음
· RunPod 정가표(보안/커뮤니티 클라우드): 날짜별로 labdata/gpu_rent.json 에 누적
"""
import argparse
import json
import os
from datetime import datetime, timedelta, timezone

import requests

# [키, 이름, 세대, Vast 이름들, RunPod id]
GPUS = [
    ["B300", "B300", "블랙웰 울트라", ["B300"], "NVIDIA B300 SXM6 AC"],
    ["B200", "B200", "블랙웰", ["B200"], "NVIDIA B200"],
    ["H200", "H200 SXM", "호퍼", ["H200"], "NVIDIA H200"],
    ["H100", "H100 SXM", "호퍼", ["H100 SXM"], "NVIDIA H100 80GB HBM3"],
    ["A100", "A100 SXM", "암페어", ["A100 SXM4"], "NVIDIA A100-SXM4-80GB"],
    ["MI300X", "AMD MI300X", "AMD", [], "AMD Instinct MI300X OAM"],
    ["PRO6000", "RTX PRO 6000", "블랙웰(추론)", ["RTX PRO 6000 S"], "NVIDIA RTX PRO 6000 Blackwell Server Edition"],
    ["L40S", "L40S", "에이다(추론)", ["L40S"], "NVIDIA L40S"],
    ["5090", "RTX 5090", "소비자용", ["RTX 5090"], "NVIDIA GeForce RTX 5090"],
    ["4090", "RTX 4090", "소비자용", ["RTX 4090"], "NVIDIA GeForce RTX 4090"],
]
UA = {"User-Agent": "mini-bblab/1.0 (gpu rent index)"}


FEED = "https://storage.googleapis.com/vast-public-gpu-pricing/gpu-pricing-public.json"


def vast_feed():
    try:
        d = requests.get(FEED, headers=UA, timeout=30).json()
        return d.get("gpus") or {}, d.get("updated"), d.get("license")
    except (requests.RequestException, ValueError) as e:
        print(f"[gpu] vast 피드 오류 {type(e).__name__}")
        return {}, None, None


def runpod():
    try:
        r = requests.post("https://api.runpod.io/graphql", headers={**UA, "content-type": "application/json"}, timeout=30,
                          json={"query": "{ gpuTypes { id securePrice communityPrice } }"})
        return {g["id"]: g for g in r.json()["data"]["gpuTypes"]}
    except (requests.RequestException, ValueError, KeyError) as e:
        print(f"[gpu] runpod 오류 {type(e).__name__}")
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="public_lab/gpu_rent.json")
    a = ap.parse_args()
    path = os.path.join(os.environ.get("LAB_DATA", "labdata"), "gpu_rent.json")
    try:
        store = json.load(open(path))
    except (OSError, ValueError):
        store = {"days": {}}
    now = datetime.now(timezone.utc)
    day = (now + timedelta(hours=9)).strftime("%Y-%m-%d")   # 한국 날짜
    feed, updated, lic = vast_feed()
    byname = {g.get("name"): g for g in feed.values()}
    rp, snap, out_g = runpod(), {}, []
    for key, name, gen, vnames, rid in GPUS:
        g = rp.get(rid) or {}
        r = {}
        for k, f in (("rp_secure", "securePrice"), ("rp_comm", "communityPrice")):
            v = g.get(f) or 0
            # RunPod 은 매물이 없는 칸에 0.5 를 넣어 둠(예: MI300X 커뮤니티) — 다른 칸이 1.5 넘는데 0.5 면 버림
            other = max(g.get("securePrice") or 0, g.get("communityPrice") or 0)
            if v > 0 and not (v == 0.5 and other > 1.5):
                r[k] = v
        if r:
            snap[key] = r
        vg = next((byname[n] for n in vnames if n in byname), None)
        cur = (vg or {}).get("current") or {}
        out_g.append({"key": key, "name": name, "gen": gen,
                      "vast": {"min": cur.get("min"), "p10": cur.get("p10"), "median": cur.get("median"), "available": cur.get("available"),
                               "daily": sorted([[x["date"], x["median"]] for x in (vg or {}).get("daily", []) if x.get("median") is not None])} if vg else None})
    # RunPod 정가만 누적 (예전에 넣었던 Vast 숫자는 지움)
    store["days"] = {d: {k: {f: v for f, v in s.items() if f.startswith("rp_")} for k, s in g.items()} for d, g in store.get("days", {}).items()}
    if snap:
        store["days"][day] = snap
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        json.dump(store, open(path, "w"), separators=(",", ":"))
    days = sorted(store["days"])[-400:]
    out = {"at": now.isoformat(timespec="seconds"), "vast_updated": updated, "vast_license": lic, "gpus": out_g,
           "runpod": [[d, store["days"][d]] for d in days]}
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    json.dump(out, open(a.out, "w"), ensure_ascii=False, separators=(",", ":"))
    print(f"[gpu] vast 피드 {updated} · RunPod 누적 {len(days)}일 → {a.out}")
    for x in out_g:
        v = x["vast"] or {}
        print(f"  {x['key']:8s} vast 중간 {v.get('median')} 최저 {v.get('min')} 매물 {v.get('available')} 이력 {len(v.get('daily', []))}일 · runpod {snap.get(x['key'])}")


if __name__ == "__main__":
    main()
