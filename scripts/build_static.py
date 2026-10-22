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
from market_moves import build_moves, merge_archive, session_window  # noqa: E402
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
    # 시장을 움직인 뉴스: 정규장 중엔 전일 종가 이후(지수 전일비 기준), 장 마감 뒤엔 마지막 종가 이후(선물) — 실패하면 12시간
    #   뉴스는 이번 수집(14시간)에 지난 실행분 기억(MOVES_NEWS, 72시간)을 합쳐 앞선 구간에도 붙임
    try:
        e_ts = int(end.timestamp())
        s_ts, mode = session_window(e_ts) or (e_ts - WINDOW_HOURS * 3600, "window")
        topics = merge_archive(data.get("topics", []), os.environ.get("MOVES_NEWS"))
        data["moves"] = build_moves(data, s_ts, e_ts, topics, mode)
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
    # 미국 당일 테마 화면 (데이터: briefings/us_themes.json·options_live.json — briefing.yml 의 뒤 단계에서 만듦)
    with open(os.path.join(ROOT, "templates", "themes.html"), encoding="utf-8") as f:
        themes_html = f.read()
    with open(os.path.join(args.out, "themes.html"), "w", encoding="utf-8") as f:
        f.write(themes_html)
    # 랩(mini-bblab) 화면이 장중 옵션 수급 등을 가져갈 수 있게 briefings/ 는 다른 사이트에서도 읽기 허용
    with open(os.path.join(args.out, "_headers"), "w", encoding="utf-8") as f:
        f.write("/briefings/*\n  Access-Control-Allow-Origin: *\n"
                # 기본 보안 헤더: 다른 사이트가 이 화면을 몰래 틀 안에 넣지 못하게, 파일 형식 추측 금지, 주소 유출 최소화
                "/*\n  X-Frame-Options: SAMEORIGIN\n  X-Content-Type-Options: nosniff\n"
                "  Referrer-Policy: strict-origin-when-cross-origin\n")
    print(f"[build] {start:%m-%d %H:%M} ~ {end:%m-%d %H:%M} KST 수집 → {args.out}/")


if __name__ == "__main__":
    main()
