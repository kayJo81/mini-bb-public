"""
US Market Dashboard — Flask backend
Sources: Alpaca (stocks), Finnhub (indices), Yahoo Finance v7 direct (futures/FX/bonds)
"""
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from flask import Flask, jsonify, request, send_from_directory

from news_scanner import TOPICS, Scanner

app = Flask(__name__, template_folder="templates")

# ── API Keys (환경변수 또는 .env 로 설정 — 코드에 직접 적지 말 것) ──────────────────────────────────────────────────────────
FINNHUB_KEY   = os.environ.get("FINNHUB_KEY",   "")
ALPACA_KEY_ID = os.environ.get("ALPACA_KEY_ID", "")
ALPACA_SECRET = os.environ.get("ALPACA_SECRET", "")

FINNHUB_BASE = "https://finnhub.io/api/v1"
FINNHUB_URL  = f"{FINNHUB_BASE}/news?category=general&token={FINNHUB_KEY}"
JUICE_URL    = "https://www.financialjuice.com/feed.ashx?xy=1"
WSCN_URL     = "https://api-one.wallstcn.com/apiv1/content/lives?channel=global-channel&limit=30"
ALPACA_BASE  = "https://data.alpaca.markets/v2"
NYFED_BASE   = "https://markets.newyorkfed.org/read"
FRED_BASE    = "https://fred.stlouisfed.org/graph/fredgraph.csv"
YF_V7_URL    = "https://query1.finance.yahoo.com/v7/finance/quote"
YF_CRUMB_URL = "https://query2.finance.yahoo.com/v1/test/getcrumb"

ALPACA_HDR = {
    "APCA-API-KEY-ID":     ALPACA_KEY_ID,
    "APCA-API-SECRET-KEY": ALPACA_SECRET,
}
HTTP_HDR = {"User-Agent": "Mozilla/5.0 (compatible; MarketDashboard/1.0)"}
YF_HDR = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://finance.yahoo.com/",
}

ET_TZ = ZoneInfo("America/New_York")

# ── Universe ──────────────────────────────────────────────────────────
ALL_STOCKS = list(dict.fromkeys([
    "NVDA","AMD","MU","AVGO","INTC","TSM","AMAT","LRCX","KLAC","ARM","MRVL","WDC",
    "CRWV","NBIS","IREN","GEV","BE","CEG","SMR","ANET","COHR","LITE",
    "MSFT","GOOGL","META","ORCL","PLTR","NOW","CRM",
    "AAPL","AMZN",
    "DELL","HPE","SMCI","VRT",
    "APLD","WULF","LUMN","FFIV",
    "TSLA","RKLB","ASTS","LUNR","RDW","PL","IRDM",
]))
INDEX_SYMS  = ["^DJI","^GSPC","^IXIC","^RUT","^SOX"]
ALL_SYMBOLS = INDEX_SYMS + ALL_STOCKS

# ── Futures / FX / Bonds ──────────────────────────────────────────────
FUTURES_SYMS = ["ES=F","NQ=F","YM=F","RTY=F","CL=F","GC=F"]
FUTURES_META = {
    "ES=F":  {"name":"S&P 선물",    "dec":0},
    "NQ=F":  {"name":"나스닥 선물", "dec":0},
    "YM=F":  {"name":"다우 선물",   "dec":0},
    "RTY=F": {"name":"러셀 선물",   "dec":0},
    "CL=F":  {"name":"WTI 원유",   "dec":2},
    "GC=F":  {"name":"금",          "dec":2},
}
FX_SYMS = ["KRW=X","JPY=X","CNY=X","EURUSD=X","DX-Y.NYB"]
FX_META = {
    "KRW=X":    {"name":"원/달러",    "dec":0},
    "JPY=X":    {"name":"달러/엔",    "dec":2},
    "CNY=X":    {"name":"달러/위안",  "dec":4},
    "EURUSD=X": {"name":"유로/달러",  "dec":4},
    "DX-Y.NYB": {"name":"달러인덱스", "dec":2},
}
BOND_SYMS = ["^IRX","^TNX","^TYX"]
BOND_META = {
    "^IRX": {"key":"2Y",  "name":"미국채 2년",  "dec":3},
    "^TNX": {"key":"10Y", "name":"미국채 10년", "dec":3},
    "^TYX": {"key":"30Y", "name":"미국채 30년", "dec":3},
}

