"""
포트폴리오 가상 운용 — 랩에 쌓이는 시장 환경·매크로 지표·심층 뉴스·뉴스 신호를 함께 반영  (상태: labdata/portfolio.json)

  python lab/portfolio.py --out public_lab

하루 흐름 (랩이 미국 장 마감 뒤 07:00 KST 에 실행)
  1) 어제 정한 목표 비중을 오늘 시가로 체결 (신호·판단은 장 마감 뒤에 하므로 그날 종가로는 못 삼)
  2) 오늘 종가로 평가 · 현금에는 3개월 국채 금리만큼 이자
  3) 다음 거래일 목표 비중 계산 → 대기 주문으로 저장

목표 비중
  · 주식 비중(위험 예산) = 시장 환경 점수 + 매크로 점수 → 40~90%, 나머지는 현금
      시장 환경: VIX 수준, VIX/VIX3M(1 넘으면 공포), 딜러 감마(GEX), 채권 변동성(MOVE), 장단기 금리차
      매크로: 근원 PCE·근원 CPI 의 최근 3개월 연율 vs 전년비(재가속이면 감점), PPI 최종수요, 10년물 5% 이상
  · 주식 안에서: 코어 50% · 테마 35% · 뉴스 신호 15% (신호가 없으면 그만큼 코어로)
      테마 = 심층 뉴스 기사마다 적어 둔 '시각'(종목·방향·기한) + GPU 임대료·AI 비용 PPI 로 만든 데이터 테마
        (데이터 테마는 종목마다 자기 옵션 쏠림에 따라 보는 기간을 바꿈: 같은 방향 쏠림 → 전월비·1주, 중립 → 3개월·1달, 반대 쏠림 → 전년비·3달)
      위험 점수가 -3 이하면 테마 롱 절반, 숏은 테마의 1/3 까지
      뉴스 신호 = signals.py 후보(최근 3거래일) 상위 2개, 각 5거래일 보유 — 옵션 포지션이 반대면 건너뜀
  · 편입 종목은 10개 이내: 코어 ETF 1개(점수 0 이상 QQQ, 아니면 SPY) + 신호 + 테마(강한 순)
  · 개별 종목 옵션(CBOE, 시장 환경 기록): 풋/콜 거래 비율·25델타 스큐·딜러 감마·콜월/풋월·며칠 새 쏠림 변화로 방향 점수
      → 테마 점수에 곱해(같은 방향 최대 1.5배, 반대 최소 0.5배; 이미 크게 쏠렸으면(±0.8) 키우지 않음) 순위를 정하고, 비중은 내재변동성(IV)에 반비례
      → IV ÷ 실제 변동성(20일)이 1.5배 넘으면 '옵션 비쌈'으로 표시
  · 다시 맞추기(리밸런싱): 위험 단계가 바뀌거나, 테마 구성이 바뀌거나, 마지막 조정 뒤 5거래일 — 아니면 신호 몫만 매일 조정
  · 비용 0.1% (사고팔 때 각각) · 비교: 같은 날 QQQ / SPY 에 전부 넣었을 때
"""
import argparse
import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES = {"version": 2, "capital": 100000.0, "cost": 0.001, "core": 0.5, "theme": 0.35, "signal": 0.15,
         "signal_max": 2, "signal_hold": 5, "rebalance_days": 5, "min_trade": 0.003, "max_names": 10}
EXPOSURE = [(1, 0.9), (0, 0.8), (-1, 0.7), (-2, 0.6), (-3, 0.5)]   # 점수 이상 → 주식 비중, 그 아래는 40%


def load(path, default=None):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return default


# ── 1. 시장 환경 점수 ──────────────────────────────────────────────
def env_score(env):
    row = (env or {}).get("summary", [{}])[-1] if (env or {}).get("summary") else {}
    comp = []

    def add(pts, label, why, key):
        comp.append({"group": "시장 환경", "key": key, "pts": pts, "label": label, "why": why})
    vix = row.get("vix")
    if vix is not None:
        p = 1 if vix < 15 else 0 if vix < 20 else -1 if vix < 25 else -2
        add(p, f"VIX {vix:.1f}", "15 미만 안정(+1) · 20~25 경계(−1) · 25 이상 공포(−2)", "vix")
    t = row.get("vix_term")
    if t is not None:
        p = 1 if t < 0.9 else 0 if t <= 1.0 else -2
        add(p, f"VIX/VIX3M {t:.2f}", "0.9 미만 여유(+1) · 1 넘으면 단기 공포가 더 큼(−2)", "vix_term")
    gex = row.get("spx_gex")
    if gex is not None:
        add(-1 if gex < 0 else 0, f"S&P 딜러 감마 {gex/1000:+.1f}십억$", "음수면 딜러가 변동을 키우는 쪽(−1)", "gex")
    mv = row.get("move")
    if mv is not None:
        add(-1 if mv > 120 else 0, f"MOVE {mv:.0f}", "채권 변동성 120 넘으면 −1", "move")
    cv = row.get("curve_10y3m")
    if cv is not None:
        add(-1 if cv < 0 else 0, f"10년−3개월 {cv:+.2f}%p", "역전이면 −1", "curve")
    return comp, row


