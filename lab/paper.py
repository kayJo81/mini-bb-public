"""
가상 매매(페이퍼 트레이딩) — 미니 블벅 랩 신호를 실제로 따라 했다면?  (매 랩 실행 뒤, 상태: labdata/paper.json)

  python lab/paper.py --signals public_lab/data.json --out public_lab

규칙 (처음 실행 때 정해져 상태 파일에 저장 — 도중에 바뀌지 않음)
  · 매 거래일 '종목 후보'(signals.py picks: 뉴스 급증 z≥1.5·3건↑·논조 차이 2↑, 과거 표본 충분한 유형)에서
    이미 보유한 종목을 빼고 순위대로 최대 4종목 — 통과 종목이 적으면 그만큼만, 없으면 쉼
  · 시작 자금 $100,000, 종목당 $5,000 (하루 최대 $20,000, 현금이 모자라면 건너뜀)
  · 신호가 뜬 날의 '다음 거래일 시가'에 진입 (신호는 장 마감 뒤에 계산되므로 그날 종가로는 살 수 없음)
  · 방향: 그 신호 유형의 과거 5일 평균 초과수익 부호(롱/숏) — '검증됨'이 아니어도 시험 삼아 따라 함
  · 진입 후 5거래일째 종가에 청산, 매매마다 0.1% 비용(진입·청산 각각)
  · 새 신호는 처음 10거래일 동안만 받고, 남은 포지션이 모두 청산되면 끝
  · 비교: 같은 금액을 같은 날 QQQ 에 넣었다 같은 날 뺐을 때
"""
import argparse
import json
import os
import sys
from bisect import bisect_right

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import BENCH, DATA  # noqa: E402

RULES = {"version": 2, "capital": 100000.0, "size": 5000.0, "max_per_day": 4, "hold": 5, "cost": 0.001, "entry_days": 10}