# ── Sector ETFs ───────────────────────────────────────────────────────
SECTOR_ETFS = {
    "반도체":    "SOXX",
    "빅테크":    "QQQ",
    "소프트웨어": "IGV",
    "에너지":    "XLE",
    "헬스케어":  "XLV",
    "금융":      "XLF",
    "산업재":    "XLI",
    "소비재":    "XLY",
}

# ── Macro ─────────────────────────────────────────────────────────────
MACRO_INFLATION = {
    "CPI":     ("CPIAUCSL","CPI 헤드라인","mom_pct","https://www.bls.gov/cpi/"),
    "CoreCPI": ("CPILFESL","CPI 근원",    "mom_pct","https://www.bls.gov/cpi/"),
    "PPI":     ("PPIACO",  "PPI 헤드라인","mom_pct","https://www.bls.gov/ppi/"),
    "PCE":     ("PCEPI",   "PCE 헤드라인","mom_pct","https://www.bea.gov/data/personal-consumption-expenditures-price-index"),
    "CorePCE": ("PCEPILFE","PCE 근원",    "mom_pct","https://www.bea.gov/data/personal-consumption-expenditures-price-index"),
}
MACRO_EMPLOYMENT = {
    "UNRATE": ("UNRATE","실업률",      "level",    "https://www.bls.gov/cps/"),
    "JTSLDR": ("JTSLDR","해고율",      "level",    "https://www.bls.gov/jlt/"),
    "NFP":    ("PAYEMS","NFP 신규고용","mom_diff",  "https://www.bls.gov/ces/"),
}
MACRO_PMI = {
    "ISM_Mfg": ("NAPM",  "ISM 제조업",       "level","https://www.ismworld.org/"),
    "ISM_Svc": ("NMFBAI","ISM 서비스업",     "level","https://www.ismworld.org/"),
    "SPG_Mfg": ("ISPMAN","S&P Global 제조업","level","https://www.ismworld.org/"),
}

BULLISH_KW = {"surge","beat","raise","record","rally","gain","upgrade","profit",
              "soar","jump","strong","outperform"}
BEARISH_KW = {"miss","cut","decline","risk","drop","fall","warn","downgrade",
              "loss","crash","tariff","sanction","weak"}

# ── Thread-safe caches ────────────────────────────────────────────────
_quotes       = {};  _ql  = threading.Lock()
_futures_data = {"futures":{},"fx":{},"bonds":{}};  _fl  = threading.Lock()
_news         = [];  _nl  = threading.Lock()
_liq          = {};  _ll  = threading.Lock()
_sector_perf  = {};  _spl = threading.Lock()
_macro        = {"inflation":{},"employment":{},"pmi":{}};  _ml  = threading.Lock()

# ── Yahoo Finance session (crumb auth) ────────────────────────────────
_yf_session = None
_yf_crumb   = None
_yf_sl      = threading.Lock()


def _refresh_yf_crumb() -> bool:
    global _yf_session, _yf_crumb
    try:
        s = requests.Session()
        s.get("https://finance.yahoo.com", headers=YF_HDR, timeout=12)
        r = s.get(YF_CRUMB_URL, headers=YF_HDR, timeout=10)
        crumb = r.text.strip()
        if crumb and len(crumb) > 2:
            with _yf_sl:
                _yf_session = s
                _yf_crumb   = crumb
            print(f"[yf] crumb ok")
            return True
    except Exception as e:
        print(f"[yf_crumb] {e}")
    return False


