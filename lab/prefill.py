"""
매일 아침 프리필 (회의 전 자동 작성 문서) — Plan G 형식 + 우리 데이터

  python lab/prefill.py --out public_lab

입력: public_lab/risk.json(위험·유동성) · env.json(옵션 수급) · portfolio.json(가상 운용) ·
      lab-data weekly.json(이번 주 일정) · 터미널 briefings/live.json(밤사이 뉴스·시장 변동 원인)
출력: public_lab/prefill.json · prefill.docx · prefill/<날짜>.json(지난 문서) / lab-data prefill/<날짜>.json(어제와 비교용)

판단(확률·행동)은 비워 두고, 숫자·신호·조건만 채운다. 신호 기준은 각 표 아래에 적는다.
"""
import argparse
import glob
import json
import math
import os
import sys
from datetime import date, datetime, timedelta, timezone

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA  # noqa: E402

KST = timezone(timedelta(hours=9))
LIVE_URL = "https://mini-bbterminal.pages.dev/briefings/live.json"
WEEKLY_URL = "https://raw.githubusercontent.com/kayJo81/mini-bb-public/lab-data/weekly.json"
R, Y, G = "🔴", "⚠️", "✅"
WD = "월화수목금토일"


# ── 작은 도구 ───────────────────────────────────────────────────
def load(path, default=None):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return default


def fetch(url):
    try:
        r = requests.get(url, timeout=40, headers={"User-Agent": "mini-bblab prefill"})
        return r.json() if r.ok else None
    except Exception as e:
        print(f"[prefill] {url[:50]}… {type(e).__name__}")
        return None


def num(v, d=2):
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "–"
    return f"{v:,.{d}f}"


def pct(v, d=2):
    return "–" if v is None else f"{v:+.{d}f}%"


def bp(v):
    return "–" if v is None else f"{v:+.0f}bp"


def sig(v, d=2):
    return "–" if v is None else f"{v:+.{d}f}σ"


def cell(t, b=False, c=None):
    """표 칸: 굵게(b)·색(c: up/dn/red/amber/green)"""
    return {"t": t, **({"b": True} if b else {}), **({"c": c} if c else {})}


def tone(v):
    return None if v is None or v == 0 else ("up" if v > 0 else "dn")


def md(d):
    """2026-10-16 → 10/16"""
    return f"{int(d[5:7])}/{int(d[8:10])}" if d and len(d) >= 10 else (d or "")


# ── 입력 정리 ───────────────────────────────────────────────────
class Data:
    def __init__(self, out):
        self.risk = load(os.path.join(out, "risk.json"), {}) or {}
        self.env = load(os.path.join(out, "env.json"), {}) or {}
        self.pf = load(os.path.join(out, "portfolio.json"), None)
        self.weekly = load(os.path.join(DATA, "weekly.json"), None) or fetch(WEEKLY_URL)
        self.live = fetch(LIVE_URL)
        self.A = {x["sym"]: x for g in self.risk.get("assets", []) for x in g["items"]}
        self.F = {x["id"]: x for g in (self.risk.get("fred") or {}).values() for x in g}
        self.RAT = {x["name"]: x for x in self.risk.get("ratios", [])}
        self.btc = self.risk.get("btc") or {}
        latest = self.env.get("latest") or {}
        self.opt = latest.get("options") or {}
        self.emac = latest.get("macro") or {}
        self.pc = latest.get("cboe_pc") or {}
        self.env_date = latest.get("date")
        self.status = {"시세·Z점수": bool(self.A), "금리·신용·유동성(FRED)": bool(self.F), "선물 포지션(CFTC)": bool(self.risk.get("cot")),
                       "비트코인 옵션(Deribit)": bool(self.btc), "옵션 수급(CBOE)": bool(self.opt), "가상 운용": bool(self.pf),
                       "이번 주 일정": bool(self.weekly), "밤사이 뉴스": bool(self.live)}
        self.rates_live()

    def rates_live(self):
        """FRED 국채 금리는 하루 늦게 올라옴 → 야후 종가가 더 새로우면 그 값으로 (* 표시)"""
        for fid, ek in (("DGS3MO", "UST3M"), ("DGS5", "UST5Y"), ("DGS10", "UST10Y"), ("DGS30", "UST30Y")):
            f, e = self.F.get(fid), self.emac.get(ek)
            if f and e and e.get("date", "") > f["date"]:
                f.update({"prev": f["last"], "prev_date": f["date"], "last": round(e["close"], 3), "date": e["date"], "live": True})

    def a(self, sym, k="last"):
        return (self.A.get(sym) or {}).get(k)

    def f(self, fid, k="last"):
        return (self.F.get(fid) or {}).get(k)

    def fchg(self, fid, k, mult=1.0):
        x = self.F.get(fid)
        return None if not x or x.get(k) is None else (x["last"] - x[k]) * mult


# ── I. MARKET ──────────────────────────────────────────────────
SNAP = [("^GSPC", "S&P 500", 2, ""), ("^IXIC", "나스닥", 2, ""), ("^DJI", "다우", 2, ""), ("^RUT", "러셀2000", 2, ""),
        ("^SOX", "반도체(SOX)", 2, ""), ("^VIX", "VIX", 2, ""), ("CL=F", "WTI", 2, "$"), ("BZ=F", "브렌트", 2, "$"),
        ("GC=F", "금", 1, "$"), ("HG=F", "구리", 3, "$"), ("DX-Y.NYB", "달러지수", 2, ""), ("KRW=X", "원/달러", 1, ""),
        ("JPY=X", "엔/달러", 2, ""), ("BTC-USD", "비트코인", 0, "$"), ("ETH-USD", "이더리움", 0, "$"), ("^KS11", "코스피", 2, ""),
        ("^N225", "니케이", 0, ""), ("^HSI", "항셍", 0, "")]


def snap_note(x):
    n = []
    if x.get("z_level") is not None and abs(x["z_level"]) >= 2:
        n.append(f"1년 기준 {x['z_level']:+.1f}σ 극단")
    if x.get("z_1d") is not None and abs(x["z_1d"]) >= 2:
        n.append(f"하루 변동 {x['z_1d']:+.1f}σ")
    if x.get("m1") is not None and abs(x["m1"]) >= 10:
        n.append(f"한 달 {x['m1']:+.0f}%")
    elif x.get("w1") is not None and abs(x["w1"]) >= 5:
        n.append(f"한 주 {x['w1']:+.0f}%")
    return n


def market(D):
    rows = []
    for sym, nm, d, cur in SNAP:
        x = D.A.get(sym)
        if not x:
            continue
        n = snap_note(x)
        rows.append([cell(nm, bool(n)), cell(cur + num(x["last"], d), bool(n)), cell(pct(x["d1"]), c=tone(x["d1"])),
                     cell(pct(x["w1"]), c=tone(x["w1"])), cell(pct(x["m1"]), c=tone(x["m1"])),
                     cell(sig(x["z_level"]), abs(x["z_level"] or 0) >= 2), cell(sig(x["z_1d"]), abs(x["z_1d"] or 0) >= 2),
                     cell(" · ".join(n))])
    blocks = [{"type": "table", "title": "📊 시장 스냅샷", "cols": ["자산", "종가", "1D", "1W", "1M", "Z(1년 수준)", "Z(1일 변동)", "비고"],
               "rows": rows, "note": "Z(1년 수준) = 지난 1년 평균 대비 몇 표준편차 위·아래인지 · Z(1일 변동) = 어제 하루 변동이 평소 하루 변동의 몇 배인지 · ±2σ 이상은 굵게"}]

    # 금리
    RT = [("DFF", "연방기금 실효"), ("SOFR", "SOFR"), ("IORB", "지준부리(IORB)"), ("DGS3MO", "3개월"), ("DGS2", "2년"), ("DGS5", "5년"),
          ("DGS10", "10년"), ("DGS30", "30년"), ("T10Y2Y", "10년−2년"), ("T10Y3M", "10년−3개월"), ("DFII10", "10년 실질금리"),
          ("T10YIE", "10년 기대인플레"), ("MORTGAGE30US", "30년 모기지")]
    rr = []
    for fid, nm in RT:
        x = D.F.get(fid)
        if not x:
            continue
        n = []
        if fid in ("DGS10", "DGS30") and x["last"] >= 5:
            n.append("5% 위 — 성장주 할인율 부담")
        if fid in ("T10Y2Y", "T10Y3M"):
            n.append("역전(침체 신호)" if x["last"] < 0 else "정상 기울기")
        if x.get("pct") is not None and x["pct"] >= 95 and fid not in ("T10Y2Y", "T10Y3M"):
            n.append("3년 중 상위 5%")
        if fid == "DFII10" and (x.get("z") or 0) >= 2:
            n.append("실질금리 3년 중 극단")
        w1 = D.fchg(fid, "w1", 100)
        rr.append([cell(nm + ("*" if x.get("live") else ""), bool(n) and fid in ("DGS10", "DGS30", "DFII10")), cell(num(x["last"], 2) + "%"),
                   cell(bp(w1), c=tone(w1)), cell(bp(D.fchg(fid, "m1", 100)), c=tone(D.fchg(fid, "m1", 100))),
                   cell(sig(x.get("z"))), cell(md(x["date"])), cell(" · ".join(n))])
    blocks.append({"type": "table", "title": "💰 금리 환경", "cols": ["만기·지표", "수준", "1주", "1달", "Z(3년)", "기준일", "비고"], "rows": rr,
                   "note": "FRED(세인트루이스 연은) · * 표시는 FRED 가 아직 안 올라와 야후 종가로 대신한 값 · 단위 bp = 0.01%p"})

    blocks += news(D)
    blocks += movers()
    return {"id": "market", "title": "Section I. MARKET", "blocks": blocks}


