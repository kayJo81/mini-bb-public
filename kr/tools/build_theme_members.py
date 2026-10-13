"""
종목 → 테마 표 (KR 장중 테마의 '급등주가 몰린 테마' 찾기용). 네이버 증권 테마 약 260개의 구성 종목을 받아
kr/lib/theme_members.json 으로 저장 ({"codes": {종목코드: [테마번호, …]}}).
테마 구성은 자주 바뀌지 않아서 가끔 다시 실행하면 된다.

  python kr/tools/build_theme_members.py
"""
import json
import os
from concurrent.futures import ThreadPoolExecutor

import requests

H = {"User-Agent": "Mozilla/5.0 (iPhone)", "Referer": "https://m.stock.naver.com/"}
API = "https://m.stock.naver.com/api/stocks/theme"
OUT = os.path.join(os.path.dirname(__file__), "..", "lib", "theme_members.json")


def members(no):
    d = requests.get(f"{API}/{no}?page=1&pageSize=100", headers=H, timeout=20).json()
    return no, [s["itemCode"] for s in d.get("stocks") or []]


groups = []
for p in (1, 2, 3):
    groups += requests.get(f"{API}?page={p}&pageSize=100", headers=H, timeout=20).json().get("groups") or []
with ThreadPoolExecutor(8) as pool:
    res = list(pool.map(members, [g["no"] for g in groups]))
codes = {}
for no, cs in res:
    for c in cs:
        codes.setdefault(c, []).append(no)
json.dump({"codes": codes}, open(OUT, "w", encoding="utf-8"), separators=(",", ":"))
print(f"테마 {len(groups)}개 · 종목 {len(codes)}개 → {OUT}")