# ── 2. 매크로 점수 ────────────────────────────────────────────────
def series(macro, sid):
    for g in (macro or {}).get("groups", []):
        for it in g["items"]:
            if it["id"] == sid:
                return it
    return None


def chg(it, k, n):
    v = it["v"]
    a, b = v[k] if 0 <= k < len(v) else None, v[k - n] if 0 <= k - n < len(v) else None
    return (a / b - 1) * 100 if a and b else None


def last_k(it):
    k = len(it["v"]) - 1
    while k >= 0 and it["v"][k] is None:
        k -= 1
    return k


def macro_score(macro, envrow):
    comp = []
    for sid, nm, key in (("PCEPILFE", "근원 PCE", "core_pce"), ("CPILFESL", "근원 CPI", "core_cpi")):
        it = series(macro, sid)
        if not it:
            continue
        k = last_k(it)
        y, a3 = chg(it, k, 12), None
        if k >= 3 and it["v"][k] and it["v"][k - 3]:
            a3 = ((it["v"][k] / it["v"][k - 3]) ** 4 - 1) * 100
        if y is None or a3 is None:
            continue
        p = -1 if a3 > y + 0.5 else 1 if a3 < y - 0.5 else 0
        comp.append({"group": "매크로", "key": key, "pts": p, "label": f"{nm} 3개월 연율 {a3:.1f}% vs 전년비 {y:.1f}%",
                     "why": "최근 3개월이 더 빠르면 물가 재가속(−1), 느리면 둔화(+1)"})
    it = series(macro, "PPIFIS")
    if it:
        y = chg(it, last_k(it), 12)
        if y is not None:
            comp.append({"group": "매크로", "key": "ppi", "pts": -1 if y > 5 else 0, "label": f"PPI 최종수요 전년비 {y:.1f}%",
                         "why": "5% 넘으면 비용 압력(−1)"})
    u = envrow.get("ust10y")
    if u is not None:
        comp.append({"group": "매크로", "key": "ust10y", "pts": -1 if u >= 5 else 0, "label": f"미 10년물 {u:.2f}%",
                     "why": "5% 이상이면 성장주 할인율 부담(−1)"})
    return comp


def exposure(score):
    for th, e in EXPOSURE:
        if score >= th:
            return e
    return 0.4


# ── 3. 테마 (심층 뉴스 시각 + 데이터 테마) ────────────────────────
def gpu_trend(gpu, days=30):
    """H100·H200 시장 중간값 7일 평균의 변화(%) — days 일 전 대비 (1주 7 · 1달 30 · 약 3달 89)"""
    out = []
    for g in (gpu or {}).get("gpus", []):
        if g["key"] not in ("H100", "H200") or not g.get("vast"):
            continue
        dl = g["vast"].get("daily") or []
        ma = [sum(x[1] for x in dl[max(0, i - 6):i + 1]) / len(dl[max(0, i - 6):i + 1]) for i in range(len(dl))]
        if len(ma) > days + 6:
            out.append((ma[-1] / ma[-1 - days] - 1) * 100)
    return sum(out) / len(out) if out else None


def crowd(o):
    """옵션 쏠림 −1(풋 쪽) ~ +1(콜 쪽): 풋/콜 거래량 비율(1 기준)과 25델타 스큐(4 기준)만 사용"""
    if not o:
        return None
    parts = []
    if o.get("pc_vol") is not None:
        parts.append(max(-1.0, min(1.0, (1 - o["pc_vol"]) / 0.5)))
    if o.get("skew25") is not None:
        parts.append(max(-1.0, min(1.0, (4 - o["skew25"]) / 4)))
    return sum(parts) / len(parts) if parts else None


