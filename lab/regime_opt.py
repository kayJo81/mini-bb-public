"""
위험 예산 점수의 항목별 가중치 최적화 — 2017년부터 매일 '그날 알 수 있었던 값'으로 되돌려 시험
  python lab/regime_opt.py --macro public_lab/macro.json --out public_lab/regime_weights.json

· 자료: 야후 일봉 ^VIX ^VIX3M ^MOVE ^TNX(10년물) ^IRX(3개월물) SPY(배당 조정) + macro.json(근원 PCE·근원 CPI·PPI)
    매크로는 발표 시차를 둠: CPI·PPI 는 그 달 1일부터 45일 뒤, PCE 는 60일 뒤부터 '알 수 있었던 값'
· 항목 점수 규칙(임계값)은 portfolio.py 와 같고, 여기서는 항목별 가중치(0~2, 0.5 단위)만 찾음
    딜러 감마(GEX)는 과거 자료가 없어 시험에서 빼고 가중치 1 고정
· 전략: 점수(가중합) → 주식 비중 40~90% (portfolio.py 와 같은 단계) · 다음 날 SPY 수익 × 비중 + 나머지 현금(3개월물 금리)
· 걷기 검증: 2020년부터 매년 그 전 해까지의 자료로만 가중치를 찾아 그 해에 적용 → 합산 성과가
    '같은 가중치(모두 1)'보다 낫거나 같으면 전체 기간으로 찾은 가중치를 채택 (아니면 같은 가중치 유지)
  · 참고용으로 학습 2017~2022 / 검증 2023~현재 한 번 나눈 결과도 함께 남김
"""
import argparse
import json
import random
from datetime import date, datetime, timedelta, timezone

import numpy as np
import requests

HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
KEYS = ["vix", "vix_term", "move", "curve", "core_pce", "core_cpi", "ppi", "ust10y"]
NAMES = {"vix": "VIX 수준", "vix_term": "VIX/VIX3M", "gex": "S&P 딜러 감마", "move": "MOVE(채권 변동성)", "curve": "10년−3개월 금리차",
         "core_pce": "근원 PCE 재가속", "core_cpi": "근원 CPI 재가속", "ppi": "PPI 최종수요", "ust10y": "미 10년물 5%"}
EXPOSURE = [(1, 0.9), (0, 0.8), (-1, 0.7), (-2, 0.6), (-3, 0.5)]
GRID = [0, 0.5, 1, 1.5, 2]
TRAIN_END = "2022-12-31"


def yahoo(sym, adj=False):
    r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range=10y", headers=HDR, timeout=30).json()
    r = r["chart"]["result"][0]
    off = r["meta"].get("gmtoffset", -14400)
    vals = r["indicators"]["adjclose"][0]["adjclose"] if adj else r["indicators"]["quote"][0]["close"]
    return {datetime.fromtimestamp(t + off, timezone.utc).date().isoformat(): v for t, v in zip(r["timestamp"], vals) if v}


def monthly(macro, sid):
    for g in macro.get("groups", []):
        for it in g["items"]:
            if it["id"] == sid:
                y, m = map(int, it["start"].split("-"))
                out = {}
                for v in it["v"]:
                    if v is not None:
                        out[date(y, m, 1)] = v
                    y, m = (y + 1, 1) if m == 12 else (y, m + 1)
                return out
    return {}


def known(series, day, lag):
    """day 에 알 수 있었던 가장 최근 달 → (그 달, 값들 dict)"""
    ms = [m for m in series if m + timedelta(days=lag) <= day]
    return max(ms) if ms else None


def accel_pts(series, m):
    if m is None:
        return 0
    ks = sorted(series)
    i = ks.index(m)
    if i < 12:
        return 0
    v, v3, v12 = series[m], series[ks[i - 3]], series[ks[i - 12]]
    a3, y = ((v / v3) ** 4 - 1) * 100, (v / v12 - 1) * 100
    return -1 if a3 > y + 0.5 else 1 if a3 < y - 0.5 else 0


def exposure_arr(S):
    E = np.full(S.shape, 0.4)
    for th, e in reversed(EXPOSURE):
        E[S >= th - 1e-9] = e
    return E


