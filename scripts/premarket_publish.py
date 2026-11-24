"""미국 개장전 이슈체크 — 받은 메일(PDF 첨부)을 터미널 Breaking! 박스용 자료로 올린다.

  python scripts/premarket_publish.py --pdf 개장전_섹터별_이슈체크_2026-11-24.pdf --date 2026-11-24 [--body mail.txt] [--dry-run]

흐름
  1) PDF 각 쪽을 JPEG(가로 900px)로 바꾼다 (poppler 의 pdftoppm 필요)
  2) premarket.json 을 만든다: 게시 시각(지금)·내림 시각(날짜 다음 날 01:00 KST)·쪽 이미지·출처 링크
  3) 저장소의 'premarket-feed' 가지에 '그 하루치만' 새 커밋으로 강제 푸시한다 (기록이 쌓이지 않음 → 저장소가 커지지 않고,
     다음 날 올리면 어제 것은 사라짐). 터미널은 내림 시각이 지나면 스스로 숨긴다 (templates/news.html 의 Breaking!)
  · 같은 날짜를 다시 올리면(재시도) 아무것도 바꾸지 않고 끝낸다 (--force 로 덮어쓰기)
"""
import argparse
import datetime as dt
import glob
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRANCH = "premarket-feed"
RAW = f"https://raw.githubusercontent.com/kayJo81/mini-bb-public/{BRANCH}/premarket.json"
KST = dt.timezone(dt.timedelta(hours=9))
WD = "월화수목금토일"


def run(*a, cwd=None):
    return subprocess.run(a, cwd=cwd, check=True, capture_output=True, text=True).stdout


def sources(body):
    out = []
    for m in re.finditer(r"^[·•\-]\s*(.+?):\s*(https?://\S+)\s*$", body or "", flags=re.M):
        out.append({"t": m.group(1).strip()[:120], "u": m.group(2)})
    return out[:60]


def current_date():
    try:
        with urllib.request.urlopen(RAW + f"?t={int(dt.datetime.now().timestamp())}", timeout=20) as r:
            return json.load(r).get("date")
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--date", required=True, help="이슈체크 날짜 YYYY-MM-DD (메일 제목의 날짜)")
    ap.add_argument("--body", help="메일 본문 텍스트 파일(출처 링크 추출용, 선택)")
    ap.add_argument("--dry-run", action="store_true", help="이미지·JSON 만 만들고 푸시하지 않음")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--out", help="--dry-run 결과 폴더")
    a = ap.parse_args()
    d = dt.date.fromisoformat(a.date)
    if not a.dry_run and not a.force and current_date() == a.date:
        print(f"[premarket] {a.date} 이미 올라가 있음 — 그대로 둠")
        return
    work = a.out or tempfile.mkdtemp(prefix="premarket_")
    os.makedirs(work, exist_ok=True)
    for f in glob.glob(os.path.join(work, "p*.jpg")):
        os.remove(f)
    run("pdftoppm", "-jpeg", "-jpegopt", "quality=82", "-scale-to-x", "900", "-scale-to-y", "-1", a.pdf, os.path.join(work, "p"))
    pages = sorted(os.path.basename(f) for f in glob.glob(os.path.join(work, "p*.jpg")))
    if not pages:
        sys.exit("PDF 에서 쪽 이미지를 만들지 못함")
    body = open(a.body, encoding="utf-8").read() if a.body else ""
    now = dt.datetime.now(KST).replace(microsecond=0)
    expire = dt.datetime.combine(d + dt.timedelta(days=1), dt.time(1, 0), tzinfo=KST)
    feed = {"date": a.date, "title": "미국 개장전 이슈체크", "label": f"{d.month}/{d.day}({WD[d.weekday()]})",
            "posted_at": now.isoformat(), "expire_at": expire.isoformat(), "pages": pages, "sources": sources(body)}
    if now >= expire:
        print(f"[premarket] 내림 시각({expire:%m-%d %H:%M})이 이미 지남 — 올리지 않음")
        return
    json.dump(feed, open(os.path.join(work, "premarket.json"), "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    if a.dry_run:
        print(f"[premarket] dry-run: {len(pages)}쪽 → {work}")
        return
    shutil_pdf = os.path.join(work, "issue-check.pdf")
    with open(a.pdf, "rb") as s, open(shutil_pdf, "wb") as t:
        t.write(s.read())
    remote = os.environ.get("PREMARKET_REMOTE") or run("git", "remote", "get-url", "origin", cwd=ROOT).strip()   # Actions 에서는 토큰 포함 주소를 넘김
    g = tempfile.mkdtemp(prefix="pmgit_")
    run("git", "init", "-q", "-b", BRANCH, cwd=g)
    for f in os.listdir(work):
        os.replace(os.path.join(work, f), os.path.join(g, f))
    run("git", "add", "-A", cwd=g)
    run("git", "-c", "user.name=mini-bb", "-c", "user.email=noreply@anthropic.com", "commit", "-q", "-m",
        f"미국 개장전 이슈체크 {a.date} (하루치만 유지)\n\nCo-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>", cwd=g)
    run("git", "push", "-q", "--force", remote, f"{BRANCH}:{BRANCH}", cwd=g)
    print(f"[premarket] {a.date} {len(pages)}쪽 게시 · 내림 {expire:%m-%d %H:%M} KST")


if __name__ == "__main__":
    main()