def _yf_v7_batch(symbols: list) -> dict:
    """Batch quote via Yahoo Finance v7 (works for futures/FX/bonds/indices)."""
    if not symbols:
        return {}
    with _yf_sl:
        s, crumb = _yf_session, _yf_crumb
    if not s or not crumb:
        if not _refresh_yf_crumb():
            return {}
        with _yf_sl:
            s, crumb = _yf_session, _yf_crumb
    try:
        r = s.get(
            YF_V7_URL,
            params={
                "symbols":     ",".join(symbols),
                "crumb":       crumb,
                "lang":        "en-US",
                "region":      "US",
                "corsDomain":  "finance.yahoo.com",
            },
            headers=YF_HDR,
            timeout=20,
        )
        if r.status_code in (401, 403):
            _refresh_yf_crumb()
            return {}
        r.raise_for_status()
        results = r.json().get("quoteResponse", {}).get("result", [])
        return {q["symbol"]: q for q in results}
    except Exception as e:
        print(f"[yf_v7] {e}")
        return {}


# ── Alpaca snapshots ──────────────────────────────────────────────────
def _alpaca_snapshots(symbols: list) -> dict:
    """Latest trade + daily bars for US stocks via Alpaca."""
    result = {}
    for i in range(0, len(symbols), 50):
        batch = symbols[i:i+50]
        for feed in ("iex", "sip"):
            try:
                r = requests.get(
                    f"{ALPACA_BASE}/stocks/snapshots",
                    params={"symbols": ",".join(batch), "feed": feed},
                    headers=ALPACA_HDR, timeout=15,
                )
                data = r.json()
                if not isinstance(data, dict):
                    continue
                for sym, snap in data.items():
                    if not isinstance(snap, dict):
                        continue
                    try:
                        latest_p = snap["latestTrade"]["p"]
                        prev_c   = (snap.get("prevDailyBar") or {}).get("c")
                        daily_c  = (snap.get("dailyBar")     or {}).get("c")
                        result[sym] = {
                            "latest": latest_p,
                            "prev":   prev_c,
                            "daily":  daily_c,
                        }
                    except (KeyError, TypeError):
                        pass
                if result:
                    break
            except Exception as e:
                print(f"[alpaca_snap feed={feed}] {e}")
    return result


