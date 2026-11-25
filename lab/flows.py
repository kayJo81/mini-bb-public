"""글로벌 머니플로우 — 1주 단위로 자금이 어디로 움직이는지 추적  (상태: labdata/flows.json, 출력: public_lab/flows.json)

  python lab/flows.py --out public_lab

무엇을 재나
  A) 실측 — 외국인 순매수(그 시장 거래소·포털이 공개하는 값, 주(월~금)별 합계)
       · 한국: 네이버 증권 모바일 API 의 코스피·코스닥 외국인 순매수(억원) — 매일 저장해 쌓음(과거는 못 받아 오늘부터)
       · 대만: 대만증권거래소(TWSE) 삼대법인 매매금액 '외자 및 중국계'(억 NT$) — 날짜별 조회라 최근 8주 소급
       · (인도 FPI 등 다른 시장은 믿을 만한 자동 수집원이 없어 아직 없음 → 화면에 '미수집'으로 표시)
  B) 정황 — 시장별 주간 수익률(현지·달러 환산)·환율·미국(S&P) 대비 상대강도
       '지수가 오르고 통화도 강하면 자금 유입 정황, 둘 다 약하면 유출 정황' (직접 통계가 아니라 해석)
  C) 안전자산·위험자산 — 금·달러지수·미 10년 금리·브렌트·비트코인·VIX 주간 변화
  · 주 = 월~금 (마지막 주는 진행 중이면 '진행 중' 표시), 최근 8주
  · 시세는 야후 파이낸스 차트(공개 JSON), 모든 수집은 실패해도 화면이 깨지지 않게 시장별로 따로 처리
"""
import argparse
import datetime as dt
import json
import os
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA  # noqa: E402

UA = {"User-Agent": "MiniBBLab research"}
KST = dt.timezone(dt.timedelta(hours=9))
NWEEKS = 8

# 키, 이름, 묶음, 지수 심볼, 환율 심볼, 환율 종류(per_usd: 1달러가 현지 통화 얼마 / usd_per: 1현지통화가 달러 얼마)
MARKETS = [
    ("KR", "한국 코스피", "동아시아", "^KS11", "KRW=X", "per_usd"),
    ("TW", "대만 가권", "동아시아", "^TWII", "TWD=X", "per_usd"),
    ("JP", "일본 닛케이", "동아시아", "^N225", "JPY=X", "per_usd"),
    ("CN", "중국 상하이", "동아시아", "000001.SS", "CNY=X", "per_usd"),
    ("HK", "홍콩 항셍", "동아시아", "^HSI", "HKD=X", "per_usd"),
    ("IN", "인도 니프티50", "남아시아·동남아", "^NSEI", "INR=X", "per_usd"),
    ("ID", "인도네시아 JCI", "남아시아·동남아", "^JKSE", "IDR=X", "per_usd"),
    ("EEM", "신흥시장 ETF(EEM)", "신흥·선진", "EEM", None, None),
    ("US", "미국 S&P500", "신흥·선진", "^GSPC", None, None),
    ("NDX", "미국 나스닥", "신흥·선진", "^IXIC", None, None),
    ("EU", "유로 스톡스50", "신흥·선진", "^STOXX50E", "EURUSD=X", "usd_per"),
    ("UK", "영국 FTSE100", "신흥·선진", "^FTSE", "GBPUSD=X", "usd_per"),
]
CROSS = [
    ("GOLD", "금", "GC=F", "pct", "안전자산 선호"),
    ("DXY", "달러지수", "DX-Y.NYB", "pct", "오르면 신흥국 자금 이탈 압력"),
    ("UST10", "미 10년 금리", "^TNX", "pp", "오르면 위험자산 할인율 부담"),
    ("BRENT", "브렌트유", "BZ=F", "pct", "내리면 수입국(인도 등)에 호재"),
    ("BTC", "비트코인", "BTC-USD", "pct", "유동성·위험 선호"),
    ("VIX", "VIX", "^VIX", "pp", "공포 지수"),
]


def load(path, default=None):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return default


def monday(d):
    return d - dt.timedelta(days=d.weekday())


def history(sym):
    """일별 종가 {날짜: 종가} (거래소 현지 날짜). 실패하면 None"""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{requests.utils.quote(sym)}?range=6mo&interval=1d"
    for k in range(3):
        try:
            r = requests.get(url.replace("query1", "query2" if k % 2 else "query1"), headers=UA, timeout=25)
            if r.status_code == 429:
                time.sleep(3 + 3 * k)
                continue
            res = r.json()["chart"]["result"][0]
            off = res["meta"].get("gmtoffset", 0)
            out = {}
            for t, c in zip(res["timestamp"], res["indicators"]["quote"][0]["close"]):
                if c is not None:
                    out[(dt.datetime.fromtimestamp(t, dt.timezone.utc) + dt.timedelta(seconds=off)).date()] = float(c)
            return out or None
        except Exception:
            time.sleep(1)
    return None


