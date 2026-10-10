"""
미국 장 마감 종가 PDF — 'M월 D일 시장 등락' (주요 지수 + 분야별 종목, 등락률 순)

  python scripts/close_pdf.py --out reports_close [--date 2026-10-09]

  · 종가: 야후 파이낸스 일봉(장 마감 뒤 확정되는 공식 종가), 변동 = 그날 종가 − 직전 거래일 종가
    (그날이 배당락일이면 증권사 앱처럼 배당만큼 조정한 직전 종가 기준 — 예: 오라클 10/9)
  · 반올림은 마지막에 한 번만, 사사오입(4.395 → 4.40)
  · 마감일 확인: 모든 종목의 마지막 일봉 날짜가 같은 날이어야 함 — 미국 휴장일(오늘 일봉 없음)이면 PDF를 만들지 않고 끝냄
  · 받지 못한 종목은 표에 '—'로 남기고 PDF 아래에 이름을 적는다 (조용히 빠지지 않게)
  · 종목 목록은 아래 GROUPS 를 고치면 된다
"""
import argparse
import html
import os
import time
from decimal import ROUND_HALF_UP, Decimal
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

ET, KST = ZoneInfo("America/New_York"), ZoneInfo("Asia/Seoul")
HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
URL = "https://query1.finance.yahoo.com/v8/finance/chart/{}?range=10d&interval=1d"

INDICES = [("^DJI", "다우존스"), ("^GSPC", "S&P 500"), ("^IXIC", "나스닥종합지수"), ("^RUT", "러셀 2000")]
GROUPS = [
    ("Big Tech", [("AMZN", "Amazon"), ("MSFT", "Microsoft"), ("GOOGL", "Alphabet A"), ("META", "Meta Platforms"),
                  ("NVDA", "NVIDIA"), ("AAPL", "Apple")]),
    ("Semi", [("SNPS", "Synopsys"), ("CDNS", "Cadence"), ("STX", "Seagate"), ("WDC", "Western Digital"), ("ASML", "ASML"),
              ("AVGO", "Broadcom"), ("MRVL", "Marvell"), ("ADI", "Analog Devices"), ("QCOM", "Qualcomm"),
              ("AMAT", "Applied Materials"), ("NVDA", "NVIDIA"), ("LRCX", "Lam Research"), ("KLAC", "KLA"), ("MU", "Micron"),
              ("SOXX", "iShares Semiconductor ETF"), ("SKHY", "SK hynix"), ("TSM", "TSMC"), ("STM", "STMicroelectronics"),
              ("NXPI", "NXP"), ("TXN", "Texas Instruments"), ("SNDK", "SanDisk"), ("AMD", "AMD"), ("INTC", "Intel"),
              ("CBRS", "Cerebras"), ("GFS", "GlobalFoundries"), ("ON", "ON Semiconductor"), ("ARM", "Arm Holdings"),
              ("SWKS", "Skyworks")]),
    ("Serve & Infra", [("LITE", "Lumentum"), ("HPE", "Hewlett Packard Enterprise"), ("COHR", "Coherent"), ("CSCO", "Cisco"),
                       ("ANET", "Arista"), ("GLW", "Corning"), ("DELL", "Dell"), ("NVT", "nVent"), ("ETN", "Eaton"),
                       ("WULF", "TeraWulf"), ("NBIS", "Nebius"), ("CRWV", "CoreWeave"), ("VRT", "Vertiv"),
                       ("APLD", "Applied Digital"), ("POET", "POET Technologies"), ("NVTS", "Navitas"), ("IREN", "IREN"),
                       ("POWI", "Power Integrations"), ("SMCI", "Super Micro")]),
    ("Cloud / Software", [("SNOW", "Snowflake"), ("DDOG", "Datadog"), ("AKAM", "Akamai"), ("PLTR", "Palantir"),
                          ("PANW", "Palo Alto Networks"), ("ORCL", "Oracle"), ("CRWD", "CrowdStrike"),
                          ("CLOU", "Global X Cloud ETF"), ("ZM", "Zoom"), ("IGV", "iShares Software ETF"), ("RDDT", "Reddit"),
                          ("NOW", "ServiceNow"), ("CRM", "Salesforce"), ("ADBE", "Adobe"), ("IBM", "IBM"), ("WDAY", "Workday"),
                          ("APP", "AppLovin"), ("HUBS", "HubSpot")]),
    ("Nuclear / Energy", [("CEG", "Constellation Energy"), ("BE", "Bloom Energy"), ("CCJ", "Cameco"), ("LTBR", "Lightbridge"),
                          ("GEV", "GE Vernova"), ("NNE", "NANO Nuclear"), ("OKLO", "Oklo"), ("LEU", "Centrus Energy"),
                          ("SMR", "NuScale"), ("FRMI", "Fermi")]),
    ("Space", [("SPCX", "SpaceX"), ("ITA", "iShares Aerospace & Defense ETF"), ("PL", "Planet Labs"),
               ("UFO", "Procure Space ETF"), ("RKLB", "Rocket Lab"), ("RDW", "Redwire"), ("LUNR", "Intuitive Machines"),
               ("ASTS", "AST SpaceMobile")]),
    ("Crypto", [("CRCL", "Circle"), ("COIN", "Coinbase"), ("BLSH", "Bullish"), ("FIGR", "Figure Technology"),
                ("MSTR", "Strategy"), ("HOOD", "Robinhood"), ("BMNR", "BitMine"), ("GEMI", "Gemini")]),
    ("EV / Mobility", [("TSLA", "Tesla")]),
]
WD = "월화수목금토일"