def _alpaca_bars_batch(symbols: list, days: int = 35) -> dict:
    """Return daily closing prices for the past `days` calendar days."""
    result = {}
    start = (datetime.now(ET_TZ) - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00Z")
    for feed in ("sip", "iex"):
        try:
            r = requests.get(
                f"{ALPACA_BASE}/stocks/bars",
                params={
                    "symbols":   ",".join(symbols),
                    "timeframe": "1Day",
                    "start":     start,
                    "feed":      feed,
                    "limit":     500,
                },
                headers=ALPACA_HDR, timeout=20,
            )
            data = r.json().get("bars", {})
            for sym, bars in data.items():
                result[sym] = [b["c"] for b in bars]
            break
        except Exception as e:
            print(f"[alpaca_bars feed={feed}] {e}")
    return result


def _finnhub_quote(symbol: str) -> dict:
    """Single-symbol quote from Finnhub (supports ^GSPC, ^DJI, etc.)."""
    try:
        r = requests.get(
            f"{FINNHUB_BASE}/quote",
            params={"symbol": symbol, "token": FINNHUB_KEY},
            timeout=10,
        )
        d = r.json()
        if d.get("c"):
            return {"c": d["c"], "dp": round(d.get("dp", 0.0), 2), "pc": d.get("pc")}
    except Exception as e:
        print(f"[finnhub/{symbol}] {e}")
    return {}


# ── Quote builder ─────────────────────────────────────────────────────
def _pct(a, b):
    return round((a - b) / b * 100, 2) if a and b else None


def _r(v, d=2):
    return round(v, d) if v is not None else None


def _build_quote(sym, ms,
                 reg_p=None, reg_chg=None,
                 pre_p=None, pre_chg=None,
                 post_p=None, post_chg=None,
                 ovn_p=None, ovn_chg=None) -> dict:
    return {
        "symbol": sym, "marketState": ms,
        "regularMarketPrice":         _r(reg_p),
        "regularMarketChangePercent": _r(reg_chg),
        "preMarketPrice":             _r(pre_p),
        "preMarketChangePercent":     _r(pre_chg),
        "postMarketPrice":            _r(post_p),
        "postMarketChangePercent":    _r(post_chg),
        "overnightPrice":             _r(ovn_p),
        "overnightChangePercent":     _r(ovn_chg),
    }


def _stock_quote_from_snap(sym, snap, ms) -> dict:
    """Convert Alpaca snapshot → quote dict for given market state."""
    if not snap:
        return {"symbol": sym, "error": "no data", "marketState": ms}
    lp = snap.get("latest")
    pc = snap.get("prev")
    dc = snap.get("daily")

    if ms == "REGULAR":
        return _build_quote(sym, ms, reg_p=lp, reg_chg=_pct(lp, pc))

    if ms == "PRE":
        # reg shows prev close; pre badge shows latest trade vs prev close
        return _build_quote(sym, ms, reg_p=pc, reg_chg=0.0,
                            pre_p=lp, pre_chg=_pct(lp, pc))

    if ms in ("POST", "CLOSED", "WEEKEND"):
        # reg shows today's close; post badge shows latest trade vs today's close
        ref = dc or pc
        return _build_quote(sym, ms, reg_p=ref, reg_chg=_pct(ref, pc),
                            post_p=lp, post_chg=_pct(lp, ref))

    # OVERNIGHT
    return _build_quote(sym, ms, reg_p=pc, reg_chg=0.0,
                        ovn_p=lp, ovn_chg=_pct(lp, pc))


# ─────────────────────────────────────────────────────────────────────
# Market state
# ─────────────────────────────────────────────────────────────────────
def market_state() -> str:
    now  = datetime.now(ET_TZ)
    wday = now.weekday()
    mins = now.hour * 60 + now.minute
    if wday >= 5:   return "WEEKEND"
    if mins < 240:  return "OVERNIGHT"
    if mins < 570:  return "PRE"
    if mins < 960:  return "REGULAR"
    if mins < 1200: return "POST"
    return "OVERNIGHT"


def classify(text: str) -> str:
    words = set(text.lower().split())
    if words & BULLISH_KW: return "bullish"
    if words & BEARISH_KW: return "bearish"
    return "neutral"


# ─────────────────────────────────────────────────────────────────────
# Background: quotes (Alpaca stocks + Finnhub indices)
# ─────────────────────────────────────────────────────────────────────
def _quote_loop():
    while True:
        ms     = market_state()
        result = {}

        # US stocks via Alpaca snapshots (1-2 batch calls)
        snap = _alpaca_snapshots(ALL_STOCKS)
        for sym in ALL_STOCKS:
            result[sym] = _stock_quote_from_snap(sym, snap.get(sym), ms)

        # Indices via Finnhub (5 calls, ~1s total)
        for sym in INDEX_SYMS:
            d = _finnhub_quote(sym)
            if d.get("c"):
                result[sym] = _build_quote(sym, ms,
                                           reg_p=d["c"], reg_chg=d["dp"])
            else:
                result[sym] = {"symbol": sym, "error": "no data", "marketState": ms}
            time.sleep(0.3)

        with _ql:
            for k, v in result.items():
                if "error" not in v:
                    _quotes[k] = v
                elif k not in _quotes:
                    _quotes[k] = v
                else:
                    _quotes[k]["marketState"] = ms
        print(f"[quotes] {len(result)} | state={ms}")
        time.sleep(28)


# ─────────────────────────────────────────────────────────────────────
# Background: futures + FX + bonds (Yahoo Finance v7 direct)
# ─────────────────────────────────────────────────────────────────────
def _futures_loop():
    while True:
        all_syms = FUTURES_SYMS + FX_SYMS + BOND_SYMS
        data = _yf_v7_batch(all_syms)

        f_res = {}
        for sym in FUTURES_SYMS:
            meta = FUTURES_META[sym]
            q    = data.get(sym, {})
            p    = q.get("regularMarketPrice")
            chg  = q.get("regularMarketChangePercent")
            f_res[sym] = {
                "symbol": sym, "name": meta["name"],
                "price": _r(p, meta["dec"]) if p else None,
                "changePercent": _r(chg) if chg is not None else None,
            }

        fx_res = {}
        for sym in FX_SYMS:
            meta = FX_META[sym]
            q    = data.get(sym, {})
            p    = q.get("regularMarketPrice")
            chg  = q.get("regularMarketChangePercent")
            fx_res[sym] = {
                "symbol": sym, "name": meta["name"],
                "price": _r(p, meta["dec"]) if p else None,
                "changePercent": _r(chg) if chg is not None else None,
            }

        bond_res = {}
        for sym in BOND_SYMS:
            meta = BOND_META[sym]
            q    = data.get(sym, {})
            p    = q.get("regularMarketPrice")
            chg  = q.get("regularMarketChangePercent")
            bond_res[meta["key"]] = {
                "symbol": sym, "name": meta["name"],
                "price": _r(p, meta["dec"]) if p else None,
                "changePercent": _r(chg) if chg is not None else None,
            }

        tnx = bond_res.get("10Y", {}).get("price")
        irx = bond_res.get("2Y",  {}).get("price")
        if tnx is not None and irx is not None:
            bond_res["spread"] = {
                "symbol": "spread", "name": "10Y-2Y 스프레드",
                "price": round(tnx - irx, 3), "changePercent": None,
            }

        with _fl:
            if f_res:    _futures_data["futures"].update(f_res)
            if fx_res:   _futures_data["fx"].update(fx_res)
            if bond_res: _futures_data["bonds"].update(bond_res)
        print(f"[futures] f={len(f_res)} fx={len(fx_res)} bonds={len(bond_res)}")
        time.sleep(30)


# ─────────────────────────────────────────────────────────────────────
# Background: sector performance
# ─────────────────────────────────────────────────────────────────────
def _sector_perf_loop():
    while True:
        etf_syms = list(SECTOR_ETFS.values())
        snap  = _alpaca_snapshots(etf_syms)
        hists = _alpaca_bars_batch(etf_syms, days=35)

        result = {}
        for name, sym in SECTOR_ETFS.items():
            s  = snap.get(sym, {})
            lp = s.get("latest")
            pc = s.get("prev")
            d1 = round(_pct(lp, pc) or 0.0, 2)
            closes = hists.get(sym, [])
            w1 = round((closes[-1]-closes[-6])/closes[-6]*100, 2) if len(closes) >= 6 else 0.0
            m1 = round((closes[-1]-closes[0]) /closes[0] *100, 2) if len(closes) >= 2 else 0.0
            result[name] = {"1d": d1, "1w": w1, "1m": m1}

        with _spl:
            _sector_perf.update(result)
        print(f"[sector_perf] {len(result)} ETFs")
        time.sleep(3600)


# ─────────────────────────────────────────────────────────────────────
# Background: news
# ─────────────────────────────────────────────────────────────────────
def _dedup(arts):
    seen, out = set(), []
    for a in arts:
        k = a["headline"][:30]
        if k not in seen:
            seen.add(k); out.append(a)
    return out


def _fetch_finnhub():
    try:
        items = requests.get(FINNHUB_URL, timeout=10).json()
        out = []
        for item in items[:25]:
            h  = item.get("headline", "")
            ts = item.get("datetime", 0)
            out.append({
                "headline": h, "source": "Finnhub", "url": item.get("url", ""),
                "published": ts,
                "published_str": datetime.fromtimestamp(ts, tz=ET_TZ).strftime("%H:%M") if ts else "",
                "sentiment": classify(h),
            })
        return out
    except Exception as e:
        print(f"[Finnhub] {e}"); return []


def _fetch_juice():
    try:
        r    = requests.get(JUICE_URL, timeout=10, headers=HTTP_HDR)
        root = ET.fromstring(r.content)
        out  = []
        for item in root.findall(".//item"):
            title   = (item.findtext("title")   or "").strip()
            link    = (item.findtext("link")    or "").strip()
            pub_str = (item.findtext("pubDate") or "").strip()
            try:
                from email.utils import parsedate_to_datetime
                dt   = parsedate_to_datetime(pub_str)
                ts   = int(dt.timestamp())
                tstr = dt.astimezone(ET_TZ).strftime("%H:%M")
            except Exception:
                ts, tstr = 0, ""
            out.append({"headline": title, "source": "FinJuice", "url": link,
                        "published": ts, "published_str": tstr, "sentiment": classify(title)})
        return out
    except Exception as e:
        print(f"[FinJuice] {e}"); return []


def _fetch_wscn():
    try:
        r    = requests.get(WSCN_URL, timeout=10, headers=YF_HDR)
        data = r.json()
        items = data.get("data", {}).get("items", [])
        out = []
        for item in items:
            title = (item.get("title") or item.get("content_short") or "").strip()
            if not title:
                continue
            ts_raw = item.get("display_time") or item.get("created_at") or 0
            try:
                ts = int(ts_raw)
            except (ValueError, TypeError):
                ts = 0
            tstr = datetime.fromtimestamp(ts, tz=ET_TZ).strftime("%H:%M") if ts else ""
            url  = item.get("uri") or item.get("content_uri") or ""
            out.append({"headline": title, "source": "WSCN", "url": url,
                        "published": ts, "published_str": tstr, "sentiment": classify(title)})
        return out
    except Exception as e:
        print(f"[WSCN] {e}"); return []


def _news_loop():
    while True:
        merged = _dedup(sorted(_fetch_finnhub() + _fetch_juice() + _fetch_wscn(),
                               key=lambda x: x["published"], reverse=True))[:40]
        with _nl:
            _news[:] = merged
        time.sleep(120)


# ─────────────────────────────────────────────────────────────────────
# Background: liquidity
# ─────────────────────────────────────────────────────────────────────
def _fetch_sofr() -> list:
    start = (datetime.now(ET_TZ)-timedelta(days=20)).strftime("%Y-%m-%d")
    url   = (f"{NYFED_BASE}?productCode=50&startDt={start}"
             f"&eventCodes=520&limit=30&sort=postDt:-1&format=json")
    data  = requests.get(url, timeout=15, headers=HTTP_HDR).json()
    rows  = sorted([r for r in data.get("refRates", []) if r.get("type") == "SOFR"],
                   key=lambda x: x["effectiveDate"])[-7:]
    return [(r["effectiveDate"], r["percentRate"]) for r in rows]


def _fetch_rrp() -> list:
    start = (datetime.now(ET_TZ)-timedelta(days=30)).strftime("%Y-%m-%d")
    url   = (f"{NYFED_BASE}?productCode=70&startDt={start}"
             f"&limit=50&sort=postDt:-1&format=json")
    ops   = requests.get(url, timeout=15, headers=HTTP_HDR).json() \
                    .get("repo", {}).get("operations", [])
    rows  = sorted([o for o in ops
                    if o.get("operationType") == "Reverse Repo" and o.get("term") == "Overnight"],
                   key=lambda x: x["operationDate"])[-7:]
    return [(o["operationDate"], round(o["totalAmtAccepted"]/1e9, 3)) for o in rows]


def _fetch_tbill3m() -> list:
    for offset in range(3):
        ym  = (datetime.now(ET_TZ)-timedelta(days=30*offset)).strftime("%Y%m")
        url = (f"https://home.treasury.gov/resource-center/data-chart-center/"
               f"interest-rates/daily-treasury-rates.csv/all/{ym}"
               f"?type=daily_treasury_bill_rates&field_tdr_date_value_month={ym}&download=true")
        try:
            text = requests.get(url, timeout=15, headers=HTTP_HDR).text
            rows = []
            for line in text.strip().splitlines()[1:]:
                parts = line.split(",")
                if len(parts) >= 8:
                    try: rows.append((parts[0], float(parts[7])))
                    except ValueError: pass
            if len(rows) >= 3: return sorted(rows, key=lambda x: x[0])[-7:]
        except Exception: pass
    return []


def _fetch_fed_assets() -> list:
    r    = requests.get("https://www.federalreserve.gov/releases/h41/current/h41.htm",
                        timeout=15, headers=HTTP_HDR)
    text = r.text
    idx  = text.find("Total assets", text.find("Total assets")+1)
    if idx < 0: idx = text.find("Total assets")
    seg  = text[idx:idx+3000]
    vals = []
    for n in re.findall(r'(\d{1,3}(?:,\d{3})+)', seg):
        v = float(n.replace(",", ""))
        if 5_000_000 <= v <= 12_000_000:
            vals.append(round(v/1000, 1))
        if len(vals) >= 7: break
    if not vals: return []
    today = datetime.now(ET_TZ)
    dates = [(today-timedelta(weeks=len(vals)-1-i)).strftime("%Y-%m-%d") for i in range(len(vals))]
    return list(zip(dates, vals))


def _liq_loop():
    FETCHERS = {
        "sofr":  (_fetch_sofr,       "SOFR",       "%"),
        "rrp":   (_fetch_rrp,        "RRP 역환매", "B$"),
        "dtb3":  (_fetch_tbill3m,    "T-Bill 3M",  "%"),
        "walcl": (_fetch_fed_assets, "연준 총자산","B$"),
    }
    while True:
        result = {}
        for key, (fn, label, unit) in FETCHERS.items():
            try:
                rows = fn()
                if not rows: continue
                dates  = [r[0] for r in rows]
                values = [r[1] for r in rows]
                change = round(values[-1]-values[0], 3) if len(values) > 1 else 0
                result[key] = {"label": label, "unit": unit, "latest": values[-1],
                               "change": change, "dates": dates, "values": values}
                print(f"[liq] {key}={values[-1]} {unit}")
            except Exception as e:
                print(f"[liq/{key}] {e}")
        with _ll: _liq.update(result)
        time.sleep(3600)


# ─────────────────────────────────────────────────────────────────────
# Background: macro (FRED CSV)
# ─────────────────────────────────────────────────────────────────────
def _fetch_fred_n(series_id: str, n: int = 5) -> list:
    url  = f"{FRED_BASE}?id={series_id}"
    resp = requests.get(url, timeout=15, headers=HTTP_HDR)
    resp.raise_for_status()
    rows = []
    for line in resp.text.strip().splitlines()[1:]:
        parts = line.strip().split(",")
        if len(parts) >= 2 and parts[1].strip() not in (".", "", "NA"):
            try:
                rows.append((parts[0].strip(), float(parts[1].strip())))
            except ValueError:
                pass
    return rows[-n:] if len(rows) >= n else rows


def _build_macro_entry(series_id, label, mode, url):
    rows = _fetch_fred_n(series_id, 5)
    if not rows:
        return None
    if mode == "mom_pct":
        history = []
        for i in range(1, len(rows)):
            pv, cv = rows[i-1][1], rows[i][1]
            history.append({"date": rows[i][0][:7], "value": round((cv-pv)/pv*100, 3) if pv else None})
        history = history[-3:]
        latest_val  = history[-1]["value"] if history else None
        latest_date = history[-1]["date"]  if history else None
        change = round(history[-1]["value"]-history[-2]["value"], 3) \
            if len(history) >= 2 and None not in (history[-1]["value"], history[-2]["value"]) else None
    elif mode == "mom_diff":
        history = []
        for i in range(1, len(rows)):
            history.append({"date": rows[i][0][:7], "value": round(rows[i][1]-rows[i-1][1], 1)})
        history = history[-3:]
        latest_val  = history[-1]["value"] if history else None
        latest_date = history[-1]["date"]  if history else None
        change = round(history[-1]["value"]-history[-2]["value"], 1) \
            if len(history) >= 2 and None not in (history[-1]["value"], history[-2]["value"]) else None
    else:
        history     = [{"date": r[0][:7], "value": round(r[1], 2)} for r in rows[-3:]]
        latest_val  = rows[-1][1]
        latest_date = rows[-1][0][:7]
        change      = round(rows[-1][1]-rows[-2][1], 3) if len(rows) >= 2 else None
    return {
        "label": label, "url": url, "latest": latest_val,
        "latestDate": latest_date, "change": change, "history": history,
    }


def _macro_loop():
    while True:
        inf_r = {}; emp_r = {}; pmi_r = {}
        for key, (sid, label, mode, url) in MACRO_INFLATION.items():
            try:
                d = _build_macro_entry(sid, label, mode, url)
                if d: inf_r[key] = d
            except Exception as e:
                print(f"[macro/inf/{key}] {e}")
        for key, (sid, label, mode, url) in MACRO_EMPLOYMENT.items():
            try:
                d = _build_macro_entry(sid, label, mode, url)
                if d: emp_r[key] = d
            except Exception as e:
                print(f"[macro/emp/{key}] {e}")
        for key, (sid, label, mode, url) in MACRO_PMI.items():
            try:
                d = _build_macro_entry(sid, label, mode, url)
                if d: pmi_r[key] = d
            except Exception as e:
                print(f"[macro/pmi/{key}] {e}")
        with _ml:
            _macro["inflation"].update(inf_r)
            _macro["employment"].update(emp_r)
            _macro["pmi"].update(pmi_r)
        time.sleep(3600)


# ─────────────────────────────────────────────────────────────────────
# Flask routes
# ─────────────────────────────────────────────────────────────────────
@app.route("/")
def root():
    return send_from_directory("templates", "index.html")


@app.route("/api/state")
def api_state():
    return jsonify({"state": market_state()})


@app.route("/api/quotes")
def api_quotes():
    param = request.args.get("symbols", "")
    with _ql:
        if not param:
            return jsonify(_quotes)
        ms   = market_state()
        syms = [s.strip() for s in param.split(",") if s.strip()]
        return jsonify({s: _quotes.get(s, {"symbol": s, "error": "pending", "marketState": ms}) for s in syms})


@app.route("/api/futures")
def api_futures():
    with _fl:
        return jsonify(_futures_data)


@app.route("/api/news")
def api_news():
    with _nl:
        return jsonify(_news)


@app.route("/news")
def news_page():
    return send_from_directory("templates", "news.html")


@app.route("/api/briefing")
def api_briefing():
    """조회 시점 기준 직전 12시간 뉴스 (화면에서 12h로 다시 자름)."""
    return jsonify(_scanner.get_live())


@app.route("/api/briefing/topics")
def api_briefing_topics():
    return jsonify([{k: t[k] for k in ("id", "group", "name", "tickers")} for t in TOPICS])


@app.route("/api/liquidity")
def api_liquidity():
    with _ll:
        return jsonify(_liq)


@app.route("/api/sector-perf")
def api_sector_perf():
    with _spl:
        return jsonify(_sector_perf)


@app.route("/api/macro")
def api_macro():
    with _ml:
        return jsonify(_macro)


# ─────────────────────────────────────────────────────────────────────
_scanner = Scanner(FINNHUB_KEY)


def start_background_threads():
    _refresh_yf_crumb()
    for fn in (_quote_loop, _futures_loop, _news_loop,
               _liq_loop, _sector_perf_loop, _macro_loop, _scanner.loop):
        threading.Thread(target=fn, daemon=True).start()


start_background_threads()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"http://localhost:{port}  (first load ~20-30s)")
    app.run(host="0.0.0.0", port=port, debug=False)
