"""
뉴스 신호 → 과거 검증(이벤트 스터디) → 오늘의 롱/숏 후보  (결과: <out>/data.json)

  python lab/signals.py --out public_lab

신호(이벤트): 그날 회사 언급 기사 수가 직전 60거래일 평균보다 크게 많고(z ≥ 2, 3건 이상)
             호재/악재 기사 수 차이가 뚜렷한 날 → 논조(호재·악재) × 당일 주가 반응(상승·하락) 4가지로 나눔
검증: 이벤트 날 종가에 들어가서 1·5·10거래일 뒤 종가까지의 'QQQ 대비 초과수익'
      · 같은 종목 이벤트가 5거래일 안에 또 나오면 첫 번째만 (겹치는 표본으로 부풀리지 않게)
      · 전반기/후반기로 나눠 둘 다 같은 방향인지 (우연 걸러내기)
      · 5일 기준 표본 30건 이상 · |t| ≥ 2 · 두 기간 같은 방향 → '검증됨', 방향은 과거 평균의 부호
"""
import argparse
import json
import math
import os
import sys
from bisect import bisect_left
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import BENCH, DATA, tickers  # noqa: E402

KST = timezone(timedelta(hours=9))
LOOKBACK = 60
MIN_HIST = 40
Z_MIN, N_MIN, NET_MIN = 2.0, 3, 2
HORIZONS = (1, 5, 10)
DEDUP = 5
BUCKETS = [
    ("pos_up", "관심 급증 · 호재 · 당일 상승", "호재 기사가 몰렸고 주가도 올랐던 날"),
    ("pos_dn", "관심 급증 · 호재 · 당일 하락", "호재 기사가 몰렸는데 주가는 내렸던 날"),
    ("neg_dn", "관심 급증 · 악재 · 당일 하락", "악재 기사가 몰렸고 주가도 내렸던 날"),
    ("neg_up", "관심 급증 · 악재 · 당일 상승", "악재 기사가 몰렸는데 주가는 올랐던 날"),
]