def nxt(cal, d):
    i = bisect_right(cal, d)
    return cal[i] if i < len(cal) else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--signals", default="public_lab/data.json")
    ap.add_argument("--out", default="public_lab")
    a = ap.parse_args()
    sig = json.load(open(a.signals, encoding="utf-8"))
    prices = json.load(open(os.path.join(DATA, "prices.json"), encoding="utf-8"))
    cal = sorted(prices[BENCH])
    path = os.path.join(DATA, "paper.json")
    try:
        st = json.load(open(path, encoding="utf-8"))
        if st.get("rules", {}).get("version") != RULES["version"]:
            raise ValueError("규칙이 바뀌어 새로 시작")
    except Exception:
        last = sig["last_trading_day"]
        st = {"rules": RULES, "start": last, "signal_days": [], "seen": [], "pending": [], "open": [], "closed": [],
              "cash": RULES["capital"], "curve": [], "picks_log": []}
    R = st["rules"]
    px = lambda s, d, k: prices.get(s, {}).get(d, [None, None])[k]  # noqa: E731  k=0 시가, 1 종가

    # 1) 새 종목 받기: 날짜별로 보유·대기 중이 아닌 종목을 순위대로 최대 4개 (처음 10거래일 동안, 시작일 이후)
    held = {p["sym"] for p in st["open"] + st["pending"]}
    by_day = {}
    for c in sig.get("picks", []):
        if c["date"] not in st["signal_days"]:
            by_day.setdefault(c["date"], []).append(c)
    # 시작일부터 마지막 거래일까지 모든 거래일을 한 번씩 (후보가 없는 날도 '쉰 날'로 센다)
    for day in [d for d in cal if st["start"] <= d <= sig["last_trading_day"] and d not in st["signal_days"]]:
        if len(st["signal_days"]) >= R["entry_days"]:
            break
        st["signal_days"].append(day)
        chosen = []
        for c in by_day.get(day, []):
            if len(chosen) >= R["max_per_day"]:
                break
            if c["sym"] in held:
                continue
            held.add(c["sym"])
            chosen.append(c)
            st["pending"].append({"sym": c["sym"], "dir": c["direction"], "b": c["b"], "verdict": c["verdict"],
                                  "signal_date": c["date"], "edge": c["edge"], "z": c["z"], "head": (c.get("heads") or [[0, ""]])[0][1]})
        st.setdefault("picks_log", []).append({"date": day, "chosen": [c["sym"] for c in chosen],
                                               "pool": len(by_day.get(day, []))})

    # 2) 진입: 신호 다음 거래일 시가가 나왔으면 체결
    still = []
    for p in st["pending"]:
        d = nxt(cal, p["signal_date"])
        o, bo = (px(p["sym"], d, 0), px(BENCH, d, 0)) if d else (None, None)
        if not (o and bo):
            still.append(p)
            continue
        if st["cash"] < R["size"]:
            p["skipped"] = "현금 부족"
            st["closed"].append(p)
            continue
        st["cash"] -= R["size"]
        st["open"].append(p | {"entry_date": d, "entry": o, "bench_entry": bo, "size": R["size"]})
    st["pending"] = still

    # 3) 평가·청산: 진입일 포함 5거래일째 종가
    sign = {"롱": 1, "숏": -1}
    keep, mtm, bench_mtm = [], 0.0, 0.0
    for p in st["open"]:
        i = cal.index(p["entry_date"])
        exit_d = cal[i + R["hold"] - 1] if i + R["hold"] - 1 < len(cal) else None
        last_d = exit_d if exit_d else cal[-1]
        c, bc = px(p["sym"], last_d, 1), px(BENCH, last_d, 1)
        if not (c and bc):
            keep.append(p)
            mtm += p["size"]
            bench_mtm += p["size"]
            continue
        ret = sign[p["dir"]] * (c / p["entry"] - 1) - 2 * R["cost"]
        bret = bc / p["bench_entry"] - 1 - 2 * R["cost"]
        if exit_d:
            st["cash"] += p["size"] * (1 + ret)
            st["closed"].append(p | {"exit_date": exit_d, "exit": c, "ret": round(ret * 100, 2), "bench_ret": round(bret * 100, 2),
                                     "pnl": round(p["size"] * ret, 2)})
        else:
            keep.append(p | {"now": c, "ret": round(ret * 100, 2), "bench_ret": round(bret * 100, 2), "asof": last_d})
            mtm += p["size"] * (1 + ret)
            bench_mtm += p["size"] * (1 + bret)
    st["open"] = keep

    equity = st["cash"] + mtm
    done = [x for x in st["closed"] if "ret" in x]
    bench_equity = R["capital"] + sum(x["size"] * x["bench_ret"] / 100 for x in done) + (bench_mtm - sum(p["size"] for p in keep))
    st["curve"] = [x for x in st["curve"] if x[0] != cal[-1]] + [[cal[-1], round(equity, 2), round(bench_equity, 2)]]
    finished = len(st["signal_days"]) >= R["entry_days"] and not st["open"] and not st["pending"]
    st["status"] = "종료" if finished else "진행 중"
    json.dump(st, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    wins = [x for x in done if x["ret"] > 0]
    summary = {
        "status": st["status"], "start": st["start"], "asof": cal[-1], "rules": R,
        "signal_days": len(st["signal_days"]), "equity": round(equity, 2), "bench_equity": round(bench_equity, 2),
        "ret": round((equity / R["capital"] - 1) * 100, 2), "bench_ret": round((bench_equity / R["capital"] - 1) * 100, 2),
        "trades": len(done), "win_rate": round(len(wins) / len(done) * 100, 1) if done else None,
        "avg_trade": round(sum(x["ret"] for x in done) / len(done), 2) if done else None,
        "avg_excess": round(sum(x["ret"] - x["bench_ret"] for x in done) / len(done), 2) if done else None,
        "open": st["open"], "pending": st["pending"], "closed": sorted(st["closed"], key=lambda x: x.get("exit_date", ""), reverse=True),
        "curve": st["curve"], "picks_log": st.get("picks_log", []), "pick_rules": sig.get("pick_rules"),
        "today_pool": [c for c in sig.get("picks", []) if c["date"] == sig["last_trading_day"]],
    }
    os.makedirs(a.out, exist_ok=True)
    json.dump(summary, open(os.path.join(a.out, "paper.json"), "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f"[paper] {st['status']} · 신호일 {len(st['signal_days'])}/{R['entry_days']} · 평가액 ${equity:,.0f} "
          f"({summary['ret']:+.2f}%, QQQ {summary['bench_ret']:+.2f}%) · 보유 {len(keep)} · 대기 {len(st['pending'])} · 청산 {len(done)}")


if __name__ == "__main__":
    main()
