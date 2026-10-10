"""
종목 → 업종 표 (KR 맵 화면용). 네이버 증권 업종 79개의 구성 종목을 받아 kr/lib/sectors.json 으로 저장.
업종 구성은 거의 바뀌지 않아서 가끔만 다시 실행하면 된다 (새로 상장한 종목은 맵에서 '기타'로 표시).

  python kr/tools/build_sectors.py
"""
import json
import os
from concurrent.futures import ThreadPoolExecutor

import requests

H = {"User-Agent": "Mozilla/5.0 (iPhone)", "Referer": "https://m.stock.naver.com/"}
API = "https://m.stock.naver.com/api/stocks/industry"
OUT = os.path.join(os.path.dirname(__file__), "..", "lib", "sectors.json")


def members(g):
    out, page = [], 1
    while True:
        d = requests.get(f"{API}/{g['no']}?page={page}&pageSize=100", headers=H, timeout=20).json()
        out += [s["itemCode"] for s in d.get("stocks") or []]
        if page * 100 >= (d.get("totalCount") or 0):
            return g["name"], out
        page += 1


groups = requests.get(f"{API}?page=1&pageSize=100", headers=H, timeout=20).json()["groups"]
with ThreadPoolExecutor(8) as pool:
    res = list(pool.map(members, groups))
names = sorted({n for n, _ in res})
codes = {c: names.index(n) for n, cs in res for c in cs}
json.dump({"names": names, "codes": codes}, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
print(f"[sectors] 업종 {len(names)}개 · 종목 {len(codes)}개 → {OUT}")
