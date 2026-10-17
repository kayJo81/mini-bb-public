"""
거시 위험·유동성·포지션 데이터 — 랩 아침 실행(07:00 KST)마다
  python lab/risk_data.py --out public_lab/risk.json

· 자산 시세(야후, 1년 일별): 수준·1일/1주/1달/3달/1년 변화, Z점수(수준: 1년 평균 대비 / 1일: 1년 일간 변동 대비)
· FRED: 금리 곡선(1개월~30년)·정책금리·기대인플레·실질금리, 신용 스프레드(IG·HY·CCC), 금융상황(NFCI·STLFSI),
        경기침체 확률·삼 법칙, 유동성(연준 자산·재무부 계좌·역레포·지준금 → 순유동성), 실업수당
· CFTC 선물 포지션(COT, 매주 화요일 기준 · 금요일 공개): 레버리지 펀드·자산운용사·투기(관리)자금 순포지션, 3년 백분위
· Deribit 비트코인 옵션: ATM 내재변동성 기간구조, 25델타 스큐(근사), 미결제 분포·맥스페인, 펀딩·베이시스, 예상 범위
· 항목마다 실패해도 나머지는 저장
"""
import argparse
import csv
import io
import json
import math
import os
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}

# ── 1. 자산 시세 ─────────────────────────────────────────────────
# 3년치 받아 Z점수는 최근 1년으로, S&P 주간 종가는 3년 전체(순유동성과 겹쳐 보기)
ASSETS = [
    ("미국 지수", [("^GSPC", "S&P 500"), ("^IXIC", "나스닥"), ("^DJI", "다우"), ("^RUT", "러셀2000"), ("^SOX", "반도체(SOX)")]),
    ("해외 지수", [("^KS11", "코스피"), ("^N225", "니케이"), ("^HSI", "항셍"), ("000001.SS", "상하이"), ("^TWII", "대만"),
                ("^STOXX50E", "유로스톡스50"), ("^GDAXI", "DAX")]),
    ("원자재", [("CL=F", "WTI"), ("BZ=F", "브렌트"), ("GC=F", "금"), ("SI=F", "은"), ("HG=F", "구리"), ("NG=F", "천연가스")]),
    ("환율", [("DX-Y.NYB", "달러지수"), ("KRW=X", "원/달러"), ("JPY=X", "엔/달러"), ("EURUSD=X", "유로/달러"), ("CNY=X", "위안/달러")]),
    ("가상자산", [("BTC-USD", "비트코인"), ("ETH-USD", "이더리움")]),
    ("변동성·신용", [("^VIX", "VIX"), ("^VIX3M", "VIX 3개월"), ("^MOVE", "MOVE(채권 변동성)"), ("HYG", "하이일드 채권 ETF"),
                  ("LQD", "투자등급 채권 ETF"), ("TLT", "미 장기국채 ETF"), ("BIZD", "BDC(사모대출) ETF"), ("OWL", "블루아울(사모신용)"),
                  ("KRE", "지방은행 ETF")]),
]


CRYPTO = {"BTC-USD", "ETH-USD"}


def yahoo(sym):
    r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{requests.utils.quote(sym)}",
                     params={"interval": "1d", "range": "3y"}, headers=UA, timeout=20)
    res = r.json()["chart"]["result"][0]
    q = res["indicators"]["quote"][0]["close"]
    out = [(datetime.fromtimestamp(t, timezone.utc).date().isoformat(), c) for t, c in zip(res.get("timestamp") or [], q) if c]
    return out


def stats(series, n_year=252, ann=252):
    """수준·변화·Z점수·20일 실현 변동성(연율, %)"""
    if len(series) < 30:
        return None
    v = [c for _, c in series]
    last = v[-1]

    def chg(k):
        return round((last / v[-1 - k] - 1) * 100, 2) if len(v) > k and v[-1 - k] else None
    w = v[-n_year:]
    m = sum(w) / len(w)
    sd = (sum((x - m) ** 2 for x in w) / (len(w) - 1)) ** 0.5
    rets = [math.log(w[i] / w[i - 1]) for i in range(1, len(w)) if w[i - 1] > 0 and w[i] > 0]
    rsd = (sum(r * r for r in rets) / max(1, len(rets) - 1)) ** 0.5 if rets else None
    r1 = math.log(v[-1] / v[-2]) if len(v) > 1 and v[-2] > 0 and v[-1] > 0 else None
    r20 = rets[-20:]
    rv20 = (sum(r * r for r in r20) / max(1, len(r20) - 1)) ** 0.5 * math.sqrt(ann) * 100 if len(r20) >= 10 else None
    return {"date": series[-1][0], "last": round(last, 4), "d1": chg(1), "w1": chg(5), "m1": chg(21), "m3": chg(63), "y1": chg(252),
            "z_level": round((last - m) / sd, 2) if sd else None,
            "z_1d": round(r1 / rsd, 2) if r1 is not None and rsd else None,
            "rv20": round(rv20, 1) if rv20 else None,
            "spark": [round(x, 4) for x in v[-63:][::3]]}


