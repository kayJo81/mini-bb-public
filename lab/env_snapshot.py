"""
미니 블벅 랩 — 시장 환경 스냅샷 (매 랩 실행 = 화~토 07:00 KST, 미국 장 마감 뒤)

  python lab/env_snapshot.py            → labdata/env/daily/YYYY-MM-DD.json + labdata/env/summary.json(날짜별 요약 한 줄)

과거치를 나중에 받을 수 없는 '그날의 상태'를 매일 저장해 두는 것이 목적 (국면별 전략 검증용 표본).
  · 옵션 수급 (CBOE 지연 시세 — 옵션 전체 체인): 종목·지수별
      풋/콜 거래량·미결제약정 비율, IV30, 30일 만기 ATM IV, 25델타 스큐(풋 IV − 콜 IV),
      감마 익스포저 추정(GEX, 1% 움직임당 달러 — 딜러가 콜 매도·풋 매수 쪽이라는 통상 가정), 콜월·풋월(30일 안 최대 미결제약정 행사가)
  · 시장 전체 풋/콜 비율 (CBOE 일일 통계: 전체·지수·ETP·주식·VIX)
  · 변동성·금리·달러·원자재 (야후 일봉): VIX9D·VIX·VIX3M·VIX6M·VVIX·SKEW·MOVE, 미 국채 3개월·5·10·30년,
      달러지수, WTI·금·구리, 비트코인, HYG·LQD(신용)
  · 공매도 거래 비중 (FINRA 일별 공매도 거래량): 랩 종목 + 주요 ETF
  · 예측시장 (Polymarket): 경제·연준·정치·지정학 시장 중 거래 많은 것들의 확률
하나가 실패해도 나머지는 저장한다 (빠진 항목은 errors 에 기록).
"""
import json
import math
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA  # noqa: E402

ET = ZoneInfo("America/New_York")
HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
OUT = os.path.join(DATA, "env")

INDEX_OPT = ["_SPX", "SPY", "QQQ", "IWM", "SMH", "TLT", "HYG", "GLD"]
YAHOO = {"VIX9D": "^VIX9D", "VIX": "^VIX", "VIX3M": "^VIX3M", "VIX6M": "^VIX6M", "VVIX": "^VVIX", "SKEW": "^SKEW",
         "MOVE": "^MOVE", "UST3M": "^IRX", "UST5Y": "^FVX", "UST10Y": "^TNX", "UST30Y": "^TYX", "DXY": "DX-Y.NYB",
         "WTI": "CL=F", "GOLD": "GC=F", "COPPER": "HG=F", "BTC": "BTC-USD", "HYG": "HYG", "LQD": "LQD",
         "SPX": "^GSPC", "NDX": "^NDX", "RUT": "^RUT"}
SHORT_EXTRA = ["SPY", "QQQ", "IWM", "SMH", "SOXX", "XLE", "XLF", "TLT", "HYG", "ARKK"]
PM_TAGS = ["economy", "fed", "fed-rates", "inflation", "geopolitics", "politics", "trade-war", "tariffs", "crypto"]
PM_SKIP = re.compile(r"tweet|post .* tweets|mention|say .* during|nba|nfl|mlb|nhl|ufc|soccer|dota|esports|"
                     r"temperature|weather|box office|album|eurovision|oscars", re.I)


def get(url, **kw):
    for i in range(3):
        try:
            r = requests.get(url, headers=HDR, timeout=kw.pop("timeout", 30), **kw)
            r.raise_for_status()
            return r
        except Exception:
            if i == 2:
                raise
            time.sleep(2 * (i + 1))


# ── 옵션 수급 ──
OPT = re.compile(r"(\d{6})([CP])(\d{8})$")


