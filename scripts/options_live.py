"""
장중 옵션 수급 (15분마다 briefing.yml) → public/briefings/options_live.json

  python scripts/options_live.py --out public/briefings/options_live.json --store .cache/opt_intraday.json

  · CBOE 지연 시세(약 15분 지연) 옵션 전체 체인 — 계산은 랩 시장 환경 기록(lab/env_snapshot.options)과 같음
    풋/콜 거래량·미결제약정 비율, IV30·ATM IV, 25델타 스큐, 감마 추정, 콜월·풋월
  · 미국 정규장(09:30~16:00 ET)과 마감 직후 30분에만 새로 받는다. 그 밖에는 마지막 값을 그대로 둔다
  · 장중 흐름: 그날 받은 값들을 store 에 모아 종목별 시간대 흐름으로 함께 저장 (다음 거래일이 되면 새로 시작)
  · 시장 전체 풋/콜 비율(CBOE 일일 통계)도 함께
"""
import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lab"))
from env_snapshot import cboe_ratios, options  # noqa: E402

ET, KST = ZoneInfo("America/New_York"), ZoneInfo("Asia/Seoul")
SYMS = ["_SPX", "SPY", "QQQ", "IWM", "NVDA", "TSLA", "AAPL", "MSFT", "AMZN", "META", "GOOGL", "AVGO", "AMD", "MU", "PLTR",
        "COIN", "MSTR", "SMH"]
KEYS = ("price", "pc_vol", "pc_oi", "iv30", "atm_iv30", "skew25", "gex_musd", "call_wall", "put_wall", "call_vol", "put_vol")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="public/briefings/options_live.json")
    ap.add_argument("--store", default=".cache/opt_intraday.json")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    now = datetime.now(ET)
    mins = now.hour * 60 + now.minute
    session = now.weekday() < 5 and 9 * 60 + 30 <= mins <= 16 * 60 + 30
    try:
        store = json.load(open(a.store, encoding="utf-8"))
    except Exception:
        store = {}
    if not session and not a.force and store:
        print(f"[options_live] 장 시간 아님 ({now:%a %H:%M} ET) — 마지막 값 유지")
    else:
        with ThreadPoolExecutor(6) as pool:
            fut = {s: pool.submit(options, s) for s in SYMS}
            rat = pool.submit(cboe_ratios)
            snap, errs = {}, []
            for s, f in fut.items():
                try:
                    snap[s.lstrip("_")] = {k: f.result().get(k) for k in KEYS + ("asof",)}
                except Exception as ex:
                    errs.append(s)
                    print(f"[options_live] {s}: {ex}")
            try:
                ratios = rat.result()
            except Exception:
                ratios = {}
        day = (snap.get("SPX") or {}).get("asof") or now.date().isoformat()
        if store.get("date") != day:
            store = {"date": day, "points": []}
        t = now.strftime("%H:%M")
        store["points"] = [p for p in store["points"] if p["t"] != t] + [{"t": t, "kst": datetime.now(KST).strftime("%H:%M"),
                                                                       "s": snap, "pc": ratios}]
        store["errors"] = errs
        os.makedirs(os.path.dirname(a.store) or ".", exist_ok=True)
        json.dump(store, open(a.store, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
        print(f"[options_live] {day} {t} ET · {len(snap)}종목 · 기록 {len(store['points'])}회" + (f" · 실패 {errs}" if errs else ""))
    if not store:
        return
    pts = store.get("points") or []
    latest = pts[-1] if pts else {}
    series = {s: {k: [p["s"].get(s, {}).get(k) for p in pts] for k in ("price", "pc_vol", "atm_iv30", "skew25", "gex_musd")}
              for s in (latest.get("s") or {})}
    out = {"date": store.get("date"), "at": datetime.now(KST).isoformat(timespec="minutes"), "session": session,
           "times": [p["kst"] for p in pts], "times_et": [p["t"] for p in pts], "latest": latest.get("s") or {},
           "pc": latest.get("pc") or {}, "series": series}
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    json.dump(out, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))


if __name__ == "__main__":
    main()