def _stats(xs):
    n = len(xs)
    if n == 0:
        return None
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1)) if n > 1 else 0
    s = sorted(xs)
    return {"n": n, "mean": round(m * 100, 2), "median": round(s[n // 2] * 100, 2),
            "hit": round(sum(x > 0 for x in xs) / n * 100, 1), "t": round(m / (sd / math.sqrt(n)), 2) if sd else 0}


def load():
    prices = json.load(open(os.path.join(DATA, "prices.json"), encoding="utf-8"))
    cal = sorted(prices[BENCH])
    news = {}
    for sym in tickers():
        try:
            news[sym] = json.load(open(os.path.join(DATA, "news", f"{sym}.json"), encoding="utf-8"))
        except Exception:
            news[sym] = {}
    return prices, cal, news


def series(sym, prices, cal, raw):
    """거래일 기준 일간 표: 뉴스 집계(주말·휴일 뉴스는 다음 거래일로) + 주가"""
    if not raw:
        return []
    start = min(raw)
    rows = {d: {"n": 0, "pos": 0, "neg": 0, "s": 0, "heads": []} for d in cal if d >= start}
    for d, v in raw.items():
        i = bisect_left(cal, d)
        if i >= len(cal):
            continue          # 아직 거래일이 오지 않은 날(예: 오늘 장 마감 후 기사)은 다음 거래일에 반영
        r = rows.get(cal[i])
        if r is None:
            continue
        for k in ("n", "pos", "neg", "s"):
            r[k] += v[k]
        r["heads"] += v["heads"]
    px, bx = prices.get(sym, {}), prices[BENCH]
    out = []
    for d in sorted(rows):
        if d not in px:
            continue
        r = rows[d]
        r["date"] = d
        r["close"] = px[d][1]
        r["bclose"] = bx[d][1]
        out.append(r)
    for i, r in enumerate(out):
        hist = [x["n"] for x in out[max(0, i - LOOKBACK):i]]
        if len(hist) >= MIN_HIST:
            m = sum(hist) / len(hist)
            sd = math.sqrt(sum((x - m) ** 2 for x in hist) / len(hist))
            r["avg"] = round(m, 2)
            r["z"] = round((r["n"] - m) / max(sd, 1.0), 2)
        else:
            r["avg"], r["z"] = None, None
        r["react"] = (round((r["close"] / out[i - 1]["close"] - r["bclose"] / out[i - 1]["bclose"]) * 100, 2)
                      if i else None)
        for h in HORIZONS:
            r[f"f{h}"] = ((out[i + h]["close"] / r["close"]) - (out[i + h]["bclose"] / r["bclose"])
                          if i + h < len(out) else None)
    return out


def bucket_of(r):
    if r.get("z") is None or r["z"] < Z_MIN or r["n"] < N_MIN or r["react"] is None:
        return None
    net = r["pos"] - r["neg"]
    if net >= NET_MIN:
        return "pos_up" if r["react"] > 0 else "pos_dn"
    if net <= -NET_MIN:
        return "neg_dn" if r["react"] < 0 else "neg_up"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="public_lab")
    a = ap.parse_args()
    prices, cal, news = load()
    events, latest_rows, cover = [], {}, []
    for sym in tickers():
        rows = series(sym, prices, cal, news[sym])
        if not rows:
            continue
        cover.append(rows[0]["date"])
        last_ev = -99
        for i, r in enumerate(rows):
            b = bucket_of(r)
            if b and i - last_ev >= DEDUP:
                events.append({"sym": sym, "i": i, "b": b, **{k: r[k] for k in ("date", "n", "avg", "z", "pos", "neg", "react")},
                               **{f"f{h}": r[f"f{h}"] for h in HORIZONS}, "heads": r["heads"][:4]})
                last_ev = i
        latest_rows[sym] = rows

    dates = sorted(e["date"] for e in events)
    mid = dates[len(dates) // 2] if dates else ""
    buckets = []
    for bid, name, desc in BUCKETS:
        evs = [e for e in events if e["b"] == bid]
        st = {f"h{h}": _stats([e[f"f{h}"] for e in evs if e[f"f{h}"] is not None]) for h in HORIZONS}
        first = _stats([e["f5"] for e in evs if e["f5"] is not None and e["date"] < mid])
        second = _stats([e["f5"] for e in evs if e["f5"] is not None and e["date"] >= mid])
        s5 = st["h5"]
        consistent = bool(first and second and first["mean"] * second["mean"] > 0)
        if not s5 or s5["n"] < 30:
            verdict, direction = "표본 부족", None
        elif abs(s5["t"]) >= 2 and consistent:
            verdict, direction = "검증됨", ("롱" if s5["mean"] > 0 else "숏")
        else:
            verdict, direction = "근거 약함", ("롱" if s5["mean"] > 0 else "숏")
        buckets.append({"id": bid, "name": name, "desc": desc, **st, "first": first, "second": second,
                        "consistent": consistent, "verdict": verdict, "direction": direction})
    bmap = {b["id"]: b for b in buckets}

    # 오늘의 후보: 최근 3거래일 이벤트
    recent = set(cal[-3:])
    cands = []
    for e in events:
        if e["date"] in recent:
            b = bmap[e["b"]]
            cands.append({k: e[k] for k in ("sym", "date", "b", "n", "avg", "z", "pos", "neg", "react", "heads")} |
                         {"verdict": b["verdict"], "direction": b["direction"], "h5": b["h5"]})
    cands.sort(key=lambda c: ({"검증됨": 0, "근거 약함": 1}.get(c["verdict"], 2), c["date"] == cal[-1] and 0 or 1, -c["z"]))

    # 오늘 뉴스량 상위 (이벤트 조건과 무관하게 관심이 몰린 종목)
    movers = []
    for sym, rows in latest_rows.items():
        r = rows[-1]
        if r.get("z") is not None and r["n"] >= 2:
            movers.append({"sym": sym, "date": r["date"], "n": r["n"], "avg": r["avg"], "z": r["z"], "pos": r["pos"],
                           "neg": r["neg"], "react": r["react"], "heads": r["heads"][:3],
                           "spark": [x["n"] for x in rows[-30:]], "tone": [x["pos"] - x["neg"] for x in rows[-30:]]})
    movers.sort(key=lambda m: -m["z"])

    past = sorted([e for e in events if e["f5"] is not None], key=lambda e: e["date"], reverse=True)[:60]
    out = {
        "generated": datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
        "last_trading_day": cal[-1],
        "coverage": {"start": min(cover) if cover else None, "end": cal[-1], "tickers": len(latest_rows),
                     "events": len(events), "split": mid},
        "rules": {"z": Z_MIN, "n": N_MIN, "net": NET_MIN, "lookback": LOOKBACK, "dedup": DEDUP, "bench": BENCH},
        "buckets": buckets,
        "candidates": cands,
        "movers": movers[:20],
        "past": [{k: e[k] for k in ("sym", "date", "b", "n", "z", "pos", "neg", "react")} |
                 {f"f{h}": round(e[f"f{h}"] * 100, 2) if e[f"f{h}"] is not None else None for h in HORIZONS} |
                 {"head": e["heads"][0][1] if e["heads"] else ""} for e in past],
    }
    os.makedirs(a.out, exist_ok=True)
    json.dump(out, open(os.path.join(a.out, "data.json"), "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f"[signals] 기간 {out['coverage']['start']}~{cal[-1]}, 이벤트 {len(events)}건, 후보 {len(cands)}건")
    for b in buckets:
        s = b["h5"] or {}
        print(f"  {b['name']}: N={s.get('n')} 5일 {s.get('mean')}% t={s.get('t')} 적중 {s.get('hit')}% → {b['verdict']} {b['direction'] or ''}")


if __name__ == "__main__":
    main()
