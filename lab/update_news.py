"""
종목별 일간 뉴스 집계 저장 (labdata/news/<티커>.json)

  python lab/update_news.py --days 4        # 매일: 최근 며칠 다시 받아 덮어쓰기
  python lab/update_news.py --days 365      # 처음 한 번: 1년치 채우기 (Finnhub 무료 한도 60회/분 → 약 1시간)

  날짜별: n(회사 언급 기사 수), pos/neg(호재·악재 기사 수), s(논조 점수 합), heads(점수가 큰 대표 제목 6개)
"""
import argparse
import json
import os
import sys
import time
from datetime import date, timedelta

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, NOISE, mentions, score, session_date, tickers  # noqa: E402

URL = "https://finnhub.io/api/v1/company-news?symbol={}&from={}&to={}&token={}"
_last = [0.0]


def _call(sym, d0, d1, token):
    wait = 1.05 - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    _last[0] = time.time()
    for i in range(4):
        try:
            r = requests.get(URL.format(sym, d0, d1, token), timeout=30)
            if r.status_code == 429:
                time.sleep(20 * (i + 1))
                continue
            items = r.json()
            return items if isinstance(items, list) else []
        except Exception as ex:
            print(f"[news] {sym} {d0}~{d1}: {ex}")
            time.sleep(5)
    return []


def fetch(sym, d0, d1, token):
    """기간이 길어 결과가 잘릴 수 있으면(≥240건) 반으로 나눠 다시 받는다"""
    items = _call(sym, d0, d1, token)
    if len(items) >= 240 and d1 > d0:
        mid = d0 + (d1 - d0) // 2
        return fetch(sym, d0, mid, token) + fetch(sym, mid + timedelta(days=1), d1, token)
    return items


def aggregate(sym, items):
    days, seen = {}, set()
    for it in items:
        h = (it.get("headline") or "").strip()
        ts = int(it.get("datetime") or 0)
        key = h.lower()[:90]
        if not h or not ts or key in seen or NOISE.search(h) or not mentions(sym, h):
            continue
        seen.add(key)
        sc = score(h)
        d = days.setdefault(session_date(ts), {"n": 0, "pos": 0, "neg": 0, "s": 0, "heads": []})
        d["n"] += 1
        d["pos"] += sc > 0
        d["neg"] += sc < 0
        d["s"] += sc
        d["heads"].append([ts, h[:200], it.get("source") or "", it.get("url") or "", sc])
    for d in days.values():   # 대표 제목: 논조가 뚜렷한 것 우선, 같으면 최신
        d["heads"] = sorted(d["heads"], key=lambda x: (-abs(x[4]), -x[0]))[:6]
    return days


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=4)
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    token = os.environ.get("FINNHUB_KEY", "")
    if not token:
        sys.exit("FINNHUB_KEY 가 없습니다")
    os.makedirs(os.path.join(DATA, "news"), exist_ok=True)
    end = date.today()
    start = end - timedelta(days=a.days)
    syms = a.only.split(",") if a.only else tickers()
    for i, sym in enumerate(syms, 1):
        items = []
        d = start
        while d <= end:    # 한 달씩 (많으면 fetch 가 더 쪼갬)
            d1 = min(d + timedelta(days=30), end)
            items += fetch(sym, d, d1, token)
            d = d1 + timedelta(days=1)
        days = aggregate(sym, items)
        path = os.path.join(DATA, "news", f"{sym}.json")
        try:
            old = json.load(open(path, encoding="utf-8"))
        except Exception:
            old = {}
        # 기간 첫날은 전날 밤 기사가 빠져 있을 수 있으므로 덮어쓰지 않는다
        keep_from = (start + timedelta(days=1)).isoformat()
        for k, v in days.items():
            if k >= keep_from or k not in old:
                old[k] = v
        for k in [k for k in old if k >= keep_from and k not in days]:
            old[k] = {"n": 0, "pos": 0, "neg": 0, "s": 0, "heads": []}
        json.dump(dict(sorted(old.items())), open(path, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
        print(f"[news] ({i}/{len(syms)}) {sym}: 기사 {len(items)}건 → 회사 언급 {sum(v['n'] for v in days.values())}건, {len(days)}일")


if __name__ == "__main__":
    main()