def options(sym):
    d = get(f"https://cdn-api.cboe.com/api/global/delayed_quotes/options/{sym}.json", timeout=90).json()["data"]
    S = d.get("current_price") or d.get("close")
    asof = (d.get("last_trade_time") or "")[:10]
    today = datetime.strptime(asof, "%Y-%m-%d").date() if asof else datetime.now(ET).date()
    ltt = d.get("last_trade_time") or ""
    closed = ltt[11:16] >= "16:00" or datetime.now(ET).date() > today or datetime.now(ET).hour >= 16
    cv = pv = coi = poi = 0.0
    gex = 0.0
    near = {}   # 만기 → [(행사가, C/P, iv, delta, oi)]
    for o in d["options"]:
        m = OPT.search(o["option"])
        if not m:
            continue
        exp = datetime.strptime(m.group(1), "%y%m%d").date()
        if exp < today:
            continue
        cp, k = m.group(2), int(m.group(3)) / 1000
        vol, oi = o.get("volume") or 0, o.get("open_interest") or 0
        if cp == "C":   # 거래량·미결제 비율에는 당일 만기(0DTE)도 포함
            cv += vol; coi += oi
        else:
            pv += vol; poi += oi
        if exp == today and closed:
            continue   # 장 마감 뒤: 그날 만기 옵션은 끝났으므로 감마·콜월/풋월 계산에서 뺌 (장중엔 포함)
        g = o.get("gamma") or 0
        gex += (1 if cp == "C" else -1) * g * oi * 100 * S * S * 0.01
        dte = (exp - today).days
        if 0 <= dte <= 60:
            near.setdefault(exp, []).append((k, cp, o.get("iv") or 0, o.get("delta") or 0, oi))
    res = {"price": S, "asof": asof, "iv30": d.get("iv30"),
           "call_vol": int(cv), "put_vol": int(pv), "call_oi": int(coi), "put_oi": int(poi),
           "pc_vol": round(pv / cv, 3) if cv else None, "pc_oi": round(poi / coi, 3) if coi else None,
           "gex_musd": round(gex / 1e6, 1)}
    # 30일에 가장 가까운 만기 (7일 이상)
    exps = [e for e in near if (e - today).days >= 7]
    if exps:
        e30 = min(exps, key=lambda e: abs((e - today).days - 30))
        rows = near[e30]
        res["exp30"] = e30.isoformat()
        atm = min({r[0] for r in rows}, key=lambda k: abs(k - S))
        ivs = [r[2] for r in rows if r[0] == atm and r[2] > 0]
        res["atm_iv30"] = round(sum(ivs) / len(ivs) * 100, 2) if ivs else None

        def iv_at(cp, target):
            pts = sorted((r[3], r[2]) for r in rows if r[1] == cp and r[2] > 0 and r[3])
            for (d1, v1), (d2, v2) in zip(pts, pts[1:]):
                if min(d1, d2) <= target <= max(d1, d2) and d1 != d2:
                    return v1 + (v2 - v1) * (target - d1) / (d2 - d1)
            return None
        p25, c25 = iv_at("P", -0.25), iv_at("C", 0.25)
        res["skew25"] = round((p25 - c25) * 100, 2) if p25 and c25 else None
    # 콜월·풋월: 30일 안 만기들의 행사가별 미결제약정 합계 최대
    cw, pw = {}, {}
    for e, rows in near.items():
        if (e - today).days <= 30:
            for k, cp, _, _, oi in rows:
                (cw if cp == "C" else pw)[k] = (cw if cp == "C" else pw).get(k, 0) + oi
    if cw:
        res["call_wall"] = max(cw, key=cw.get)
    if pw:
        res["put_wall"] = max(pw, key=pw.get)
    return res


def cboe_ratios():
    h = get("https://www.cboe.com/markets/us/options/market-statistics/daily").text.replace('\\"', '"')
    out = {}
    for name, val in re.findall(r'"name":"([A-Z /()]*PUT/CALL RATIO)","value":"([\d.]+)"', h):
        key = {"TOTAL PUT/CALL RATIO": "total", "INDEX PUT/CALL RATIO": "index", "EQUITY PUT/CALL RATIO": "equity",
               "EXCHANGE TRADED PRODUCTS PUT/CALL RATIO": "etp"}.get(name, "vix" if "VIX" in name else name.lower())
        if key in ("total", "index", "equity", "etp", "vix"):
            out.setdefault(key, float(val))
    m = re.search(r'"date":"(\d{4}-\d{2}-\d{2})', h)
    if m:
        out["date"] = m.group(1)
    return out


