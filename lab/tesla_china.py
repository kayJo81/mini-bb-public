"""
테슬라 중국 주간 판매(보험 등록 대수) — CnEVPost 주간 기사에서 (2022년 9월~)
  python lab/tesla_china.py --out lab/tesla_cn_weekly.json
  · 기사 제목의 'Tesla 15,350' 같은 수치, 제목에 없으면 본문의 'Tesla … sold 12,800 units' 같은 문장
  · 주차: 기사에 나온 '주간 마지막 날(일요일)' 기준 ISO 주차 (제목에 날짜가 없으면 기사 날짜 직전 일요일)
  · CnEVPost 는 2025년 10월 초를 끝으로 주간 기사를 내지 않음 → 새 기사가 생기면 다시 실행해 이어 붙임
"""
import argparse
import html
import json
import os
import re
import time
from datetime import date, timedelta

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}
MON = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
BODY = [r"Tesla[^.]{0,80}?\bsold ([\d,]{3,6}) (?:units|vehicles)", r"Tesla[^.]{0,40}?\bat ([\d,]{3,6}) units",
        r"Tesla[^.]{0,60}?\bhad ([\d,]{3,6})", r"Tesla[^.]{0,60}?registrations? (?:of|were|was|at) ([\d,]{3,6})",
        r"Tesla(?: China)?:? ([\d,]{3,6})\b"]


def search(q, pages=60):
    out = {}
    for p in range(1, pages + 1):
        r = requests.get(f"https://cnevpost.com/page/{p}/?s={q.replace(' ', '+')}", headers=UA, timeout=30)
        if r.status_code != 200:
            break
        items = re.findall(r'<h\d[^>]*class="[^"]*title[^"]*"[^>]*>\s*<a href="([^"]+)"[^>]*>(.*?)</a>', r.text, re.S)
        if not items:
            break
        new = 0
        for u, t in items:
            if u not in out:
                out[u] = html.unescape(re.sub(r"<[^>]+>", "", t)).strip()
                new += 1
        if not new:
            break
        time.sleep(0.5)
    return out


def week_end(url, title):
    m = re.search(r"/(20\d\d)/(\d\d)/(\d\d)/", url)
    posted = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    t = re.search(r"week ending (jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.? (\d{1,2})", title, re.I)
    if t:
        d = date(posted.year, MON[t.group(1).lower()], int(t.group(2)))
        return d if d <= posted else date(posted.year - 1, d.month, d.day)
    return posted - timedelta(days=(posted.weekday() + 1) % 7 or 7)   # 기사 날짜 직전 일요일


def tesla_num(url, title):
    m = re.search(r"Tesla[^,;:]*?\s([\d,]{3,7})", title)
    if m:
        return int(m.group(1).replace(",", "")), "제목"
    try:
        s = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", requests.get(url, headers=UA, timeout=30).text)))
    except Exception:
        return None, ""
    for p in BODY:
        m = re.search(p, s)
        if m:
            v = int(m.group(1).replace(",", ""))
            if 300 <= v <= 40000:
                return v, "본문"
    return None, ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="lab/tesla_cn_weekly.json")
    a = ap.parse_args()
    try:
        old = {w["url"]: w for w in json.load(open(a.out, encoding="utf-8"))["weeks"]}
    except Exception:
        old = {}
    posts = {}
    for q in ("insurance registrations", "registrations week ending"):
        posts.update(search(q))
    weekly = {u: t for u, t in posts.items() if re.search(r"week", t, re.I) and re.search(r"regist|sales", t, re.I)}
    rows = {}
    for u, t in weekly.items():
        if u in old:
            w = old[u]
        else:
            v, how = tesla_num(u, t)
            if v is None:
                continue
            we = week_end(u, t)
            iy, iw, _ = we.isocalendar()
            w = {"week_end": we.isoformat(), "year": iy, "week": iw, "tesla": v, "from": how, "url": u, "title": t}
            time.sleep(0.3)
        k = (w["year"], w["week"])
        if k not in rows or w["from"] == "제목":
            rows[k] = w
    weeks = sorted(rows.values(), key=lambda w: w["week_end"])
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    json.dump({"source": "CnEVPost 주간 보험 등록 대수 기사", "weeks": weeks}, open(a.out, "w", encoding="utf-8"),
              ensure_ascii=False, indent=0)
    print(f"[tesla_cn] 주간 {len(weeks)}개 ({weeks[0]['week_end']} ~ {weeks[-1]['week_end']})")


if __name__ == "__main__":
    main()