NEWS_TOPICS = {"fed", "us_macro", "eu_macro", "asia_macro", "boj", "ecb", "mideast", "us_politics", "midterms", "nvda", "semi_mega",
               "semi_other", "ai_hw", "ai_sw", "msft", "googl", "amzn", "meta", "aapl", "tesla", "btc", "crypto_policy", "earnings"}


def news(D):
    L = D.live or {}
    out = []
    mv = L.get("moves") or {}
    if mv:
        S = mv.get("summary")
        items = []
        if S:
            dr = ", ".join(d["name"] for d in S.get("drivers", [])[:3])
            items.append({"h": f"나스닥 선물 {pct(S['nq_pct'])} — 원인 유형: {dr or '뚜렷하지 않음'}",
                          "m": " · ".join(f"{l['name']} {pct(l['pct'], 1)}" for l in S.get("leaders", [])[:5])})
        Dy = mv.get("day") or {}
        hm = lambda t: datetime.fromtimestamp(t, KST).strftime("%H:%M")
        T = Dy.get("trigger")
        if Dy.get("issues"):
            ref = Dy.get("ref_pct", Dy.get("nq_pct", 0))
            items.append({"h": f"{'나스닥100(전일 종가 대비)' if Dy.get('mode') == 'regular' else '나스닥 선물(장 마감 이후)'} {pct(ref)} — "
                               f"{'왜 빠졌나' if ref < 0 else '왜 올랐나'}", "m": ""})
            for x in Dy["issues"][:5]:
                h_ = x.get("head") or {}
                items.append({"h": f"{x['name']} {x['pp']:+.2f}%p ({'·'.join(x['phases'])})", "m": h_.get("headline", ""), "u": h_.get("url", "")})
            T = None
        if T:
            ex = (T.get("explain") or T.get("news") or [{}])[0]
            items.append({"h": f"🎯 방향을 정한 계기: {hm(T['start'])}~{hm(T['end'])} {T['cause']} 나스닥 선물 {pct(T['nq_pct'])} "
                               f"({'하락' if T['nq_pct'] < 0 else '상승'} 구간 합의 {T['share']}%)",
                          "m": ex.get("headline", ""), "u": ex.get("url", "")})
        if Dy.get("segments") and not Dy.get("issues"):
            for r in Dy["segments"]:
                h_ = r.get("head") or {}
                items.append({"h": f"{hm(r['start'])}~{hm(r['end'])} KST 나스닥 선물 {pct(r['nq_pct'])} · {r['cause']}",
                              "m": h_.get("headline", ""), "u": h_.get("url", "")})
        for e in ([] if Dy.get("segments") else (mv.get("events") or [])[:4]):
            t0 = datetime.fromtimestamp(e["start"], KST).strftime("%H:%M")
            t1 = datetime.fromtimestamp(e["end"], KST).strftime("%H:%M")
            dr = (e.get("drivers") or [{}])[0].get("name", "")
            ex = (e.get("explain") or e.get("news") or [{}])[0]
            items.append({"h": f"{t0}~{t1} KST 나스닥 선물 {pct(e['nq_pct'])} · {dr}", "m": ex.get("headline", ""), "u": ex.get("url", "")})
        out.append({"type": "news", "title": "📰 밤사이 시장을 움직인 뉴스", "sub": (L.get("window") or {}).get("label", ""), "items": items or [{"h": "변동이 작아 원인 분석 구간이 없어요", "m": ""}],
                    "note": "터미널 '시장' 탭과 같은 분석: 나스닥 선물을 시간대별 구간으로 나눠 → 같은 시각 다른 자산 움직임으로 구간별 원인 추정 → 원인별로 더해 방향을 정한 계기 찾기"})
    cand = []
    for t in L.get("topics", []):
        if t["id"] not in NEWS_TOPICS:
            continue
        for s in t.get("stories", []):
            if s.get("is_opinion") == "True" or s.get("is_rumor") == "True":
                continue
            n = int(s.get("n_sources") or 1)
            if n < 2 and s.get("verdict") != "VERIFIED":
                continue
            sc = n * (1.5 if s.get("verdict") == "VERIFIED" else 1) + (2 if t.get("section") in ("매크로", "지정학") else 0)
            cand.append((sc, t["name"], s))
    cand.sort(key=lambda x: -x[0])
    seen, per, items = set(), {}, []
    for sc, tn, s in cand:
        h = s.get("headline_ko") or s["headline"]
        if h[:30] in seen or per.get(tn, 0) >= 2:
            continue
        seen.add(h[:30])
        per[tn] = per.get(tn, 0) + 1
        sent = {"bullish": "호재", "bearish": "악재"}.get(s.get("sentiment"), "")
        items.append({"h": h, "m": f"{tn} · 매체 {s.get('n_sources')}곳" + (" · 교차 확인" if s.get("verdict") == "VERIFIED" else "") + (f" · {sent}" if sent else ""),
                      "u": s.get("url", "")})
        if len(items) >= 10:
            break
    if items:
        out.append({"type": "news", "title": "🗞️ 밤사이 핵심 헤드라인", "sub": (L.get("window") or {}).get("label", ""), "items": items,
                    "note": "매크로·지정학·AI/반도체·빅테크·비트코인 주제에서 여러 매체가 다룬 기사 순 (주제당 2건) · 해석은 회의에서"})
    return out


def movers():
    P = load(os.path.join(DATA, "prices.json"), {}) or {}
    ch = []
    for s, ser in P.items():
        ds = sorted(ser)
        if len(ds) >= 2 and s not in ("QQQ", "SPY"):
            a, b = ser[ds[-2]][1], ser[ds[-1]][1]
            if a:
                ch.append((s, (b / a - 1) * 100, ds[-1]))
    if not ch:
        return []
    last = max(c[2] for c in ch)
    ch = sorted([c for c in ch if c[2] == last], key=lambda c: -c[1])
    up = " | ".join(f"{s} {v:+.1f}%" for s, v, _ in ch[:8] if v > 0)
    dn = " | ".join(f"{s} {v:+.1f}%" for s, v, _ in ch[::-1][:8] if v < 0)
    return [{"type": "kv", "title": f"⑦ 추적 종목 등락 ({md(last)})", "rows": [["상승", up or "–"], ["하락", dn or "–"]]}]


