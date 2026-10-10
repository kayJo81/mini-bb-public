"""
미니 블벅터 정적 사이트 빌드 (GitHub Actions에서 1시간마다 실행)

  python scripts/build_static.py --out public

  · 직전 LIVE_SCAN_HOURS(14h) 뉴스를 수집해 briefings/live.json 으로 저장
  · 화면(index.html)은 조회하는 순간 기준 12시간 이내 보도만 남겨 보여준다
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from elections import build_midterms  # noqa: E402
from market_moves import build_moves  # noqa: E402
from news_scanner import LIVE_SCAN_HOURS, WINDOW_HOURS, build_briefing, live_window  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="public")
    args = ap.parse_args()

    token = os.environ.get("FINNHUB_KEY", "")
    if not token:
        print("[build] FINNHUB_KEY 없음 — Google News만으로 스캔합니다")

    out_b = os.path.join(args.out, "briefings")
    os.makedirs(out_b, exist_ok=True)
    start, end = live_window(hours=LIVE_SCAN_HOURS)
    data = build_briefing(start, end, "live", token)
    # 시장을 움직인 뉴스: 화면과 같은 '수집 시각 기준 12시간'
    try:
        data["moves"] = build_moves(data, int(end.timestamp()) - WINDOW_HOURS * 3600, int(end.timestamp()))
    except Exception as ex:
        print(f"[build] moves 실패: {ex}")
    # 미국 중간선거: 여론조사 평균 + 예측시장 확률
    try:
        data["midterms"] = build_midterms()
    except Exception as ex:
        print(f"[build] midterms 실패: {ex}")
    with open(os.path.join(out_b, "live.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))

    with open(os.path.join(ROOT, "templates", "news.html"), encoding="utf-8") as f:
        html = f.read()
    html = html.replace("<script>", "<script>window.MBB_STATIC=true;</script>\n<script>", 1)
    with open(os.path.join(args.out, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[build] {start:%m-%d %H:%M} ~ {end:%m-%d %H:%M} KST 수집 → {args.out}/")


if __name__ == "__main__":
    main()