def assets():
    syms = [s for _, g in ASSETS for s, _ in g]
    with ThreadPoolExecutor(6) as ex:
        fut = {s: ex.submit(yahoo, s) for s in syms}
    raw, out = {}, []
    for grp, items in ASSETS:
        rows = []
        for s, name in items:
            try:
                raw[s] = fut[s].result()
                st = stats(raw[s], ann=365 if s in CRYPTO else 252)
                if st:
                    rows.append({"sym": s, "name": name, **st})
            except Exception as e:
                print(f"[risk] {s}: {type(e).__name__}")
        out.append({"group": grp, "items": rows})
    # 비율: 유가/구리(공급 충격), 구리/금(경기 기대), 금/은, VIX 기간구조
    ratios = []
    for a, b, nm, why in (("CL=F", "HG=F", "유가/구리", "오르면 공급 충격형(유가만 오름)"),
                          ("HG=F", "GC=F", "구리/금", "오르면 경기 기대, 내리면 안전 선호"),
                          ("GC=F", "BTC-USD", "금/비트코인", "오르면 위험 회피(금 선호), 내리면 위험 선호"),
                          ("^VIX", "^VIX3M", "VIX/VIX3M", "1 넘으면 단기 공포가 더 큼(백워데이션)")):
        if a in raw and b in raw:
            db = dict(raw[b])
            ser = [(d, c / db[d]) for d, c in raw[a] if d in db and db[d]]
            st = stats(ser)
            if st:
                ratios.append({"name": nm, "why": why, **st})
    # S&P 500 주간 종가 (순유동성과 겹쳐 보기)
    spx_w, seen = [], set()
    for d, c in raw.get("^GSPC", []):
        wk = date.fromisoformat(d).isocalendar()[:2]
        if wk in seen:
            spx_w[-1] = [d, round(c, 2)]
        else:
            seen.add(wk)
            spx_w.append([d, round(c, 2)])
    return out, ratios, spx_w


# ── 2. FRED ─────────────────────────────────────────────────────
FRED = {
    "curve": ["DGS1MO", "DGS3MO", "DGS6MO", "DGS1", "DGS2", "DGS3", "DGS5", "DGS7", "DGS10", "DGS20", "DGS30"],
    "policy": ["DFF", "SOFR", "IORB", "MORTGAGE30US", "T10Y2Y", "T10Y3M", "T5YIE", "T10YIE", "DFII10"],
    "credit": ["BAMLC0A0CM", "BAMLH0A0HYM2", "BAMLH0A3HYC"],
    "stress": ["NFCI", "ANFCI", "STLFSI4", "RECPROUSM156N", "SAHMREALTIME", "ICSA", "CCSA", "UNRATE"],
    "liq": ["WALCL", "WTREGEN", "RRPONTSYD", "WRESBAL", "M2SL"],
}
FNAME = {"DGS1MO": "1개월", "DGS3MO": "3개월", "DGS6MO": "6개월", "DGS1": "1년", "DGS2": "2년", "DGS3": "3년", "DGS5": "5년",
         "DGS7": "7년", "DGS10": "10년", "DGS20": "20년", "DGS30": "30년",
         "DFF": "연방기금 실효금리", "SOFR": "SOFR", "IORB": "지준부리(IORB)", "MORTGAGE30US": "30년 모기지", "T10Y2Y": "10년−2년",
         "T10Y3M": "10년−3개월", "T5YIE": "5년 기대인플레", "T10YIE": "10년 기대인플레", "DFII10": "10년 실질금리",
         "BAMLC0A0CM": "투자등급(IG) 스프레드", "BAMLH0A0HYM2": "하이일드(HY) 스프레드", "BAMLH0A3HYC": "CCC 스프레드",
         "NFCI": "시카고 연은 금융상황(NFCI)", "ANFCI": "조정 NFCI", "STLFSI4": "세인트루이스 금융스트레스",
         "RECPROUSM156N": "경기침체 확률(모형)", "SAHMREALTIME": "삼 법칙 지표", "ICSA": "신규 실업수당 청구", "CCSA": "계속 실업수당 청구",
         "UNRATE": "실업률", "WALCL": "연준 총자산", "WTREGEN": "재무부 계좌(TGA)", "RRPONTSYD": "역레포(ON RRP)", "WRESBAL": "지준금",
         "M2SL": "M2 통화량"}
