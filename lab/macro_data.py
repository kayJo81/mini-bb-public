"""물가 지표 (CPI · PCE · PPI 항목별 월간 + PPI AI 비용 항목) — FRED 에서 받아 lab-data 에 누적
  python lab/macro_data.py --out public_lab/macro.json

· FRED 의 fredgraph.csv (키 필요 없음) 로 2017년 1월부터 받아요.
· 누적 저장: labdata/macro_history.json
    values : {id: {YYYY-MM: 최신 값}}   — FRED 가 안 될 때도 저장분으로 페이지를 만듦
    first  : {id: {YYYY-MM: 처음 본 값}} — 나중에 수정(개정)됐는지 확인용
· 계절조정(SA) 아닌 항목(산업별 PPI 대부분)은 월간 변화가 들쭉날쭉해서 페이지에서 전년비 위주로 봐요.
"""
import argparse
import csv
import io
import json
import os
import time
from datetime import datetime, timezone

import requests

START = "2017-01-01"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}

# [id, 이름, 들여쓰기(0=헤드라인·1=주요·2=세부), 계절조정 여부]
GROUPS = [
    {"key": "cpi", "name": "CPI 소비자물가", "src": "BLS (FRED)", "items": [
        ["CPIAUCSL", "CPI 전체", 0, 1],
        ["CPILFESL", "근원 CPI (식품·에너지 제외)", 0, 1],
        ["CUSR0000SASL2RS", "슈퍼코어 (주거 제외 서비스)", 0, 1],
        ["CPIUFDSL", "식품", 1, 1],
        ["CUSR0000SAF11", "가정 내 식품", 2, 1],
        ["CPIENGSL", "에너지", 1, 1],
        ["CUSR0000SACL1E", "근원 상품", 1, 1],
        ["CUSR0000SETA01", "신차", 2, 1],
        ["CUSR0000SETA02", "중고차", 2, 1],
        ["CPIAPPSL", "의류", 2, 1],
        ["CUSR0000SASLE", "근원 서비스", 1, 1],
        ["CUSR0000SAH1", "주거 (Shelter)", 2, 1],
        ["CUSR0000SEHA", "임대료", 2, 1],
        ["CUSR0000SEHC", "자가주거비 (OER)", 2, 1],
        ["CPIMEDSL", "의료", 2, 1],
        ["CUSR0000SAM2", "의료 서비스", 2, 1],
        ["CUSR0000SAS4", "운송 서비스", 2, 1],
        ["CUSR0000SETG01", "항공료", 2, 1],
        ["CUSR0000SEHF01", "전기요금", 2, 1],
    ]},
    {"key": "pce", "name": "PCE 개인소비지출 물가", "src": "BEA (FRED)", "items": [
        ["PCEPI", "PCE 전체", 0, 1],
        ["PCEPILFE", "근원 PCE (식품·에너지 제외)", 0, 1],
        ["IA001260M", "슈퍼코어 (에너지·주거 제외 서비스)", 0, 1],
        ["IA001176M", "식품·에너지·주거 제외", 1, 1],
        ["DGDSRG3M086SBEA", "상품", 1, 1],
        ["DDURRG3M086SBEA", "내구재", 2, 1],
        ["DNDGRG3M086SBEA", "비내구재", 2, 1],
        ["DSERRG3M086SBEA", "서비스", 1, 1],
        ["DFXARG3M086SBEA", "식품", 1, 1],
        ["DNRGRG3M086SBEA", "에너지", 1, 1],
    ]},
    {"key": "ppi", "name": "PPI 생산자물가 (최종수요)", "src": "BLS (FRED)", "items": [
        ["PPIFIS", "PPI 최종수요", 0, 1],
        ["PPICOR", "근원 PPI (식품·에너지 제외)", 0, 1],
        ["PPIFDG", "상품", 1, 1],
        ["PPIFDF", "식품", 2, 1],
        ["PPIFDE", "에너지", 2, 1],
        ["PPIFDS", "서비스", 1, 1],
        ["PPIDTS", "유통 마진 (무역 서비스)", 2, 1],
        ["PCU523940523940", "포트폴리오 운용 수수료", 2, 0],
        ["PCU481111481111", "항공 여객", 2, 0],
        ["PCU488510488510", "화물 운송 주선", 2, 0],
    ]},
    # AI 투자에 드는 비용 — 연산(반도체·서버)·클라우드·전력·전력망·냉각·건설 인력
    {"key": "ai", "name": "AI 비용 관련 PPI", "src": "BLS (FRED)", "items": [
        ["PCU518210518210", "데이터 처리·호스팅 (클라우드)", 1, 0, "클라우드"],
        ["PCU511210511210", "소프트웨어", 1, 0, "클라우드"],
        ["PCU517311517311", "유선 통신", 1, 0, "클라우드"],
        ["PCU334413334413", "반도체", 1, 0, "연산"],
        ["PCU3344133344131", "반도체 패키지 (IC)", 2, 0, "연산"],
        ["PCU334111334111", "컴퓨터 (서버 포함)", 1, 0, "연산"],
        ["PCU334112334112", "저장장치", 1, 0, "연산"],
        ["PCU334419334419", "기타 전자부품", 2, 0, "연산"],
        ["WPU0543", "산업용 전기요금", 1, 0, "전력"],
        ["WPU0542", "상업용 전기요금", 1, 0, "전력"],
        ["WPU0553", "발전용 천연가스", 2, 0, "전력"],
        ["PCU333611333611", "터빈·발전기", 2, 0, "전력"],
        ["PCU335311335311", "변압기", 1, 0, "전력망"],
        ["PCU335313335313", "개폐기·배전반", 1, 0, "전력망"],
        ["WPU10260314", "구리 전선·케이블", 2, 0, "전력망"],
        ["PCU335929335929", "통신·전력 전선", 2, 0, "전력망"],
        ["PCU333415333415", "냉방·냉각 장비", 1, 0, "냉각·건설"],
        ["PCU541330541330", "엔지니어링 서비스", 2, 0, "냉각·건설"],
    ]},
]
COMPARE = "PPIFIS"   # AI 항목과 비교하는 기준 (PPI 최종수요)


