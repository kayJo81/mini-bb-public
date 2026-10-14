"""
테슬라 분기 인도량(라인업별)·생산량·에너지 저장 배치(GWh) — SEC 공시(8-K Ex.99.1 '생산·인도 발표')에서 2020년부터
  python lab/tesla_data.py --out public_lab/tesla_q.json
  · 테슬라 IR 사이트는 자동 접속을 막아서, 같은 발표문을 SEC EDGAR 공시 첨부문서로 받음
  · 라인업: 2022년까지 'Model S/X', 2023년부터 'Other Models'(S/X·사이버트럭 등) + 'Model 3/Y'
  · 에너지 배치(GWh): 발표문에 'deployed … GWh' 가 있는 분기 (2023년 이전은 실적 발표 8-K 에서 찾아 보충)
"""
import argparse
import html
import json
import os
import re
import time

import requests

UA = {"User-Agent": "MiniBBLab research yidiar80k@gmail.com"}
CIK = "1318605"
NUM = r"([\d,]+)"


def get(url):
    for i in range(3):
        r = requests.get(url, headers=UA, timeout=30)
        if r.status_code == 200:
            return r
        time.sleep(1 + i)
    r.raise_for_status()


def text_of(acc):
    a = acc.replace("-", "")
    items = get(f"https://www.sec.gov/Archives/edgar/data/{CIK}/{a}/index.json").json()["directory"]["item"]
    ex = next((x["name"] for x in items if re.search(r"ex(?:hibit)?[-_.]?99", x["name"], re.I) and x["name"].endswith(".htm")), None)
    if not ex:
        return ""
    s = get(f"https://www.sec.gov/Archives/edgar/data/{CIK}/{a}/{ex}").text
    time.sleep(0.2)   # SEC: 초당 10회 이하
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s)))


def parse_pd(t):
    m = re.search(r"Q([1-4]) (20\d\d)", t) or re.search(r"(First|Second|Third|Fourth) Quarter (20\d\d)", t)
    if not m:
        return None
    q = {"First": "1", "Second": "2", "Third": "3", "Fourth": "4"}.get(m.group(1), m.group(1))
    out = {"q": f"{m.group(2)}Q{q}"}
    for key, pat in (("m3y", r"Model 3/Y"), ("other", r"(?:Other Models|Model S/X)"), ("total", r"Total")):
        mm = re.search(pat + r"\s+([\d,]+|-)\s+" + NUM, t)
        if mm:
            out[key + "_prod"] = 0 if mm.group(1) == "-" else int(mm.group(1).replace(",", ""))
            out[key] = int(mm.group(2).replace(",", ""))
    # 원문 숫자가 '258,5 8 0' 처럼 깨진 분기가 있어 합계는 라인업 합으로 맞춤
    if "m3y" in out and "other" in out:
        out["total"], out["total_prod"] = out["m3y"] + out["other"], out.get("m3y_prod", 0) + out.get("other_prod", 0)
    out["other_label"] = "Model S/X" if "Model S/X" in t else "Other Models"
    # 첫 문장 '이번 분기에 … deployed X GWh' 만 (연간 누계 문장은 제외)
    g = re.search(r"In the (?:first|second|third|fourth) quarter[^.]*?deployed ([\d.]+) GWh", t)
    if g:
        out["storage_gwh"] = float(g.group(1))
    return out if "total" in out else None


def storage_from_update(t):
    """실적 발표(업데이트) 8-K 본문: 운영 요약 표 'Storage deployed (MWh|GWh) 5개 분기' 의 마지막 값(이번 분기), 없으면 문장 표현"""
    m = re.search(r"Storage deployed \((MWh|GWh)\)\s+((?:[\d,.]+\s+){4}[\d,.]+)", t)
    if m:
        v = float(m.group(2).split()[-1].replace(",", ""))
        return round(v / 1000, 3) if m.group(1) == "MWh" else v
    for pat in (r"deployed ([\d.]+) GWh of energy storage", r"storage deployments? (?:of|reached|were) ([\d.]+) GWh",
                r"([\d.]+) GWh of (?:energy )?storage (?:deployed|deployments)"):
        m = re.search(pat, t, re.I)
        if m:
            return float(m.group(1))
    return None