# ── II. FLOW ───────────────────────────────────────────────────
def flow(D):
    blocks = []
    nl, res, tga, rrp = (D.F.get(k) for k in ("NETLIQ", "WRESBAL", "WTREGEN", "RRPONTSYD"))
    rows = []

    def add(name, lvl, trend, s, why, b=False):
        rows.append([cell(name, b), cell(lvl, b), cell(trend), cell(s), cell(why)])
    if nl:
        m1 = D.fchg("NETLIQ", "m1")
        s = R if m1 is not None and m1 <= -150 else Y if m1 is not None and m1 <= -50 else G
        add("순유동성", f"${nl['last'] / 1000:.2f}T", f"1주 {D.fchg('NETLIQ', 'w1') or 0:+.0f}B · 4주 {m1 or 0:+.0f}B", s,
            "연준 자산 − 재무부 계좌 − 역레포. 줄면 위험자산에 역풍", True)
    if res:
        m1 = D.fchg("WRESBAL", "m1")
        add("지준금", f"${res['last'] / 1000:.2f}T", f"4주 {m1 or 0:+.0f}B", Y if res["last"] < 2900 else G, "2.9조 달러 아래면 단기자금 빡빡해질 위험")
    if tga:
        m1 = D.fchg("WTREGEN", "m1")
        add("재무부 계좌(TGA)", f"${tga['last']:.0f}B", f"4주 {m1 or 0:+.0f}B", Y if (m1 or 0) >= 100 else G, "늘면 재무부가 시중 자금을 빨아들임")
    if rrp:
        add("역레포(ON RRP)", f"${rrp['last']:.1f}B", f"1달 전 ${rrp['m1'] or 0:.0f}B", Y if rrp["last"] < 50 else G,
            "거의 소진 — 유동성 완충 없음" if rrp["last"] < 50 else "남은 완충")
    so, io = D.f("SOFR"), D.f("IORB")
    if so is not None and io is not None:
        sp = (so - io) * 100
        add("SOFR − 지준부리", f"{sp:+.0f}bp", "", R if sp > 10 else Y if sp > 0 else G, "SOFR 가 지준부리보다 높으면 단기자금 시장 압박")
    if D.F.get("DGS10"):
        add("10년물", f"{D.f('DGS10'):.2f}%", f"1주 {bp(D.fchg('DGS10', 'w1', 100))}", Y if D.f("DGS10") >= 5 else G, "5% 위면 주식 밸류에이션 부담")
    if D.F.get("T10Y2Y"):
        v = D.f("T10Y2Y")
        add("10년−2년", f"{v:+.2f}%p", f"1달 {bp(D.fchg('T10Y2Y', 'm1', 100))}", Y if v < 0 else G, "역전이면 침체 경고, 역전 해소 직후도 주의")
    vt = D.RAT.get("VIX/VIX3M")
    if vt:
        add("VIX 기간구조", f"{vt['last']:.2f}", f"1주 {pct(vt['w1'], 1)}", R if vt["last"] > 1 else Y if vt["last"] > 0.95 else G,
            "1 넘으면 단기 공포가 더 큼(백워데이션)")
    if D.pc.get("equity") is not None:
        e = D.pc["equity"]
        add("주식 풋/콜(CBOE)", f"{e:.2f}", f"전체 {D.pc.get('total')}", Y if e >= 0.8 or e <= 0.5 else G, "0.8↑ 공포·헤지 몰림 / 0.5↓ 낙관 과열")
    spx = D.opt.get("SPX") or {}
    if spx.get("gex_musd") is not None:
        g = spx["gex_musd"] / 1000
        add("S&P 딜러 감마", f"{g:+.1f}십억$", f"콜월 {num(spx.get('call_wall'), 0)} · 풋월 {num(spx.get('put_wall'), 0)}", Y if g < 0 else G,
            "음수면 딜러가 움직임을 키우는 쪽(변동 확대)")
    for nm, th, why in (("유가/구리", 10, "오르면 공급 충격형(유가만 오름)"), ("금/비트코인", 10, "오르면 위험 회피")):
        x = D.RAT.get(nm)
        if x:
            add(nm, num(x["last"], 2 if x["last"] > 1 else 4), f"1달 {pct(x['m1'], 1)}", Y if (x["m1"] or 0) >= th else G, why)
    b = D.btc
    if b.get("funding_8h") is not None:
        f = b["funding_8h"]
        add("BTC 펀딩(8시간)", f"{f:.4f}%", "", Y if f < 0 or f > 0.03 else G, "음수 = 숏 쏠림 · 0.03% 넘으면 롱 과열")
    if b.get("basis_ann") is not None:
        add("BTC 선물 베이시스(연율)", f"{b['basis_ann']:.1f}%", b.get("basis_contract", ""), Y if b["basis_ann"] < 3 or b["basis_ann"] > 15 else G,
            "3% 아래 = 레버리지 수요 약함 · 15% 위 = 과열")
    if rows:
        blocks.append({"type": "table", "title": "💧 유동성·자금 흐름", "cols": ["지표", "수준", "추이", "신호", "해석"], "rows": rows,
                       "note": "연준 H.4.1(매주 목) · 재무부 · CBOE · Deribit · ✅ 양호 ⚠️ 주의 🔴 경보 — 기준은 해석 칸"})
    blocks += cot_block(D)
    blocks += btc_blocks(D)
    return {"id": "flow", "title": "Section II. FLOW", "blocks": blocks}


def cot_block(D):
    C = D.risk.get("cot") or {}
    rows, ext = [], []
    for grp in ("financial", "commodity"):
        for r in C.get(grp, []):
            for k in ("lev", "am", "mm"):
                x = r.get(k)
                if not x:
                    continue
                p = x.get("pctile")
                flag = p is not None and (p >= 90 or p <= 10)
                big = x.get("wow") is not None and r["oi"] and abs(x["wow"]) / r["oi"] >= 0.02
                if flag:
                    ext.append(f"{r['market']} {x['name']} {'최대 매수' if p >= 90 else '최대 매도'} 쪽 ({p})")
                rows.append([cell(r["market"], flag), cell(x["name"]), cell(f"{x['net']:+,}", flag, tone(x["net"])),
                             cell(f"{x['wow']:+,}" if x.get("wow") is not None else "–", big, tone(x.get("wow"))),
                             cell(f"{x['pct_oi']:+.1f}%" if x.get("pct_oi") is not None else "–"), cell(str(p) if p is not None else "–", flag),
                             cell(R if flag else (Y if big else ""))])
    if not rows:
        return []
    dt = next((r["date"] for g in C.values() for r in g), "")
    return [{"type": "table", "title": f"📌 선물 포지션 (CFTC, {md(dt)} 기준)", "cols": ["시장", "주체", "순포지션", "1주 변화", "미결제 대비", "3년 위치", ""],
             "rows": rows, "sub": ("극단: " + " · ".join(ext)) if ext else "3년 범위 끝(10 이하·90 이상)에 있는 포지션 없음",
             "note": "3년 위치 = 지난 3년 순포지션 범위에서 지금 위치(0 최대 매도 ~ 100 최대 매수) · 레버리지 펀드 = 헤지펀드·CTA, 자산운용사 = 연기금·뮤추얼펀드 · 매주 금요일 발표(화요일 기준)"}]


