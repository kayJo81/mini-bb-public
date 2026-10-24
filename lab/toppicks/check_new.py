"""
Top Picks Lab — 새 실적 발표가 나왔는지 점검 (SEC 제출 목록, 8-K Item 2.02 '실적 발표')

  python lab/toppicks/check_new.py            → 새 발표가 있으면 'NEW <티커> <제출일> <URL>' 줄을 찍고 종료코드 0
  python lab/toppicks/check_new.py --set      → 각 JSON 의 released(이미 반영한 최신 실적 발표 제출일)를 지금 SEC 기준으로 맞춤(처음 한 번)

  · 기준: 각 기업 JSON 의 "released" (반영 끝낸 마지막 실적 8-K 제출일) 보다 늦은 2.02 제출이 있으면 NEW
  · 갱신 방법은 lab/toppicks/UPDATE.md
"""
import glob
import json
import os
import sys
import time

import requests

UA = {"User-Agent": "MiniBBLab research yidiar80k@gmail.com"}
CIK = {"TSLA": 1318605, "SPCX": 1181412, "COIN": 1679788, "INTC": 50863, "NVDA": 1045810, "AAPL": 320193, "GOOGL": 1652044,
       "MSFT": 789019, "AMZN": 1018724, "META": 1326801, "AVGO": 1730168, "MRVL": 1835632, "GEV": 1996810, "BE": 1664703}
HERE = os.path.dirname(os.path.abspath(__file__))


def earnings_filings(cik):
    """최근 제출 중 8-K Item 2.02 — [(제출일, 접수번호, 대표문서)] 최신순"""
    for i in range(3):
        r = requests.get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json", headers=UA, timeout=30)
        if r.status_code == 200:
            break
        time.sleep(1 + i)
    r.raise_for_status()
    rec = r.json()["filings"]["recent"]
    out = []
    for form, d, acc, items, doc in zip(rec["form"], rec["filingDate"], rec["accessionNumber"], rec["items"], rec["primaryDocument"]):
        if form == "8-K" and "2.02" in (items or "").split(","):
            out.append((d, acc, doc))
    return sorted(out, reverse=True)


def main():
    setting = "--set" in sys.argv
    new = []
    for f in sorted(glob.glob(os.path.join(HERE, "*.json"))):
        t = os.path.basename(f)[:-5]
        if t not in CIK:
            continue
        d = json.load(open(f, encoding="utf-8"))
        try:
            L = earnings_filings(CIK[t])
        except Exception as e:
            print(f"[{t}] SEC 조회 실패: {e}", file=sys.stderr)
            continue
        time.sleep(0.2)                      # SEC: 초당 10회 이하
        if not L:
            continue
        top = L[0]
        if setting:
            d["released"] = top[0]
            json.dump(d, open(f, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            print(f"{t}: released = {top[0]}  (최신 분기 {d['latest_reported']})")
            continue
        if top[0] > d.get("released", "0000-00-00"):
            url = f"https://www.sec.gov/Archives/edgar/data/{CIK[t]}/{top[1].replace('-', '')}/{top[2]}"
            new.append(t)
            print(f"NEW {t} {top[0]} {url}")
    if not setting and not new:
        print("새 실적 발표 없음")


if __name__ == "__main__":
    main()