# 단위를 십억 달러로 맞춤 (WALCL·WRESBAL·WTREGEN 은 백만 달러)
SCALE = {"WALCL": 0.001, "WRESBAL": 0.001, "WTREGEN": 0.001}


def fred(ids, start):
    for url in ("https://fred.stlouisfed.org/graph/fredgraph.csv", "https://alfred.stlouisfed.org/graph/alfredgraph.csv"):
        try:
            r = requests.get(url, params={"id": ",".join(ids), "cosd": start}, timeout=40)
            if not r.ok:
                print(f"[risk] FRED {r.status_code} {ids[:2]}…")
                time.sleep(2)
                continue
            # 주기가 섞이면 주기별 CSV 를 zip 으로 묶어 줌(이때 cosd 는 무시됨)
            if r.content[:2] == b"PK":
                z = zipfile.ZipFile(io.BytesIO(r.content))
                texts = [z.read(n).decode() for n in z.namelist() if n.endswith(".csv")]
            else:
                texts = [r.text]
            out = {}
            for t in texts:
                if not t.startswith(("observation_date", "DATE")):
                    continue
                rows = list(csv.reader(io.StringIO(t)))
                head = [h.split("_")[0] if url.startswith("https://alfred") else h for h in rows[0][1:]]
                for row in rows[1:]:
                    if row[0] < start:
                        continue
                    for sid, val in zip(head, row[1:]):
                        if val not in ("", "."):
                            out.setdefault(sid, []).append((row[0], float(val) * SCALE.get(sid, 1)))
            if out:
                return out
            print(f"[risk] FRED 빈 응답 {ids[:2]}…")
        except (requests.RequestException, zipfile.BadZipFile) as e:
            print(f"[risk] FRED 오류 {type(e).__name__}")
        time.sleep(2)
    return {}


def fstats(ser):
    """FRED 시리즈: 최근 값·날짜, 1주/1달/3달/1년 전 대비(날짜 기준), 3년 Z점수, 화면용 추이(주 단위)"""
    if not ser:
        return None
    d_last, last = ser[-1]
    dl = date.fromisoformat(d_last)

    def ago(days):
        tgt = (dl - timedelta(days=days)).isoformat()
        prev = [v for d, v in ser if d <= tgt]
        return prev[-1] if prev else None
    vals = [v for _, v in ser]
    m = sum(vals) / len(vals)
    sd = (sum((x - m) ** 2 for x in vals) / max(1, len(vals) - 1)) ** 0.5
    pct = sum(1 for x in vals if x <= last) / len(vals) * 100
    hist, seen = [], set()
    for d, v in ser:                      # 주 단위로 줄여 화면 추이용
        wk = date.fromisoformat(d).isocalendar()[:2]
        if wk not in seen:
            seen.add(wk)
            hist.append([d, round(v, 4)])
        else:
            hist[-1] = [d, round(v, 4)]
    prev = ser[-2] if len(ser) > 1 else (None, None)
    return {"date": d_last, "last": round(last, 4), "prev": prev[1], "prev_date": prev[0], "w1": ago(7), "m1": ago(30), "m3": ago(91), "y1": ago(365),
            "z": round((last - m) / sd, 2) if sd else None, "pct": round(pct), "hist": hist[-160:]}