def data_theme(src, name, syms, d, vals, labels, thr, rev, why, raw_opts):
    """데이터 테마 하나: 옵션이 어느 쪽에 몰렸는지로 볼 기간을 고름
       · 같은 방향으로 쏠림 → 짧은 기간(전월비·1주): 긴 추세는 이미 가격에 반영, 꺾이는지가 중요
       · 중립 → 중간 기간(3개월·1달)
       · 반대로 쏠림 → 긴 기간(전년비·3달): 시장이 아직 안 믿는 큰 추세
       반환: 테마(비중↑/↓) · '보류'(긴 추세는 맞지만 짧은 변화가 멈춤) · 없음"""
    cs = [c for c in (crowd(raw_opts.get(x)) for x in syms) if c is not None]
    c = sum(cs) / len(cs) if cs else None
    if c is None:
        h, hw = "mid", "옵션 자료 없음 — 중간 기간"
    elif c * d >= 0.4:
        h, hw = "short", f"옵션이 같은 방향으로 쏠림({c:+.2f}) → {labels['short']} 변화로 꺾임 확인"
    elif c * d <= -0.4:
        h, hw = "long", f"옵션이 반대로 쏠림({c:+.2f}) → 시장이 덜 믿는 큰 추세, {labels['long']} 기준"
    else:
        h, hw = "mid", f"옵션 쏠림 중립({c:+.2f}) → {labels['mid']} 기준"
    vals = {k: (None if x is None else round(x, 2) + 0.0) for k, x in vals.items()}   # −0.0 표시 방지
    v = vals.get(h)
    if v is None:
        h, hw = "mid", hw + " (자료 부족 → 중간 기간)"
        v = vals.get("mid")
    if v is None:
        return None
    allv = " / ".join(f"{labels[k]} {vals[k]:+.1f}%" for k in ("short", "mid", "long") if vals.get(k) is not None)
    base = {"src": src, "syms": syms, "horizon": h, "crowd": None if c is None else round(c, 2), "vals": vals}
    long_ok = vals.get("long") is not None and vals["long"] * d > thr["long"]
    if v * d > thr[h]:
        return {**base, "title": f"{name} {labels[h]} {v:+.1f}%", "dir": d, "w": 0.5, "why": f"{why} · 관찰: {hw} · {allv}"}
    if h == "short" and long_ok and v * d <= rev:
        return {**base, "title": f"{name} {labels[h]} {v:+.1f}% — 쏠린 쪽 되돌림 위험", "dir": -d, "w": 0.5,
                "why": f"긴 추세({labels['long']} {vals['long']:+.1f}%)는 맞지만 옵션이 이미 쏠린 상태에서 짧은 변화가 반대로 꺾임 · 관찰: {hw} · {allv}"}
    if long_ok:
        return {**base, "title": f"{name} — 보류 ({labels[h]} {v:+.1f}%)", "dir": 0, "w": 0,
                "why": f"긴 추세({labels['long']} {vals['long']:+.1f}%)는 강하지만 {labels[h]} 변화가 기준({thr[h]:g}%)에 못 미쳐 비중 확대 보류 · 관찰: {hw} · {allv}"}
    return None


def themes(today, deep, gpu, macro, universe, raw_opts=None):
    raw_opts = raw_opts or {}
    tilts = []
    for a in deep or []:
        for t in a.get("tilts", []):
            if a["date"] <= today <= t.get("until", "9999"):
                tilts.append({"src": "심층 뉴스", "title": a["title"], "id": a["id"], **t})
    gv = {"short": gpu_trend(gpu, 7), "mid": gpu_trend(gpu, 30), "long": gpu_trend(gpu, 83)}
    gl = {"short": "1주", "mid": "1달", "long": "약 3달"}
    if gv["mid"] is not None:
        # 종목마다 자기 옵션 쏠림으로 볼 기간을 따로 고름
        for sym in ("NVDA", "AVGO", "TSM"):
            t = data_theme("GPU 임대료", "H100·H200 임대료", [sym], 1, gv, gl,
                           {"short": 1, "mid": 5, "long": 10}, -3, "빌리는 값이 오르면 AI 칩 수요가 견조하다는 뜻", raw_opts)
            if t:
                tilts.append(t)
        for sym in ("CRWV", "NBIS", "IREN"):
            t = data_theme("GPU 임대료", "H100·H200 임대료", [sym], -1, gv, gl,
                           {"short": 2, "mid": 10, "long": 20}, -3, "임대료가 빠지면 GPU 임대업(네오클라우드) 회수가 늦어짐", raw_opts)
            if t:
                tilts.append(t)
    st = series(macro, "PCU334112334112")
    if st:
        k = last_k(st)
        sv = {"short": chg(st, k, 1), "mid": chg(st, k, 3), "long": chg(st, k, 12)}
        for sym in ("MU", "SNDK"):
            t = data_theme("AI 비용 PPI", "저장장치 PPI", [sym], 1, sv, {"short": "전월비", "mid": "3개월", "long": "전년비"},
                           {"short": 0.5, "mid": 5, "long": 20}, -2, "저장장치 생산자물가 상승 = 메모리 업체 가격 결정력", raw_opts)
            if t:
                tilts.append(t)
    score = {}
    for t in tilts:
        for s in t["syms"]:
            if s in universe:
                score[s] = score.get(s, 0) + t["dir"] * t.get("w", 1)
    return tilts, {s: v for s, v in score.items() if abs(v) >= 0.5}