def btc_blocks(D):
    b = D.btc
    if not b.get("spot"):
        return []
    rd, rw, em = b.get("range_day") or {}, b.get("range_week") or {}, b.get("expected_move") or {}
    sk = b.get("skew_near")
    rows = [["지수 가격", f"${num(b['spot'], 0)}", "", ""],
            ["ATM 내재변동성(1일)", f"{rd.get('iv', '–')}%", "", ""],
            ["DVOL", f"{b.get('dvol', '–')} ({b.get('dvol_chg') or 0:+.1f})", "전일 대비", ""],
            ["풋/콜 미결제", f"{b.get('pc_oi', '–')}", "1 아래면 콜 우세", Y if (b.get("pc_oi") or 0) > 1 else G],
            ["맥스페인(최근 만기)", f"${num(b.get('max_pain_near'), 0)}", "옵션 매수자 손실이 최대가 되는 가격", ""],
            ["25델타 스큐(최근 만기)", f"{sk:+.1f}" if sk is not None else "–", "+ 면 풋이 비쌈(하방 헤지 수요)", R if (sk or 0) >= 10 else Y if (sk or 0) >= 5 else G],
            ["선물 베이시스(연율)", f"{b.get('basis_ann', '–')}%", b.get("basis_contract", ""), ""],
            ["변동성 프리미엄(VRP)", f"{b.get('vrp', '–')}", f"1주 내재 − 20일 실현({b.get('rv20', '–')}%)", Y if (b.get("vrp") or 0) < 0 else G],
            ["펀딩(8시간)", f"{b.get('funding_8h', '–')}%", "", ""],
            ["하루 1σ 범위", f"${num(rd.get('lo1'), 0)} ~ ${num(rd.get('hi1'), 0)}", "68% 확률", ""],
            ["하루 2σ 범위", f"${num(rd.get('lo2'), 0)} ~ ${num(rd.get('hi2'), 0)}", "95% 확률", ""],
            ["주간 1σ 범위", f"${num(rw.get('lo1'), 0)} ~ ${num(rw.get('hi1'), 0)}", f"IV {rw.get('iv', '–')}%", ""],
            ["주간 2σ 범위", f"${num(rw.get('lo2'), 0)} ~ ${num(rw.get('hi2'), 0)}", "", ""]]
    if em:
        rows.append(["예상 변동(스트래들)", f"${num(em['lo'], 0)} ~ ${num(em['hi'], 0)}", f"{em['expiry']} 스트래들 ${num(em['straddle'], 0)}", ""])
    rows.append(["콜·풋 미결제 합계", f"{num(b.get('call_oi'), 0)} / {num(b.get('put_oi'), 0)}", "BTC 개수", ""])
    out = [{"type": "table", "title": "₿ 비트코인 파생상품 (Deribit)", "cols": ["지표", "수준", "의미", "신호"],
            "rows": [[cell(r[0]), cell(r[1], r[0] in ("지수 가격", "25델타 스큐(최근 만기)", "하루 1σ 범위", "주간 1σ 범위")), cell(r[2]), cell(r[3])] for r in rows]}]
    term = [t for t in b.get("term", []) if t.get("atm_iv")][:7]
    if term:
        out.append({"type": "table", "title": "📈 만기별 ATM 변동성 · 25델타 스큐", "cols": ["만기", "남은 일수", "ATM IV", "25델타 스큐", "콜/풋 미결제", "맥스페인"],
                    "rows": [[cell(t["expiry"]), cell(f"{t['days']:.1f}"), cell(f"{t['atm_iv']:.1f}%"), cell(f"{t['skew25']:+.1f}" if t.get("skew25") is not None else "–", (t.get("skew25") or 0) >= 10),
                              cell(f"{t['call_oi']:,} / {t['put_oi']:,}"), cell(f"${num(t['max_pain'], 0)}")] for t in term],
                    "sub": term_comment(term)})
    top = b.get("top_strikes") or []
    if top:
        sp = b["spot"]
        out.append({"type": "table", "title": "🎯 행사가별 미결제 (상위 10)", "cols": ["행사가", "콜", "풋", "해석"],
                    "rows": [[cell(f"${num(t['strike'], 0)}", abs(t["strike"] - sp) / sp < 0.03), cell(f"{t['call']:,}"), cell(f"{t['put']:,}"),
                              cell(("콜 우세" if t["call"] > 1.5 * t["put"] else "풋 우세" if t["put"] > 1.5 * t["call"] else "균형")
                                   + (" · 위쪽 목표·저항" if t["strike"] > sp and t["call"] > t["put"] else " · 아래쪽 방어선" if t["strike"] < sp and t["put"] > t["call"] else ""))]
                             for t in top]})
    lv_up = [f"${num(rd.get('hi1'), 0)} (하루 1σ)", f"${num(rd.get('hi2'), 0)} (2σ)", f"${num(rw.get('hi1'), 0)} (주간 1σ)"]
    lv_dn = [f"${num(rd.get('lo1'), 0)} (하루 1σ)", f"${num(rd.get('lo2'), 0)} (2σ)", f"${num(rw.get('lo1'), 0)} (주간 1σ)"]
    out.append({"type": "kv", "title": "핵심 레벨", "rows": [["위", " → ".join(lv_up)], ["아래", " → ".join(lv_dn)],
                                                         ["맥스페인", f"${num(b.get('max_pain_near'), 0)} (현재가 대비 {((b.get('max_pain_near') or sp) / sp - 1) * 100:+.1f}%)"]]})
    return out


def term_comment(term):
    a, z = term[0]["atm_iv"], term[-1]["atm_iv"]
    shape = "단기가 더 높음(역전) — 가까운 이벤트 경계" if a > z + 2 else "장기로 갈수록 높음(정상)" if z > a + 2 else "평평"
    sk = [t["skew25"] for t in term if t.get("skew25") is not None]
    sks = ""
    if sk:
        m = sum(sk) / len(sk)
        sks = " · 스큐 전 만기 풋 프리미엄" if min(sk) > 0 else " · 스큐 혼재" if max(sk) > 0 else " · 스큐 콜 프리미엄(상방 베팅)"
        sks += f"(평균 {m:+.1f})"
    return f"곡선: {shape}{sks}"


# ── III. RISK MAP ──────────────────────────────────────────────
def risk(D, prev):
    rows = []
    # (이름, 값, Z, 나쁜 방향 부호, 비고)
    spec = [("VIX", D.a("^VIX"), D.a("^VIX", "z_level"), 1, ""), ("IG 스프레드", D.f("BAMLC0A0CM"), D.f("BAMLC0A0CM", "z"), 1, "%p"),
            ("HY 스프레드", D.f("BAMLH0A0HYM2"), D.f("BAMLH0A0HYM2", "z"), 1, "%p"), ("CCC 스프레드", D.f("BAMLH0A3HYC"), D.f("BAMLH0A3HYC", "z"), 1, "%p"),
            ("MOVE(채권 변동성)", D.a("^MOVE"), D.a("^MOVE", "z_level"), 1, ""), ("NFCI(금융상황)", D.f("NFCI"), D.f("NFCI", "z"), 1, ""),
            ("세인트루이스 금융스트레스", D.f("STLFSI4"), D.f("STLFSI4", "z"), 1, ""), ("금", D.a("GC=F"), D.a("GC=F", "z_level"), 1, "$"),
            ("HYG", D.a("HYG"), D.a("HYG", "z_level"), -1, "$"), ("신규 실업수당", D.f("ICSA"), D.f("ICSA", "z"), 1, "건")]
    for nm, v, z, sgn, u in spec:
        if v is None:
            continue
        s = R if z is not None and z * sgn >= 2 else Y if z is not None and z * sgn >= 1 else G
        vt = (f"${num(v, 2)}" if u == "$" else f"{v / 1000:,.0f}천{u}" if u == "건" else num(v, 2) + ("%" if u == "%p" else ""))
        rows.append([cell(nm, s == R), cell(vt), cell(sig(z), s != G), cell(s)])
    for fid, nm in (("RECPROUSM156N", "경기침체 확률(모형)"), ("SAHMREALTIME", "삼 법칙 지표")):
        v = D.f(fid)
        if v is None:
            continue
        s = (R if v >= 0.5 else Y if v >= 0.3 else G) if fid == "SAHMREALTIME" else (R if v >= 30 else Y if v >= 10 else G)
        rows.append([cell(nm), cell(f"{v:.2f}" + ("%" if fid == "RECPROUSM156N" else "")), cell(md(D.f(fid, "date"))), cell(s)])
    blocks = [{"type": "table", "title": "🔴 위험 지표 (Z점수)", "cols": ["지표", "수준", "Z / 기준일", "신호"], "rows": rows,
               "note": "Z: 시세는 1년, FRED 지표는 3년 기준 · 나쁜 방향으로 1σ 이상 ⚠️, 2σ 이상 🔴 · 삼 법칙 0.5 이상이면 침체 진입 신호"}]

    # 사모신용 모니터
    pc = []
    for nm, kind, key in (("HY 스프레드", "f", "BAMLH0A0HYM2"), ("IG 스프레드", "f", "BAMLC0A0CM"), ("CCC 스프레드", "f", "BAMLH0A3HYC"),
                          ("HYG(하이일드 ETF)", "a", "HYG"), ("BIZD(BDC ETF)", "a", "BIZD"), ("OWL(블루아울)", "a", "OWL"), ("KRE(지방은행)", "a", "KRE")):
        if kind == "f":
            x = D.F.get(key)
            if not x:
                continue
            w1, m1 = D.fchg(key, "w1", 100), D.fchg(key, "m1", 100)
            s = R if (m1 or 0) >= 50 or (x.get("z") or 0) >= 2 else Y if (w1 or 0) >= 15 or (x.get("z") or 0) >= 1 else G
            pc.append([cell(nm, s == R), cell(s), cell(f"{x['last']:.2f}%p ({sig(x.get('z'), 1)}) · 1주 {bp(w1)} · 1달 {bp(m1)}")])
        else:
            x = D.A.get(key)
            if not x:
                continue
            s = R if (x["m1"] or 0) <= -10 else Y if (x["m1"] or 0) <= -4 or (x["w1"] or 0) <= -3 else G
            pc.append([cell(nm, s == R), cell(s), cell(f"${num(x['last'], 2)} · 1주 {pct(x['w1'], 1)} · 1달 {pct(x['m1'], 1)} · 1년 {pct(x['y1'], 0)}")])
    if pc:
        blocks.append({"type": "table", "title": "🏦 사모신용·신용 모니터", "cols": ["지표", "경보", "내용"], "rows": pc,
                       "note": "스프레드: 1주 +15bp 또는 Z≥1 ⚠️, 1달 +50bp 또는 Z≥2 🔴 · 주가: 1달 −4% ⚠️, −10% 🔴"})

    themes = risk_themes(D)
    pmap = {t["key"]: t for t in (prev or {}).get("state", {}).get("themes", [])}
    order = {G: 0, Y: 1, R: 2}
    rm = []
    for i, t in enumerate(themes, 1):
        p = pmap.get(t["key"])
        if not p:
            chg = "첫 기록"
        elif order[t["s"]] > order[p["s"]]:
            chg = f"악화 {p['s']}→{t['s']}"
        elif order[t["s"]] < order[p["s"]]:
            chg = f"완화 {p['s']}→{t['s']}"
        else:
            chg = "유지"
        t["chg"] = chg
        rm.append([cell(str(i)), cell(t["name"], t["s"] == R), cell(t["s"]), cell(chg, chg.startswith("악화")), cell(t["why"])])
    blocks.append({"type": "table", "title": "🗺️ 위험 지도 (전일 대비)", "cols": ["#", "위험", "상태", "전일 대비", "근거"], "rows": rm,
                   "note": "상태는 아래 숫자 기준으로 자동 판정 · 전일 = 직전 프리필 문서"})
    return {"id": "risk", "title": "Section III. RISK MAP", "blocks": blocks}, themes