def fred_all():
    start = (date.today() - timedelta(days=365 * 3 + 20)).isoformat()
    ids = [i for g in FRED.values() for i in g]
    raw = {}
    for k in range(0, len(ids), 9):
        raw.update(fred(ids[k:k + 9], start))
        time.sleep(1)
    out = {}
    for g, lst in FRED.items():
        out[g] = []
        for sid in lst:
            st = fstats(raw.get(sid))
            if st:
                out[g].append({"id": sid, "name": FNAME.get(sid, sid), **st})
    # 순유동성 = 연준 총자산 − 재무부 계좌 − 역레포 (주간, 연준 자산 발표일 기준)
    w, tga, rrp = raw.get("WALCL", []), dict(raw.get("WTREGEN", [])), raw.get("RRPONTSYD", [])
    net = []
    for d, v in w:
        t = tga.get(d) or next((x for dd, x in reversed(raw.get("WTREGEN", [])) if dd <= d), None)
        r = next((x for dd, x in reversed(rrp) if dd <= d), 0.0)
        if t is not None:
            net.append((d, v - t - r))
    nst = fstats(net)
    if nst:
        out["liq"].insert(0, {"id": "NETLIQ", "name": "순유동성 (연준 자산 − TGA − 역레포)", **nst})
    return out


# ── 3. CFTC 선물 포지션 ───────────────────────────────────────────
COT_TFF = [("E-MINI S&P 500 - CHICAGO MERCANTILE EXCHANGE", "S&P 500 (E-mini)"), ("NASDAQ MINI - CHICAGO MERCANTILE EXCHANGE", "나스닥100 (미니)"),
           ("UST 2Y NOTE - CHICAGO BOARD OF TRADE", "미 2년물"), ("UST 10Y NOTE - CHICAGO BOARD OF TRADE", "미 10년물"),
           ("UST BOND - CHICAGO BOARD OF TRADE", "미 장기채"), ("SOFR-3M - CHICAGO MERCANTILE EXCHANGE", "SOFR 3개월"),
           ("VIX FUTURES - CBOE FUTURES EXCHANGE", "VIX 선물"), ("JAPANESE YEN - CHICAGO MERCANTILE EXCHANGE", "엔"),
           ("EURO FX - CHICAGO MERCANTILE EXCHANGE", "유로"), ("BITCOIN - CHICAGO MERCANTILE EXCHANGE", "비트코인(CME)")]
COT_DIS = [("WTI-PHYSICAL - NEW YORK MERCANTILE EXCHANGE", "WTI 원유"), ("NAT GAS NYME - NEW YORK MERCANTILE EXCHANGE", "천연가스"),
           ("GOLD - COMMODITY EXCHANGE INC.", "금"), ("SILVER - COMMODITY EXCHANGE INC.", "은"), ("COPPER- #1 - COMMODITY EXCHANGE INC.", "구리")]


def cot_fetch(ds, names, fields):
    since = (date.today() - timedelta(days=365 * 3 + 10)).isoformat()
    where = "market_and_exchange_names in(" + ",".join("'" + n.replace("'", "''") + "'" for n in names) + f") AND report_date_as_yyyy_mm_dd >= '{since}'"
    r = requests.get(f"https://publicreporting.cftc.gov/resource/{ds}.json",
                     params={"$select": ",".join(["market_and_exchange_names", "report_date_as_yyyy_mm_dd", "open_interest_all"] + fields),
                             "$where": where, "$limit": 20000, "$order": "report_date_as_yyyy_mm_dd"}, timeout=60)
    r.raise_for_status()
    return r.json()


def cot_rows(data, names, groups):
    out = []
    for mk, label in names:
        rows = [x for x in data if x["market_and_exchange_names"] == mk]
        if not rows:
            continue
        row = {"market": label, "date": rows[-1]["report_date_as_yyyy_mm_dd"][:10], "oi": int(float(rows[-1]["open_interest_all"]))}
        for key, nm, lf, sf in groups:
            net = [float(x.get(lf) or 0) - float(x.get(sf) or 0) for x in rows]
            cur, prev = net[-1], (net[-2] if len(net) > 1 else None)
            lo, hi = min(net), max(net)
            row[key] = {"name": nm, "net": int(cur), "wow": None if prev is None else int(cur - prev),
                        "pct_oi": round(cur / row["oi"] * 100, 1) if row["oi"] else None,
                        "pctile": round((cur - lo) / (hi - lo) * 100) if hi > lo else None,   # 3년 범위 안 위치(0=최대 매도, 100=최대 매수)
                        "hist": [int(v) for v in net[-52:]]}
        out.append(row)
    return out