# ── 4. 개별 종목 옵션 ─────────────────────────────────────────────
def realized_vol(closes, n=20):
    """최근 n거래일 종가 변화로 본 실제 변동성(연율 %)"""
    import math
    c = [x for x in closes if x][-(n + 1):]
    if len(c) < n + 1:
        return None
    r = [math.log(c[i] / c[i - 1]) for i in range(1, len(c))]
    m = sum(r) / len(r)
    return (sum((x - m) ** 2 for x in r) / (len(r) - 1)) ** 0.5 * (252 ** 0.5) * 100


def opt_view(o, prev=None, closes=None):
    """종목별 옵션 상태 + 방향 점수(−1.75 ~ +1.75, +면 강세 쪽)
       · 쏠림: 풋/콜 거래 비율·25델타 스큐 (−1 풋 쪽 ~ +1 콜 쪽) 와 며칠 전 대비 변화
       · 딜러 감마 부호, 콜월·풋월까지 거리, 내재변동성(IV) ÷ 실제 변동성(20일)"""
    if not o or not o.get("price"):
        return None
    pts, why = 0.0, []
    pc = o.get("pc_vol")
    if pc is not None:
        if pc < 0.6:
            pts += 0.5; why.append(f"콜 거래 우세(풋/콜 {pc:.2f})")
        elif pc > 1.2:
            pts -= 0.5; why.append(f"풋 거래 우세(풋/콜 {pc:.2f})")
    sk = o.get("skew25")
    if sk is not None:
        if sk < 2:
            pts += 0.5; why.append(f"스큐 낮음 {sk:.1f}(상승 베팅)")
        elif sk > 8:
            pts -= 0.5; why.append(f"스큐 높음 {sk:.1f}(하락 대비)")
    g = o.get("gex_musd")
    if g is not None:
        pts += 0.25 if g > 0 else -0.25
        why.append("딜러 감마 +(변동 완화)" if g > 0 else "딜러 감마 −(변동 확대)")
    S, cw, pw = o["price"], o.get("call_wall"), o.get("put_wall")
    if cw and S >= cw * 0.99:
        pts -= 0.25; why.append(f"콜월 {cw:g} 도달(위가 막힘)")
    if pw and S <= pw * 1.02 and S >= pw * 0.97:
        pts += 0.25; why.append(f"풋월 {pw:g} 근처(지지)")
    c = crowd(o)
    cp = crowd(prev) if prev else None
    dc = None if c is None or cp is None else c - cp
    if dc is not None and abs(dc) >= 0.3:
        pts += 0.25 if dc > 0 else -0.25
        why.append(f"며칠 새 {'콜' if dc > 0 else '풋'} 쪽으로 이동({dc:+.2f})")
    iv = o.get("atm_iv30") or o.get("iv30")
    rv = realized_vol(closes or [])
    ivrv = iv / rv if iv and rv else None
    if ivrv is not None and ivrv >= 1.5:
        why.append(f"옵션 비쌈 IV/실제 {ivrv:.1f}배(이벤트·불안 대비)")
    lab = None if c is None else "콜 쏠림" if c >= 0.4 else "풋 쏠림" if c <= -0.4 else "중립"
    return {"pts": round(pts, 2), "why": why, "iv": iv, "pc": pc, "pc_oi": o.get("pc_oi"), "skew": sk,
            "gex": g, "call_wall": cw, "put_wall": pw, "asof": o.get("asof"),
            "crowd": None if c is None else round(c, 2), "crowd_label": lab,
            "crowd_chg": None if dc is None else round(dc, 2), "prev_asof": (prev or {}).get("asof") if prev else None,
            "cw_dist": round((cw / S - 1) * 100, 1) if cw else None, "pw_dist": round((pw / S - 1) * 100, 1) if pw else None,
            "rv20": None if rv is None else round(rv, 1), "iv_rv": None if ivrv is None else round(ivrv, 2)}