def weekly(h, last_monday):
    """주(월요일 날짜) → 그 주 마지막 거래일 종가. 최근 NWEEKS+1 주"""
    wk = {}
    for d in sorted(h):
        wk[monday(d)] = (d, h[d])
    mons = [last_monday - dt.timedelta(weeks=i) for i in range(NWEEKS, -1, -1)]
    return mons, wk


def pct(a, b):
    return None if a is None or b is None or b == 0 else (a / b - 1) * 100


def market_rows(today):
    last = monday(today)
    mons = [last - dt.timedelta(weeks=i) for i in range(NWEEKS, -1, -1)]
    cache = {}

    def get(sym):
        if sym not in cache:
            cache[sym] = history(sym)
            time.sleep(1.2)
        return cache[sym]
    spy_h = get("^GSPC")
    spy_w = weekly(spy_h, last)[1] if spy_h else {}
    rows = []
    for key, name, grp, sym, fxs, kind in MARKETS:
        h = get(sym)
        if not h:
            rows.append({"key": key, "name": name, "group": grp, "ok": False})
            continue
        w = weekly(h, last)[1]
        fh = get(fxs) if fxs else None
        fw = weekly(fh, last)[1] if fh else {}
        weeks = []
        for i in range(1, len(mons)):
            m0, m1 = mons[i - 1], mons[i]
            if m1 not in w or m0 not in w:
                weeks.append(None)
                continue
            ret = pct(w[m1][1], w[m0][1])
            fx = None
            if fxs and m1 in fw and m0 in fw:
                fx = pct(fw[m0][1], fw[m1][1]) if kind == "per_usd" else pct(fw[m1][1], fw[m0][1])   # + 이면 현지 통화 강세
            usd = ((1 + ret / 100) * (1 + (fx or 0) / 100) - 1) * 100 if ret is not None else None
            if fxs and fx is None:
                usd = None
            spy = pct(spy_w[m1][1], spy_w[m0][1]) if m1 in spy_w and m0 in spy_w else None
            partial = (i == len(mons) - 1) and w[m1][0].weekday() < 4 and today <= m1 + dt.timedelta(days=4)
            weeks.append({"w": (m1 + dt.timedelta(days=4)).isoformat(), "ret": r2(ret), "fx": r2(fx), "usd": r2(usd),
                          "rel": r2(None if usd is None or spy is None else usd - spy), "partial": bool(partial), "last": w[m1][0].isoformat()})
        # 최근 4주 누적(달러 환산)
        c4 = None
        v = [x["usd"] for x in weeks[-4:] if x and x["usd"] is not None]
        if len(v) == 4:
            c4 = 1.0
            for x in v:
                c4 *= 1 + x / 100
            c4 = (c4 - 1) * 100
        rows.append({"key": key, "name": name, "group": grp, "ok": True, "weeks": weeks, "usd4w": r2(c4), "has_fx": bool(fxs),
                     "last_close": round(h[max(h)], 2), "last_date": max(h).isoformat()})
    cross = []
    for key, name, sym, mode, why in CROSS:
        h = get(sym)
        if not h:
            cross.append({"key": key, "name": name, "why": why, "ok": False})
            continue
        w = weekly(h, last)[1]
        vals = []
        for i in range(1, len(mons)):
            a, b = w.get(mons[i]), w.get(mons[i - 1])
            if not a or not b:
                vals.append(None)
            else:
                vals.append(r2(a[1] - b[1]) if mode == "pp" else r2(pct(a[1], b[1])))
        cross.append({"key": key, "name": name, "why": why, "ok": True, "mode": mode, "weeks": vals,
                      "last": round(h[max(h)], 2), "last_date": max(h).isoformat()})
    return rows, cross, [(m + dt.timedelta(days=4)).isoformat() for m in mons[1:]]


def r2(x):
    return None if x is None else round(x, 2)


# ── 실측: 외국인 순매수 ──────────────────────────────────────────
def num(s):
    return float(str(s).replace(",", "").replace("+", ""))


def kr_today(state, now):
    """한국: 네이버 증권 모바일 API (장 마감 뒤 값만 저장)"""
    st = state.setdefault("KR", {})
    try:
        tot, bd = 0.0, None
        for idx in ("KOSPI", "KOSDAQ"):
            j = requests.get(f"https://m.stock.naver.com/api/index/{idx}/trend", headers=UA, timeout=20).json()
            bd = j["bizdate"]
            tot += num(j["foreignValue"])
            st.setdefault(bd, {})[idx.lower()] = num(j["foreignValue"])
        d = dt.datetime.strptime(bd, "%Y%m%d").date()
        if d == now.date() and now.hour < 16:      # 장중 값은 저장하지 않음
            st.pop(bd, None)
            return
        st[bd]["total"] = tot
    except Exception as e:
        print("[flows] 한국 수집 실패:", e)