def stats(rets, cash, E):
    ex = rets - cash
    sd = ex.std()
    eq = np.cumprod(1 + rets)
    dd = (eq / np.maximum.accumulate(eq) - 1).min()
    yrs = len(rets) / 252
    return {"sharpe": round(float(ex.mean() / sd * np.sqrt(252)) if sd else 0, 3), "cagr": round(float((eq[-1] ** (1 / yrs) - 1) * 100), 2),
            "mdd": round(float(dd * 100), 2), "avg_exp": round(float(E.mean() * 100), 1) if E is not None else 100.0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--macro", default="public_lab/macro.json")
    ap.add_argument("--out", default="public_lab/regime_weights.json")
    a = ap.parse_args()
    macro = json.load(open(a.macro, encoding="utf-8"))
    vix, v3m, move, tnx, irx, spy = (yahoo("%5EVIX"), yahoo("%5EVIX3M"), yahoo("%5EMOVE"), yahoo("%5ETNX"), yahoo("%5EIRX"),
                                     yahoo("SPY", adj=True))
    pce, cpi, ppi = monthly(macro, "PCEPILFE"), monthly(macro, "CPILFESL"), monthly(macro, "PPIFIS")
    days = sorted(d for d in spy if d >= "2017-01-01" and d in vix and d in v3m and d in tnx and d in irx)
    P = {k: [] for k in KEYS}
    for d in days:
        dd = date.fromisoformat(d)
        x = vix[d]
        P["vix"].append(1 if x < 15 else 0 if x < 20 else -1 if x < 25 else -2)
        t = vix[d] / v3m[d]
        P["vix_term"].append(1 if t < 0.9 else 0 if t <= 1.0 else -2)
        P["move"].append(-1 if move.get(d, 0) > 120 else 0)
        P["curve"].append(-1 if tnx[d] - irx[d] < 0 else 0)
        P["core_pce"].append(accel_pts(pce, known(pce, dd, 60)))
        P["core_cpi"].append(accel_pts(cpi, known(cpi, dd, 45)))
        m = known(ppi, dd, 45)
        ks = sorted(ppi)
        y = (ppi[m] / ppi[ks[ks.index(m) - 12]] - 1) * 100 if m and ks.index(m) >= 12 else 0
        P["ppi"].append(-1 if y > 5 else 0)
        P["ust10y"].append(-1 if tnx[d] >= 5 else 0)
    M = np.array([P[k] for k in KEYS], dtype=float)                     # 항목 × 날짜
    px = np.array([spy[d] for d in days])
    r_next = np.append(px[1:] / px[:-1] - 1, 0.0)                         # 오늘 정한 비중 → 다음 날 수익
    cash = np.array([irx[d] / 100 / 252 for d in days])
    tr = np.array([d <= TRAIN_END for d in days])
    te = ~tr
    r_next, cash, M = r_next[:-1], cash[:-1], M[:, :-1]
    tr, te = tr[:-1], te[:-1]

    def run(w, mask):
        E = exposure_arr(np.asarray(w) @ M[:, mask])
        rets = E * r_next[mask] + (1 - E) * cash[mask]
        return rets, E

    def sharpe(w, mask):
        rets, _ = run(w, mask)
        ex = rets - cash[mask]
        return ex.mean() / ex.std() if ex.std() else -9

    # 좌표 하강 + 무작위 재시작 (mask 구간의 샤프 최대)
    def optimize(mask, seed=7):
        rnd = random.Random(seed)
        best_w, best = [1.0] * len(KEYS), sharpe([1.0] * len(KEYS), mask)
        for _ in range(30):
            w = [rnd.choice(GRID) for _ in KEYS]
            cur = sharpe(w, mask)
            for _ in range(6):
                moved = False
                for i in range(len(KEYS)):
                    for g in GRID:
                        if g == w[i]:
                            continue
                        w2 = w[:i] + [g] + w[i + 1:]
                        s_ = sharpe(w2, mask)
                        if s_ > cur + 1e-9:
                            w, cur, moved = w2, s_, True
                if not moved:
                    break
            if cur > best:
                best_w, best = w, cur
        return best_w

    yrs = np.array([int(d[:4]) for d in days[:-1]])
    best_w = optimize(tr)
    # 걷기 검증(walk-forward): 2020년부터 매년 '그 전 해까지'로만 가중치를 찾아 그 해에 적용
    #   과적합을 줄이려 찾은 가중치를 '같은 가중치(1)' 쪽으로 당김: w = λ·최적 + (1−λ)·1, λ 도 걷기 검증으로 고름
    LAMS = [0, 0.25, 0.5, 0.75, 1.0]
    per_year = []
    for Y in range(2020, int(days[-1][:4]) + 1):
        m = yrs == Y
        if m.any():
            per_year.append((Y, m, optimize(yrs < Y, seed=Y)))
    wf_by_lam = {}
    for lam in LAMS:
        rr, ee, cc = [], [], []
        for Y, m, w_y in per_year:
            w = [lam * x + (1 - lam) * 1.0 for x in w_y]
            r_, E_ = run(w, m)
            rr.append(r_); ee.append(E_); cc.append(cash[m])
        wf_by_lam[lam] = stats(np.concatenate(rr), np.concatenate(cc), np.concatenate(ee))
    lam = max(LAMS, key=lambda l: (wf_by_lam[l]["sharpe"], -l))     # 동점이면 같은 가중치 쪽(작은 λ)
    wf_years = []
    for Y, m, w_y in per_year:
        w = [lam * x + (1 - lam) * 1.0 for x in w_y]
        r_, E_ = run(w, m)
        r0, _ = run([1.0] * len(KEYS), m)
        wf_years.append({"year": Y, "weights": dict(zip(KEYS, [round(x, 3) for x in w])), "opt": stats(r_, cash[m], E_),
                         "equal": stats(r0, cash[m], None), "spy": stats(r_next[m], cash[m], None)})
    wf = {"lambda": lam, "by_lambda": {str(k): v for k, v in wf_by_lam.items()}, "opt": wf_by_lam[lam], "equal": wf_by_lam[0],
          "years": wf_years}
    full_w = optimize(tr | te)
    eq_w = [1.0] * len(KEYS)
    res = {}
    for nm, w in (("opt", best_w), ("equal", eq_w)):
        res[nm] = {}
        for part, mask in (("train", tr), ("test", te), ("all", tr | te)):
            rets, E = run(w, mask)
            res[nm][part] = stats(rets, cash[mask], E)
    res["spy"] = {part: stats(r_next[mask], cash[mask], None) for part, mask in (("train", tr), ("test", te), ("all", tr | te))}
    # 채택: 걷기 검증 합산 샤프가 같은 가중치보다 낫거나 같으면 → 전체 기간으로 찾은 가중치 사용
    adopt = "opt" if lam > 0 else "equal"
    use = [round(lam * x + (1 - lam) * 1.0, 3) for x in full_w]
    # 월말 누적 곡선 (전략·같은 가중치·SPY)
    curve, e1, e2, e3 = [], 1.0, 1.0, 1.0
    r1, _ = run(use, tr | te)
    r2, _ = run(eq_w, tr | te)
    for i, d in enumerate(days[:-1]):
        e1 *= 1 + r1[i]; e2 *= 1 + r2[i]; e3 *= 1 + r_next[i]
        if i + 1 == len(days) - 1 or days[i + 1][:7] != d[:7]:
            curve.append([d, round(e1, 4), round(e2, 4), round(e3, 4)])
    # 항목별 단독 기여(그 항목만 0 으로 뺐을 때 검증 샤프 변화)
    contrib = {}
    for i, k in enumerate(KEYS):
        w2 = use[:i] + [0] + use[i + 1:]
        contrib[k] = round(float((sharpe(use, te) - sharpe(w2, te)) * np.sqrt(252)), 3)
    out = {"asof": days[-1], "train": [days[0], TRAIN_END], "test": [next(d for d in days if d > TRAIN_END), days[-1]],
           "adopted": adopt, "weights": {k: v for k, v in zip(KEYS, use)} | {"gex": 1.0},
           "optimized": {k: v for k, v in zip(KEYS, full_w)}, "optimized_train": {k: v for k, v in zip(KEYS, best_w)},
           "walk_forward": wf, "names": NAMES, "stats": res, "contrib": contrib,
           "curve": curve, "grid": GRID, "note": "딜러 감마(GEX)는 과거 자료가 없어 가중치 1 고정"}
    json.dump(out, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f"[regime_opt] 학습 샤프 {res['opt']['train']['sharpe']} (같은 가중치 {res['equal']['train']['sharpe']}) · "
          f"검증 {res['opt']['test']['sharpe']} vs {res['equal']['test']['sharpe']} · 걷기 검증 λ별 {[(k, v['sharpe']) for k, v in wf_by_lam.items()]} λ={lam} "
          f"→ {adopt} {dict(zip(KEYS, use))}")
    for y in wf_years:
        print(f"   {y['year']}: 최적 {y['opt']['sharpe']} / 같은 {y['equal']['sharpe']} / SPY {y['spy']['sharpe']} · {y['weights']}")


if __name__ == "__main__":
    main()