# ── 5. 목표 비중 (편입 종목 10개 이내) ────────────────────────────
def targets(R, E, score, theme_score, picks, opts):
    """반환: {(슬리브, 종목): 비중}, 테마 선정 내역"""
    w = {}
    core_sym = "QQQ" if score >= 0 else "SPY"          # 위험 점수 좋으면 나스닥, 아니면 S&P (종목 수를 아끼려 하나만)
    sig = picks[:R["signal_max"]]
    sig_w = R["signal"] * E / R["signal_max"]
    for c in sig:
        w[("signal", c["sym"])] = sig_w * (1 if c["direction"] == "롱" else -1)
    names = {core_sym} | {c["sym"] for c in sig}
    room = R["max_names"] - len(names)
    # 테마: 심층 뉴스·데이터 점수 × 옵션 확인 (같은 방향이면 키우고 반대면 줄임) → 강한 순으로 남은 자리만큼
    ranked = []
    for sym, base in theme_score.items():
        ov = opts.get(sym)
        o = ov["pts"] if ov else 0.0
        d = 1 if base > 0 else -1
        mult = max(0.5, min(1.5, 1 + 0.3 * o * d))
        note = None
        c = (ov or {}).get("crowd")
        if c is not None and c * d >= 0.8 and mult > 1:
            mult, note = 1.0, f"옵션이 이미 같은 방향으로 크게 쏠림({c:+.2f}) → 확대 안 함"
        ranked.append({"sym": sym, "base": base, "opt": o, "mult": round(mult, 2), "note": note, "adj": base * mult, "iv": (ov or {}).get("iv")})
    ranked.sort(key=lambda r: -abs(r["adj"]))
    chosen, dropped = [], []
    for r in ranked:
        if r["sym"] in names:
            chosen.append(r)          # 신호·코어와 겹치는 종목은 자리를 더 쓰지 않음
        elif room > 0:
            chosen.append(r)
            names.add(r["sym"])
            room -= 1
        else:
            dropped.append(r)
    unused = R["signal"] * E - sig_w * len(sig)
    core = R["core"] * E + unused
    tb = R["theme"] * E
    longs = [r for r in chosen if r["adj"] > 0]
    shorts = [r for r in chosen if r["adj"] < 0]
    long_budget = tb * (0.5 if score <= -3 else 1.0)
    # 변동성 반비례: |점수| ÷ 내재변동성 (옵션 없으면 평균 IV 가정)
    ivs = [r["iv"] for r in chosen if r["iv"]]
    iv0 = sum(ivs) / len(ivs) if ivs else 40
    def rw(r):
        return abs(r["adj"]) / max(15, r["iv"] or iv0)
    sl, ss = sum(rw(r) for r in longs), sum(rw(r) for r in shorts)
    short_budget = min(tb / 3, tb * ss / max(1e-9, sl + ss)) if shorts else 0
    for r in longs:
        w[("theme", r["sym"])] = long_budget * rw(r) / sl
    if not longs:
        core += long_budget
    for r in shorts:
        w[("theme", r["sym"])] = -short_budget * rw(r) / ss
    w[("core", core_sym)] = core
    return w, {"chosen": chosen, "dropped": dropped, "core": core_sym}