def risk_themes(D):
    T = []

    def add(key, name, s, why):
        T.append({"key": key, "name": name, "s": s, "why": why})
    # 1 금리
    t10, mv = D.F.get("DGS10"), D.A.get("^MOVE")
    if t10:
        s = R if t10.get("pct", 0) >= 95 and mv and (mv["z_level"] or 0) >= 1.5 else Y if t10.get("pct", 0) >= 85 or (mv and (mv["z_level"] or 0) >= 1) else G
        add("rates", "금리·채권 변동성", s, f"10년 {t10['last']:.2f}% (3년 상위 {100 - t10['pct']}%) · 1주 {bp(D.fchg('DGS10', 'w1', 100))}"
            + (f" · MOVE {mv['last']:.0f} ({sig(mv['z_level'], 1)})" if mv else ""))
    # 2 유가·인플레
    wti = D.A.get("CL=F")
    if wti:
        s = R if (wti["z_level"] or 0) >= 1.5 or (wti["m3"] or 0) >= 25 else Y if (wti["z_level"] or 0) >= 1 or (wti["w1"] or 0) >= 8 else G
        be = D.F.get("T5YIE")
        add("oil", "유가→인플레", s, f"WTI ${wti['last']:.1f} (1주 {pct(wti['w1'], 1)}, 3달 {pct(wti['m3'], 0)}, {sig(wti['z_level'], 1)})"
            + (f" · 5년 기대인플레 {be['last']:.2f}%" if be else ""))
    # 3 신용
    hy, ccc = D.F.get("BAMLH0A0HYM2"), D.F.get("BAMLH0A3HYC")
    if hy:
        m1 = D.fchg("BAMLH0A0HYM2", "m1", 100) or 0
        s = R if m1 >= 50 or (ccc and (ccc.get("z") or 0) >= 2.5) else Y if m1 >= 20 or (hy.get("z") or 0) >= 1 or (ccc and (ccc.get("z") or 0) >= 1.5) else G
        add("credit", "신용(하이일드·CCC)", s, f"HY {hy['last']:.2f}%p (1달 {bp(m1)})" + (f" · CCC {ccc['last']:.2f}%p ({sig(ccc.get('z'), 1)})" if ccc else ""))
    # 4 사모신용
    bz, owl = D.A.get("BIZD"), D.A.get("OWL")
    if bz:
        s = R if (bz["m1"] or 0) <= -8 or (owl and (owl["m1"] or 0) <= -15) else Y if (bz["m1"] or 0) <= -4 or (owl and (owl["m1"] or 0) <= -8) else G
        add("private_credit", "사모신용", s, f"BIZD 1달 {pct(bz['m1'], 1)}" + (f" · OWL 1달 {pct(owl['m1'], 1)}, 1년 {pct(owl['y1'], 0)}" if owl else ""))
    # 5 유동성
    nl = D.F.get("NETLIQ")
    if nl:
        m1 = D.fchg("NETLIQ", "m1") or 0
        so, io = D.f("SOFR"), D.f("IORB")
        sp = (so - io) * 100 if so is not None and io is not None else 0
        s = R if m1 <= -150 or sp > 10 else Y if m1 <= -50 or sp > 0 else G
        add("liquidity", "유동성", s, f"순유동성 ${nl['last'] / 1000:.2f}T (4주 {m1:+.0f}B) · SOFR−IORB {sp:+.0f}bp")
    # 6 변동성
    vix, vt = D.A.get("^VIX"), D.RAT.get("VIX/VIX3M")
    gx = (D.opt.get("SPX") or {}).get("gex_musd")
    if vix:
        s = R if vix["last"] >= 25 or (vt and vt["last"] > 1) else Y if vix["last"] >= 20 or (vt and vt["last"] > 0.95) or (gx is not None and gx < 0) else G
        add("vol", "변동성·옵션 수급", s, f"VIX {vix['last']:.1f} (1주 {pct(vix['w1'], 0)})" + (f" · VIX/VIX3M {vt['last']:.2f}" if vt else "")
            + (f" · 딜러 감마 {gx / 1000:+.0f}십억$" if gx is not None else ""))
    # 7 달러·환율
    dx, jp, kr = D.A.get("DX-Y.NYB"), D.A.get("JPY=X"), D.A.get("KRW=X")
    if dx:
        s = R if (jp and jp["last"] >= 160) or abs(dx["z_level"] or 0) >= 2.5 else Y if (jp and jp["last"] >= 155) or abs(dx["z_level"] or 0) >= 1.5 else G
        add("fx", "달러·엔 (개입 경계)", s, f"달러지수 {dx['last']:.1f} ({sig(dx['z_level'], 1)})" + (f" · 엔 {jp['last']:.1f}" if jp else "")
            + (f" · 원 {kr['last']:,.0f} (1달 {pct(kr['m1'], 1)})" if kr else ""))
    # 8 경기
    sahm, ic = D.F.get("SAHMREALTIME"), D.F.get("ICSA")
    if sahm or ic:
        s = R if sahm and sahm["last"] >= 0.5 else Y if (sahm and sahm["last"] >= 0.3) or (ic and (ic.get("z") or 0) >= 1) else G
        add("growth", "경기·고용", s, (f"삼 법칙 {sahm['last']:.2f}" if sahm else "") + (f" · 실업수당 {ic['last'] / 1000:.0f}천 ({sig(ic.get('z'), 1)})" if ic else "")
            + (f" · 실업률 {D.f('UNRATE'):.1f}%" if D.F.get("UNRATE") else ""))
    # 9 반도체·AI
    sox = D.A.get("^SOX")
    if sox:
        s = R if (sox["w1"] or 0) <= -8 or (sox["m1"] or 0) <= -12 else Y if (sox["w1"] or 0) <= -4 or (sox["z_1d"] or 0) <= -2 else G
        add("semis", "반도체·AI 밸류에이션", s, f"SOX 1일 {pct(sox['d1'], 1)} · 1주 {pct(sox['w1'], 1)} · 1달 {pct(sox['m1'], 1)} · 실현변동성 {sox.get('rv20')}%")
    # 10 크립토
    b, bt = D.btc, D.A.get("BTC-USD")
    if b.get("spot"):
        sk = b.get("skew_near") or 0
        s = R if sk >= 10 or (bt and (bt["m1"] or 0) <= -15) else Y if sk >= 5 or (b.get("funding_8h") or 0) < 0 or (bt and (bt["w1"] or 0) <= -7) else G
        add("crypto", "크립토", s, f"BTC ${b['spot']:,.0f}" + (f" (1주 {pct(bt['w1'], 1)})" if bt else "") + f" · 스큐 {sk:+.1f} · 펀딩 {b.get('funding_8h')}% · 베이시스 {b.get('basis_ann')}%")
    # 11 아시아·한국
    ks = D.A.get("^KS11")
    if ks:
        s = R if (ks["m1"] or 0) <= -10 or (ks["w1"] or 0) <= -8 else Y if (ks["w1"] or 0) <= -4 or (ks.get("rv20") or 0) >= 35 else G
        add("asia", "한국·아시아 주식", s, f"코스피 {ks['last']:,.0f} (1주 {pct(ks['w1'], 1)}, 1달 {pct(ks['m1'], 1)}, 변동성 {ks.get('rv20')}%)")
    return T


