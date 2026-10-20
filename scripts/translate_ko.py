"""
수집 단계에서 헤드라인(과 중문 요약)을 한국어로 번역해 live.json 에 넣는다.

  python scripts/translate_ko.py public/briefings/live.json --cache .cache/ko.json

· 이미 번역한 문장은 캐시(.cache/ko.json, GitHub Actions cache 로 실행 간 유지)에서 재사용 → 새 문장만 번역
· 번역: Google Translate 무료 엔드포인트, 여러 문장을 줄바꿈으로 묶어 한 번에 요청
· 실패(차단·한도)하면 그 문장은 비워 두고, 화면이 /api/translate(Workers AI) 로 보충하거나 원문을 보여준다
"""
import json
import os
import re
import sys
import time

import requests

URL = "https://translate.googleapis.com/translate_a/single"
HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
MAX_CHARS = 4000          # 한 요청에 묶는 최대 글자 수
CJK = re.compile(r"[一-鿿]")

# 기계번역에서 자주 틀리는 금융 용어 보정
FIXES = [
    ("시장 매출", "시가총액"), ("시가 총액", "시가총액"), ("모든 시간 최고", "사상 최고치"), ("사상 최고", "사상 최고치"),
    ("사상 최고치치", "사상 최고치"), ("머스크 씨", "머스크"), ("마스크", "머스크"), ("연방 준비 은행", "연준"),
    ("연방준비제도이사회", "연준"), ("연방 준비 제도", "연준"), ("중간고사", "중간선거"), ("Fed의 ", "연준 "), ("Fed ", "연준 "),
]


def _fix(s):
    for a, b in FIXES:
        s = s.replace(a, b)
    return s.strip()


def translate_batch(lines):
    """줄 단위 일괄 번역. 줄 수가 어긋나면 None."""
    for wait in (4, 12, 0):                              # 429(요청 과다)는 잠깐 쉬었다 두 번 더
        r = requests.post(URL, params={"client": "gtx", "sl": "auto", "tl": "ko", "dt": "t"},
                          data={"q": "\n".join(lines)}, headers=HDR, timeout=20)
        if r.status_code != 429 or not wait:
            break
        time.sleep(wait)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    out = "".join(seg[0] for seg in r.json()[0] if seg and seg[0])
    parts = [p for p in out.split("\n")]
    if len(parts) != len(lines):
        return None
    return [_fix(p) for p in parts]


def main():
    path = sys.argv[1]
    cache_path = sys.argv[sys.argv.index("--cache") + 1] if "--cache" in sys.argv else ".cache/ko.json"
    data = json.load(open(path, encoding="utf-8"))
    try:
        cache = json.load(open(cache_path, encoding="utf-8"))
    except Exception:
        cache = {}
    # 예전에 저장된 부분 번역(한자가 많이 남은 것)은 버리고 다시 번역
    cache = {k: _fix(v) for k, v in cache.items() if len(CJK.findall(v)) <= len(v) * 0.15}

    # 번역할 문장: 대표 헤드라인 전부 + 중문 요약(앞 240자)
    wanted = []
    for t in data.get("topics", []):
        for s in t["stories"]:
            wanted.append(s["headline"].replace("\n", " ").strip())
            if s.get("summary") and CJK.search(s["summary"]):
                wanted.append(s["summary"][:240].replace("\n", " ").strip())
    todo = [w for w in dict.fromkeys(wanted) if w and w not in cache]
    print(f"[translate] 문장 {len(set(wanted))}개 중 새로 번역 {len(todo)}개 (캐시 {len(cache)}개)")

    done = failed = 0
    batch, size = [], 0
    def flush(batch):
        nonlocal done, failed
        try:
            res = translate_batch(batch)
            if res is None and len(batch) > 1:          # 줄 수가 어긋나면 반씩 나눠 재시도
                mid = len(batch) // 2
                return flush(batch[:mid]) and flush(batch[mid:])
            for src, ko in zip(batch, res or []):
                # 번역 결과에 한자가 많이 남아 있으면(부분 번역) 버리고 화면의 보조 번역에 맡김
                if ko and len(CJK.findall(ko)) <= len(ko) * 0.15:
                    cache[src] = ko; done += 1
            time.sleep(0.4)
            return True
        except Exception as ex:
            print(f"[translate] 중단: {ex}")
            failed += len(batch)
            return False
    for w in todo:
        if batch and size + len(w) > MAX_CHARS:
            if not flush(batch):
                break
            batch, size = [], 0
        batch.append(w); size += len(w) + 1
    else:
        if batch:
            flush(batch)

    for t in data.get("topics", []):
        for s in t["stories"]:
            ko = cache.get(s["headline"].replace("\n", " ").strip())
            if ko:
                s["headline_ko"] = ko
            if s.get("summary") and CJK.search(s["summary"]):
                ko = cache.get(s["summary"][:240].replace("\n", " ").strip())
                if ko:
                    s["summary_ko"] = ko
    json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))

    # 캐시는 이번에 쓴 문장 + 최근 것 위주로 최대 30,000개 유지
    keep = {w: cache[w] for w in wanted if w in cache}
    for k, v in reversed(list(cache.items())):
        if len(keep) >= 30000:
            break
        keep.setdefault(k, v)
    os.makedirs(os.path.dirname(cache_path) or ".", exist_ok=True)
    json.dump(keep, open(cache_path, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"[translate] 번역 {done}개, 실패 {failed}개")


if __name__ == "__main__":
    main()