def fred_many(ids, tries=3):
    """여러 시리즈를 한 번에 (fredgraph.csv?id=A,B,C → 열 여러 개) — 요청 수를 줄여 FRED 차단을 피함"""
    for i in range(tries):
        try:
            r = requests.get("https://fred.stlouisfed.org/graph/fredgraph.csv",
                             params={"id": ",".join(ids)}, headers=UA, timeout=60)
            if r.ok and (r.text.startswith("observation_date") or r.text.startswith("DATE")):
                rows = list(csv.reader(io.StringIO(r.text)))
                head, out = rows[0][1:], {}
                for row in rows[1:]:
                    if row[0] < START:
                        continue
                    for sid, val in zip(head, row[1:]):
                        if val not in ("", "."):
                            out.setdefault(sid, {})[row[0][:7]] = float(val)
                return out
            print(f"[macro] FRED {r.status_code} ({len(ids)}개)")
        except requests.RequestException as e:
            print(f"[macro] FRED 오류 {type(e).__name__}")
        time.sleep(5 * (i + 1))
    return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="public_lab/macro.json")
    a = ap.parse_args()
    store_path = os.path.join(os.environ.get("LAB_DATA", "labdata"), "macro_history.json")
    try:
        store = json.load(open(store_path))
    except (OSError, ValueError):
        store = {}
    values, first = store.setdefault("values", {}), store.setdefault("first", {})
    ids = [it[0] for g in GROUPS for it in g["items"]]
    got = {}
    for k in range(0, len(ids), 10):
        got |= fred_many(ids[k:k + 10])
        time.sleep(1)
    # 묶음에서 빠진 것만 하나씩 다시
    for sid in [x for x in ids if x not in got]:
        got |= fred_many([sid], tries=2)
    ok = fail = 0
    for sid in ids:
        if not got.get(sid):
            fail += 1
            print(f"[macro] {sid} 못 받음 (저장분 사용)")
            continue
        ok += 1
        values[sid] = got[sid]
        f = first.setdefault(sid, {})
        for m, v in got[sid].items():
            f.setdefault(m, v)
    store["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    os.makedirs(os.path.dirname(store_path) or ".", exist_ok=True)
    json.dump(store, open(store_path, "w"), separators=(",", ":"))

    out = {"at": store["updated"], "compare": COMPARE, "groups": []}
    for g in GROUPS:
        items = []
        for it in g["items"]:
            sid, name, lv, sa = it[:4]
            ser = values.get(sid) or {}
            if not ser:
                continue
            months = sorted(ser)
            # 빈 달(예: 2025년 10월 셧다운으로 CPI 미발표)은 null 로 채워 달 순서를 맞춤
            full, (y, mo) = [], map(int, months[0].split("-"))
            while f"{y}-{mo:02d}" <= months[-1]:
                full.append(f"{y}-{mo:02d}")
                y, mo = (y + 1, 1) if mo == 12 else (y, mo + 1)
            fs = first.get(sid, {})
            # 처음 본 값과 지금 값이 다른 달 (개정) — 최근 12개월만
            rev = {m: fs[m] for m in months[-12:] if m in fs and abs(fs[m] - ser[m]) > 1e-9}
            items.append({"id": sid, "name": name, "lv": lv, "sa": bool(sa), "cat": it[4] if len(it) > 4 else "",
                          "start": months[0], "v": [ser.get(m) for m in full], "rev": rev})
        out["groups"].append({k: g[k] for k in ("key", "name", "src")} | {"items": items})
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    json.dump(out, open(a.out, "w"), ensure_ascii=False, separators=(",", ":"))
    print(f"[macro] 받음 {ok} · 실패 {fail} → {a.out}")


if __name__ == "__main__":
    main()
