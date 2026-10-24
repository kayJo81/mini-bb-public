"""
Top Picks Lab 기업 데이터 검사 — 구조·길이·합계를 확인한다.

  python lab/toppicks/validate.py lab/toppicks/NVDA.json [...]      (인자 없으면 폴더의 *.json 전부)

  · 분기 12개(오래된 순), 모든 값 배열 길이 12, 숫자 또는 null
  · 사업별 매출 합계 ↔ 총매출: 3% 넘게 차이 나는 분기는 경고 (기타 항목을 넣어 맞출 것)
  · 매출총이익 ≤ 매출, 영업이익·순이익 절댓값 ≤ 매출 × 3, 분기 끝 날짜가 오름차순·간격 약 3개월
"""
import glob
import json
import os
import sys
from datetime import date

UNITS = {"usd_m", "usd", "pct", "count", "count_k", "count_m", "gwh", "mw", "gw", "x", "text"}
REQ = ["revenue", "gross_profit", "op_income", "net_income", "eps_diluted"]


def check(path):
    err, warn = [], []
    d = json.load(open(path, encoding="utf-8"))
    for k in ("ticker", "name", "name_en", "fiscal_note", "currency", "updated", "latest_reported", "quarters", "metrics", "segments", "kpis", "sources"):
        if k not in d:
            err.append(f"필드 없음: {k}")
    if err:
        return err, warn
    Q = d["quarters"]
    n = len(Q)
    if n != 12:
        err.append(f"quarters 가 12개가 아님({n})")
    ends = [date.fromisoformat(q["end"]) for q in Q]
    if ends != sorted(ends):
        err.append("quarters 가 오래된 순이 아님")
    for a, b in zip(ends, ends[1:]):
        if not 70 <= (b - a).days <= 112:
            warn.append(f"분기 간격 이상: {a} → {b} ({(b - a).days}일)")

    def series(group, items, need_unit=True):
        for it in items:
            v = it.get("v")
            if not isinstance(v, list) or len(v) != n:
                err.append(f"{group}/{it.get('name') or it.get('key')}: v 길이가 {n}이 아님")
                continue
            if need_unit and it.get("unit") not in UNITS:
                err.append(f"{group}/{it.get('name') or it.get('key')}: unit 이 {sorted(UNITS)} 중 하나가 아님 ({it.get('unit')})")
            if any(x is not None and not isinstance(x, (int, float)) for x in v) and it.get("unit") != "text":
                err.append(f"{group}/{it.get('name') or it.get('key')}: 숫자가 아닌 값")
            if not it.get("name"):
                err.append(f"{group}: name 없음")
    series("metrics", d["metrics"])
    series("segments", d["segments"], need_unit=False)
    series("kpis", d["kpis"])
    M = {m["key"]: m for m in d["metrics"] if "key" in m}
    for k in REQ:
        if k not in M:
            err.append(f"필수 지표 없음: {k}")
    rev = (M.get("revenue") or {}).get("v") or [None] * n
    gp = (M.get("gross_profit") or {}).get("v") or [None] * n
    for i in range(n):
        if rev[i] is None:
            warn.append(f"{Q[i]['label']}: 매출 null")
            continue
        if gp[i] is not None and gp[i] > rev[i] * 1.001:
            err.append(f"{Q[i]['label']}: 매출총이익({gp[i]}) > 매출({rev[i]})")
        for k in ("op_income", "net_income"):
            x = (M.get(k) or {}).get("v", [None] * n)[i]
            if x is not None and abs(x) > abs(rev[i]) * 3:
                warn.append(f"{Q[i]['label']}: {k}({x}) 가 매출({rev[i]})보다 3배 넘게 큼 — 단위 확인")
    segs = [s for s in d["segments"] if not s.get("exclude_from_sum")]
    if segs:
        for i in range(n):
            vals = [s["v"][i] for s in segs if len(s.get("v", [])) == n]
            if rev[i] is None or any(v is None for v in vals) or not vals:
                continue
            diff = (sum(vals) / rev[i] - 1) * 100
            if abs(diff) > 3:
                warn.append(f"{Q[i]['label']}: 사업별 합({sum(vals):,.0f}) ↔ 매출({rev[i]:,.0f}) {diff:+.1f}%")
    if not d["sources"]:
        err.append("sources 비어 있음")
    for s in d["sources"]:
        if not str(s.get("url", "")).startswith("http"):
            err.append(f"sources url 이상: {s}")
    return err, warn


if __name__ == "__main__":
    files = sys.argv[1:] or sorted(f for f in glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), "*.json")) if not f.endswith("index.json"))
    bad = 0
    for f in files:
        try:
            e, w = check(f)
        except Exception as ex:
            e, w = [f"읽기 실패: {ex}"], []
        print(f"{'✗' if e else '✓'} {os.path.basename(f)}  오류 {len(e)} · 경고 {len(w)}")
        for x in e:
            print("   오류:", x)
        for x in w:
            print("   경고:", x)
        bad += bool(e)
    sys.exit(1 if bad else 0)