# ── IV. POSITION ───────────────────────────────────────────────
def regime_label(exp):
    return "위험 선호" if exp >= 0.9 else "중립" if exp >= 0.7 else "경계" if exp >= 0.5 else "위험 회피"


SLEEVE = {"core": "코어", "theme": "테마", "signal": "뉴스 신호"}


def position(D, prev):
    P = D.pf
    blocks = []
    if P:
        rg = P.get("regime") or {}
        exp = rg.get("exposure") or 0
        kv = [["평가액", f"${P['equity']:,.0f} ({pct(P['ret'])}, 시작 {md(P.get('start'))})"],
              ["비교", f"QQQ {pct((P.get('bench') or {}).get('QQQ'))} · SPY {pct((P.get('bench') or {}).get('SPY'))}"],
              ["위험 점수", f"{rg.get('score', 0):+.1f} → 주식 비중 {exp * 100:.0f}% ({regime_label(exp)})"],
              ["다음 결정", f"{(P.get('next') or {}).get('reason', '–')} ({md((P.get('next') or {}).get('decided'))})"]]
        neg = [c for c in rg.get("components", []) if c.get("pts")]
        if neg:
            kv.append(["점수 구성", " · ".join(f"{c['label']} ({c['pts']:+d})" for c in neg)])
        blocks.append({"type": "kv", "title": "🧭 가상 운용 현황 (규칙 기반)", "rows": kv})
        hr = []
        for h in sorted(P.get("holdings", []), key=lambda h: -h.get("weight", 0))[:12]:
            o = h.get("opt") or {}
            hr.append([cell(h["sym"], True), cell(SLEEVE.get(h.get("sleeve"), h.get("sleeve", ""))), cell(f"{h.get('weight', 0):.1f}%"), cell(f"${h.get('pnl', 0):+,.0f}", c=tone(h.get("pnl"))),
                       cell(o.get("crowd_label", "") or "–"), cell((h.get("why") or "")[:40])])
        if hr:
            blocks.append({"type": "table", "title": "보유 종목", "cols": ["종목", "구분", "비중", "손익", "옵션 쏠림", "이유"], "rows": hr})
        tr = [t for t in P.get("trades", []) if t["date"] == max((x["date"] for x in P.get("trades", [])), default="")]
        if tr:
            blocks.append({"type": "kv", "title": f"최근 거래 ({md(tr[0]['date'])})",
                           "rows": [["", " · ".join(f"{t['side']} {t['sym']} ${abs(t['amt']):,.0f}" for t in tr[:12])]]})
    v = verify(D, prev)
    if v:
        blocks.append(v)
    return {"id": "position", "title": "Section IV. POSITION 캐리오버", "blocks": blocks or [{"type": "para", "text": "가상 운용 데이터를 불러오지 못했어요."}]}


def verify(D, prev):
    """어제 문서의 자동 판단(예상 범위·위험 예산)을 오늘 실제와 맞춰 봄"""
    if not prev:
        return {"type": "para", "title": "전일 판단 검증", "text": "첫 문서라 비교할 어제 문서가 없어요. 내일부터 어제 예상 범위·판단을 자동으로 맞춰 봐요."}
    S = prev.get("state", {})
    rows = []
    spx = D.a("^GSPC")
    lv = S.get("levels", {})
    if spx and lv.get("spx_d1"):
        lo, hi = lv["spx_d1"]
        ok = lo <= spx <= hi
        rows.append([cell("S&P 하루 1σ 범위"), cell(f"{lo:,.0f} ~ {hi:,.0f}"), cell(f"{spx:,.2f}"), cell(G + " 범위 안" if ok else Y + " 범위 밖", not ok)])
    bt = D.btc.get("spot")
    if bt and lv.get("btc_d1"):
        lo, hi = lv["btc_d1"]
        ok = lo <= bt <= hi
        rows.append([cell("BTC 하루 1σ 범위"), cell(f"${lo:,.0f} ~ ${hi:,.0f}"), cell(f"${bt:,.0f}"), cell(G + " 범위 안" if ok else Y + " 범위 밖", not ok)])
    if spx and S.get("exposure") is not None and lv.get("spx"):
        r = (spx / lv["spx"] - 1) * 100
        e0 = S["exposure"]
        good = (e0 < 0.7 and r < 0) or (e0 >= 0.7 and r >= 0)
        rows.append([cell("위험 예산"), cell(f"주식 {e0 * 100:.0f}% ({regime_label(e0)})"), cell(f"S&P {r:+.2f}%"),
                     cell(G + " 방향 맞음" if good else "❌ 방향 어긋남", not good)])
    pm = {t["key"]: t for t in S.get("themes", [])}
    turned = [f"{t['name']} {pm[t['key']]['s']}→{t['s']}" for t in D._themes if t["key"] in pm and pm[t["key"]]["s"] != t["s"]]
    rows.append([cell("위험 지도 변화"), cell(f"{sum(1 for t in pm.values() if t['s'] == R)}개 🔴"),
                 cell(f"{sum(1 for t in D._themes if t['s'] == R)}개 🔴"), cell(" · ".join(turned) or "변화 없음")])
    return {"type": "table", "title": f"전일({md(prev.get('date'))}) 판단 검증", "cols": ["판단", "어제 예상", "실제", "결과"], "rows": rows,
            "note": "범위 밖이면 옵션 시장이 예상하지 못한 움직임 — 다음날 변동성 경계"}


# ── V. TRADE PLAN ──────────────────────────────────────────────
def levels(D):
    L = {}
    spx, vix = D.a("^GSPC"), D.a("^VIX")
    if spx and vix:
        d1, w1 = spx * vix / 100 / math.sqrt(252), spx * vix / 100 / math.sqrt(52)
        L.update(spx=spx, vix=vix, spx_d1=[round(spx - d1), round(spx + d1)], spx_w1=[round(spx - w1), round(spx + w1)])
    o = D.opt.get("SPX") or {}
    L.update(call_wall=o.get("call_wall"), put_wall=o.get("put_wall"), gex=o.get("gex_musd"))
    q = D.opt.get("QQQ") or {}
    if q.get("price") and q.get("atm_iv30"):
        s = q["price"] * q["atm_iv30"] / 100 / math.sqrt(52)
        L.update(qqq=q["price"], qqq_w1=[round(q["price"] - s, 1), round(q["price"] + s, 1)], qqq_cw=q.get("call_wall"), qqq_pw=q.get("put_wall"))
    rd, rw = D.btc.get("range_day") or {}, D.btc.get("range_week") or {}
    if rd:
        L["btc_d1"] = [rd["lo1"], rd["hi1"]]
    if rw:
        L["btc_w1"] = [rw["lo1"], rw["hi1"]]
    t10, mv = D.f("DGS10"), D.a("^MOVE")
    if t10 is not None and mv:
        s = mv / math.sqrt(52) / 100          # MOVE(bp 연율) → 주간 1σ(%p)
        L.update(t10=t10, t10_w1=[round(t10 - s, 2), round(t10 + s, 2)])
    wti = D.A.get("CL=F")
    if wti and wti.get("rv20"):
        s = wti["last"] * wti["rv20"] / 100 / math.sqrt(52)
        L.update(wti=wti["last"], wti_w1=[round(wti["last"] - s, 1), round(wti["last"] + s, 1)])
    hy = D.f("BAMLH0A0HYM2")
    if hy is not None:
        L["hy"] = hy
    return L


