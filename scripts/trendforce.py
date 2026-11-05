"""
TrendForce 보도자료 + 메모리(DRAM·NAND) 가격 시계열 수집 → feeds/trendforce.json · feeds/trendforce_prices.json

  python3 -I scripts/trendforce.py --out feeds --cache .cache/ko.json

· 보도자료: https://www.trendforce.com/presscenter/news (한 쪽 5건) — 제목·날짜·링크·태그·가격 관련 여부·핵심 수치 한두 줄 요약(한국어)만 저장
    (본문 전문은 저장하지 않음. 요약은 목록에 나오는 첫 문단 중 수치가 분명한 문장 1~2개를 번역한 것, 없으면 비움)
    이미 저장한 id 는 다시 처리하지 않고(번역에 실패해 비어 있는 제목·요약만 다음 실행에서 다시 시도), 최신순 최대 200건 유지
· 가격: https://www.trendforce.com/price/dram · /price/flash 의 공개 표(DRAMeXchange 가격)를 읽어 시리즈별 시계열로 누적
    한 시리즈 키는 "구역:품목" (예 "dram_spot:DDR5 16Gb (2Gx8) 4800/5600"), 한 점은 {t(페이지의 Last Update 날짜), s(세션 라벨, 예 "2H Sep"), avg, high, low, chg}
    같은 (날짜, 세션)은 덮어쓰고 새 날짜만 추가. 과거 이력(차트)은 로그인 회원 전용이라 가져오지 않음 → 처음 실행한 날부터 쌓임
    표 구조가 바뀌어 시리즈가 0건이면 오류로 끝내고 기존 JSON 을 덮어쓰지 않음
· 예의: 정직한 User-Agent, 요청 사이 2초 간격, robots.txt 가 막는 경로(/search·/searchNews·/research/feedback/·/api/)는 쓰지 않음.
    403/429/봇 확인 화면이 나오면 우회하지 않고 즉시 멈춤(종료 코드 3)
· 번역: wscn_pdf.translate (구글 번역 + 실패 시 터미널 번역 API, 캐시 .cache/ko.json) 재사용 — 실패하면 제목·요약은 비워 둠
· 내용이 바뀐 때만 파일을 다시 씀(커밋이 30분마다 생기지 않게 updated 는 데이터가 바뀐 시각)
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wscn_pdf import translate  # noqa: E402

UA = "MiniBBLab research yidiar80k@gmail.com"      # 지정된 User-Agent 그대로 (브라우저 위장 금지)
BASE = "https://www.trendforce.com"
NEWS_URL = BASE + "/presscenter/news"
PRICE_START = [BASE + "/price/dram", BASE + "/price/flash"]    # 가격 메뉴 시작 페이지 (각각 첫 탭으로 이동)
PRICE_FAMILIES = ("/price/dram/", "/price/flash/")            # 이 아래 탭만 따라감
DELAY = 2.0                                                    # 요청 간 최소 간격(초)
PER_PAGE = 5                                                   # 보도자료 목록은 한 쪽에 5건
MAX_ITEMS = 200
MAX_PTS = 2000                                                 # 시리즈당 보관할 최대 점 수
KST = timezone(timedelta(hours=9))
SOURCE_PRICE = "TrendForce(DRAMeXchange) 공개 가격표"

_last = [0.0]


class Blocked(Exception):
    """403/429/봇 확인 — 우회하지 않고 멈춘다"""


def get(url):
    wait = DELAY - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    _last[0] = time.time()
    r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    _last[0] = time.time()
    if r.status_code in (403, 429, 503) or re.search(r"cf-chl|Just a moment|captcha|are you a human", r.text[:3000], re.I):
        raise Blocked(f"HTTP {r.status_code} · {url}")
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code} · {url}")
    if not r.url.startswith(BASE + "/"):
        raise RuntimeError(f"예상 밖 이동: {r.url}")
    return r


def kst_now():
    return datetime.now(KST)


# ───────────────────────── 보도자료 ─────────────────────────
TAGS = [
    ("HBM", r"\bHBM\d*E?\b"),
    ("DRAM", r"\bDRAM\b|\bDDR[3-6]\b|\bLPDDR|\bGDDR|\bDRAM"),
    ("NAND", r"\bNAND\b|\bSSD|\beMMC\b|\bUFS\b|\bQLC\b|(?i:flash memory)"),
    ("파운드리", r"(?i:foundr(?:y|ies))|\bTSMC\b|\bSMIC\b|\bUMC\b|\bGlobalFoundries\b|\b[2-9]nm\b|(?i:wafer fab)"),
    ("패키징", r"CoWoS|(?i:advanced packaging)|(?i:hybrid bonding)|\bSoIC\b|\bFOPLP\b|(?i:glass substrate)"),
    ("AI서버", r"(?i:AI servers?)|\bGPUs?\b|\bASICs?\b|(?i:accelerators?)|\bGB[23]00\b|\bRubin\b|\bBlackwell\b|\bNVL\d+|\bHGX\b"
              r"|(?i:AI (?:data ?centers?|infrastructure|racks?|chips?|networking|inference|training|agents?))|\bCSPs?\b|\bHyperscal"),
    ("패널", r"(?i:panels?)|\bLCDs?\b|\bOLEDs?\b|\bAMOLED\b|\bMicro ?LED|\bMini ?LED|(?i:displays?)|\bTVs?\b|(?i:monitors?)"),
    ("스마트폰", r"(?i:smartphones?)|\biPhone|(?i:foldables?)|(?i:\bphones?\b)"),
    ("전기차·배터리", r"\bEVs?\b|(?i:electric vehicles?)|(?i:batter(?:y|ies))|(?i:lithium)|\bBEV|\bNEV"),
    ("태양광", r"\bPV\b|(?i:solar)|(?i:polysilicon)|(?i:photovoltaic)"),
    ("광통신", r"(?i:optical)|\bVCSEL|(?i:transceivers?)|\bCPO\b|(?i:silicon photonics)|\b800G\b|\b1\.6T\b"),
    ("MLCC", r"\bMLCCs?\b|(?i:passive components?)"),
]
MEMORY_TAGS = {"HBM", "DRAM", "NAND"}
PRICE_TITLE = re.compile(r"\b(?:prices?|pricing|ASPs?|quotes?)\b", re.I)
PRICE_LEAD = re.compile(      # 첫 문단은 가격이 중심일 때만(순위·매출 보도의 곁다리 언급은 제외)
    r"contract prices?|spot prices?|\bprices? (?:hikes?|increases?|trends?|forecasts?|outlook)|(?:price|pricing) (?:hikes?|momentum)", re.I)
NUM_RE = re.compile(
    r"(?:US\$|NT\$|\$)\s?\d|\b\d[\d,.]*\s?(?:%|percent\b|billion\b|million\b|trillion\b|GB\b|TB\b|Gb\b|Tb\b|nm\b|units\b|wafers\b|EB\b|ppts?\b|pp\b|bps\b|GW\b|MW\b|K\b)",
    re.I)
TITLE_TAIL = re.compile(r"[,;:–—-]?\s*(?:says|say|according to|reports?|reveals?|notes?|finds?|projects?|forecasts?|states?|observes?)\s+TrendForce\s*$", re.I)


def classify(title, lead):
    text = f"{title}. {lead}"
    tags = [name for name, pat in TAGS if re.search(pat, text)]
    # 제목에 걸린 태그를 앞으로 (대표 분류가 먼저 보이게)
    tags.sort(key=lambda n: 0 if re.search(dict(TAGS)[n], title) else 1)
    if not (set(tags) & MEMORY_TAGS) and re.search(r"(?i)\bmemory\b|\bmemories\b", text):
        tags.insert(0, "메모리")
    return tags[:4]


def is_price_related(title, lead):
    return bool(PRICE_TITLE.search(title) or PRICE_LEAD.search(lead))


def key_sentences(lead):
    """첫 문단에서 수치가 분명한 문장 1~2개(합계 260자 이하)를 고른다. 없으면 빈 문자열"""
    skip = re.compile(r"(?:This|These|That|Those|As a result|Consequently|Therefore|However|Meanwhile|Additionally|Moreover)\b")   # 앞 문장이 없으면 뜻이 안 통하는 문장
    sents = re.split(r"(?<=[.!?])\s+(?=[A-Z“\"(])", re.sub(r"\s+", " ", lead).strip())
    out = []
    for s in sents:
        if NUM_RE.search(s) and len(s) <= 260 and not skip.match(s):
            if sum(len(x) for x in out) + len(s) > 260:
                break
            out.append(s)
        if len(out) == 2:
            break
    return " ".join(out)


def parse_news(html):
    s = BeautifulSoup(html, "html.parser")
    out = []
    for a in s.select("a.title-link"):
        m = re.search(r"/presscenter/news/(\d{8})-(\d+)\.html", a.get("href", ""))
        if not m:
            continue
        box = a.find_parent("div")
        p = box.find("p") if box else None
        title = re.sub(r"\s+", " ", a.get_text(" ", strip=True)).strip()
        d = m.group(1)
        out.append({"id": f"{d}-{m.group(2)}", "date": f"{d[:4]}-{d[4:6]}-{d[6:]}", "title_en": title,
                    "url": f"{BASE}/presscenter/news/{d}-{m.group(2)}.html",
                    "lead": re.sub(r"\s+", " ", p.get_text(" ", strip=True)).strip() if p else ""})
    return out


def sort_key(it):
    return (it["date"], int(it["id"].split("-")[1]))


def ko_title_src(title):
    return TITLE_TAIL.sub("", title).strip()


def run_news(out_dir, cache_path, max_items, translate_on):
    path = os.path.join(out_dir, "trendforce.json")
    try:
        old = json.load(open(path, encoding="utf-8"))
        items = old.get("items", [])
    except Exception:
        old, items = {}, []
    known = {it["id"] for it in items}
    first_run = not items
    max_pages = (max_items // PER_PAGE) + 3 if first_run else 8
    print(f"[trendforce] 보도자료: 기존 {len(items)}건, {'처음 수집(최대 ' + str(max_pages) + '쪽)' if first_run else '새 글 확인'}")

    new, seen_known, err = [], False, None
    for pg in range(1, max_pages + 1):
        try:
            got = parse_news(get(f"{NEWS_URL}?page={pg}").text)
        except Blocked:
            raise
        except Exception as ex:
            err = f"{pg}쪽 실패: {ex}"
            print(f"[trendforce] 보도자료 {err}")
            break
        if not got:
            if pg == 1:
                raise RuntimeError("보도자료 목록을 하나도 읽지 못했어요(페이지 구조가 바뀌었을 수 있어요)")
            break
        for it in got:
            if it["id"] in known or any(n["id"] == it["id"] for n in new):
                seen_known = seen_known or it["id"] in known
                continue
            new.append(it)
        if not first_run and seen_known:
            break
        if first_run and len(items) + len(new) >= max_items:
            break

    # 새 글 가공 (태그·가격 관련·요약 원문)
    for it in new:
        lead = it.pop("lead")
        it["tags"] = classify(it["title_en"], lead)
        it["price_related"] = is_price_related(it["title_en"], lead)
        it["summary_en"] = key_sentences(lead)
        it["seen"] = "" if first_run else kst_now().isoformat(timespec="seconds")      # 처음 수집(백필)한 글은 언제 올라왔는지 몰라 비움 → 화면 NEW 배지는 새로 잡은 글에만
        it["title_ko"] = ""
        it["summary_ko"] = ""
    items = sorted(items + new, key=sort_key, reverse=True)[:max_items]

    # 번역: 새 글 + 앞선 실행에서 번역이 비어 있는 글 (캐시에 있으면 즉시 채움)
    changed_tr = False
    if translate_on:
        try:
            cache = json.load(open(cache_path, encoding="utf-8"))
        except Exception:
            cache = {}
        todo = [it for it in items if not it.get("title_ko") or (it.get("summary_en") and not it.get("summary_ko"))]
        srcs = []
        for it in todo:
            if not it.get("title_ko"):
                srcs.append(ko_title_src(it["title_en"]))
            if it.get("summary_en") and not it.get("summary_ko"):
                srcs.append(it["summary_en"])
        n0 = len(cache)
        if srcs:
            translate(srcs, cache)
        for it in todo:
            if not it.get("title_ko"):
                it["title_ko"] = cache.get(ko_title_src(it["title_en"]), "")
            if it.get("summary_en") and not it.get("summary_ko"):
                it["summary_ko"] = cache.get(it["summary_en"], "")
        if len(cache) != n0:
            os.makedirs(os.path.dirname(cache_path) or ".", exist_ok=True)
            json.dump(cache, open(cache_path, "w", encoding="utf-8"), ensure_ascii=False)
        changed_tr = bool(todo)

    # 키 순서를 고정해 저장
    rows = [{"id": it["id"], "date": it["date"], "title_en": it["title_en"], "title_ko": it.get("title_ko", ""),
             "url": it["url"], "tags": it.get("tags", []), "price_related": bool(it.get("price_related")),
             "summary_ko": it.get("summary_ko", ""), "summary_en": it.get("summary_en", ""), "seen": it.get("seen", "")} for it in items]
    for it in sorted(new, key=sort_key):
        print(f"[trendforce] 새 보도자료 {it['date']} · {it['title_en']}{' [가격]' if it['price_related'] else ''}")
    n_ko = sum(1 for r in rows if r["title_ko"])
    print(f"[trendforce] 보도자료 {len(rows)}건(새 {len(new)}건, 한국어 제목 {n_ko}건, 가격 관련 {sum(r['price_related'] for r in rows)}건, 요약 {sum(1 for r in rows if r['summary_ko'])}건)")
    if not rows:
        raise RuntimeError("보도자료가 0건이라 저장하지 않아요")

    if rows == old.get("items") and not err:
        print("[trendforce] 보도자료 변동 없음 — 파일을 다시 쓰지 않아요")
        return len(new), err
    payload = {"updated": kst_now().isoformat(timespec="seconds"), "source": "TrendForce 보도자료(presscenter/news)",
               "note": "제목·날짜·링크·요약만 저장(본문 전문은 저장하지 않음). 한국어는 기계번역이고 요약은 목록 첫 문단의 수치 문장 번역이에요.",
               "items": rows}
    os.makedirs(out_dir, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(dump_news(payload))
    os.replace(tmp, path)
    return len(new), err


def dump_news(p):
    head = json.dumps({k: v for k, v in p.items() if k != "items"}, ensure_ascii=False, indent=1)[:-2]   # 마지막 "\n}" 제거
    body = ",\n".join("  " + json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in p["items"])
    return f'{head},\n "items": [\n{body}\n ]\n}}\n'


# ───────────────────────── 가격 ─────────────────────────
def num(s):
    s = (s or "").replace(",", "").strip()
    m = re.fullmatch(r"-?\d+(?:\.\d+)?", s)
    return float(s) if m else None


def pct(s):
    """'▼ -1.09 %' · '▲ 1.85 %' · '— 0.00 %' → -1.09 · 1.85 · 0.0 (표에 없으면 None)"""
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*%", s or "")
    if not m:
        return None
    v = float(m.group(1))
    if "▼" in s and v > 0:
        v = -v
    return v


def hdr_role(h):
    h = re.sub(r"\s+", " ", h.strip().lower())
    return {"session high": "high", "high": "high", "session low": "low", "low": "low",
            "session average": "avg", "average": "avg",
            "session change": "chg", "average change": "chg", "change": "chg", "low change": "lchg"}.get(h)


def parse_prices(html, page_url):
    """페이지의 div.price-content 구역마다 {sid, title, session, date, rows[]} — 숫자 표가 없는 구역(회원 전용)은 건너뜀"""
    s = BeautifulSoup(html, "html.parser")
    secs = []
    for d in s.select("div.price-content"):
        sid = d.get("id") or ""
        t = d.select_one(".price-title")
        title = re.sub(r"\s+", " ", t.get_text(" ", strip=True)).strip() if t else sid
        m = re.search(r"\(([^)]+)\)\s*$", title)
        session = m.group(1).strip() if m else ""
        title = re.sub(r"\s*\([^)]+\)\s*$", "", title)
        lu = d.select_one(".price-last-update")
        mdate = re.search(r"(\d{4}-\d{2}-\d{2})", lu.get_text(" ", strip=True)) if lu else None
        table = d.find("table")
        head = table.find("thead") if table else None
        sec = {"sid": sid, "title": title, "session": session, "date": mdate.group(1) if mdate else "", "rows": [], "url": page_url,
               "skipped": ""}
        if not (sid and head and mdate):
            sec["skipped"] = "숫자 표 없음(회원 전용) 또는 갱신일 없음"
            secs.append(sec)
            continue
        heads = [re.sub(r"\s+", " ", th.get_text(" ", strip=True)).strip() for th in head.find_all("th")]
        role = {}
        for i, h in enumerate(heads):
            r = hdr_role(h)
            if r and r not in role:
                role[r] = i
        name_cols = [i for i, h in enumerate(heads) if h.lower() in ("item", "brand", "interface", "series", "capacity")]
        if "avg" not in role or not name_cols:
            sec["skipped"] = f"열 구성을 알 수 없음({heads})"
            secs.append(sec)
            continue
        for tr in (table.find("tbody") or table).find_all("tr"):
            tds = [re.sub(r"\s+", " ", td.get_text(" ", strip=True)).strip() for td in tr.find_all("td")]
            name = " ".join(tds[i] for i in name_cols if i < len(tds) and tds[i]).strip()
            try:
                avg = num(tds[role["avg"]])
            except IndexError:
                avg = None
            if not name or avg is None:
                continue
            row = {"name": name, "avg": avg}
            for k in ("high", "low"):
                if k in role and role[k] < len(tds):
                    row[k] = num(tds[role[k]])
            for k in ("chg", "lchg"):
                if k in role and role[k] < len(tds):
                    row[k] = pct(tds[role[k]])
            sec["rows"].append(row)
        secs.append(sec)
    tabs = []
    for li in s.select("[data-changetab]"):
        a = li.find("a", href=True)
        if a:
            tabs.append((li.get("data-changetab"), a["href"]))
    return secs, tabs


def fetch_price_sections():
    """가격 메뉴 시작 페이지 → 같은 HTML 에 없는 탭만 추가로 받아 구역 전체를 모은다"""
    got, urls = {}, []

    def add(url):
        r = get(url)
        secs, tabs = parse_prices(r.text, r.url)
        urls.append(r.url)
        for sec in secs:
            got.setdefault(sec["sid"], sec)
        return tabs

    for start in PRICE_START:
        tabs = add(start)
        for sid, href in tabs:
            if sid in got:
                continue
            url = href if href.startswith("http") else BASE + href
            if not url.startswith(tuple(BASE + p for p in PRICE_FAMILIES)):
                continue
            add(url)
    return list(got.values()), urls


def merge_prices(old_series, secs):
    """구역·품목마다 점을 더하거나(새 날짜·세션) 덮어쓴다(같은 날짜·세션). 반환: 새 시리즈 dict, 새 점 수, 바뀐 점 수"""
    series = {k: dict(v, pts=[dict(p) for p in v.get("pts", [])]) for k, v in old_series.items()}
    n_new = n_upd = 0
    for sec in secs:
        for r in sec["rows"]:
            key = f"{sec['sid']}:{r['name']}"
            s = series.setdefault(key, {"name": r["name"], "group": sec["sid"], "title": sec["title"], "unit": "USD",
                                        "url": sec["url"], "pts": []})
            s["title"], s["url"] = sec["title"], sec["url"]
            pt = {"t": sec["date"], "s": sec["session"], "avg": r["avg"]}
            for k in ("high", "low", "chg"):
                if r.get(k) is not None:
                    pt[k] = r[k]
            idx = next((i for i, p in enumerate(s["pts"]) if p["t"] == pt["t"] and p.get("s", "") == pt["s"]), None)
            if idx is None:
                s["pts"].append(pt)
                n_new += 1
            elif s["pts"][idx] != pt:
                s["pts"][idx] = pt
                n_upd += 1
            s["pts"].sort(key=lambda p: (p["t"], p.get("s", "")))
            s["pts"] = s["pts"][-MAX_PTS:]
    return series, n_new, n_upd


def dump_prices(p):
    head = json.dumps({k: v for k, v in p.items() if k != "series"}, ensure_ascii=False, indent=1)[:-2]
    blocks = []
    for key, s in p["series"].items():
        meta = json.dumps({k: v for k, v in s.items() if k != "pts"}, ensure_ascii=False, separators=(",", ":"))[:-1]
        pts = ",\n".join("    " + json.dumps(pt, ensure_ascii=False, separators=(",", ":")) for pt in s["pts"])
        blocks.append(f'  {json.dumps(key, ensure_ascii=False)}: {meta},"pts":[\n{pts}\n  ]}}')
    return f'{head},\n "series": {{\n' + ",\n".join(blocks) + "\n }\n}\n"


def run_prices(out_dir):
    path = os.path.join(out_dir, "trendforce_prices.json")
    try:
        old = json.load(open(path, encoding="utf-8"))
    except Exception:
        old = {}
    secs, urls = fetch_price_sections()
    for sec in secs:
        if sec["skipped"]:
            print(f"[trendforce] 가격 구역 건너뜀 {sec['sid']}: {sec['skipped']}")
        else:
            print(f"[trendforce] 가격 구역 {sec['sid']} ({sec['title']}{' · ' + sec['session'] if sec['session'] else ''}) "
                  f"갱신일 {sec['date']} · 품목 {len(sec['rows'])}개")
    n_rows = sum(len(sec["rows"]) for sec in secs)
    if n_rows == 0:
        raise RuntimeError("가격 표를 하나도 읽지 못했어요(페이지 구조가 바뀌었을 수 있어요) — 기존 JSON 을 그대로 둡니다")
    series, n_new, n_upd = merge_prices(old.get("series", {}), secs)
    n_pts = sum(len(s["pts"]) for s in series.values())
    print(f"[trendforce] 가격: 이번에 읽은 시리즈·행 {n_rows}개 → 누적 시리즈 {len(series)}개·점 {n_pts}개 "
          f"(새 점 {n_new}, 덮어쓴 점 {n_upd})")
    if series == old.get("series"):
        print("[trendforce] 가격 변동 없음 — 파일을 다시 쓰지 않아요")
        return n_new, n_upd
    payload = {"updated": kst_now().isoformat(timespec="seconds"), "source": SOURCE_PRICE,
               "note": "공식 데이터가 아니며 지연될 수 있어요. 점의 날짜는 페이지의 Last Update 날짜, s 는 계약가 세션(예 2H Sep).",
               "series": series}
    os.makedirs(out_dir, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(dump_prices(payload))
    os.replace(tmp, path)
    return n_new, n_upd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="feeds")
    ap.add_argument("--cache", default=".cache/ko.json")
    ap.add_argument("--max-items", type=int, default=MAX_ITEMS)
    ap.add_argument("--only", choices=("news", "prices"), help="한쪽만 실행(시험용)")
    ap.add_argument("--no-translate", action="store_true")
    a = ap.parse_args()
    rc = 0
    try:
        if a.only != "prices":
            try:
                run_news(a.out, a.cache, a.max_items, not a.no_translate)
            except Blocked:
                raise
            except Exception as ex:
                print(f"[trendforce] 보도자료 오류: {ex}")
                rc = 1
        if a.only != "news":
            try:
                run_prices(a.out)
            except Blocked:
                raise
            except Exception as ex:
                print(f"[trendforce] 가격 오류: {ex}")
                rc = 1
    except Blocked as ex:
        print(f"[trendforce] 차단 응답을 받아 멈춰요(우회하지 않아요): {ex}")
        sys.exit(3)
    sys.exit(rc)


if __name__ == "__main__":
    main()
