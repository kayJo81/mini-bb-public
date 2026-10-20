"""
터미널 '⚡ 속보' 화면용 실시간 속보 모음 → briefings/flash.json (15분 수집마다)

  python scripts/flash_feed.py --out public/briefings/flash.json --fj-store .cache/fj.json --cache .cache/ko.json

  · FinancialJuice: 수집마다 누적한 목록(fj_pdf.py --accumulate, 최대 30시간)
  · 월스트리트견문(华尔街见闻) 요문 채널: 직전 24시간 (사이트의 빨간 '중요' 표시 = score 2 이상)
  · 시나재경(新浪财经) 7x24: 직전 24시간 — A주 '회사' 태그만 붙은 공시성 속보는 뺌, '焦点(포커스)' 태그는 중요 표시
  · 중문·영문 모두 한국어 번역(translate_ko 와 같은 캐시), 최신분은 화면이 30초마다 직접(또는 api/sina) 받아 보충
"""
import argparse
import html
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wscn_pdf import translate  # noqa: E402

HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
WSCN = "https://api-one.wallstcn.com/apiv1/content/lives?channel=global-channel&limit=100"
SINA = "https://zhibo.sina.com.cn/api/zhibo/feed"
CN_HOURS, FJ_HOURS = 24, 30
CST = timezone(timedelta(hours=8))
TAG_KO = {"焦点": "포커스", "国际": "국제", "宏观": "거시", "市场": "시장", "央行": "중앙은행", "数据": "지표", "观点": "의견",
          "行业": "산업", "A股": "A주", "公司": "기업", "其他": "기타"}


def _clean(s, n=160):
    s = re.sub(r"<[^>]+>", " ", html.unescape(s or ""))
    return re.sub(r"\s+", " ", s).strip()[:n]


def wscn(cut):
    out, cursor = [], None
    for _ in range(12):
        try:
            d = requests.get(WSCN + (f"&cursor={cursor}" if cursor else ""), headers=HDR, timeout=20).json().get("data") or {}
        except Exception as ex:
            print(f"[flash] 월스트리트견문: {ex}")
            break
        items = d.get("items") or []
        for it in items:
            ts = int(it.get("display_time") or 0)
            h = _clean(it.get("title") or it.get("content_text"))
            if ts >= cut and h:
                out.append({"ts": ts, "h": h, "u": it.get("uri") or "https://wallstreetcn.com/live", "s": "wscn",
                            "imp": int(it.get("score") or 1) >= 2})
        cursor = d.get("next_cursor")
        if not items or not cursor or min(int(i.get("display_time") or 0) for i in items) < cut:
            break
    return out


def sina(cut):
    out = []
    for page in range(1, 40):
        try:
            d = requests.get(SINA, params={"page": page, "page_size": 100, "zhibo_id": 152, "tag_id": 0, "dire": "f", "dpc": 1, "type": 0},
                             headers=HDR, timeout=20).json()["result"]["data"]["feed"]["list"]
        except Exception as ex:
            print(f"[flash] 시나재경: {ex}")
            break
        old = False
        for it in d:
            try:
                ts = int(datetime.strptime(it["create_time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=CST).timestamp())
            except Exception:
                continue
            if ts < cut:
                old = True
                continue
            tags = [t.get("name") for t in it.get("tag") or []]
            if tags and set(tags) <= {"公司"}:
                continue
            text = _clean(it.get("rich_text"), 400)
            m = re.match(r"【([^】]{4,})】", text)
            h = (m.group(1) if m else text)[:160]
            if h:
                out.append({"ts": ts, "h": h, "u": it.get("docurl") or "https://finance.sina.com.cn/7x24/", "s": "sina",
                            "imp": "焦点" in tags, "tag": next((TAG_KO.get(t, t) for t in tags if t != "公司"), "")})
        if not d or old:
            break
    return out


def build(out_path, fj_store, cache_path):
    now = time.time()
    try:
        fj = json.load(open(fj_store, encoding="utf-8"))
    except Exception:
        fj = []
    items = [{"ts": x["ts"], "h": x["headline"], "u": x.get("url") or "", "s": "fj"} for x in fj if x["ts"] >= now - FJ_HOURS * 3600]
    w, s = wscn(now - CN_HOURS * 3600), sina(now - CN_HOURS * 3600)
    items += w + s
    seen, uniq = set(), []
    for x in sorted(items, key=lambda x: -x["ts"]):
        k = (x["s"], x["h"])
        if k not in seen:
            seen.add(k)
            uniq.append(x)
    try:
        cache = json.load(open(cache_path, encoding="utf-8"))
    except Exception:
        cache = {}
    translate([x["h"] for x in uniq], cache)
    os.makedirs(os.path.dirname(cache_path) or ".", exist_ok=True)
    json.dump(cache, open(cache_path, "w", encoding="utf-8"), ensure_ascii=False)
    for x in uniq:
        if cache.get(x["h"]):
            x["ko"] = cache[x["h"]]
        if not x.get("imp"):
            x.pop("imp", None)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    json.dump({"at": int(now), "items": uniq}, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    n = {k: sum(1 for x in uniq if x["s"] == k) for k in ("fj", "wscn", "sina")}
    print(f"[flash] FinancialJuice {n['fj']} · 월스트리트견문 {n['wscn']} · 시나재경 {n['sina']} · 번역 {sum(1 for x in uniq if x.get('ko'))}/{len(uniq)} → {out_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="public/briefings/flash.json")
    ap.add_argument("--fj-store", default=".cache/fj.json")
    ap.add_argument("--cache", default=".cache/ko.json")
    a = ap.parse_args()
    build(a.out, a.fj_store, a.cache)