def ostate_rows(sel, picks, pos, w, opts, tilts):
    """화면용: 후보·보유 종목마다 옵션 상태와 그게 판단에 어떻게 쓰였는지"""
    rows, seen = [], set()
    r_by = {r["sym"]: r for r in sel["chosen"] + sel["dropped"]}
    syms = [r["sym"] for r in sel["chosen"] + sel["dropped"]] + [c["sym"] for c in picks] + [k[1] for k in list(w) + list(pos)]
    for t in tilts:
        if t["src"] != "심층 뉴스":
            syms += t["syms"]
    for s_ in syms:
        if s_ in seen or s_ not in opts:
            continue
        seen.add(s_)
        o, r = opts[s_], r_by.get(s_)
        use = []
        if r:
            use.append(r["note"] or f"테마 점수 {r['base']:+g} × 옵션 {r.get('mult', 1):.2f}배 = {r['adj']:+.2f}")
        for t in tilts:
            if t["src"] != "심층 뉴스" and t.get("syms") == [s_]:
                hz = {"short": "짧은 기간", "mid": "중간 기간", "long": "긴 기간"}[t["horizon"]]
                use.append(f"{t['src']} {'보류' if not t['dir'] else '비중↑' if t['dir'] > 0 else '비중↓'} — 쏠림 {t['crowd']:+.2f}라 {hz} 기준" if t.get("crowd") is not None
                           else f"{t['src']} {'보류' if not t['dir'] else '비중↑' if t['dir'] > 0 else '비중↓'} — {hz} 기준")
        if ("signal", s_) in w:
            use.append("뉴스 신호 매매 중 (옵션 점수 −1 이하였으면 건너뜀)")
        elif any(c["sym"] == s_ for c in picks):
            use.append("뉴스 신호 후보 (상위 2개 밖)")
        held = any(k[1] == s_ for k in list(w) + list(pos))
        rows.append({"sym": s_, "in": held, **{k: o.get(k) for k in ("pts", "crowd", "crowd_label", "crowd_chg", "prev_asof", "pc", "pc_oi", "skew",
                                                                       "gex", "cw_dist", "pw_dist", "iv", "rv20", "iv_rv", "asof")}, "use": use})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="public_lab")
    a = ap.parse_args()
    prices = load(os.path.join(DATA, "prices.json"), {})
    cal = sorted(prices.get("QQQ", {}))
    if not cal:
        print("[portfolio] 가격 없음")
        return
    today = cal[-1]
    env = load(os.path.join(a.out, "env.json"), {})
    macro = load(os.path.join(a.out, "macro.json"), {})
    gpu = load(os.path.join(a.out, "gpu_rent.json"), {})
    sig = load(os.path.join(a.out, "data.json"), {})
    deep = load(os.path.join(ROOT, "lab", "deepnews", "index.json"), [])
    px = lambda s, d, k: (prices.get(s, {}).get(d) or [None, None])[k]  # noqa: E731

    path = os.path.join(DATA, "portfolio.json")
    st = load(path)
    if not st or st.get("rules", {}).get("version") != RULES["version"]:
        st = {"rules": RULES, "start": today, "cash": RULES["capital"], "pos": {}, "pending": None, "curve": [],
              "trades": [], "log": [], "last_rebal": None, "bench": {}, "interest": 0.0, "done_days": []}
    R = st["rules"]
    pos = {tuple(k.split("|")): v for k, v in st["pos"].items()}   # (슬리브, 종목) → 주식 수 (숏은 음수)

    # 1) 대기 주문 체결 — 결정일 다음 거래일 시가
    p = st.get("pending")
    if p and p["decided"] < today:
        exec_day = next(d for d in cal if d > p["decided"])
        val = st["cash"] + sum(sh * (px(s, exec_day, 0) or px(s, p["decided"], 1) or 0) for (_, s), sh in pos.items())
        tw = {tuple(k.split("|")): v for k, v in p["w"].items()}
        keys = set(tw) | set(pos)
        for k in sorted(keys):
            if not p["full"] and k[0] != "signal" and k in pos:
                continue                                  # 신호 몫만 조정하는 날: 코어·테마는 그대로
            o = px(k[1], exec_day, 0)
            if not o:
                continue
            want = tw.get(k, 0) * val / o
            have = pos.get(k, 0)
            if abs(want - have) * o < R["min_trade"] * val and not (want == 0 and have):
                continue
            d_sh = want - have
            st["cash"] -= d_sh * o + abs(d_sh) * o * R["cost"]
            pos[k] = want
            if k[0] == "signal" and not have and want:
                st.setdefault("sig_entry", {})[k[1]] = exec_day     # 신호 종목 진입일 (5거래일 보유 계산용)
            if abs(pos[k]) < 1e-9:
                pos.pop(k)
                if k[0] == "signal":
                    st.get("sig_entry", {}).pop(k[1], None)
            st["trades"].append({"date": exec_day, "sleeve": k[0], "sym": k[1], "side": "매수" if d_sh > 0 else "매도",
                                 "shares": round(d_sh, 4), "px": o, "amt": round(d_sh * o, 2), "why": p["why"].get("|".join(k), "")})
        if p["full"]:
            st["last_rebal"] = exec_day
        st["log"].append({"date": exec_day, "full": p["full"], "reason": p["reason"], "score": p["score"], "exposure": p["exposure"]})
        # 비교 기준: 첫 체결일 시가에 QQQ·SPY 전부
        for b in ("QQQ", "SPY"):
            if b not in st["bench"] and px(b, exec_day, 0):
                st["bench"][b] = {"date": exec_day, "px": px(b, exec_day, 0)}
        st["pending"] = None

    # 2) 평가 + 현금 이자 (하루 한 번)
    envrow = ((env or {}).get("summary") or [{}])[-1]
    if today not in st["done_days"] and st["log"]:
        r3m = envrow.get("ust3m") or 0
        if st["cash"] > 0 and r3m:
            it = st["cash"] * r3m / 100 / 252
            st["cash"] += it
            st["interest"] += it
        st["done_days"].append(today)
    mv = {k: sh * (px(k[1], today, 1) or 0) for k, sh in pos.items()}
    equity = st["cash"] + sum(mv.values())
    bq = {b: R["capital"] * (px(b, today, 1) / v["px"]) * (1 - R["cost"]) for b, v in st["bench"].items() if px(b, today, 1)}
    st["curve"] = [c for c in st["curve"] if c[0] != today] + ([[today, round(equity, 2), round(bq.get("QQQ", R["capital"]), 2),
                                                                  round(bq.get("SPY", R["capital"]), 2)]] if st["log"] else [])

    # 3) 다음 거래일 목표
    ec, envrow = env_score(env)
    mc = macro_score(macro, envrow)
    comps = ec + mc
    # 항목별 가중치: regime_opt.py 가 2017년부터 되돌려 시험해 고른 값 (없으면 모두 1)
    rw = load(os.path.join(a.out, "regime_weights.json"), {}) or {}
    wts = rw.get("weights") or {}
    for c in comps:
        c["w"] = wts.get(c["key"], 1.0)
    score = round(sum(c["pts"] * c["w"] for c in comps), 2)
    E = exposure(score)
    universe = set(prices)
    raw_opts = (env or {}).get("latest", {}).get("options") or {}
    tilts, tscore = themes(today, deep, gpu, macro, universe, raw_opts)
    # 며칠 전(약 5거래일) 옵션 스냅샷 — 쏠림 변화 계산용
    ddir = os.path.join(DATA, "env", "daily")
    snaps = sorted(f for f in (os.listdir(ddir) if os.path.isdir(ddir) else []) if f.endswith(".json") and f[:10] < (raw_opts.get("SPX") or {}).get("asof", today))
    prev_opts = (load(os.path.join(ddir, snaps[-5 if len(snaps) >= 5 else 0]), {}) or {}).get("options", {}) if snaps else {}
    closes = {k: [v[1] for _, v in sorted(prices.get(k, {}).items())] for k in raw_opts}
    opts = {k: v for k, v in ((k, opt_view(o, prev_opts.get(k), closes.get(k))) for k, o in raw_opts.items()) if v}
    days3 = cal[-3:]
    seen_sig = set()
    picks = []
    for c in sorted((sig or {}).get("picks", []), key=lambda c: c["date"], reverse=True):
        if c["date"] in days3 and c["sym"] not in seen_sig and c["sym"] in universe:
            seen_sig.add(c["sym"])
            picks.append(c)
    picks = [c for c in picks if c["sym"] not in st.get("sig_entry", {})]   # 이미 한 번 들어갔던 종목은 다시 안 탐
    # 이미 들고 있는 신호 종목은 진입일 포함 5거래일째 종가까지 유지 (그다음 시가에 정리)
    keep = []
    for (sl, s), sh in pos.items():
        d0 = st.get("sig_entry", {}).get(s)
        if sl == "signal" and d0 in cal and cal.index(today) - cal.index(d0) + 1 < R["signal_hold"]:
            keep.append({"sym": s, "direction": "롱" if sh > 0 else "숏", "date": d0, "held": True})
    # 심층 뉴스 시각과 반대 방향인 신호는 건너뜀 (며칠짜리 뉴스 신호보다 깊게 따져 본 시각을 우선)
    deep_dir = {}
    for t in tilts:
        if t["src"] == "심층 뉴스":
            for s_ in t["syms"]:
                deep_dir[s_] = deep_dir.get(s_, 0) + t["dir"]
    skipped = []
    ok = []
    for c in picks:
        d = 1 if c["direction"] == "롱" else -1
        ov = opts.get(c["sym"])
        if deep_dir.get(c["sym"], 0) * d < 0:
            skipped.append({"sym": c["sym"], "direction": c["direction"], "date": c["date"],
                            "why": "심층 뉴스 시각과 반대 — " + " · ".join(t["title"][:30] for t in tilts if t["src"] == "심층 뉴스" and c["sym"] in t["syms"])})
        elif ov and ov["pts"] * d <= -1:
            skipped.append({"sym": c["sym"], "direction": c["direction"], "date": c["date"],
                            "why": "옵션 포지션이 반대 — " + " · ".join(ov["why"])})
        else:
            ok.append(c)
    picks = keep + [c for c in ok if c["sym"] not in {k["sym"] for k in keep}]
    w, sel = targets(R, E, score, tscore, picks, opts)

    bucket = E
    prev = st["log"][-1] if st["log"] else None
    prev_theme = sorted(k[1] for k in pos if k[0] == "theme")
    new_theme = sorted(k[1] for k in w if k[0] == "theme")
    since = cal.index(today) - cal.index(st["last_rebal"]) + 1 if st.get("last_rebal") in cal else 99
    reasons = []
    if not st["log"]:
        reasons.append("첫 구성")
    if prev and abs(prev["exposure"] - bucket) > 1e-9:
        reasons.append(f"위험 예산 {prev['exposure']*100:.0f}% → {bucket*100:.0f}%")
    if prev and prev_theme != new_theme:
        reasons.append("테마 구성 변경")
    if since >= R["rebalance_days"]:
        reasons.append(f"정기 조정({R['rebalance_days']}거래일)")
    full = bool(reasons)
    why = {}
    for k in w:
        if k[0] == "core":
            why["|".join(k)] = "코어(지수)"
        elif k[0] == "signal":
            c = next((c for c in picks if c["sym"] == k[1]), {})
            why["|".join(k)] = f"뉴스 신호 {c.get('direction','')} ({c.get('date','')})"
        else:
            why["|".join(k)] = " · ".join(t["title"][:40] for t in tilts if k[1] in t["syms"] and t["dir"])
            ov = opts.get(k[1])
            if ov and ov["why"]:
                why["|".join(k)] += " / 옵션: " + ", ".join(ov["why"][:2])
    st["pending"] = {"decided": today, "w": {"|".join(k): round(v, 5) for k, v in w.items()}, "full": full,
                     "reason": " · ".join(reasons) or "신호 몫만 조정", "score": score, "exposure": bucket, "why": why}
    st["pos"] = {"|".join(k): v for k, v in pos.items()}
    os.makedirs(DATA, exist_ok=True)
    json.dump(st, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=0)

    hold = []
    for k, sh in pos.items():
        c = px(k[1], today, 1) or 0
        buys = [t for t in st["trades"] if t["sym"] == k[1] and t["sleeve"] == k[0]]
        cost = sum(t["amt"] for t in buys)
        hold.append({"opt": opts.get(k[1]), "sleeve": k[0], "sym": k[1], "shares": round(sh, 4), "px": c, "value": round(sh * c, 2),
                     "weight": round(sh * c / equity * 100, 2) if equity else 0,
                     "pnl": round(sh * c - cost, 2), "why": why.get("|".join(k)) or (buys[-1]["why"] if buys else "")})
    hold.sort(key=lambda h: (["core", "theme", "signal"].index(h["sleeve"]), -abs(h["value"])))
    first = st["curve"][0] if st["curve"] else None
    out = {
        "asof": today, "start": st["start"], "rules": R, "status": "진행 중",
        "equity": round(equity, 2), "cash": round(st["cash"], 2), "interest": round(st["interest"], 2),
        "ret": round((equity / R["capital"] - 1) * 100, 2),
        "bench": {b: round((v / R["capital"] - 1) * 100, 2) for b, v in bq.items()},
        "curve": st["curve"], "holdings": hold,
        "regime": {"score": score, "exposure": E, "components": comps, "env_date": envrow.get("date"),
                   "weights_src": {k: rw.get(k) for k in ("asof", "adopted", "train", "test", "walk_forward", "stats", "optimized", "names", "note")} if rw else None},
        "tilts": tilts, "next": {"decided": today, "full": full, "reason": st["pending"]["reason"],
                                 "w": [{"sleeve": k[0], "sym": k[1], "w": round(v * 100, 2), "why": why.get("|".join(k), ""), "opt": opts.get(k[1])}
                                       for k, v in sorted(w.items(), key=lambda kv: (["core", "theme", "signal"].index(kv[0][0]), -abs(kv[1])))]},
        "ostate": ostate_rows(sel, picks, pos, w, opts, tilts),
        "log": st["log"][-30:], "trades": st["trades"][-60:], "started": bool(first), "skipped_signals": skipped,
        "selection": {"core": sel["core"], "max_names": R["max_names"],
                      "chosen": [r | {"o": opts.get(r["sym"])} for r in sel["chosen"]], "dropped": [r | {"o": opts.get(r["sym"])} for r in sel["dropped"]]},
    }
    json.dump(out, open(os.path.join(a.out, "portfolio.json"), "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f"[portfolio] {today} 점수 {score:+.2f} → 주식 {E*100:.0f}% · 테마 {len(tscore)}종목 · 신호 {len(picks[:R['signal_max']])} · "
          f"평가액 ${equity:,.0f} ({out['ret']:+.2f}%) · 다음: {st['pending']['reason']}")


if __name__ == "__main__":
    main()