def plan(D, L, themes):
    blocks = []
    rows = []
    if L.get("spx"):
        rows.append([cell("S&P 500", True), cell(f"{L['spx']:,.0f}"), cell(f"{L['spx_d1'][0]:,} ~ {L['spx_d1'][1]:,}"), cell(f"{L['spx_w1'][0]:,} ~ {L['spx_w1'][1]:,}"),
                     cell(f"콜월 {num(L.get('call_wall'), 0)} · 풋월 {num(L.get('put_wall'), 0)} · 감마 {(L.get('gex') or 0) / 1000:+.0f}십억$")])
    if L.get("qqq"):
        rows.append([cell("QQQ"), cell(f"{L['qqq']:,.2f}"), cell("–"), cell(f"{L['qqq_w1'][0]} ~ {L['qqq_w1'][1]}"),
                     cell(f"콜월 {num(L.get('qqq_cw'), 0)} · 풋월 {num(L.get('qqq_pw'), 0)}")])
    if L.get("t10") is not None:
        rows.append([cell("미 10년물"), cell(f"{L['t10']:.2f}%"), cell("–"), cell(f"{L['t10_w1'][0]:.2f} ~ {L['t10_w1'][1]:.2f}%"), cell("MOVE 기준")])
    if L.get("wti"):
        rows.append([cell("WTI"), cell(f"${L['wti']:.2f}"), cell("–"), cell(f"${L['wti_w1'][0]} ~ ${L['wti_w1'][1]}"), cell("20일 실현변동성 기준")])
    if L.get("btc_d1"):
        rows.append([cell("비트코인"), cell(f"${D.btc['spot']:,.0f}"), cell(f"${L['btc_d1'][0]:,} ~ ${L['btc_d1'][1]:,}"),
                     cell(f"${L['btc_w1'][0]:,} ~ ${L['btc_w1'][1]:,}" if L.get("btc_w1") else "–"), cell(f"맥스페인 ${num(D.btc.get('max_pain_near'), 0)}")])
    blocks.append({"type": "table", "title": "📏 핵심 레벨 (옵션 내재 범위)", "cols": ["자산", "현재", "하루 1σ", "주간 1σ", "옵션 벽·기준"], "rows": rows,
                   "note": "S&P = VIX, QQQ = 30일 ATM 내재변동성, 10년물 = MOVE 로 계산한 1표준편차(68%) 범위 · 콜월/풋월 = 미결제가 가장 많이 쌓인 행사가"})
    if L.get("spx"):
        vix = L["vix"]
        lo, hi = L["spx_w1"]
        pw = L.get("put_wall")
        hy = L.get("hy")
        sc = [["Bull", f"S&P {hi:,} (주간 +1σ) 위 안착 + VIX {max(12, vix - 3):.0f} 아래" + (f" + HY 스프레드 {hy - 0.15:.2f}%p 아래" if hy else ""), "[ ]%", "약 16%", "[회의에서 판단]"],
              ["Base", f"S&P {lo:,} ~ {hi:,} 박스 + VIX {max(12, vix - 3):.0f}~{vix + 3:.0f}", "[ ]%", "약 68%", "[회의에서 판단]"],
              ["Bear", f"S&P {lo:,} (주간 −1σ) 이탈" + (f" → 풋월 {pw:,.0f}" if pw and pw < lo else "") + f" + VIX {max(25, vix + 5):.0f} 위 또는 VIX/VIX3M > 1"
               + (f" + HY {hy + 0.25:.2f}%p 위" if hy else ""), "[ ]%", "약 16%", "[회의에서 판단]"]]
        blocks.append({"type": "table", "title": "🎲 시나리오별 행동 계획", "cols": ["시나리오", "트리거(자동)", "확률", "옵션 내재(참고)", "행동"],
                       "rows": [[cell(s[0], True), cell(s[1]), cell(s[2]), cell(s[3]), cell(s[4])] for s in sc],
                       "note": "트리거 숫자는 오늘 데이터로 자동 계산 · 옵션 내재 = 1주 뒤 정규분포 가정 확률(스큐 미반영) · 확률·행동은 회의에서"})
    blocks += calendar(D)
    return {"id": "plan", "title": "Section V. TRADE PLAN", "blocks": blocks}


def calendar(D):
    W = D.weekly
    if not W:
        return []
    today = datetime.now(KST).date().isoformat()
    rows = []
    for m in W.get("macro", []):
        if m.get("day", "") < today or not (m.get("star") or m.get("kind") in ("고용", "물가", "성장", "중앙은행") and m.get("who", "").startswith("🇺🇸")):
            continue
        fp = " · ".join(x for x in (f"예상 {m['fc']}" if m.get("fc") else "", f"이전 {m['prev']}" if m.get("prev") else "") if x)
        rows.append((m.get("ts") or 0, m["day"], m.get("kst", "")[-5:], f"{m.get('who', '')} {m.get('ko') or m.get('title')}", fp, "⭐⭐⭐" if m.get("star") else "⭐⭐"))
    for e in W.get("earnings", []):
        if e.get("day", "") >= today and e.get("star"):
            rows.append((0, e["day"], e.get("when", ""), f"실적 {e['sym']} ({e.get('name', '')[:24]})", f"EPS 예상 {e.get('eps', '–')}", "⭐⭐"))
    for i in W.get("industry", []):
        if i.get("day", "") >= today and i.get("tm"):
            rows.append((0, i["day"], "일정", f"{i.get('ko') or i.get('title')}{i.get('suffix', '')}", i.get("who", ""), "⭐"))
    if not rows:
        return [{"type": "para", "title": "📅 이번 주 일정", "text": f"이번 주 남은 주요 일정이 없어요 (주간 일정 {md(W.get('mon'))}~{md(W.get('fri'))} 기준, 매주 일요일 갱신)."}]
    rows.sort(key=lambda r: (r[1], r[0]))
    out = []
    for ts, d, tm, ev, fp, st in rows[:30]:
        dd = date.fromisoformat(d)
        lab = ("오늘 " if d == today else "") + f"{dd.month}/{dd.day} ({WD[dd.weekday()]})"
        out.append([cell(lab, d == today), cell(tm), cell(ev, st == "⭐⭐⭐"), cell(fp), cell(st)])
    return [{"type": "table", "title": f"📅 이번 주 일정 ({md(W.get('mon'))}~{md(W.get('fri'))})", "cols": ["날짜", "시간(KST)", "이벤트", "예상·이전", "중요도"], "rows": out,
             "note": "랩 '주간 전략' 일정 중 ⭐ 표시·미국 핵심 지표·대형 실적·주요 산업 행사"}]