# ── 변동성·금리·원자재 ──
def yahoo(sym):
    r = get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=10d&interval=1d").json()["chart"]["result"][0]
    tz = ZoneInfo(r["meta"].get("exchangeTimezoneName") or "America/New_York")
    pts = [(datetime.fromtimestamp(t, tz).date().isoformat(), c)
           for t, c in zip(r["timestamp"], r["indicators"]["quote"][0]["close"]) if c is not None]
    return pts[-1] if pts else None


# ── 공매도 ──
def short_volume(day, syms):
    for back in range(0, 5):   # 그날 파일이 아직 없으면 직전 거래일
        d = day - timedelta(days=back)
        try:
            t = get(f"https://cdn.finra.org/equity/regsho/daily/CNMSshvol{d:%Y%m%d}.txt").text
        except Exception:
            continue
        out = {}
        for line in t.splitlines()[1:]:
            p = line.split("|")
            if len(p) >= 5 and p[1] in syms:
                sv, tv = float(p[2]), float(p[4])
                out[p[1]] = {"short_vol": int(sv), "total_vol": int(tv), "short_ratio": round(sv / tv, 3) if tv else None}
        return d.isoformat(), out
    return None, {}


# ── 예측시장 ──
def polymarket():
    seen, out = set(), []
    for tag in PM_TAGS:
        try:
            ev = get(f"https://gamma-api.polymarket.com/events?limit=40&active=true&closed=false&order=volume24hr"
                     f"&ascending=false&tag_slug={tag}").json()
        except Exception:
            continue
        for e in ev:
            for m in e.get("markets") or []:
                q = m.get("question") or ""
                if m.get("id") in seen or PM_SKIP.search(q) or PM_SKIP.search(e.get("title") or ""):
                    continue
                try:
                    yes = float(json.loads(m.get("outcomePrices") or "[]")[0])
                except Exception:
                    continue
                vol = float(m.get("volume") or 0)
                if not (0.02 <= yes <= 0.98) or vol < 100000:
                    continue
                seen.add(m.get("id"))
                out.append({"id": m.get("id"), "event": e.get("title"), "q": q, "yes": round(yes, 4),
                            "vol": round(vol), "vol24": round(float(m.get("volume24hr") or 0)), "end": (m.get("endDate") or "")[:10],
                            "tag": tag})
    out.sort(key=lambda x: -x["vol24"])
    return out[:60]


def g(d, *keys):
    """중첩 dict 에서 값 꺼내기 (없으면 None)"""
    for k in keys:
        d = d.get(k) if isinstance(d, dict) else None
    return d


