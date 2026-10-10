"""
일봉 가격 저장 (labdata/prices.json) — 추적 종목 + QQQ·SPY, 최근 2년, 매일 전체 새로 받기 (야후 차트)

  {"NVDA": {"2026-10-09": [시가, 종가], ...}, ...}
"""
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import BENCH, DATA, tickers  # noqa: E402

URL = "https://query1.finance.yahoo.com/v8/finance/chart/{}?interval=1d&range=2y"
HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}


def daily(sym):
    for i in range(3):
        try:
            r = requests.get(URL.format(sym), headers=HDR, timeout=20).json()["chart"]["result"][0]
            off = r["meta"].get("gmtoffset", -14400)
            q = r["indicators"]["quote"][0]
            out = {}
            closes = list(q["close"])
            # 마지막 봉 종가가 비어 있으면(장 마감 직후 흔함) 현재가로 채움
            if closes and closes[-1] is None and r["meta"].get("regularMarketPrice"):
                closes[-1] = r["meta"]["regularMarketPrice"]
            for t, o, c in zip(r["timestamp"], q["open"], closes):
                o = o or c
                if o and c:
                    d = datetime.fromtimestamp(t + off, timezone.utc).date().isoformat()
                    out[d] = [round(o, 4), round(c, 4)]
            return out
        except Exception as ex:
            print(f"[prices] {sym}: {ex}")
            time.sleep(3 * (i + 1))
    return {}


def main():
    os.makedirs(DATA, exist_ok=True)
    path = os.path.join(DATA, "prices.json")
    try:
        old = json.load(open(path, encoding="utf-8"))
    except Exception:
        old = {}
    for sym in tickers() + [BENCH, "SPY"]:
        new = daily(sym)
        if new:
            old[sym] = {**old.get(sym, {}), **new}
        time.sleep(0.3)
    json.dump(old, open(path, "w", encoding="utf-8"), separators=(",", ":"))
    print(f"[prices] {len(old)}종목, {BENCH} {len(old.get(BENCH, {}))}일")


if __name__ == "__main__":
    main()
