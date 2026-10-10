"""
미니 블벅 랩(뉴스 기반 롱/숏 탐색) 공통 설정

  · 종목: 미니 블벅터 추적 종목 59개 (news_scanner.TOPICS 의 tickers)
  · 뉴스: Finnhub company-news (1년 과거치까지 같은 출처로 일관되게) → 제목에 회사명/티커가 있는 기사만
  · 논조: 금융 헤드라인용 단어 사전 (+1 호재 단어, -1 악재 단어) — 단순하지만 과거·현재에 똑같이 적용
  · 날짜: 미국 동부시간 16:00(장 마감) 이후 기사는 다음 거래일로 넘긴다 (장중에 알 수 있었던 뉴스만 그날 신호)
"""
import os
import re
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
DATA = os.environ.get("LAB_DATA", os.path.join(ROOT, "labdata"))
BENCH = "QQQ"          # 초과수익 기준 (나스닥100)

# 회사명 별칭 (제목에서 대소문자 무시) — 티커 자체는 대문자 그대로일 때만 인정
ALIASES = {
    "TSLA": r"tesla", "GM": r"general motors", "F": r"\bford\b", "STLA": r"stellantis", "RIVN": r"rivian",
    "LCID": r"lucid", "ISRG": r"intuitive surgical", "SYM": r"symbotic", "TER": r"teradyne",
    "ROK": r"rockwell automation", "SERV": r"serve robotics", "AUR": r"aurora innovation|aurora's|aurora\b",
    "MBLY": r"mobileye", "RKLB": r"rocket lab", "ASTS": r"ast spacemobile", "LUNR": r"intuitive machines",
    "RDW": r"redwire", "PL": r"planet labs", "IRDM": r"iridium", "ALB": r"albemarle", "QS": r"quantumscape",
    "FLNC": r"fluence energy|fluence\b", "ENVX": r"enovix", "SMR": r"nuscale", "OKLO": r"\boklo\b",
    "NNE": r"nano nuclear", "CEG": r"constellation energy", "BWXT": r"bwx", "CCJ": r"cameco", "VST": r"vistra",
    "NVDA": r"nvidia", "AAPL": r"\bapple\b|iphone", "GOOGL": r"alphabet|google", "MSFT": r"microsoft",
    "AMZN": r"amazon|\baws\b", "META": r"\bmeta\b|facebook|instagram|zuckerberg", "TSM": r"tsmc|taiwan semi",
    "INTC": r"\bintel\b", "AMD": r"\bamd\b|advanced micro", "MU": r"micron", "SNDK": r"sandisk",
    "AVGO": r"broadcom", "QCOM": r"qualcomm", "MRVL": r"marvell", "ARM": r"\barm holdings|\barm\b",
    "ASML": r"\basml\b", "AMAT": r"applied materials", "LRCX": r"lam research", "KLAC": r"\bkla\b",
    "CRWV": r"coreweave", "NBIS": r"nebius", "IREN": r"\biren\b", "APLD": r"applied digital", "VRT": r"vertiv",
    "SMCI": r"super ?micro", "DELL": r"\bdell\b", "ORCL": r"oracle", "COHR": r"\bcoherent\b", "LITE": r"lumentum",
}

POS = re.compile(
    r"\b(beat|beats|tops|top estimates|surge[sd]?|soar(s|ed)?|jump(s|ed)?|rall(y|ies|ied)|climb(s|ed)?|gain(s|ed)?|"
    r"record|upgrade[sd]?|raise[sd]? (guidance|outlook|forecast|price target|target)|boost(s|ed)?|strong|"
    r"outperform|overweight|buy rating|wins?|won|award(s|ed)?|approv(al|es|ed)|deal|partnership|contract|"
    r"expand(s|ed)?|buyback|repurchase|dividend (hike|increase)|breakthrough|launch(es|ed)?|demand)\b", re.I)
NEG = re.compile(
    r"\b(miss(es|ed)?|fall(s|en)?|fell|drop(s|ped)?|plunge[sd]?|slump(s|ed)?|sink(s)?|sank|tumble[sd]?|"
    r"slide[sd]?|decline[sd]?|downgrade[sd]?|cut(s)? (guidance|outlook|forecast|price target|target|rating)|"
    r"lower(s|ed)? (guidance|outlook|forecast|target)|weak|warn(s|ed|ing)?|loss(es)?|lawsuit|sue[sd]?|probe|"
    r"investigation|recall(s|ed)?|delay(s|ed)?|halt(s|ed)?|ban(s|ned)?|layoffs?|job cuts|underperform|"
    r"sell rating|short seller|fraud|bankrupt\w*|default|resign(s|ed)?|tariffs?|export curbs?|antitrust)\b", re.I)
# 종목 추천·순위·광고성 글은 신호에서 제외 (특정 사건이 아님)
NOISE = re.compile(r"\b(stocks? to (buy|watch)|top \d+|\d+ (stocks|reasons)|should you|is it time|better buy|"
                   r"vs\.? |prediction|could make you|millionaire|what to expect|earnings preview|options? (activity|volume))\b",
                   re.I)

_ALIAS_RE = {t: re.compile(a, re.I) for t, a in ALIASES.items()}
_SYM_RE = {t: re.compile(rf"(?<![A-Za-z$])\$?{t}(?![A-Za-z])") for t in ALIASES if len(t) >= 3}


def mentions(ticker, headline):
    return bool(_ALIAS_RE[ticker].search(headline) or (ticker in _SYM_RE and _SYM_RE[ticker].search(headline)))


def score(headline):
    return len(POS.findall(headline)) - len(NEG.findall(headline))


def tickers():
    from news_scanner import TOPICS
    out = []
    for t in TOPICS:
        for s in t["tickers"]:
            if s not in out:
                out.append(s)
    return out


def _et_offset(dt_utc):
    """미국 동부시간 오프셋 (서머타임: 3월 둘째 일요일 ~ 11월 첫째 일요일)"""
    y = dt_utc.year
    mar = datetime(y, 3, 8, 7, tzinfo=timezone.utc)
    start = mar + timedelta(days=(6 - mar.weekday()) % 7)
    nov = datetime(y, 11, 1, 6, tzinfo=timezone.utc)
    end = nov + timedelta(days=(6 - nov.weekday()) % 7)
    return timedelta(hours=-4 if start <= dt_utc < end else -5)


def session_date(ts):
    """기사 시각 → 신호에 쓰일 날짜 (동부시간 16:00 이후면 다음 날). 주말·휴일은 나중에 다음 거래일로 맞춘다"""
    u = datetime.fromtimestamp(ts, timezone.utc)
    et = u + _et_offset(u)
    d = et.date()
    if et.hour >= 16:
        d += timedelta(days=1)
    return d.isoformat()