def fetch(sym):
    """야후 일봉 → {날짜: 종가} (확정 종가만). 실패하면 한 번 더 시도."""
    for attempt in range(3):
        try:
            r = requests.get(URL.format(sym), headers=HDR, timeout=20)
            res = r.json()["chart"]["result"][0]
            ts, closes = res["timestamp"], res["indicators"]["quote"][0]["close"]
            adj = (res["indicators"].get("adjclose") or [{}])[0].get("adjclose") or closes   # 지수는 조정 종가가 없음
            tz = ZoneInfo(res["meta"].get("exchangeTimezoneName") or "America/New_York")
            return {datetime.fromtimestamp(t, tz).date(): (c, a or c) for t, c, a in zip(ts, closes, adj) if c is not None}
        except Exception as ex:
            err = ex
            time.sleep(2 * (attempt + 1))
    print(f"[close] {sym}: {err}")
    return {}


def row(sym, series, day):
    if day not in series:
        return None
    prev = [d for d in series if d < day]
    if not prev:
        return None
    (c, ca), (pc, pa) = series[day], series[max(prev)]
    # 직전 종가를 그날 기준으로 조정 (배당락·분할이 그날 있었으면 반영, 없으면 그대로)
    p = pa * c / ca if ca else pc
    # 야후 값은 4.39499… 처럼 부동소수 오차가 섞여 있어 소수 셋째 자리로 먼저 정리 (가격은 최소 0.001 단위)
    c, p = snap(c), snap(p)
    return {"close": c, "chg": c - p, "pct": (c / p - 1) * 100}


def snap(v):
    return Decimal(f"{v:.6f}").quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)


def r2(v):
    """사사오입 소수 둘째 자리 (부동소수 오차 없이: 4.395 → 4.40)"""
    return Decimal(v).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if isinstance(v, Decimal) else \
        Decimal(f"{v:.6f}").quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def fmt(v, sign=False, pct=False):
    d = r2(v)
    s = f"{d:+,.2f}" if sign else f"{d:,.2f}"
    return s.replace("+-", "-").replace("-0.00", "0.00").replace("+0.00", "0.00") + ("%" if pct else "")


def table(head, rows, ticker=True):
    cls = lambda v: "up" if r2(v) > 0 else "dn" if r2(v) < 0 else ""  # noqa: E731
    th = "".join(f"<th>{h}</th>" for h in head)
    body = ""
    for r in rows:
        d = r["d"]
        cells = (f"<td class='tk'>{html.escape(r['sym'])}</td>" if ticker else "") + f"<td class='{'nm' if ticker else 'tk'}'>{html.escape(r['name'])}</td>"
        if d:
            cells += (f"<td class='n'>{fmt(d['close'])}</td><td class='n {cls(d['chg'])}'>{fmt(d['chg'], True)}</td>"
                      f"<td class='n {cls(d['pct'])}'>{fmt(d['pct'], True, True)}</td>")
        else:
            cells += "<td class='n'>—</td><td class='n'>—</td><td class='n'>—</td>"
        body += f"<tr>{cells}</tr>"
    return f"<table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>"


