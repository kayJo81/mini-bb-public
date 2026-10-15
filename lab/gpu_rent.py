"""GPU 임대료 지수 — 공개 가격을 매일 받아 lab-data 에 누적
  python lab/gpu_rent.py --out public_lab/gpu_rent.json

· Vast.ai 마켓플레이스: 지금 빌릴 수 있는 매물의 GPU 1장당 시간당 호가(스토리지 제외 dph_base)
    → GPU 수로 가중한 중간값·하위 25%·최저가, 매물 GPU 수 (신뢰도 0.9 미만 호스트 제외)
· RunPod 정가표: 보안(데이터센터) / 커뮤니티 클라우드 시간당 가격
· 누적: labdata/gpu_rent.json — 날짜별 한 번(같은 날 다시 돌면 덮어씀)
"""
import argparse
import json
import os
import time
from datetime import datetime, timedelta, timezone

import requests

# [키, 이름, 세대, Vast 이름들, RunPod id]
GPUS = [
    ["B300", "B300", "블랙웰 울트라", ["B300"], "NVIDIA B300 SXM6 AC"],
    ["B200", "B200", "블랙웰", ["B200"], "NVIDIA B200"],
    ["H200", "H200 SXM", "호퍼", ["H200"], "NVIDIA H200"],
    ["H100", "H100 SXM", "호퍼", ["H100 SXM"], "NVIDIA H100 80GB HBM3"],
    ["A100", "A100 SXM 80GB", "암페어", ["A100 SXM4"], "NVIDIA A100-SXM4-80GB"],
    ["MI300X", "AMD MI300X", "AMD", [], "AMD Instinct MI300X OAM"],
    ["PRO6000", "RTX PRO 6000", "블랙웰(추론)", ["RTX PRO 6000 S"], "NVIDIA RTX PRO 6000 Blackwell Server Edition"],
    ["L40S", "L40S", "에이다(추론)", ["L40S"], "NVIDIA L40S"],
    ["5090", "RTX 5090", "소비자용", ["RTX 5090"], "NVIDIA GeForce RTX 5090"],
    ["4090", "RTX 4090", "소비자용", ["RTX 4090"], "NVIDIA GeForce RTX 4090"],
]
UA = {"User-Agent": "mini-bblab/1.0 (gpu rent index)"}


def wq(vals, q):
    """GPU 수로 가중한 분위수: vals = [(가격, GPU 수)]"""
    vals = sorted(vals)
    tot, acc = sum(n for _, n in vals), 0
    for p, n in vals:
        acc += n
        if acc >= q * tot:
            return p
    return vals[-1][0]


def vast(names):
    offers = []
    for name in names:
        q = {"gpu_name": {"eq": name}, "rentable": {"eq": True}, "type": "on-demand", "limit": 500}
        try:
            r = requests.get("https://console.vast.ai/api/v0/bundles/", params={"q": json.dumps(q)}, headers=UA, timeout=30)
            offers += r.json().get("offers", [])
        except (requests.RequestException, ValueError) as e:
            print(f"[gpu] vast {name} 오류 {type(e).__name__}")
        time.sleep(1)
    vals = [((o.get("dph_base") or o["dph_total"]) / o["num_gpus"], o["num_gpus"]) for o in offers
            if o.get("num_gpus") and (o.get("reliability2") or 0) >= 0.9]
    if not vals:
        return {}
    return {"vast_med": round(wq(vals, .5), 3), "vast_p25": round(wq(vals, .25), 3), "vast_min": round(min(p for p, _ in vals), 3),
            "vast_gpus": sum(n for _, n in vals), "vast_offers": len(vals)}


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
    rp, snap = runpod(), {}
    for key, _, _, vnames, rid in GPUS:
        s = vast(vnames) if vnames else {}
        g = rp.get(rid) or {}
        for k, f in (("rp_secure", "securePrice"), ("rp_comm", "communityPrice")):
            v = g.get(f) or 0
            # RunPod 은 매물이 없는 칸에 0.5 를 넣어 둠(예: MI300X 커뮤니티) — 다른 칸이 1.5 넘는데 0.5 면 버림
            other = max(g.get("securePrice") or 0, g.get("communityPrice") or 0)
            if v > 0 and not (v == 0.5 and other > 1.5):
                s[k] = v
        if s:
            snap[key] = s
    if snap:
        store["days"][day] = snap
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        json.dump(store, open(path, "w"), separators=(",", ":"))
    days = sorted(store["days"])[-400:]
    out = {"at": now.isoformat(timespec="seconds"),
           "gpus": [{"key": k, "name": n, "gen": g} for k, n, g, _, _ in GPUS],
           "days": [{"d": d, **{"g": store["days"][d]}} for d in days]}
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    json.dump(out, open(a.out, "w"), ensure_ascii=False, separators=(",", ":"))
    print(f"[gpu] {day} {len(snap)}종 · 누적 {len(days)}일 → {a.out}")
    for k, s in snap.items():
        print(f"  {k:8s} vast 중간 {s.get('vast_med')} 최저 {s.get('vast_min')} ({s.get('vast_gpus', 0)}장) · runpod {s.get('rp_secure')} / {s.get('rp_comm')}")


if __name__ == "__main__":
    main()