# ── VI. TODAY ──────────────────────────────────────────────────
def today_sec(D, L, themes):
    rows = []
    if L.get("put_wall") and L.get("spx"):
        rows.append(["S&P 가 풋월 " + f"{L['put_wall']:,.0f}" + " 아래로", "딜러 헤지 매도가 하락을 키울 수 있는 구간 — [회의에서 판단]"])
    if L.get("spx_w1"):
        rows.append([f"S&P {L['spx_w1'][1]:,} 돌파 + VIX 하락", "위험 선호 복귀 신호 — [회의에서 판단]"])
    rows.append(["VIX 25 돌파 또는 VIX/VIX3M > 1", "가상 운용 규칙상 위험 점수 −2 → 주식 비중 축소 — [회의에서 판단]"])
    if L.get("t10_w1"):
        rows.append([f"10년물 {L['t10_w1'][1]:.2f}% 돌파 (주간 +1σ)", "성장주·장기채 압박 확대 — [회의에서 판단]"])
    if L.get("hy") is not None:
        rows.append([f"HY 스프레드 {L['hy'] + 0.25:.2f}%p 위 (+25bp)", "신용 경보 — 사모신용·지방은행 점검 — [회의에서 판단]"])
    if L.get("btc_w1"):
        rows.append([f"BTC ${L['btc_w1'][0]:,} 이탈 / ${L['btc_w1'][1]:,} 돌파", "주간 1σ 밖 — 추세 전환 검토 — [회의에서 판단]"])
    for t in themes:
        if t["s"] == R:
            rows.append([f"{t['name']} 추가 악화 (지금 🔴)", f"{t['why']} — [회의에서 판단]"])
    ev = next_events(D, 3)
    for e in ev[:2]:
        rows.append([f"{e} 결과가 예상과 크게 다름", "[회의에서 판단]"])
    blocks = [{"type": "table", "title": "If-Then 플랜", "cols": ["조건", "행동"], "rows": [[cell(a), cell(b)] for a, b in rows]}]
    exp = ((D.pf or {}).get("regime") or {}).get("exposure")
    reg = regime_label(exp) if exp is not None else "–"
    reds = [t["name"] for t in themes if t["s"] == R]
    inv = []
    if L.get("call_wall") and L.get("vix"):
        inv.append(f"S&P 콜월 {L['call_wall']:,.0f} 회복 + VIX {max(12, L['vix'] - 4):.0f} 아래")
    if L.get("hy") is not None:
        inv.append(f"HY 스프레드 {L['hy'] + 0.25:.2f}%p 돌파")
    chk = [["1. 레짐 판단", [f"현 레짐 유지 (규칙: {reg})", "위험 회피 강화", "위험 선호 전환", "판단 보류 (지표 후)"]],
           ["2. 미스프라이싱", ["하방 미프 (" + (", ".join(reds[:2]) or "위험") + " 저평가)", "상방 미프 (반등 저평가)", "양방향 동일", "변경 없음"]],
           ["3. 포지션 방향", [f"규칙대로 (주식 {exp * 100:.0f}%)" if exp is not None else "규칙대로", "노출 더 축소 / 현금↑", "헤지 추가 (풋·VIX)", "분할 매수 시작"]],
           ["4. 무효화 조건", inv + ["기타: ________"]],
           ["5. 다음 포지션 검토", (ev[:3] or ["다음 주요 지표 발표 후"]) + ["기타: ________"]],
           ["6. 확신도 (1~10)", ["_____ / 10"]]]
    blocks.append({"type": "check", "title": "회의 판단 항목 (옵션 선택 → 회의에서 확정)", "items": chk})
    return {"id": "today", "title": f"Section VI. TODAY — {md(datetime.now(KST).date().isoformat())}({WD[datetime.now(KST).weekday()]}) 액션", "blocks": blocks}


def next_events(D, n):
    W = D.weekly or {}
    today = datetime.now(KST).date().isoformat()
    ev = []
    for m in sorted(W.get("macro", []), key=lambda m: m.get("ts") or 0):
        if m.get("day", "") >= today and m.get("star"):
            ev.append(f"{md(m['day'])} {m.get('ko') or m.get('title')}")
    for e in W.get("earnings", []):
        if e.get("day", "") >= today and e.get("star") and (e.get("cap") or 0) >= 5e11:
            ev.append(f"{md(e['day'])} {e['sym']} 실적")
    return ev[:n]


# ── 요약 (맨 위) ───────────────────────────────────────────────
def headline(D, themes, prev):
    c = {s: sum(1 for t in themes if t["s"] == s) for s in (R, Y, G)}
    worse = [t for t in themes if t.get("chg", "").startswith("악화")]
    better = [t for t in themes if t.get("chg", "").startswith("완화")]
    pts = [f"위험 신호 {R} {c[R]} · {Y} {c[Y]} · {G} {c[G]}" + (f" — 전일 대비 악화 {len(worse)}, 완화 {len(better)}" if prev else "")]
    big = sorted((x for x in D.A.values() if x.get("z_1d") is not None and abs(x["z_1d"]) >= 2), key=lambda x: -abs(x["z_1d"]))
    if big:
        pts.append("어제 이례적 움직임: " + ", ".join(f"{x['name']} {pct(x['d1'], 1)} ({x['z_1d']:+.1f}σ)" for x in big[:4]))
    ext = sorted((x for x in D.A.values() if x.get("z_level") is not None and abs(x["z_level"]) >= 2), key=lambda x: -abs(x["z_level"]))
    if ext:
        pts.append("1년 기준 극단: " + ", ".join(f"{x['name']} {x['z_level']:+.1f}σ" for x in ext[:5]))
    fx = [x for x in D.F.values() if x.get("z") is not None and abs(x["z"]) >= 2.5 and x["id"] not in ("DGS20", "DGS7", "DGS3")]
    if fx:
        pts.append("3년 기준 극단: " + ", ".join(f"{x['name']} {num(x['last'], 2)} ({x['z']:+.1f}σ)" for x in sorted(fx, key=lambda x: -abs(x["z"]))[:4]))
    for t in worse[:3]:
        pts.append(f"악화: {t['name']} — {t['why']}")
    return pts


# ── 저장 ───────────────────────────────────────────────────────
def build(out):
    D = Data(out)
    now = datetime.now(KST)
    day = now.date().isoformat()
    hist_dir = os.path.join(DATA, "prefill")
    os.makedirs(hist_dir, exist_ok=True)
    prevs = sorted(p for p in glob.glob(os.path.join(hist_dir, "*.json")) if os.path.basename(p)[:10] < day)
    prev = load(prevs[-1]) if prevs else None

    sec_risk, themes = risk(D, prev)
    D._themes = themes
    L = levels(D)
    us_close = D.a("^GSPC", "date")
    doc = {"date": day, "weekday": WD[now.weekday()], "generated_at": now.isoformat(timespec="minutes"), "us_close": us_close,
           "meta": [["일시", f"{now:%Y.%m.%d} ({WD[now.weekday()]})"], ["생성", f"{now:%H:%M} KST 자동 프리필"], ["상태", "⏳ 회의 전"],
                    ["기준", f"미국 {md(us_close)} 종가 · FRED·CFTC·Deribit·CBOE·야후 · 미니 블벅 터미널 뉴스"],
                    ["데이터", " · ".join(f"{k} {'✓' if v else '✗'}" for k, v in D.status.items())]],
           "summary": None, "sections": []}
    doc["sections"] = [market(D), flow(D), sec_risk, position(D, prev), plan(D, L, themes), today_sec(D, L, themes)]
    doc["summary"] = headline(D, themes, prev)
    doc["state"] = {"levels": L, "themes": [{k: t[k] for k in ("key", "name", "s", "why")} for t in themes],
                    "exposure": ((D.pf or {}).get("regime") or {}).get("exposure")}
    doc["prev_date"] = prev.get("date") if prev else None

    json.dump(doc, open(os.path.join(hist_dir, f"{day}.json"), "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    json.dump(doc, open(os.path.join(out, "prefill.json"), "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    # 지난 문서 (최근 60개)
    pub = os.path.join(out, "prefill")
    os.makedirs(pub, exist_ok=True)
    days = sorted(os.path.basename(p)[:10] for p in glob.glob(os.path.join(hist_dir, "*.json")))[-60:]
    for d in days:
        src = os.path.join(hist_dir, f"{d}.json")
        with open(src, encoding="utf-8") as f, open(os.path.join(pub, f"{d}.json"), "w", encoding="utf-8") as g:
            g.write(f.read())
    json.dump(days[::-1], open(os.path.join(pub, "index.json"), "w"), separators=(",", ":"))
    try:
        from prefill_docx import write_docx
        write_docx(doc, os.path.join(out, "prefill.docx"))
        doc_ok = True
    except Exception as e:
        print(f"[prefill] docx 실패: {type(e).__name__} {e}")
        doc_ok = False
    n = sum(len(b.get("rows") or b.get("items") or []) for s in doc["sections"] for b in s["blocks"])
    print(f"[prefill] {day} · 섹션 {len(doc['sections'])} · 항목 {n} · 전일 {doc['prev_date']} · docx {'✓' if doc_ok else '✗'} · "
          + " ".join(f"{k}{'✓' if v else '✗'}" for k, v in D.status.items()))
    return doc


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="public_lab")
    build(ap.parse_args().out)