def cot():
    res = {}
    try:
        tff = cot_fetch("gpe5-46if", [n for n, _ in COT_TFF],
                        ["lev_money_positions_long", "lev_money_positions_short", "asset_mgr_positions_long", "asset_mgr_positions_short"])
        res["financial"] = cot_rows(tff, COT_TFF, [("lev", "레버리지 펀드", "lev_money_positions_long", "lev_money_positions_short"),
                                                   ("am", "자산운용사", "asset_mgr_positions_long", "asset_mgr_positions_short")])
    except Exception as e:
        print(f"[risk] COT 금융: {type(e).__name__} {e}")
    try:
        dis = cot_fetch("72hh-3qpy", [n for n, _ in COT_DIS], ["m_money_positions_long_all", "m_money_positions_short_all"])
        res["commodity"] = cot_rows(dis, COT_DIS, [("mm", "투기(관리)자금", "m_money_positions_long_all", "m_money_positions_short_all")])
    except Exception as e:
        print(f"[risk] COT 원자재: {type(e).__name__} {e}")
    return res


# ── 4. Deribit 비트코인 옵션 ──────────────────────────────────────
MON = {m: i + 1 for i, m in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}


def _exp(code):          # 17OCT26 → 만기 시각(08:00 UTC)
    d, m, y = int(code[:-5]), MON[code[-5:-2]], 2000 + int(code[-2:])
    return datetime(y, m, d, 8, tzinfo=timezone.utc)


def _interp(pts, x):
    pts = sorted(pts)
    if not pts:
        return None
    if x <= pts[0][0]:
        return pts[0][1]
    if x >= pts[-1][0]:
        return pts[-1][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x0 <= x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0) if x1 > x0 else y0