def storage_ytd(since):
    """분기보고서(10-Q)·연차보고서(10-K)의 '연초부터 누적 배치량' → {분기: 누적 GWh}"""
    sub = get(f"https://data.sec.gov/submissions/CIK{CIK.zfill(10)}.json").json()["filings"]["recent"]
    ytd = {}
    for i, form in enumerate(sub["form"]):
        if form not in ("10-Q", "10-K") or sub["reportDate"][i] < since:
            continue
        rd = sub["reportDate"][i]
        q = f"{rd[:4]}Q{(int(rd[5:7]) - 1) // 3 + 1}"
        a = sub["accessionNumber"][i].replace("-", "")
        t = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", get(
            f"https://www.sec.gov/Archives/edgar/data/{CIK}/{a}/{sub['primaryDocument'][i]}").text)))
        time.sleep(0.2)
        m = (re.search(r"In (?:the first half of )?20\d\d, we (?:have )?deployed ([\d.,]+) (GWh|MWh) of energy storage", t))
        if m:
            v = float(m.group(1).replace(",", ""))
            if m.group(2) == "MWh" or v > 100:   # 2021년 1분기 보고서의 '445 GWh'는 MWh 오기
                v /= 1000
            ytd[q] = v
    return ytd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="public_lab/tesla_q.json")
    ap.add_argument("--since", default="2020-01-01")
    a = ap.parse_args()
    sub = get(f"https://data.sec.gov/submissions/CIK{CIK.zfill(10)}.json").json()["filings"]["recent"]
    rows, updates = {}, []
    for i, form in enumerate(sub["form"]):
        if form != "8-K" or "2.02" not in (sub["items"][i] or "") or sub["filingDate"][i] < a.since:
            continue
        d, acc = sub["filingDate"][i], sub["accessionNumber"][i]
        if int(d[8:10]) <= 6:   # 분기 첫 주 = 생산·인도 발표
            t = text_of(acc)
            r = parse_pd(t)
            if r:
                r["filed"] = d
                rows[r["q"]] = r
        else:
            updates.append((d, acc))
    # 에너지 배치가 없는 분기는 그 분기 실적 발표에서
    for d, acc in updates:
        y, mo = int(d[:4]), int(d[5:7])
        qn = (mo - 1) // 3   # 실적 발표는 다음 분기 첫 달 → 직전 분기
        q = f"{y - 1}Q4" if qn == 0 else f"{y}Q{qn}"
        if q in rows and "storage_gwh" not in rows[q]:
            g = storage_from_update(text_of(acc))
            if g is not None:
                rows[q]["storage_gwh"] = g
    # 그래도 없는 분기: 연초 누적 배치량의 차이 (예: 2022년 3분기 = 1~9월 누적 − 1~6월 누적)
    ytd = storage_ytd(a.since)
    for q, r in rows.items():
        if "storage_gwh" in r or q not in ytd:
            continue
        n = int(q[-1])
        prev = ytd.get(f"{q[:4]}Q{n - 1}", 0 if n == 1 else None)
        if prev is not None:
            r["storage_gwh"], r["storage_note"] = round(ytd[q] - prev, 3), "연초 누적 차이로 계산"
    out = sorted((r for r in rows.values() if r["q"] >= "2020Q1"), key=lambda r: r["q"])
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    json.dump({"source": "SEC EDGAR 8-K Ex.99.1 (Tesla Production, Deliveries & Deployments)", "quarters": out},
              open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[tesla] 분기 {len(out)}개 ({out[0]['q']}~{out[-1]['q']}) · 에너지 배치 {sum('storage_gwh' in r for r in out)}개 분기")


if __name__ == "__main__":
    main()