CSS = """
@page{size:A4;margin:14mm 12mm}
body{font-family:'Noto Sans CJK KR','Noto Sans KR',sans-serif;color:#111827;font-size:9.5pt}
h1{font-size:20pt;margin:0 0 4px}.sub{color:#6b7280;font-size:9pt;margin-bottom:14px}
h2{font-size:12.5pt;margin:14px 0 5px;break-after:avoid}
table{width:100%;border-collapse:collapse;break-inside:auto}
thead{display:table-header-group}tr{break-inside:avoid}
th{background:#111b33;color:#fff;font-weight:700;text-align:right;padding:4px 7px;font-size:8.5pt}
th:first-child,th:nth-child(2){text-align:left}
td{padding:3.5px 7px;border-bottom:.5px solid #e5e7eb}
td.tk{font-weight:700;width:52px}td.nm{color:#374151}
td.n{text-align:right;font-variant-numeric:tabular-nums;width:88px}
.up{color:#dc2626}.dn{color:#2563eb}
.note{margin-top:14px;font-size:8pt;color:#6b7280}
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="reports_close")
    ap.add_argument("--date", help="마감일(미국, YYYY-MM-DD). 없으면 가장 최근 마감일")
    ap.add_argument("--pdf-only", action="store_true", help="PDF만 (일일 뉴스 메일에 함께 붙일 때 — 메일 제목·요약 파일은 건드리지 않음)")
    a = ap.parse_args()
    syms = [s for s, _ in INDICES] + sorted({s for _, g in GROUPS for s, _ in g})
    with ThreadPoolExecutor(8) as pool:
        data = dict(zip(syms, pool.map(fetch, syms)))

    if a.date:
        day = datetime.strptime(a.date, "%Y-%m-%d").date()
    else:
        # 지수 4개가 모두 가진 가장 최근 날짜 = 마감일. 장중(아직 마감 전)이면 그 전날
        days = set.intersection(*[set(data[s]) for s, _ in INDICES if data[s]])
        now = datetime.now(ET)
        day = max(d for d in days if d < now.date() or now.hour >= 16)
        # 자동 실행(장 마감 뒤)인데 오늘 일봉이 없으면 휴장 → 만들지 않음
        if os.environ.get("CLOSE_REQUIRE_TODAY") and day != now.date():
            print(f"[close] {now:%m/%d} 미국 휴장(마지막 마감 {day}) — PDF 만들지 않음")
            open(os.path.join(os.environ.get("GITHUB_OUTPUT", os.devnull)), "a").write("skip=true\n")
            return

    missing = []
    idx_rows = []
    for s, n in INDICES:
        d = row(s, data[s], day)
        if not d:
            missing.append(n)
        idx_rows.append({"sym": s, "name": n, "d": d})
    parts = [table(["지수", "종가", "변동", "등락률"], idx_rows, ticker=False)]
    for title, items in GROUPS:
        rows = []
        for s, n in items:
            d = row(s, data[s], day)
            if not d:
                missing.append(s)
            rows.append({"sym": s, "name": n, "d": d})
        rows.sort(key=lambda r: -(r["d"]["pct"] if r["d"] else Decimal(-1e9)))
        parts.append(f"<h2>{html.escape(title)}</h2>" + table(["티커", "종목", "종가", "변동", "등락률"], rows))

    kday = day + timedelta(days=1)   # 한국 날짜(다음 날 아침)
    title = f"{kday.month}월 {kday.day}일 시장 등락"
    sub = f"미국 {day.month}/{day.day}({WD[day.weekday()]}) 마감 기준 · 등락률 순 정렬"
    note = ("종가: 야후 파이낸스 일봉(장 마감 확정 종가) · 변동 = 종가 − 직전 거래일 종가(배당락일은 배당 조정)"
            + (f"<br>⚠️ 받지 못한 종목: {html.escape(', '.join(dict.fromkeys(missing)))}" if missing else ""))
    doc = (f"<html><head><meta charset='utf-8'><style>{CSS}</style></head><body><h1>{title}</h1><div class='sub'>{sub}</div>"
           f"<h2>주요 지수</h2>{''.join(parts)}<div class='note'>{note}</div></body></html>")

    os.makedirs(a.out, exist_ok=True)
    path = os.path.join(a.out, f"{kday.month}.{kday.day:02d} 시장 등락.pdf")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None)
        pg = b.new_page()
        pg.set_content(doc, wait_until="load")
        pg.pdf(path=path, format="A4", print_background=True, margin={"top": "14mm", "bottom": "14mm", "left": "12mm", "right": "12mm"})
        b.close()
    if not a.pdf_only:
        open(os.path.join(a.out, "summary.txt"), "w", encoding="utf-8").write(f"미니 블벅터 · {title} · {sub}")
        open(os.path.join(a.out, "subject.txt"), "w", encoding="utf-8").write(f"[미니 블벅터] {title} (미국 {day.month}/{day.day} 마감)")
    print(f"[close] {path} · 마감일 {day} · 종목 {len(syms)}개" + (f" · 못 받음 {missing}" if missing else ""))


if __name__ == "__main__":
    main()