def deribit():
    api = "https://www.deribit.com/api/v2/public/"
    g = lambda m, **p: requests.get(api + m, params=p, timeout=30).json()["result"]
    opts = g("get_book_summary_by_currency", currency="BTC", kind="option")
    spot = g("get_index_price", index_name="btc_usd")["index_price"]
    now = datetime.now(timezone.utc)
    by = {}
    for o in opts:
        try:
            _, ex, k, cp = o["instrument_name"].split("-")
            by.setdefault(ex, []).append((float(k), cp, o.get("mark_iv"), o.get("open_interest") or 0.0, o.get("underlying_price") or spot,
                                          o.get("mark_price")))
        except ValueError:
            continue
    term, oi_k, call_oi, put_oi = [], {}, 0.0, 0.0
    for ex in sorted(by, key=_exp):
        T = (_exp(ex) - now).total_seconds() / (365 * 86400)
        if T <= 0:
            continue
        rows = by[ex]
        F = sum(r[4] for r in rows) / len(rows)
        # OTM 옵션 기준 스마일 (풋: K<F, 콜: K>F)
        smile = [(math.log(k / F), iv) for k, cp, iv, oi, *_ in rows if iv and ((cp == "P" and k <= F) or (cp == "C" and k >= F))]
        atm = _interp(smile, 0.0)
        sk = None
        if atm:
            s = atm / 100 * math.sqrt(T)
            kp, kc = -0.6745 * s + 0.5 * s * s, 0.6745 * s + 0.5 * s * s       # 25델타 근사 행사가(로그 머니니스)
            ip, ic = _interp([p for p in smile if p[0] <= 0], kp), _interp([p for p in smile if p[0] >= 0], kc)
            sk = round(ip - ic, 2) if ip and ic else None
        c_oi = sum(oi for k, cp, iv, oi, *_ in rows if cp == "C")
        p_oi = sum(oi for k, cp, iv, oi, *_ in rows if cp == "P")
        call_oi, put_oi = call_oi + c_oi, put_oi + p_oi
        strikes = sorted({k for k, *_ in rows})
        pain = None
        if c_oi + p_oi > 0:   # 맥스페인: 만기에 옵션 매수자 가치가 가장 작아지는 가격
            pain = min(strikes, key=lambda S: sum(oi * max(0, S - k) if cp == "C" else oi * max(0, k - S) for k, cp, iv, oi, *_ in rows))
        for k, cp, iv, oi, *_ in rows:
            d = oi_k.setdefault(k, [0.0, 0.0])
            d[0 if cp == "C" else 1] += oi
        # ATM 스트래들(콜+풋, 선도가에 가장 가까운 행사가) → 만기까지 예상 변동폭
        mk = {(k, cp): m for k, cp, iv, oi, u, m in rows if m}
        both = [k for k in strikes if (k, "C") in mk and (k, "P") in mk]
        strad = None
        if both:
            ka = min(both, key=lambda k: abs(k - F))
            strad = round((mk[(ka, "C")] + mk[(ka, "P")]) * F)
        term.append({"expiry": ex, "days": round(T * 365, 1), "atm_iv": round(atm, 1) if atm else None, "skew25": sk,
                     "call_oi": round(c_oi), "put_oi": round(p_oi), "max_pain": pain, "fwd": round(F), "straddle": strad})
    top = sorted(oi_k.items(), key=lambda kv: -(kv[1][0] + kv[1][1]))[:10]
    out = {"spot": round(spot, 2), "asof": now.isoformat(timespec="minutes"), "term": term[:10],
           "pc_oi": round(put_oi / call_oi, 3) if call_oi else None, "call_oi": round(call_oi), "put_oi": round(put_oi),
           "top_strikes": [{"strike": k, "call": round(v[0]), "put": round(v[1])} for k, v in sorted(top)]}
    near = next((t for t in term if t["days"] >= 0.5 and t["atm_iv"]), None)
    wk = next((t for t in term if t["days"] >= 6 and t["atm_iv"]), None)
    if near:
        s1 = spot * near["atm_iv"] / 100 * math.sqrt(1 / 365)
        out["range_day"] = {"iv": near["atm_iv"], "lo1": round(spot - s1), "hi1": round(spot + s1), "lo2": round(spot - 2 * s1), "hi2": round(spot + 2 * s1)}
        out["max_pain_near"] = near["max_pain"]
        if near.get("straddle"):
            out["expected_move"] = {"expiry": near["expiry"], "straddle": near["straddle"],
                                    "lo": round(spot - near["straddle"]), "hi": round(spot + near["straddle"])}
        out["skew_near"] = near["skew25"]
    if wk:
        s7 = spot * wk["atm_iv"] / 100 * math.sqrt(7 / 365)
        out["range_week"] = {"iv": wk["atm_iv"], "lo1": round(spot - s7), "hi1": round(spot + s7), "lo2": round(spot - 2 * s7), "hi2": round(spot + 2 * s7)}
    try:
        dv = g("get_volatility_index_data", currency="BTC", resolution="1D",
               start_timestamp=int((now - timedelta(days=10)).timestamp() * 1000), end_timestamp=int(now.timestamp() * 1000))["data"]
        if dv:
            out["dvol"] = round(dv[-1][4], 1)
            out["dvol_chg"] = round(dv[-1][4] - dv[-2][4], 1) if len(dv) > 1 else None
    except Exception as e:
        print(f"[risk] DVOL: {type(e).__name__}")
    try:
        perp = g("ticker", instrument_name="BTC-PERPETUAL")
        out["funding_8h"] = round((perp.get("funding_8h") or 0) * 100, 4)
        futs = [f for f in g("get_book_summary_by_currency", currency="BTC", kind="future") if "PERPETUAL" not in f["instrument_name"]]
        fq = sorted(((f, (_exp(f["instrument_name"].split("-")[1]) - now).days) for f in futs if f.get("mark_price")), key=lambda x: x[1])
        fq = [x for x in fq if x[1] >= 20]
        if fq:
            f, days = fq[0]
            out["basis_ann"] = round((f["mark_price"] / spot - 1) * 365 / days * 100, 2)
            out["basis_contract"] = f["instrument_name"]
    except Exception as e:
        print(f"[risk] 펀딩·베이시스: {type(e).__name__}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="public_lab/risk.json")
    a = ap.parse_args()
    out = {"at": datetime.now(timezone.utc).isoformat(timespec="minutes")}
    for key, fn in (("assets", assets), ("fred", fred_all), ("cot", cot), ("btc", deribit)):
        t0 = time.time()
        try:
            r = fn()
            if key == "assets":
                out["assets"], out["ratios"], out["spx_w"] = r
            else:
                out[key] = r
            print(f"[risk] {key} 완료 {time.time() - t0:.0f}초")
        except Exception as e:
            print(f"[risk] {key} 실패: {type(e).__name__} {e}")
    # 비트코인 변동성 위험 프리미엄(VRP) = 1주 ATM 내재변동성 − 20일 실현변동성
    b = out.get("btc") or {}
    rv = next((x.get("rv20") for g in out.get("assets", []) for x in g["items"] if x["sym"] == "BTC-USD"), None)
    if b and rv:
        b["rv20"] = rv
        if b.get("range_week"):
            b["vrp"] = round(b["range_week"]["iv"] - rv, 1)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    json.dump(out, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f"[risk] 저장 {a.out} ({os.path.getsize(a.out) // 1024}KB)")


if __name__ == "__main__":
    main()