def tw_range(state, today, maxreq=16):
    """대만: TWSE 삼대법인 매매금액 '외자 및 중국계 (자영 제외)' 순매수 — 최근 날짜부터 거꾸로, 한 번에 maxreq 건만
       (거래소가 연속 요청을 막으므로 며칠에 걸쳐 8주를 채움. 휴장일은 None 으로 기록해 다시 안 물음)"""
    st = state.setdefault("TW", {})
    d, done, fails = today, 0, 0
    stop = monday(today - dt.timedelta(weeks=NWEEKS + 1))
    while d >= stop and done < maxreq:
        key = d.strftime("%Y%m%d")
        if d.weekday() < 5 and key not in st:
            try:
                j = requests.get("https://www.twse.com.tw/rwd/zh/fund/BFI82U", params={"type": "day", "dayDate": key, "response": "json"},
                                 headers=UA, timeout=20).json()
                if j.get("stat") == "OK":
                    row = next((r for r in j["data"] if r[0].startswith("外資及陸資")), None)
                    if row:
                        st[key] = {"net": num(row[3]) / 1e8}     # 억 NT$
                else:
                    st[key] = None                              # 휴장일·아직 안 나온 날 — 오늘 것만 다시 물음
                    if d == today:
                        st.pop(key, None)
                done += 1
                fails = 0
                time.sleep(3)
            except Exception as e:
                fails += 1
                if fails >= 2:
                    print("[flows] 대만 수집 중단(다음 실행에서 이어서):", e)
                    return
                time.sleep(12)
                continue
        d -= dt.timedelta(days=1)


def actual_weeks(st, field, mons):
    out = []
    for ms in mons:
        m = dt.date.fromisoformat(ms)
        tot, n = 0.0, 0
        for i in range(5):
            k = (m - dt.timedelta(days=4) + dt.timedelta(days=i)).strftime("%Y%m%d")      # m 은 금요일 → 그 주 월~금
            v = (st or {}).get(k)
            if v and v.get(field) is not None:
                tot += v[field]
                n += 1
        out.append({"w": ms, "net": round(tot) if n else None, "days": n})
    return out


def summary(rows, actual, cross, wk_last):
    good = [r for r in rows if r.get("ok") and r["weeks"][-1] and r["weeks"][-1]["usd"] is not None]
    asia = [r for r in good if r["group"] != "신흥·선진" or r["key"] == "EEM"]
    lines = []
    if good:
        srt = sorted([r for r in good if r["group"] != "신흥·선진"], key=lambda r: -r["weeks"][-1]["usd"])
        if srt:
            a, b = srt[0], srt[-1]
            lines.append(f"이번 주 달러 환산 수익률은 {a['name']} {a['weeks'][-1]['usd']:+.1f}%가 가장 좋고 {b['name']} {b['weeks'][-1]['usd']:+.1f}%가 가장 약해요(아시아 시장 중).")
    for k, nm, unit in (("KR", "한국", "억원"), ("TW", "대만", "억 NT$")):
        a = actual.get(k, {}).get("weeks", [])
        if a and a[-1]["net"] is not None:
            v = a[-1]["net"]
            lines.append(f"{nm} 외국인은 이번 주 {abs(v):,}{unit} 순{'매수' if v > 0 else '매도'}예요({a[-1]['days']}거래일 합계, 실측).")
    for c in cross:
        if c["ok"] and c["key"] in ("BRENT", "DXY") and c["weeks"][-1] is not None:
            lines.append(f"{c['name']} {c['weeks'][-1]:+.1f}%({c['why']}).")
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="public_lab")
    a = ap.parse_args()
    now = dt.datetime.now(KST)
    today = now.date()
    path = os.path.join(DATA, "flows.json")
    state = load(path, {}) or {}
    rows, cross, wk = market_rows(today)
    kr_today(state, now)
    tw_range(state, today)
    os.makedirs(DATA, exist_ok=True)
    json.dump(state, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    actual = {"KR": {"unit": "억원", "name": "한국(코스피+코스닥)", "weeks": actual_weeks(state.get("KR"), "total", wk)},
              "TW": {"unit": "억 NT$", "name": "대만(외자·중국계)", "weeks": actual_weeks(state.get("TW"), "net", wk)}}
    out = {"asof": now.isoformat(timespec="minutes"), "weeks": wk, "markets": rows, "cross": cross, "actual": actual,
           "summary": summary(rows, actual, cross, wk), "missing": ["인도 FPI(자동 수집원 없음)", "일본·중국 외국인 순매수", "ETF 자금 유출입"]}
    json.dump(out, open(os.path.join(a.out, "flows.json"), "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    ok = sum(1 for r in rows if r.get("ok"))
    print(f"[flows] 시장 {ok}/{len(rows)} · 한국 {len(state.get('KR', {}))}일 · 대만 {sum(1 for v in state.get('TW', {}).values() if v)}일 · 요약 {len(out['summary'])}줄")


if __name__ == "__main__":
    main()