def main():
    from news_scanner import TOPICS
    lab_syms = list(dict.fromkeys(s for t in TOPICS for s in t["tickers"]))
    snap, errors = {"collected_at": datetime.now(ET).isoformat(timespec="seconds")}, {}

    with ThreadPoolExecutor(6) as pool:
        opt_syms = INDEX_OPT + lab_syms
        fut = {s: pool.submit(options, s) for s in opt_syms}
        yf = {k: pool.submit(yahoo, v) for k, v in YAHOO.items()}
        fr = pool.submit(cboe_ratios)
        pm = pool.submit(polymarket)
        snap["options"] = {}
        for s, f in fut.items():
            try:
                snap["options"][s.lstrip("_")] = f.result()
            except Exception as ex:
                errors[f"options:{s}"] = str(ex)[:120]
        snap["macro"] = {}
        for k, f in yf.items():
            try:
                v = f.result()
                if v:
                    snap["macro"][k] = {"date": v[0], "close": round(v[1], 4)}
            except Exception as ex:
                errors[f"yahoo:{k}"] = str(ex)[:120]
        for k, f in (("cboe_pc", fr), ("polymarket", pm)):
            try:
                snap[k] = f.result()
            except Exception as ex:
                errors[k] = str(ex)[:120]

    # 기준일 = 미국 마지막 거래일 (SPX 옵션 시세 시각, 없으면 S&P 일봉)
    day = (snap["options"].get("SPX") or {}).get("asof") or (snap["macro"].get("SPX") or {}).get("date") or datetime.now(ET).date().isoformat()
    snap["date"] = day
    try:
        sd, sv = short_volume(date.fromisoformat(day), set(lab_syms + SHORT_EXTRA))
        snap["short"] = {"date": sd, "symbols": sv}
    except Exception as ex:
        errors["short"] = str(ex)[:120]
    snap["errors"] = errors

    os.makedirs(os.path.join(OUT, "daily"), exist_ok=True)
    json.dump(snap, open(os.path.join(OUT, "daily", f"{day}.json"), "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))

    # 날짜별 요약 한 줄 (화면·검증에서 바로 쓰는 핵심 값)
    o, mc, pc = snap["options"], snap["macro"], snap.get("cboe_pc") or {}
    row = {"date": day,
           "vix": g(mc, "VIX", "close"), "vix9d": g(mc, "VIX9D", "close"), "vix3m": g(mc, "VIX3M", "close"),
           "vvix": g(mc, "VVIX", "close"), "skew_idx": g(mc, "SKEW", "close"), "move": g(mc, "MOVE", "close"),
           "ust10y": g(mc, "UST10Y", "close"), "ust3m": g(mc, "UST3M", "close"), "dxy": g(mc, "DXY", "close"),
           "wti": g(mc, "WTI", "close"), "gold": g(mc, "GOLD", "close"), "btc": g(mc, "BTC", "close"),
           "pc_total": pc.get("total"), "pc_equity": pc.get("equity"), "pc_index": pc.get("index"),
           "spx": g(o, "SPX", "price"), "spx_gex": g(o, "SPX", "gex_musd"), "spx_skew25": g(o, "SPX", "skew25"),
           "spx_atm_iv30": g(o, "SPX", "atm_iv30"), "spx_call_wall": g(o, "SPX", "call_wall"), "spx_put_wall": g(o, "SPX", "put_wall"),
           "qqq_pc_vol": g(o, "QQQ", "pc_vol"), "qqq_gex": g(o, "QQQ", "gex_musd"),
           "spy_short_ratio": g(snap, "short", "symbols", "SPY", "short_ratio"),
           "qqq_short_ratio": g(snap, "short", "symbols", "QQQ", "short_ratio"),
           "n_options": len(o), "n_polymarket": len(snap.get("polymarket") or []), "n_errors": len(errors)}
    if row["vix"] and row["vix3m"]:
        row["vix_term"] = round(row["vix"] / row["vix3m"], 3)   # 1 초과 = 백워데이션(공포)
    if row["ust10y"] is not None and row["ust3m"] is not None:
        row["curve_10y3m"] = round(row["ust10y"] - row["ust3m"], 3)
    path = os.path.join(OUT, "summary.json")
    try:
        summ = json.load(open(path, encoding="utf-8"))
    except Exception:
        summ = []
    summ = [r for r in summ if r["date"] != day] + [row]
    summ.sort(key=lambda r: r["date"])
    json.dump(summ, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    # 랩 화면용 (public_lab/env.json): 날짜별 요약 + 최신 스냅샷
    out_dir = os.environ.get("LAB_OUT")
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        json.dump({"summary": summ[-250:], "latest": snap}, open(os.path.join(out_dir, "env.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, separators=(",", ":"))
    print(f"[env] {day} · 옵션 {len(o)}종목 · 거시 {len(mc)} · 풋/콜 {pc} · 공매도 {len((snap.get('short') or {}).get('symbols', {}))}종목 · "
          f"예측시장 {row['n_polymarket']} · 실패 {len(errors)}" + (f" {list(errors)[:5]}" if errors else ""))
    return snap, summ


if __name__ == "__main__":
    main()
