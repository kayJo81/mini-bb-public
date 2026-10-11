"""
미니 블벅터 (Mini Bloomberg Terminal) — 테마별 뉴스 스캐너 + 팩트 체크

검색 창(window)
  · 조회 시점 기준 직전 12시간. 수집은 갱신 주기만큼 여유를 둔 창(LIVE_SCAN_HOURS)으로 하고,
    화면에서 조회하는 순간 기준 12시간 이내 보도만 남긴다.

수집 소스
  · Google News RSS 검색 (US 에디션 + UK/유럽 에디션, after:/before: 날짜 연산자)
  · Finnhub company-news (티커별)

팩트 체크 (모든 기사에 필수 적용)
  1) 동일 사건 클러스터링: 헤드라인 토큰 유사도로 같은 사건을 묶는다.
  2) 출처 신뢰도 등급: official(회사 IR/보도자료/규제기관) > tier1(통신사·메이저 경제지)
                       > tier2(전문지·대형 매체) > tier3(블로그·투자 의견 사이트)
  3) 교차 검증: 독립 매체 수와 등급으로 판정
       VERIFIED   — 공식 발표 포함, 또는 독립 매체 2곳 이상(그 중 tier1/2 최소 1곳)
       SINGLE     — tier1/2 단독 보도 (추가 확인 필요)
       UNVERIFIED — tier3 단독, 또는 익명 소식통·루머 표현만 있고 공식 확인 없음
  4) 의견/분석 기사(“Should you buy…”, Motley Fool 등)는 사실 보도와 분리 표시
"""
import json
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus, urlparse
from zoneinfo import ZoneInfo

import requests

KST = ZoneInfo("Asia/Seoul")
WINDOW_HOURS    = 12          # 조회 시점 기준 12시간
LIVE_REFRESH_SEC = 15 * 60    # 실시간 창 갱신 주기 (Flask 서버 모드)
LIVE_SCAN_HOURS  = 14         # 정적 배포: 1시간마다 갱신 + 지연 여유 → 화면에서 12h로 다시 자름

GNEWS_URL = "https://news.google.com/rss/search?q={q}&hl={hl}&gl={gl}&ceid={ceid}"
EDITIONS = {
    "US": {"hl": "en-US", "gl": "US", "ceid": "US:en"},
    "EU": {"hl": "en-GB", "gl": "GB", "ceid": "GB:en"},   # 유럽 영문 뉴스
    "KR": {"hl": "ko", "gl": "KR", "ceid": "KR:ko"},       # 한국 증시용(kr_news.py)
}
HTTP_HDR = {"User-Agent": "Mozilla/5.0 (compatible; MiniBBTerminal/1.0)"}


# ─────────────────────────────────────────────────────────────────────
# 검색 대상 (토픽 정의)
#   queries  : (Google News 검색어, 에디션)
#   tickers  : Finnhub company-news 대상 티커
#   keywords : 헤드라인/요약에 하나 이상 포함돼야 관련 기사로 인정 (정규식, 대소문자 무시)
# ─────────────────────────────────────────────────────────────────────
TOPICS = [
    # 1) 모빌리티 & 에너지 ------------------------------------------------
    {"id": "tesla", "group": "모빌리티 & 에너지", "name": "테슬라",
     "queries": [("Tesla", "US"), ("Tesla", "EU")],
     "tickers": ["TSLA"],
     "keywords": [r"tesla", r"musk", r"cybertruck", r"robotaxi", r"optimus", r"\bfsd\b", r"cybercab"]},
    {"id": "auto", "group": "모빌리티 & 에너지", "name": "자동차 (미국·유럽)",
     "queries": [('automaker OR "auto industry" OR "car sales"', "US"),
                 ("GM OR Ford OR Stellantis OR Rivian OR Lucid", "US"),
                 ("Volkswagen OR BMW OR Mercedes-Benz OR Renault OR Stellantis", "EU"),
                 ('"car market" Europe OR ACEA OR "EU car"', "EU")],
     "tickers": ["GM", "F", "STLA", "RIVN", "LCID"],
     "keywords": [r"auto", r"car\b", r"cars\b", r"vehicle", r"\bev\b", r"\bevs\b", r"\bgm\b", r"general motors",
                  r"\bford\b", r"stellantis", r"rivian", r"lucid", r"volkswagen", r"\bvw\b", r"\bbmw\b",
                  r"mercedes", r"renault", r"porsche", r"volvo", r"toyota", r"hyundai", r"\bbyd\b", r"acea",
                  r"tariff"]},
    {"id": "robotics", "group": "모빌리티 & 에너지", "name": "로보틱스 (휴머노이드·자율주행)",
     "queries": [('humanoid robot OR "Figure AI" OR "Boston Dynamics" OR "Agility Robotics" OR Apptronik OR Unitree', "US"),
                 ('robotaxi OR Waymo OR Zoox OR "autonomous driving" OR "self-driving"', "US"),
                 ('"physical AI" OR "industrial robot" OR robotics startup OR "Intuitive Surgical" OR Symbotic', "US"),
                 ('humanoid robot OR robotics OR robotaxi OR Wayve OR "Neura Robotics"', "EU")],
     "tickers": ["ISRG", "SYM", "TER", "ROK", "SERV", "AUR", "MBLY"],
     "keywords": [r"robot", r"humanoid", r"robotaxi", r"self-driving", r"driverless",
                  r"autonomous (?:vehicle|driving|truck|car)", r"waymo", r"zoox", r"aurora innovation", r"mobileye",
                  r"wayve", r"boston dynamics", r"figure ai", r"figure 0\d", r"agility robotics", r"apptronik", r"unitree",
                  r"\b1x\b", r"optimus", r"physical ai", r"intuitive surgical", r"da vinci", r"symbotic", r"teradyne",
                  r"fanuc", r"\babb\b", r"rockwell automation", r"serve robotics", r"neura"]},
    {"id": "space", "group": "모빌리티 & 에너지", "name": "우주항공",
     "queries": [("SpaceX OR Starship OR Starlink", "US"), ('"Rocket Lab" OR Neutron rocket', "US"),
                 ('"space industry" OR "satellite launch" OR "Blue Origin" OR "AST SpaceMobile"', "US"),
                 ("Arianespace OR ESA launch OR \"Isar Aerospace\" OR \"space sector\"", "EU")],
     "tickers": ["RKLB", "ASTS", "LUNR", "RDW", "PL", "IRDM"],
     "keywords": [r"spacex", r"starship", r"starlink", r"rocket", r"satellite", r"\bspace\b", r"orbit", r"launch",
                  r"blue origin", r"nasa", r"\besa\b", r"ariane", r"lunar", r"ast spacemobile", r"intuitive machines",
                  r"redwire", r"planet labs", r"iridium", r"neutron"]},
    {"id": "battery", "group": "모빌리티 & 에너지", "name": "배터리 (2차전지)",
     "queries": [('battery EV OR lithium OR gigafactory OR "energy storage"', "US"),
                 ('"solid-state battery" OR LFP OR "battery plant"', "US"),
                 ('battery gigafactory Europe OR Northvolt OR "battery cell" OR lithium', "EU")],
     "tickers": ["ALB", "QS", "FLNC", "ENVX"],
     "keywords": [r"batter", r"lithium", r"cathode", r"anode", r"solid-state", r"gigafactory", r"\blfp\b",
                  r"energy storage", r"\bess\b", r"northvolt", r"\bcatl\b", r"lg energy", r"panasonic",
                  r"samsung sdi", r"\bsk on\b", r"albemarle", r"quantumscape", r"fluence"]},
    {"id": "smr", "group": "모빌리티 & 에너지", "name": "에너지 (SMR·원자력)",
     "queries": [('"small modular reactor" OR "nuclear power" OR NuScale OR Oklo', "US"),
                 ('nuclear reactor OR uranium OR "Nuclear Regulatory Commission"', "US"),
                 ('"small modular reactor" OR "Rolls-Royce SMR" OR "nuclear power" OR EDF nuclear', "EU")],
     "tickers": ["SMR", "OKLO", "NNE", "CEG", "BWXT", "CCJ", "VST"],
     "keywords": [r"nuclear", r"reactor", r"\bsmr", r"uranium", r"nuscale", r"oklo", r"\bnrc\b", r"fission",
                  r"fusion", r"constellation", r"bwxt", r"cameco", r"westinghouse", r"\bedf\b", r"vistra"]},

    # 2) 메가테크 ---------------------------------------------------------
    {"id": "nvda", "group": "메가테크", "name": "엔비디아",
     "queries": [("Nvidia", "US"), ("Nvidia", "EU")], "tickers": ["NVDA"],
     "keywords": [r"nvidia", r"jensen huang", r"\bnvda\b", r"blackwell", r"rubin"]},
    {"id": "aapl", "group": "메가테크", "name": "애플",
     "queries": [("Apple iPhone OR \"Apple Inc\" OR \"Tim Cook\"", "US"), ("Apple", "EU")], "tickers": ["AAPL"],
     "keywords": [r"apple", r"iphone", r"tim cook", r"\baapl\b", r"\bios\b", r"vision pro", r"\bmac\b"]},
    {"id": "googl", "group": "메가테크", "name": "알파벳",
     "queries": [("Alphabet OR Google", "US"), ("Google", "EU")], "tickers": ["GOOGL"],
     "keywords": [r"alphabet", r"google", r"gemini", r"waymo", r"youtube", r"deepmind", r"pichai", r"\btpu"]},
    {"id": "msft", "group": "메가테크", "name": "마이크로소프트",
     "queries": [("Microsoft", "US"), ("Microsoft", "EU")], "tickers": ["MSFT"],
     "keywords": [r"microsoft", r"azure", r"copilot", r"nadella", r"\bmsft\b", r"openai"]},
    {"id": "amzn", "group": "메가테크", "name": "아마존",
     "queries": [("Amazon OR AWS", "US"), ("Amazon", "EU")], "tickers": ["AMZN"],
     "keywords": [r"amazon", r"\baws\b", r"jassy", r"\bamzn\b", r"kuiper", r"trainium"]},
    {"id": "meta", "group": "메가테크", "name": "메타",
     "queries": [('"Meta Platforms" OR Zuckerberg OR "Meta AI"', "US"), ("Meta Platforms OR Zuckerberg", "EU")],
     "tickers": ["META"],
     "keywords": [r"\bmeta\b", r"zuckerberg", r"facebook", r"instagram", r"whatsapp", r"llama"]},

    # 3) AI 밸류체인 ------------------------------------------------------
    {"id": "semi_mega", "group": "AI 밸류체인", "name": "반도체 메가캡",
     "queries": [("TSMC OR Intel OR AMD", "US"), ("Micron OR SanDisk", "US"),
                 ('"Samsung Electronics" OR "SK Hynix" chip OR memory OR HBM', "US"),
                 ("TSMC OR Intel OR \"SK Hynix\" OR Samsung chip", "EU")],
     "tickers": ["TSM", "INTC", "AMD", "MU", "SNDK"],
     "keywords": [r"tsmc", r"taiwan semiconductor", r"intel\b", r"\bamd\b", r"advanced micro", r"micron",
                  r"sandisk", r"samsung", r"sk hynix", r"hynix", r"\bhbm", r"\bdram", r"nand"]},
    {"id": "semi_other", "group": "AI 밸류체인", "name": "기타 반도체",
     "queries": [('semiconductor OR chipmaker OR "chip stocks"', "US"),
                 ("Broadcom OR Qualcomm OR Marvell OR Arm Holdings", "US"),
                 ("ASML OR \"Applied Materials\" OR \"Lam Research\" OR KLA", "US"),
                 ("semiconductor OR ASML OR Infineon OR STMicro", "EU")],
     "tickers": ["AVGO", "QCOM", "MRVL", "ARM", "ASML", "AMAT", "LRCX", "KLAC"],
     "keywords": [r"semiconductor", r"\bchip", r"foundry", r"wafer", r"broadcom", r"qualcomm", r"marvell",
                  r"\barm\b", r"asml", r"applied materials", r"lam research", r"\bkla\b", r"infineon",
                  r"stmicro", r"nxp", r"lithograph", r"export control"]},
    {"id": "ai_sw", "group": "AI 밸류체인", "name": "AI 소프트웨어",
     "queries": [("OpenAI OR ChatGPT OR \"Sam Altman\"", "US"), ("Anthropic OR Claude AI", "US"),
                 ('"Mistral AI" OR xAI OR Perplexity OR "AI startup"', "US"),
                 ("OpenAI OR Anthropic OR \"Mistral AI\" OR \"AI Act\"", "EU")],
     "tickers": [],
     "keywords": [r"openai", r"chatgpt", r"altman", r"anthropic", r"claude", r"mistral", r"\bxai\b", r"grok",
                  r"perplexity", r"cohere", r"\bai startup", r"ai model", r"\bllm", r"ai act", r"safe superintelligence",
                  r"thinking machines", r"deepseek"]},
    {"id": "ai_hw", "group": "AI 밸류체인", "name": "AI 하드웨어 인프라 (DC·네오클라우드·광통신)",
     "queries": [('"data center" AI OR hyperscale OR "AI infrastructure"', "US"),
                 ('Coherent OR Lumentum OR Ciena OR "optical transceiver" OR "silicon photonics" OR "co-packaged optics"', "US"),
                 ('"optical interconnect" OR "optical networking" OR Corning fiber AI OR "Credo Technology"', "US"),
                 ('CoreWeave OR Nebius OR Lambda OR Crusoe OR neocloud OR "IREN"', "US"),
                 ('Stargate OR "GPU cloud" OR "Vertiv" OR "Super Micro"', "US"),
                 ('"data centre" AI OR hyperscale OR Nebius', "EU")],
     "tickers": ["CRWV", "NBIS", "IREN", "APLD", "VRT", "SMCI", "DELL", "ORCL", "COHR", "LITE"],
     "keywords": [r"data cent", r"datacent", r"hyperscal", r"neocloud", r"coreweave", r"nebius", r"lambda",
                  r"crusoe", r"stargate", r"gpu cloud", r"\bgpus?\b", r"vertiv", r"super micro", r"supermicro",
                  r"\bdell\b", r"oracle", r"\biren\b", r"applied digital", r"ai infrastructure", r"compute",
                  r"power purchase", r"gigawatt", r"\bgw\b",
                  r"coherent", r"lumentum", r"ciena", r"corning", r"credo", r"optical", r"photonic", r"transceiver",
                  r"\bcpo\b", r"co-packaged", r"fiber", r"fibre", r"interconnect", r"800g", r"1\.6t"]},

    # 4) 암호화폐 — Finnhub 종목 호출은 이미 분당 한도(60)에 가까워 Google News 만 사용
    {"id": "btc", "group": "암호화폐", "name": "비트코인",
     "queries": [("Bitcoin OR BTC price", "US"), ('"bitcoin ETF" OR "spot bitcoin" OR "bitcoin treasury"', "US"),
                 ("Bitcoin", "EU")],
     "tickers": [],
     "keywords": [r"bitcoin", r"\bbtc\b", r"satoshi", r"halving", r"\bibit\b", r"bitcoin treasury"]},
    {"id": "alt", "group": "암호화폐", "name": "이더리움·알트코인",
     "queries": [("Ethereum OR Solana OR XRP OR altcoin", "US"), ('"ether ETF" OR "Solana ETF" OR "XRP ETF" OR DeFi', "US"),
                 ("Ethereum OR Solana OR XRP", "EU")],
     "tickers": [],
     "keywords": [r"ethereum", r"\bether\b", r"\beth\b", r"solana", r"\bsol\b", r"\bxrp\b", r"ripple", r"cardano",
                  r"dogecoin", r"altcoin", r"defi", r"\btoken", r"memecoin", r"layer[ -]2"]},
    {"id": "crypto_policy", "group": "암호화폐", "name": "스테이블코인·규제",
     "queries": [('stablecoin OR "GENIUS Act" OR "CLARITY Act" OR Tether OR USDC', "US"),
                 ('SEC crypto OR "crypto regulation" OR "crypto bill" OR CFTC crypto', "US"),
                 ('MiCA crypto OR stablecoin Europe OR "digital euro"', "EU")],
     "tickers": [],
     "keywords": [r"stablecoin", r"tether", r"\busdt\b", r"\busdc\b", r"genius act", r"clarity act", r"\bmica\b",
                  r"crypto (?:bill|law|regulat|rule|policy)", r"\bsec\b.*crypto", r"crypto.*\bsec\b", r"cftc",
                  r"digital euro", r"cbdc"]},
    {"id": "crypto_corp", "group": "암호화폐", "name": "크립토 기업·채굴",
     "queries": [('Coinbase OR Binance OR Circle OR Kraken OR Gemini crypto exchange', "US"),
                 ('Strategy MicroStrategy OR "Michael Saylor" OR "bitcoin miner" OR "Marathon Digital" OR "Riot Platforms"', "US"),
                 ("Binance OR Coinbase OR crypto exchange", "EU")],
     "tickers": [],
     "keywords": [r"coinbase", r"binance", r"circle", r"kraken", r"gemini", r"bullish", r"robinhood", r"microstrategy",
                  r"\bstrategy\b.*bitcoin", r"saylor", r"bitcoin miner", r"crypto miner", r"marathon digital", r"\bmara\b",
                  r"riot platforms", r"cleanspark", r"crypto exchange", r"\bokx\b", r"bybit"]},

    # ── 기술주 실적: 기술기업 이름(must) + 실적 용어(keywords)가 함께 있을 때만 ──
    {"id": "earnings", "section": "산업", "group": "기술주 실적", "name": "기술주 실적 (어닝)",
     "queries": [('earnings OR "quarterly results" Nvidia OR Apple OR Microsoft OR Alphabet OR Amazon OR Meta OR Tesla', "US"),
                 ('earnings beat OR miss OR guidance Broadcom OR AMD OR Micron OR Oracle OR Intel OR Qualcomm OR Arm', "US"),
                 ('"quarterly results" OR "preliminary results" OR earnings TSMC OR Samsung OR "SK Hynix" OR ASML OR SAP', "US"),
                 ('earnings OR "quarterly results" Palantir OR Salesforce OR Adobe OR Netflix OR ServiceNow OR CoreWeave OR Dell', "US"),
                 ('ASML OR SAP OR Infineon OR STMicro OR "Samsung Electronics" quarterly results OR earnings', "EU")],
     "tickers": [],
     "keywords": [  # 실제 발표된 실적 표현만 (미리보기·일정 기사 제외는 아래 exclude)
                  r"\b(?:beats?|tops?|misses|missed|surpass(?:es|ed)?|exceed(?:s|ed)?) (?:\w+ )?(?:estimates|expectations|forecasts|consensus)",
                  r"\b(?:reports?|reported|posts?|posted|delivers?|logs?|swings? to) (?:record |strong |weak |a |an )?(?:quarterly |q[1-4] |first|second|third|fourth)?[- ]?(?:quarter )?(?:results|earnings|profit|loss|revenue|sales|eps)",
                  r"\beps (?:of )?\$?-?\d", r"\b(?:adj\.? )?eps \$?-?\d[\d.]* (?:vs\.?|versus)",
                  r"\bq[1-4] (?:eps|revenue|sales|results|earnings|net income|profit)\b.*(?:\$\d|\bvs\b|\best\b|\brises?\b|\bfalls?\b|\bjumps?\b|\bup\b|\bdown\b|\d+%)",
                  r"(?:revenue|sales|profit|net income|operating (?:profit|income)|earnings) (?:rose|rises|fell|falls|jumped|jumps|surged|surges|soared|slumped|declined|dropped|grew|climbed|plunged|more than doubled|tripled)",
                  r"(?:raises?|raised|lifts?|hikes?|cuts?|lowers?|lowered|reaffirms?|boosts?) (?:its |full-year |annual |fy |q[1-4] )*(?:guidance|outlook|forecast)",
                  r"preliminary (?:results|earnings|operating profit|q[1-4])", r"(?:first|second|third|fourth)[- ]quarter (?:results|profit|revenue|earnings) (?:beat|miss|top|rise|fall|jump)",
                  r"results? (?:beat|top|miss)"]},

    # ── 매크로: 로컬 매크로 ──
    {"id": "us_macro", "section": "매크로", "group": "로컬 매크로", "name": "미국",
     "queries": [('CPI OR inflation OR "jobs report" OR payrolls OR GDP OR "retail sales" US economy', "US"),
                 ('"jobless claims" OR "consumer confidence" OR PCE OR ISM OR "Treasury yields" OR "US economy"', "US")],
     "tickers": [],
     "keywords": [r"\bcpi\b", r"\bppi\b", r"inflation", r"payrolls?", r"jobs report", r"unemployment", r"jobless", r"\bgdp\b",
                  r"retail sales", r"\bpmi\b", r"\bism\b", r"\bpce\b", r"consumer (?:confidence|sentiment|spending|prices)",
                  r"treasury yields?", r"10-year", r"housing starts", r"home sales", r"durable goods", r"trade deficit",
                  r"us economy", r"u\.s\. economy", r"recession", r"labor market", r"wage growth"]},
    {"id": "asia_macro", "section": "매크로", "group": "로컬 매크로", "name": "아시아 (한국·일본·대만·중국·인도)",
     "queries": [('Korea economy OR "Bank of Korea" OR KOSPI OR "Korean won" OR "South Korea exports"', "US"),
                 ('China economy OR PBOC OR yuan OR "China exports" OR "China CPI" OR "China PMI"', "US"),
                 ('Japan economy OR yen OR Nikkei OR "Japan inflation" OR "Taiwan exports" OR "Taiwan economy"', "US"),
                 ('India economy OR RBI OR rupee OR "India GDP" OR "India inflation"', "US")],
     "tickers": [],
     "keywords": [r"korea", r"kospi", r"\bwon\b", r"bank of korea", r"japan", r"\byen\b", r"nikkei", r"taiwan", r"taiex",
                  r"china", r"chinese", r"yuan", r"renminbi", r"pboc", r"beijing", r"india", r"\brbi\b", r"rupee", r"sensex",
                  r"nifty"]},
    {"id": "eu_macro", "section": "매크로", "group": "로컬 매크로", "name": "유럽",
     "queries": [('eurozone economy OR "euro area" inflation OR "German economy" OR "UK economy" OR "Bank of England"', "EU"),
                 ('Bund yields OR gilts OR "euro zone" GDP OR PMI OR "UK inflation" OR "France economy"', "EU")],
     "tickers": [],
     "keywords": [r"euro ?zone", r"euro area", r"german", r"germany", r"france", r"french", r"\buk\b", r"britain", r"british",
                  r"\bbund", r"gilts?", r"bank of england", r"\bboe\b", r"\bifo\b", r"\bzew\b", r"italy", r"spain", r"\beu\b",
                  r"europe", r"\beuro\b"]},

    # ── 매크로: 중앙은행 ──
    {"id": "fed", "section": "매크로", "group": "중앙은행", "name": "연준 (Fed)",
     "queries": [('"Federal Reserve" OR FOMC OR "Fed chair" OR "Fed officials" OR "Fed rate"', "US"),
                 ('Fed "rate cut" OR "rate hike" OR "Fed minutes" OR "Fed governor"', "US")],
     "tickers": [],
     "keywords": [r"federal reserve", r"\bfed\b", r"fomc", r"powell", r"fed chair", r"fed governor", r"fed minutes",
                  r"rate (?:cut|hike)s?", r"monetary policy", r"dot plot"]},
    {"id": "ecb", "section": "매크로", "group": "중앙은행", "name": "ECB (유럽중앙은행)",
     "queries": [('ECB OR "European Central Bank" OR Lagarde', "EU"), ('ECB rate OR "ECB policymakers"', "US")],
     "tickers": [],
     "keywords": [r"\becb\b", r"european central bank", r"lagarde", r"governing council"]},
    {"id": "boj", "section": "매크로", "group": "중앙은행", "name": "BOJ (일본은행)",
     "queries": [('"Bank of Japan" OR BOJ OR Ueda', "US"), ('"Bank of Japan" OR BOJ rate', "EU")],
     "tickers": [],
     "keywords": [r"bank of japan", r"\bboj\b", r"ueda", r"\bjgbs?\b"]},

    # ── 지정학 ──
    {"id": "midterms", "section": "지정학", "group": "지정학", "name": "미국 중간선거",
     "queries": [('"midterm elections" OR "2026 midterms" OR "midterm" House OR Senate', "US"),
                 ('"Senate race" OR "House race" OR "generic ballot" OR battleground 2026 election', "US"),
                 ('"midterm" poll OR "swing district" OR "toss-up" Senate OR House 2026', "US"),
                 ('"Senate debate" OR "governor debate" OR "Senate candidate" OR "House candidate" 2026', "US")],
     "tickers": [],
     "keywords": [r"midterm", r"generic ballot", r"senate race", r"house race", r"battleground", r"swing (?:district|state)",
                  r"toss-?up", r"\bpolls?\b.*(?:senate|house|congress|election)", r"(?:senate|house|congress).*\bpolls?\b",
                  r"election day", r"early voting", r"ballot", r"campaign", r"candidate", r"primary", r"cook political",
                  r"(?:flip|hold) the (?:house|senate)", r"control of (?:the )?(?:house|senate|congress)",
                  r"(?:senate|house|gubernatorial|congressional) (?:debate|candidate|seat|nominee)", r"congressional district"]},
    {"id": "us_politics", "section": "지정학", "group": "지정학", "name": "미국 정계",
     "queries": [('"White House" OR Congress OR "Trump administration" OR Senate OR "government shutdown"', "US"),
                 ('Trump tariffs OR "executive order" OR "Supreme Court" OR "House Republicans" OR Democrats', "US")],
     "tickers": [],
     "keywords": [r"trump", r"white house", r"congress", r"senate", r"house speaker", r"republican", r"democrat", r"\bgop\b",
                  r"shutdown", r"election", r"supreme court", r"tariff", r"executive order", r"administration", r"lawmakers",
                  r"vance", r"capitol"]},
    {"id": "mideast", "section": "지정학", "group": "지정학", "name": "이란 & 중동",
     "queries": [('Iran OR Israel OR Gaza OR Hezbollah OR Houthi OR "Middle East"', "US"),
                 ('"Strait of Hormuz" OR "Red Sea" OR Saudi OR Qatar OR Yemen OR tanker attack', "US"),
                 ('Iran OR Israel OR "Middle East" oil', "EU")],
     "tickers": [],
     "keywords": [r"iran", r"israel", r"gaza", r"hezbollah", r"houthi", r"yemen", r"hormuz", r"saudi", r"qatar", r"lebanon",
                  r"syria", r"iraq", r"tehran", r"\bidf\b", r"red sea", r"ukmto", r"middle east", r"hamas", r"tanker"]},
]
TOPIC_BY_ID = {t["id"]: t for t in TOPICS}

# ─────────────────────────────────────────────────────────────────────
# 실시간 속보 피드 (FinancialJuice 영문, 월스트리트견문 중문) — 토픽 구분 없는 전체 피드라
# 넓은 단어(tariff, launch 등) 대신 토픽별 고유한 기업·제품명(영문+중문)이 있을 때만 해당 토픽에 넣는다
# ─────────────────────────────────────────────────────────────────────
FEED_KEYWORDS = {
    "tesla": [r"tesla", r"\bmusk\b", r"cybertruck", r"robotaxi", r"optimus", r"\bfsd\b", "特斯拉", "马斯克"],
    "auto": [r"general motors", r"\bgm\b", r"\bford\b", r"stellantis", r"rivian", r"lucid", r"volkswagen", r"\bbmw\b",
             r"mercedes", r"renault", r"porsche", r"toyota", r"hyundai", r"\bbyd\b", r"automaker", r"car sales", r"\bacea\b",
             "通用汽车", "福特", "大众汽车", "宝马", "奔驰", "丰田", "现代汽车", "比亚迪", "车企", "汽车销量", "汽车关税"],
    "robotics": [r"humanoid", r"robotaxi", r"waymo", r"zoox", r"self-driving", r"driverless", r"boston dynamics",
                 r"figure ai", r"unitree", r"physical ai", r"intuitive surgical", r"robotics",
                 "机器人", "人形机器人", "自动驾驶", "无人驾驶", "宇树", "具身智能"],
    "space": [r"spacex", r"starship", r"starlink", r"rocket lab", r"blue origin", r"\bnasa\b", r"satellite launch",
              r"ast spacemobile", "太空探索", "星舰", "星链", "火箭", "卫星发射", "航天"],
    "battery": [r"batter(?:y|ies)", r"lithium", r"gigafactory", r"energy storage", r"\bcatl\b", r"solid-state",
                "(?<!太阳能)电池", "锂", "储能", "宁德时代", "固态电池"],
    # 이란 우라늄 농축 등 안보 뉴스가 섞이지 않도록 '원자력 발전' 맥락의 표현만
    "smr": [r"nuclear (?:power|plant|energy|reactor|fuel)", r"small modular", r"\bsmr\b", r"nuscale", r"oklo", r"\bnrc\b",
            r"westinghouse", r"constellation energy", r"cameco", "核电", "核电站", "核能发电", "小型模块化", "模块化反应堆"],
    "nvda": [r"nvidia", r"jensen huang", r"\bnvda\b", r"blackwell", "英伟达", "黄仁勋"],
    "aapl": [r"\bapple\b", r"iphone", r"tim cook", r"\baapl\b", "苹果公司", "苹果", "蒂姆·库克", "苹果CEO库克"],
    "googl": [r"alphabet", r"google", r"gemini", r"waymo", r"youtube", r"deepmind", "谷歌", "字母表"],
    "msft": [r"microsoft", r"azure", r"copilot", r"nadella", "微软"],
    "amzn": [r"amazon", r"\baws\b", r"jassy", "亚马逊"],
    "meta": [r"\bmeta\b", r"zuckerberg", r"facebook", r"instagram", r"whatsapp", "扎克伯格", "脸书"],
    "semi_mega": [r"tsmc", r"\bintel\b", r"\bamd\b", r"micron", r"sandisk", r"samsung", r"hynix", r"\bhbm\d?\b",
                  "台积电", "英特尔", "美光", "闪迪", "三星", "海力士", "存储芯片", "内存"],
    "semi_other": [r"semiconductor", r"chipmaker", r"broadcom", r"qualcomm", r"marvell", r"\basml\b", r"applied materials",
                   r"lam research", r"chip export", r"export controls?", "半导体", "芯片", "博通", "高通", "阿斯麦", "光刻", "出口管制"],
    "ai_sw": [r"openai", r"chatgpt", r"altman", r"anthropic", r"\bclaude\b", r"mistral", r"\bxai\b", r"\bgrok\b",
              r"perplexity", r"deepseek", "大模型", "人工智能公司", "奥特曼"],
    "ai_hw": [r"data cent(?:er|re)", r"hyperscal", r"neocloud", r"coreweave", r"nebius", r"crusoe", r"stargate",
              r"\boracle\b", r"super micro", r"vertiv", r"\bdell\b", r"coherent", r"lumentum", r"ciena", r"optical",
              r"photonic", r"transceiver", r"co-packaged", "数据中心", "算力", "甲骨文", "星际之门", "超微电脑",
              "光通信", "光模块", "光芯片", "硅光", "CPO", "中际旭创", "新易盛", "天孚通信", "光迅科技"],
    "btc": [r"bitcoin", r"\bbtc\b", "比特币"],
    "alt": [r"ethereum", r"\bether\b", r"\beth\b", r"solana", r"\bxrp\b", r"ripple", r"dogecoin", r"altcoin",
            "以太坊", "索拉纳", "瑞波", "狗狗币"],
    "crypto_policy": [r"stablecoin", r"tether", r"\busdt\b", r"\busdc\b", r"genius act", r"clarity act", r"crypto (?:bill|law|regulat)",
                      "稳定币", "泰达币", "加密货币监管", "加密资产监管"],
    "earnings": [r"\beps\b.*(?:\bvs\b|\bexp\b|\best\b|\$\d)", r"\bq[1-4]\b.*(?:revenue|eps|sales|results).*(?:\bvs\b|\bexp\b|\$\d|\d+%)",
                 r"(?:beats?|misses|tops?) (?:\w+ )?(?:estimates|expectations)", r"(?:raises?|cuts?|lowers?) (?:\w+ )?(?:guidance|outlook|forecast)",
                 r"(?:revenue|profit|net income|sales) (?:rose|fell|jumped|surged|declined|grew)", r"preliminary (?:results|operating profit)",
                 "财报显示", "营收同比", "净利润同比", "季度营收", "季度净利润", "每股收益为", "业绩超预期", "业绩不及预期", "上调.*指引", "下调.*指引",
                 "营业利润初步"],
    "us_macro": [r"\bcpi\b", r"\bppi\b", r"nonfarm|non-farm", r"payrolls?", r"jobless claims", r"unemployment rate",
                 r"\bus gdp\b", r"u\.s\. gdp", r"retail sales", r"\bism\b", r"\bpce\b", r"consumer confidence",
                 r"michigan sentiment", r"treasury yields?", r"jolts", r"durable goods", r"housing starts",
                 "美国CPI", "美国PPI", "非农", "美国通胀", "美国GDP", "初请", "美债收益率", "美国零售销售", "美国PCE", "美国ISM",
                 "美国失业率", "美国消费者信心"],
    "asia_macro": [r"bank of korea", r"\bkospi\b", r"korean won", r"south korea(?:n)? (?:exports|gdp|inflation|cpi)",
                   r"japan(?:ese)? (?:gdp|cpi|inflation|exports|wages)", r"\byen\b", r"nikkei", r"taiwan (?:exports|gdp|cpi)",
                   r"\btaiex\b", r"china(?:'s)? (?:gdp|cpi|ppi|exports|imports|pmi|economy|trade)", r"\bpboc\b", r"yuan",
                   r"india(?:'s)? (?:gdp|cpi|inflation|economy)", r"\brbi\b", r"rupee",
                   "韩国央行", "韩元", "韩国出口", "韩国CPI", "日元", "日经", "日本CPI", "日本GDP", "台湾出口", "台股",
                   "中国CPI", "中国PPI", "中国GDP", "中国出口", "社融", "中国PMI", "人民币", "中国人民银行", "央行降准",
                   "印度央行", "印度GDP", "卢比", "印度CPI"],
    "eu_macro": [r"euro ?zone", r"euro area", r"german (?:gdp|cpi|inflation|economy|ifo|zew)", r"\bifo\b", r"\bzew\b",
                 r"uk (?:gdp|cpi|inflation|economy)", r"bank of england", r"\bboe\b", r"\bgilts?\b", r"\bbunds?\b",
                 r"france (?:gdp|cpi)", r"eur/usd", "欧元区", "德国CPI", "德国GDP", "英国CPI", "英国GDP", "英国央行",
                 "英债", "德债", "欧元兑美元"],
    "fed": [r"federal reserve", r"\bfed\b", r"fomc", r"powell", r"fed chair", r"fed's", r"fed governor", r"fed minutes",
            "美联储", "鲍威尔", "联储", "FOMC"],
    "ecb": [r"\becb\b", r"european central bank", r"lagarde", "欧洲央行", "拉加德"],
    "boj": [r"bank of japan", r"\bboj\b", r"ueda", "日本央行", "日银", "植田"],
    "midterms": [r"midterm", r"generic ballot", r"senate race", r"house race", r"battleground", r"toss-?up",
                 r"control of (?:the )?(?:house|senate|congress)",
                 r"(?:senate|house|gubernatorial|congressional) (?:debate|candidate|seat|nominee)", r"congressional district",
                 "中期选举", "中期大选", "众议院选举", "参议院选举"],
    "us_politics": [r"trump", r"white house", r"congress", r"senate", r"house speaker", r"republicans?", r"democrats?",
                    r"shutdown", r"supreme court", r"executive order", r"\bvance\b", r"tariffs?",
                    "特朗普", "白宫", "国会", "参议院", "众议院", "共和党", "民主党", "政府停摆", "最高法院", "万斯", "关税"],
    "mideast": [r"iran", r"israel", r"gaza", r"hezbollah", r"houthi", r"yemen", r"hormuz", r"saudi", r"qatar", r"lebanon",
                r"syria", r"iraq", r"tehran", r"\bidf\b", r"red sea", r"ukmto", r"middle east", r"hamas", r"opec",
                "伊朗", "以色列", "加沙", "真主党", "胡塞", "也门", "霍尔木兹", "沙特", "卡塔尔", "黎巴嫩", "叙利亚", "中东", "红海",
                "哈马斯", "欧佩克"],
    "crypto_corp": [r"coinbase", r"binance", r"microstrategy", r"saylor", r"bitcoin miner", r"crypto exchange", r"\bokx\b",
                    r"\bkraken\b", "币安", "微策略", "加密货币交易所", "矿企"],
}
# 실적 토픽: 기술기업 이름이 반드시 함께 있어야 함 (영문+중문)
TECH_NAMES = [r"nvidia", r"\bapple\b", r"microsoft", r"alphabet", r"google", r"amazon", r"\bmeta\b", r"tesla", r"broadcom",
              r"\bamd\b", r"micron", r"oracle", r"tsmc", r"taiwan semiconductor", r"\bintel\b", r"qualcomm", r"\basml\b",
              r"\barm\b", r"marvell", r"salesforce", r"adobe", r"netflix", r"palantir", r"servicenow", r"snowflake",
              r"crowdstrike", r"\bdell\b", r"\bhpe\b", r"super micro", r"coreweave", r"nebius", r"samsung", r"hynix",
              r"\bsap\b", r"\bibm\b", r"cisco", r"applied materials", r"lam research", r"\bkla\b", r"sandisk",
              r"western digital", r"seagate", r"coherent", r"lumentum", r"ciena", r"arista", r"vertiv", r"infineon",
              r"stmicro", r"uber", r"shopify", r"spotify", r"accenture",
              "英伟达", "苹果", "微软", "谷歌", "亚马逊", "特斯拉", "博通", "美光", "甲骨文", "台积电", "英特尔", "高通", "阿斯麦",
              "三星", "海力士", "奈飞"]
TOPIC_BY_ID["earnings"]["must"] = TECH_NAMES
# 선거 기사는 '미국 중간선거'로 모으고 '미국 정계'에서는 뺀다
TOPIC_BY_ID["us_politics"]["exclude"] = [r"midterm", r"generic ballot", r"senate race", r"house race", r"battleground",
                                          r"toss-?up", r"control of (?:the )?(?:house|senate|congress)",
                                          r"(?:senate|house|gubernatorial|governor'?s?|congressional) (?:debate|candidate|seat|nominee|contest)",
                                          r"congressional district", r"election day", r"early voting",
                                          r"campaign trail", "中期选举", "中期大选"]
# 실적 '발표 전' 기사(미리보기·일정·예상·전망·의견) 제외
TOPIC_BY_ID["earnings"]["exclude"] = [
    r"preview", r"ahead of", r"\bto (?:report|announce|release|post)\b", r"will (?:report|announce|release)", r"set to",
    r"expected to", r"what to (?:expect|watch)", r"earnings (?:date|call set|season|calendar|whisper|week)", r"scheduled",
    r"about to", r"\bcould\b", r"\bmight\b", r"\bmay\b", r"\?\s*$", r"options traders?", r"implied move",
    r"heads? into", r"going into", r"open questions", r"things to", r"\bkeys? to watch", r"\bshould you\b", r"\bbuy\b", r"\bpredict", r"\bupcoming\b", r"\bnext week\b", r"\btomorrow\b",
    "前瞻", "预期", "将于", "即将", "预告", "有望", "或将"]


for _t in TOPICS:
    _t.setdefault("section", "산업")
    _t["_kw"] = re.compile("|".join(_t["keywords"]), re.I)
    _t["_must"] = re.compile("|".join(_t["must"]), re.I) if _t.get("must") else None
    _t["_exclude"] = re.compile("|".join(_t["exclude"]), re.I) if _t.get("exclude") else None
    _t["_feed"] = re.compile("|".join(FEED_KEYWORDS.get(_t["id"], [r"(?!x)x"])), re.I)

# ─────────────────────────────────────────────────────────────────────
# 출처 신뢰도
# ─────────────────────────────────────────────────────────────────────
OFFICIAL_DOMAINS = {
    "businesswire.com", "prnewswire.com", "globenewswire.com", "accesswire.com", "sec.gov", "nrc.gov",
    "nasa.gov", "energy.gov", "esa.int", "europa.eu", "federalreserve.gov",
    "tesla.com", "spacex.com", "rocketlabusa.com", "rocketlabcorp.com", "nvidia.com", "apple.com", "abc.xyz",
    "blog.google", "microsoft.com", "aboutamazon.com", "amazon.com", "about.fb.com", "meta.com",
    "tsmc.com", "intel.com", "amd.com", "micron.com", "sandisk.com", "samsung.com", "skhynix.com",
    "news.skhynix.com", "openai.com", "anthropic.com", "mistral.ai", "x.ai", "coreweave.com", "nebius.com",
    "nuscalepower.com", "oklo.com", "broadcom.com", "qualcomm.com", "asml.com", "arm.com", "ibm.com",
    "oracle.com", "dell.com", "supermicro.com", "vertiv.com", "gm.com", "ford.com", "stellantis.com",
    "volkswagen-group.com", "bmwgroup.com", "mercedes-benz.com", "rolls-royce.com", "edf.fr",
    # 정부·중앙은행·통계·국제기구 (사장님 사이트 목록)
    "bea.gov", "bls.gov", "dol.gov", "eia.gov", "fda.gov", "whitehouse.gov", "treasury.gov", "census.gov", "cftc.gov",
    "usda.gov", "nhtsa.gov", "senate.gov", "house.gov", "newyorkfed.org", "frbatlanta.org", "dallasfed.org",
    "stlouisfed.org", "opec.org", "iea.org", "gov.cn", "bok.or.kr", "semiconductors.org", "wsts.org", "gold.org",
}
SOURCE_TIERS = {
    "tier1": ["reuters", "bloomberg", "wall street journal", "wsj", "financial times", "ft.com", "associated press",
              "ap news", "apnews", "cnbc", "nikkei", "the information", "new york times", "nytimes", "barron's",
              "barrons", "handelsblatt", "the economist", "washington post", "axios", "yonhap", "korea economic daily",
              "bbc", "nbc news", "abc news", "cbs news", "the guardian", "chosun", "hankyung", "mk.co.kr", "joongang",
              "maeil business", "korea joongang", "the globe and mail"],
    "tier2": ["financialjuice", "wallstreetcn", "华尔街见闻", "yahoo", "marketwatch", "techcrunch", "the verge", "electrek", "spacenews", "space.com", "tom's hardware",
              "tomshardware", "datacenter dynamics", "dcd", "data center dynamics", "the register", "fortune",
              "business insider", "forbes", "investing.com", "wired", "ars technica", "semafor", "politico",
              "guardian", "le monde", "les echos", "der spiegel", "automotive news", "insideevs", "anandtech",
              "digitimes", "trendforce", "eetimes", "world nuclear news", "neimagazine", "power magazine",
              "utility dive", "fierce", "theinformation", "cnn", "fox business", "the korea herald", "korea times",
              "teslarati", "investor's business daily", "investors.com",
              "the next platform", "servethehome", "data centre magazine", "datacenterknowledge", "zdnet", "engadget",
              "venturebeat", "siliconangle", "the decoder", "marketscreener", "proactive", "the independent",
              "economic times", "economictimes", "times of india", "nikkei asia", "scmp", "south china morning post",
              "pcmag", "techradar", "9to5mac", "9to5google", "macrumors", "appleinsider", "macworld", "android authority",
              "mashable", "qz.com", "quartz", "gizmodo", "techrepublic", "pymnts", "compound semiconductor",
              "the scotsman", "sedaily", "seoul economic", "heraldcorp", "herald", "aol", "autoblog", "motor1",
              "electrive", "teslarati", "nasaspaceflight", "spaceflight now", "payload", "ans.org", "nucnet",
              "hpcwire", "datacenterfrontier", "data center frontier", "the elec", "businesskorea", "kedglobal", "morningstar", "android central",
              # 산업 전문지·속보 (사장님 사이트 목록)
              "cnevpost", "oilprice", "rigzone", "natural gas intelligence", "naturalgasintel", "hellenic shipping",
              "mining.com", "endpoints news", "endpts", "biopharma dive", "biopharmadive", "statnews", "stat news",
              "coindesk", "cointelegraph", "the block", "theblock.co", "briefing.com", "thefly", "the fly", "tradewinds",
              "barchart", "eastmoney", "东方财富", "동방재부", "新浪财经", "sina", "xinhua", "新华", "people.cn", "caixin", "财新"],
    "opinion": ["motley fool", "fool.com", "seeking alpha", "seekingalpha", "zacks", "investorplace", "24/7 wall st",
                "247wallst", "simply wall st", "gurufocus", "insider monkey", "marketbeat", "nasdaq.com",
                "finbold", "coinpedia", "ainvest", "benzinga", "tipranks", "thestreet", "tradingview", "stocktwits",
                "moomoo", "tikr", "trefis", "kalkine", "pluang", "bybit", "futunn", "stock titan", "stocktitan",
                "ad-hoc-news", "ad hoc news", "biggo", "indexbox", "tradingkey", "fxleaders", "investinglive",
                "fxstreet", "zerohedge", "mishtalk", "calculated risk", "calculatedrisk", "ritholtz", "advisor perspectives",
                "yardeni", "valuewalk"],
    "social": ["facebook", "x.com", "twitter", "reddit", "youtube", "instagram", "tiktok", "linkedin",
               "threads.net", "substack", "medium.com"],
}
OPINION_PATTERNS = re.compile(
    r"should you buy|is it time to buy|stock to buy|stocks to buy|buy now|\bprediction\b|could make you|"
    r"millionaire|better buy|top \d+ stocks|\bmy top\b|is .{1,40} a buy|undervalued|overvalued|"
    r"here'?s why|what to expect|price target raised|price target cut|\bopinion\b|\banalysis:|"
    r"jim cramer|stocks? to own|narrative\?|^should |\binvestors? (?:should|view)|\bbull case\b|\bbear case\b",
    re.I)
HEDGE_PATTERNS = re.compile(
    r"\breportedly\b|\brumou?r|\bsources? (?:say|said|tell)|people familiar|according to (?:a )?(?:report|sources)|"
    r"\bsaid to\b|\bis said\b|\bleak|\bunconfirmed\b|\bspeculat", re.I)
# 딜·협상 관련 '검토/협상' 표현 — 당사자가 확인(confirm/announce)하지 않았을 때만 루머로 취급
DEALTALK_PATTERNS = re.compile(r"\bin talks\b|\bweighs?\b(?! in)|\bexplores?\b|\bmulls?\b|\bconsidering\b|\bnears? deal", re.I)
ONRECORD_PATTERNS = re.compile(r"confirm|announc|unveil|launch|files?\b|filing|official", re.I)

STOPWORDS = set("""a an the of to in on for and or with by at from as is are be was were its it this that
after before over under into amid about new says said will may could would s vs up down more than first
how why what who when where report reports stock stocks shares share inc corp co ltd plc""".split())

# ─────────────────────────────────────────────────────────────────────
# 시간 창
# ─────────────────────────────────────────────────────────────────────
def live_window(now=None, hours=WINDOW_HOURS):
    end = (now or datetime.now(KST)).astimezone(KST).replace(second=0, microsecond=0)
    return end - timedelta(hours=hours), end


# ─────────────────────────────────────────────────────────────────────
# 수집
# ─────────────────────────────────────────────────────────────────────
def _domain(url):
    try:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def _split_gnews_title(title, source):
    """Google News 제목은 'Headline - Publisher' 형식 → 매체명 제거."""
    if source and title.endswith(" - " + source):
        return title[: -len(source) - 3].strip()
    return re.sub(r"\s+-\s+[^-]{2,60}$", "", title).strip()


def fetch_gnews(query, edition, start, end):
    ed = EDITIONS[edition]
    # after:/before: 는 날짜 단위(UTC) → 창을 포함하도록 넉넉히 잡고 로컬에서 시간 필터
    s = (start.astimezone(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    e = (end.astimezone(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
    url = GNEWS_URL.format(q=quote_plus(f"{query} after:{s} before:{e}"), **ed)
    try:
        r = requests.get(url, timeout=15, headers=HTTP_HDR)
        root = ET.fromstring(r.content)
    except Exception as ex:
        print(f"[scanner/gnews] {query!r} {edition}: {ex}")
        return []
    out = []
    for item in root.findall(".//item"):
        src_el = item.find("source")
        source = (src_el.text or "").strip() if src_el is not None else ""
        src_url = src_el.get("url", "") if src_el is not None else ""
        try:
            ts = int(parsedate_to_datetime(item.findtext("pubDate") or "").timestamp())
        except Exception:
            continue
        out.append({
            "headline": _split_gnews_title((item.findtext("title") or "").strip(), source),
            "summary": "",
            "source": source or _domain(src_url),
            "domain": _domain(src_url),
            "url": (item.findtext("link") or "").strip(),
            "published": ts,
            "via": f"Google News ({edition})",
            "edition": edition,
        })
    return out


_fh_lock = threading.Lock()
_fh_last = [0.0]
# 호출 간 최소 간격(초). 서버 모드는 시세 조회와 키를 함께 쓰므로 1.1초,
# GitHub Actions 빌드는 이 키를 혼자 쓰므로 짧게(분당 60회 한도 안에서 한 번에 몰아 호출)
FINNHUB_MIN_INTERVAL = float(os.environ.get("FINNHUB_MIN_INTERVAL", "1.1"))


def fetch_finnhub_company(symbol, start, end, token):
    """Finnhub 무료 한도(60/분) 보호를 위해 호출 간 FINNHUB_MIN_INTERVAL 간격 유지."""
    if not token:
        return []
    with _fh_lock:
        wait = FINNHUB_MIN_INTERVAL - (time.time() - _fh_last[0])
        if wait > 0:
            time.sleep(wait)
        _fh_last[0] = time.time()
    url = (f"https://finnhub.io/api/v1/company-news?symbol={symbol}"
           f"&from={start.astimezone(timezone.utc):%Y-%m-%d}&to={end.astimezone(timezone.utc):%Y-%m-%d}&token={token}")
    try:
        items = requests.get(url, timeout=15).json()
        if not isinstance(items, list):
            print(f"[scanner/finnhub] {symbol}: {str(items)[:80]}")
            return []
    except Exception as ex:
        print(f"[scanner/finnhub] {symbol}: {ex}")
        return []
    return [{
        "headline": (it.get("headline") or "").strip(),
        "summary": (it.get("summary") or "").strip()[:400],
        "source": it.get("source") or "Finnhub",
        "domain": "",
        "url": it.get("url", ""),
        "published": int(it.get("datetime") or 0),
        "via": f"Finnhub ({symbol})",
        "edition": "US",
    } for it in items]


# 위불(Webull) 종목 뉴스: 로그인 없이 열리는 종목별 뉴스 페이지에 실린 최근 기사 약 20건
WEBULL_SEARCH = "https://quotes-gw.webullfintech.com/api/search/pc/tickers?keyword={}&pageIndex=1&pageSize=5&regionId=6"
WEBULL_NEWS = "https://www.webull.com/quote/{}-{}/news"
WEBULL_FIELD = {k: re.compile(rf'"{k}":"((?:[^"\\]|\\.)*)"') for k in ("title", "newsUrl", "newsTime", "sourceName")}
_WB_HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}


def _js_str(s):
    try:
        return json.loads(f'"{s}"')
    except Exception:
        return s


def fetch_webull(symbol):
    """위불 종목 뉴스 페이지의 기사 목록 (시간 필터는 호출하는 쪽에서)"""
    try:
        data = requests.get(WEBULL_SEARCH.format(symbol), headers=_WB_HDR, timeout=15).json().get("data") or []
        hit = next((d for d in data if d.get("symbol") == symbol and d.get("regionCode") == "US"), None)
        exch = (hit or {}).get("disExchangeCode", "NASDAQ").lower()
        page = requests.get(WEBULL_NEWS.format(exch, symbol.lower()), headers=_WB_HDR, timeout=15).text
    except Exception as ex:
        print(f"[scanner/webull] {symbol}: {ex}")
        return []
    out = []
    m = re.search(r'"newsData":\[', page)
    for chunk in (page[m.end():].split('{"id":"')[1:] if m else []):
        nid = chunk.split('"', 1)[0]
        f = {k: (r.search(chunk[:4000]) or [None, ""])[1] for k, r in WEBULL_FIELD.items()}
        title, url, t, src = f["title"], f["newsUrl"], f["newsTime"], f["sourceName"]
        if not nid.isdigit() or not title or not t:
            if not nid.isdigit():
                break
            continue
        try:
            ts = int(datetime.strptime(t, "%Y-%m-%dT%H:%M:%S.%f%z").timestamp())
        except ValueError:
            continue
        src = _js_str(src).replace("‘", "'").replace("’", "'")
        out.append({"headline": _js_str(title).strip(), "summary": "", "source": src or "Webull", "domain": "",
                    "url": _js_str(url), "published": ts, "via": f"위불(Webull) {symbol}", "edition": "US", "wb_id": nid})
    return out


# ─────────────────────────────────────────────────────────────────────
# 팩트 체크
# ─────────────────────────────────────────────────────────────────────
def source_tier(source, domain):
    d = (domain or "").lower()
    if d and any(d == od or d.endswith("." + od) for od in OFFICIAL_DOMAINS):
        return "official"
    s = f"{source} {d}".lower()
    if any(k in s for k in SOURCE_TIERS["social"]):
        return "social"
    if any(k in s for k in SOURCE_TIERS["opinion"]):
        return "opinion"
    if any(k in s for k in SOURCE_TIERS["tier1"]):
        return "tier1"
    if any(k in s for k in SOURCE_TIERS["tier2"]):
        return "tier2"
    if any(k in s for k in ("press release", "newswire", "business wire", "pr newswire", "globe newswire")):
        return "official"
    return "tier3"


TIER_RANK = {"official": 4, "tier1": 3, "tier2": 2, "tier3": 1, "opinion": 0, "social": -1}


def _publisher_key(source):
    s = source.lower()
    for k in ("yahoo", "reuters", "bloomberg", "cnbc", "wsj", "wall street journal", "financial times",
              "marketwatch", "barron", "benzinga", "seeking alpha", "motley fool"):
        if k in s:
            return k
    s = re.sub(r"^www\.|\.(com|co\.uk|org|net|io|ai|news)$", "", s.strip())
    return re.sub(r"[^a-z0-9]", "", s)


def _stem(t):
    if t.isdigit() or len(t) <= 3:
        return t
    for suf in ("ing", "ed", "es", "s"):
        if t.endswith(suf) and len(t) - len(suf) >= 3:
            return t[: -len(suf)]
    return t


_CJK = re.compile(r"[\u4e00-\u9fff]+|[\uac00-\ud7a3]+")   # 중문·한글은 두 글자씩 끊어 비교


def _tokens(text):
    text = text.lower().replace("\u2019", "'").replace("'s ", " ")
    toks = (t.strip(".") for t in re.findall(r"[a-z0-9$%.]+", text))
    out = {_stem(t) for t in toks if len(t) > 1 and t not in STOPWORDS}
    # 중문은 띄어쓰기가 없어 두 글자씩 끊어 비교 (같은 사건의 중문 속보끼리 묶기 위함)
    for run in _CJK.findall(text):
        out.update(run[i:i + 2] for i in range(len(run) - 1))
    return out


def _similar(a, b, idf):
    """IDF 가중 overlap — 토픽 내 흔한 단어(ai, data, stock 등)는 영향이 작다."""
    if not a or not b:
        return 0.0
    w = lambda ts: sum(idf.get(t, 1.0) for t in ts)
    return w(a & b) / min(w(a), w(b))


def cluster(articles, threshold=0.5):
    """같은 사건을 다룬 기사끼리 묶는다 (그리디, 시간순)."""
    import math
    for a in articles:
        a["_tok"] = _tokens(a["headline"])
    df = {}
    for a in articles:
        for t in a["_tok"]:
            df[t] = df.get(t, 0) + 1
    n = max(len(articles), 1)
    idf = {t: math.log((n + 1) / (c + 0.5)) for t, c in df.items()}
    clusters = []
    for a in sorted(articles, key=lambda x: x["published"]):
        best, best_sim = None, 0.0
        for c in clusters:
            sim = max(_similar(a["_tok"], m["_tok"], idf) for m in c)
            if sim > best_sim:
                best, best_sim = c, sim
        if best is not None and best_sim >= threshold:
            best.append(a)
        else:
            clusters.append([a])
    return clusters


def _independent_groups(members):
    """같은 매체 또는 동일 헤드라인(신디케이션 전재)은 하나의 독립 출처로 본다."""
    parent = list(range(len(members)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]; i = parent[i]
        return i
    first = {}
    for i, m in enumerate(members):
        for k in ("p:" + _publisher_key(m["source"]),
                  "h:" + re.sub(r"[^a-z0-9]", "", m["headline"].lower())[:70]):
            if k in first:
                parent[find(i)] = find(first[k])
            else:
                first[k] = i
    groups = {}
    for i, m in enumerate(members):
        groups.setdefault(find(i), []).append(m)
    return list(groups.values())


def fact_check(members):
    tiers = []
    for m in members:
        m["tier"] = source_tier(m["source"], m["domain"])
        tiers.append(m["tier"])
    # SNS 게시물은 교차 검증 근거로 쓰지 않음. 전재 그룹의 등급은 보수적으로 최저 등급 적용
    pub_tiers = [min((m["tier"] for m in g), key=TIER_RANK.get)
                 for g in _independent_groups([m for m in members if m["tier"] != "social"])]
    n_pub = len(pub_tiers)
    has_official = "official" in tiers
    strong = sum(1 for t in pub_tiers if t in ("tier1", "tier2"))
    n_t1 = pub_tiers.count("tier1")
    text = " ".join(m["headline"] for m in members)
    hedged = bool(HEDGE_PATTERNS.search(text)) or (
        bool(DEALTALK_PATTERNS.search(text)) and not ONRECORD_PATTERNS.search(text))
    opinion = all(t == "opinion" or OPINION_PATTERNS.search(m["headline"]) for t, m in zip(tiers, members))
    if n_pub == 0:
        n_pub = 1

    notes = []
    if has_official:
        verdict = "VERIFIED"; notes.append("공식 발표/보도자료 확인")
    elif strong >= 2 or (n_t1 >= 1 and n_pub >= 3):
        verdict = "VERIFIED"; notes.append(f"독립 매체 {n_pub}곳 교차 확인")
    elif strong >= 1:
        verdict = "SINGLE"; notes.append("주요 매체 단독 보도 — 후속 확인 필요")
    else:
        verdict = "UNVERIFIED"; notes.append("신뢰도 낮은 출처만 존재")
    if hedged and not has_official:
        notes.append("익명 소식통·보도 인용 표현 포함 (공식 확인 전)")
        if verdict == "VERIFIED" and n_t1 < 2:
            verdict = "SINGLE"
    if opinion:
        notes.append("의견/분석성 기사")

    lead = max(members, key=lambda m: (TIER_RANK[m["tier"]], -m["published"]))
    return {
        "verdict": verdict,
        "notes": notes,
        "is_opinion": opinion,
        "is_rumor": hedged and not has_official,
        "n_sources": n_pub,
        "lead": lead,
    }


BULL = re.compile(r"surge|soar|jump|beat|record|rall(y|ies)|gain|upgrade|raise|strong|win|approv|deal|partnership|expand|"
                  r"급등|상승|강세|호실적|어닝 서프라이즈|수주|최대|신고가|돌파|상향|흑자|승인|계약", re.I)
BEAR = re.compile(r"miss|cut|decline|drop|fall|plunge|slump|warn|downgrade|loss|crash|probe|lawsuit|recall|delay|ban|halt|tariff|layoff|"
                  r"급락|하락|약세|부진|적자|하향|어닝 쇼크|소송|리콜|우려|압수수색|제재|감소", re.I)


def _sentiment(text):
    b, s = len(BULL.findall(text)), len(BEAR.findall(text))
    return "bullish" if b > s else "bearish" if s > b else "neutral"


# ─────────────────────────────────────────────────────────────────────
# 브리핑 빌드
# ─────────────────────────────────────────────────────────────────────
def _fmt_kst(ts):
    return datetime.fromtimestamp(ts, tz=KST).strftime("%m-%d %H:%M")


# 여러 소식을 묶은 요약·정리 글은 특정 토픽 뉴스가 아니므로 제외
DIGEST = re.compile(r"你需要了解|要闻|早餐|晚报|午报|日报|一览|汇总|盘点|速览|week ahead|what to watch|morning brief|calendar", re.I)
FEED_VIAS = {"FinancialJuice", "월스트리트견문(华尔街见闻)", "동방재부(东方财富) 7x24"}
FJ_URL = "https://www.financialjuice.com/feed.ashx?xy=1"
WSCN_URL = "https://api-one.wallstcn.com/apiv1/content/lives?channel=global-channel&limit=100"
BROWSER_HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}


def fetch_financialjuice():
    """FinancialJuice 실시간 헤드라인 (최근 100건). 'FinancialJuice: 제목 — 원출처' 형식."""
    try:
        root = ET.fromstring(requests.get(FJ_URL, timeout=15, headers=BROWSER_HDR).content)
    except Exception as ex:
        print(f"[scanner/financialjuice] {ex}")
        return []
    out = []
    for item in root.findall(".//item"):
        title = re.sub(r"^FinancialJuice:\s*", "", (item.findtext("title") or "").strip())
        try:
            ts = int(parsedate_to_datetime(item.findtext("pubDate") or "").timestamp())
        except Exception:
            continue
        if title and not DIGEST.search(title):
            out.append({"headline": title, "summary": "", "source": "FinancialJuice", "domain": "financialjuice.com",
                        "url": (item.findtext("link") or "").strip(), "published": ts,
                        "via": "FinancialJuice", "edition": "US"})
    # 일일 PDF 용 누적(scripts/fj_pdf.py --accumulate)이 같은 피드를 다시 요청하지 않도록 받은 목록을 남겨 둔다
    dump = os.environ.get("FJ_DUMP")
    if dump and out:
        try:
            os.makedirs(os.path.dirname(dump) or ".", exist_ok=True)
            json.dump([{"ts": a["published"], "headline": a["headline"], "source": "FinancialJuice", "url": a["url"]}
                       for a in out], open(dump, "w", encoding="utf-8"), ensure_ascii=False)
        except Exception as ex:
            print(f"[scanner/financialjuice] dump: {ex}")
    return out


def fetch_wallstreetcn(start):
    """월스트리트견문(华尔街见闻) 실시간 속보. 100건씩 거슬러 올라가며 start 시각까지 (최대 6페이지)."""
    out, cursor = [], None
    for _ in range(6):
        url = WSCN_URL + (f"&cursor={cursor}" if cursor else "")
        try:
            data = requests.get(url, timeout=15, headers=BROWSER_HDR).json().get("data", {})
        except Exception as ex:
            print(f"[scanner/wallstreetcn] {ex}")
            break
        items = data.get("items") or []
        for it in items:
            ts = int(it.get("display_time") or 0)
            text = re.sub(r"\s+", " ", (it.get("title") or it.get("content_text") or "")).strip()
            if not ts or not text or DIGEST.search(text):
                continue
            body = re.sub(r"\s+", " ", it.get("content_text") or "").strip()
            out.append({"headline": text[:140], "summary": body[:400] if body != text else "",
                        "source": "월스트리트견문", "domain": "wallstreetcn.com",
                        "url": it.get("uri") or "https://wallstreetcn.com/live", "published": ts,
                        "via": "월스트리트견문(华尔街见闻)", "edition": "CN"})
        cursor = data.get("next_cursor")
        if not items or not cursor or min(int(i.get("display_time") or 0) for i in items) < start.timestamp():
            break
    return out


# 언론사·기관 RSS (사장님 사이트 목록 중 서버에서 접속되는 곳) — 토픽 구분 없는 피드라 위 속보 피드처럼
# 제목에 토픽별 고유 명칭(FEED_KEYWORDS)이 있고 토픽 단어(_kw)도 맞을 때만 그 토픽에 넣는다
RSS_FEEDS = [
    # (출처 이름 — 등급 판정에 쓰임, 주소, 도메인)
    ("CNBC", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114", "cnbc.com"),
    ("CNBC", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=15839069", "cnbc.com"),
    ("CNBC", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258", "cnbc.com"),
    ("CNBC", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=19854910", "cnbc.com"),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories", "marketwatch.com"),
    ("Wall Street Journal", "https://feeds.content.dowjones.io/public/rss/RSSMarketsMain", "wsj.com"),
    ("Wall Street Journal", "https://feeds.content.dowjones.io/public/rss/RSSWSJD", "wsj.com"),
    ("Financial Times", "https://www.ft.com/rss/home", "ft.com"),
    ("The Economist", "https://www.economist.com/finance-and-economics/rss.xml", "economist.com"),
    ("Investing.com", "https://www.investing.com/rss/news_25.rss", "investing.com"),
    ("InvestingLive", "https://investinglive.com/feed", "investinglive.com"),
    ("Seeking Alpha", "https://seekingalpha.com/market_currents.xml", "seekingalpha.com"),
    ("Business Insider", "https://markets.businessinsider.com/rss/news", "businessinsider.com"),
    ("Politico", "https://rss.politico.com/politics-news.xml", "politico.com"),
    ("ZeroHedge", "https://feeds.feedburner.com/zerohedge/feed", "zerohedge.com"),
    ("MishTalk", "https://mishtalk.com/feed", "mishtalk.com"),
    # 산업 전문지
    ("Electrek", "https://electrek.co/feed/", "electrek.co"),
    ("CnEVPost", "https://cnevpost.com/feed/", "cnevpost.com"),
    ("The Register", "https://www.theregister.com/headlines.atom", "theregister.com"),
    ("EE Times", "https://www.eetimes.com/feed/", "eetimes.com"),
    ("Digitimes", "https://www.digitimes.com/rss/daily.xml", "digitimes.com"),
    ("MacRumors", "https://feeds.macrumors.com/MacRumors-All", "macrumors.com"),
    ("AppleInsider", "https://appleinsider.com/rss/news/", "appleinsider.com"),
    ("OilPrice", "https://oilprice.com/rss/main", "oilprice.com"),
    ("Rigzone", "https://www.rigzone.com/news/rss/rigzone_latest.aspx", "rigzone.com"),
    ("Natural Gas Intelligence", "https://www.naturalgasintel.com/feed/", "naturalgasintel.com"),
    ("Hellenic Shipping News", "https://www.hellenicshippingnews.com/feed/", "hellenicshippingnews.com"),
    ("Mining.com", "https://www.mining.com/feed/", "mining.com"),
    # 블룸버그 섹션 RSS (최신 기사)
    ("Bloomberg", "https://feeds.bloomberg.com/markets/news.rss", "bloomberg.com"),
    ("Bloomberg", "https://feeds.bloomberg.com/technology/news.rss", "bloomberg.com"),
    ("Bloomberg", "https://feeds.bloomberg.com/economics/news.rss", "bloomberg.com"),
    ("Bloomberg", "https://feeds.bloomberg.com/politics/news.rss", "bloomberg.com"),
    ("Bloomberg", "https://feeds.bloomberg.com/industries/news.rss", "bloomberg.com"),
    ("Bloomberg", "https://feeds.bloomberg.com/business/news.rss", "bloomberg.com"),
    ("Bloomberg", "https://feeds.bloomberg.com/crypto/news.rss", "bloomberg.com"),
    ("Bloomberg", "https://feeds.bloomberg.com/news.rss", "bloomberg.com"),
    # FJ 뉴스 소스 (FinancialJuice 설정에서 고른 곳들)
    ("CNBC", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=15837362", "cnbc.com"),
    ("CNBC", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=15839135", "cnbc.com"),
    ("CNBC", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=19794221", "cnbc.com"),
    ("CNBC", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=19832390", "cnbc.com"),
    ("Wall Street Journal", "https://feeds.content.dowjones.io/public/rss/RSSWorldNews", "wsj.com"),
    ("Wall Street Journal", "https://feeds.content.dowjones.io/public/rss/WSJcomUSBusiness", "wsj.com"),
    ("Wall Street Journal", "https://feeds.content.dowjones.io/public/rss/socialeconomyfeed", "wsj.com"),
    ("Financial Times", "https://www.ft.com/news-feed?format=rss", "ft.com"),
    ("Benzinga", "https://www.benzinga.com/feed", "benzinga.com"),
    ("Yahoo Finance", "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EGSPC,%5EIXIC,%5EDJI&region=US&lang=en-US", "finance.yahoo.com"),
    ("New York Times", "https://rss.nytimes.com/services/xml/rss/nyt/Dealbook.xml", "nytimes.com"),
    ("South China Morning Post", "https://www.scmp.com/rss/91/feed/", "scmp.com"),
    ("South China Morning Post", "https://www.scmp.com/rss/4/feed/", "scmp.com"),
    ("South China Morning Post", "https://www.scmp.com/rss/5/feed/", "scmp.com"),
    ("Economic Times", "https://economictimes.indiatimes.com/markets/commodities/rssfeeds/1808152121.cms", "economictimes.com"),
    ("SpotGamma", "https://spotgamma.com/feed/", "spotgamma.com"),
    ("Stock Titan", "https://www.stocktitan.net/rss", "stocktitan.net"),
    ("Endpoints News", "https://endpts.com/feed/", "endpts.com"),
    ("BioPharma Dive", "https://www.biopharmadive.com/feeds/news/", "biopharmadive.com"),
    ("STAT", "https://www.statnews.com/feed/", "statnews.com"),
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/", "coindesk.com"),
    ("Cointelegraph", "https://cointelegraph.com/rss", "cointelegraph.com"),
    ("The Block", "https://www.theblock.co/rss.xml", "theblock.co"),
    # 공식 기관
    ("Federal Reserve", "https://www.federalreserve.gov/feeds/press_all.xml", "federalreserve.gov"),
    ("Federal Reserve", "https://www.federalreserve.gov/feeds/speeches_and_testimony.xml", "federalreserve.gov"),
    ("BEA", "https://apps.bea.gov/rss/rss.xml", "bea.gov"),
    ("EIA", "https://www.eia.gov/rss/todayinenergy.xml", "eia.gov"),
    ("EIA", "https://www.eia.gov/rss/press_rss.xml", "eia.gov"),
    ("FDA", "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/press-releases/rss.xml", "fda.gov"),
    ("White House", "https://www.whitehouse.gov/news/feed/", "whitehouse.gov"),
]
_ATOM = "{http://www.w3.org/2005/Atom}"


def _feed_ts(s):
    s = (s or "").strip()
    try:
        return int(parsedate_to_datetime(s).timestamp())
    except Exception:
        pass
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except Exception:
        return 0


def fetch_rss(name, url, domain):
    try:
        root = ET.fromstring(requests.get(url, timeout=15, headers=BROWSER_HDR).content)
    except Exception as ex:
        print(f"[scanner/rss] {name}: {ex}")
        return []
    out = []
    for it in root.findall(".//item") + root.findall(f".//{_ATOM}entry"):
        title = re.sub(r"\s+", " ", it.findtext("title") or it.findtext(f"{_ATOM}title") or "").strip()
        link = (it.findtext("link") or "").strip()
        if not link:
            el = it.find(f"{_ATOM}link")
            link = el.get("href", "") if el is not None else ""
        ts = _feed_ts(it.findtext("pubDate") or it.findtext("{http://purl.org/dc/elements/1.1/}date")
                      or it.findtext(f"{_ATOM}published") or it.findtext(f"{_ATOM}updated"))
        desc = re.sub(r"<[^>]+>|\s+", " ", it.findtext("description") or it.findtext(f"{_ATOM}summary") or "").strip()
        if title and ts and not DIGEST.search(title):
            out.append({"headline": title, "summary": desc[:400], "source": name, "domain": domain, "url": link,
                        "published": ts, "via": f"RSS ({name})", "edition": "US"})
    return out


FV_ROW = re.compile(r'<td width="130" align="right">\s*([^<]+?)\s*</td>.*?class="tab-link-news" href="([^"]+)"[^>]*>\s*(.*?)\s*</a>'
                    r'.*?<span>\(([^)]*)\)</span>', re.S)
_fv_cache, _fv_lock = {}, threading.Lock()


def finviz_dump(path):
    """이번 실행에서 받은 Finviz 종목 뉴스를 파일로 (같은 실행의 다른 단계 — 미국 테마 — 가 다시 요청하지 않도록)"""
    with _fv_lock:
        data = {k: v for k, v in _fv_cache.items() if v}
    try:
        old = json.load(open(path, encoding="utf-8"))
    except Exception:
        old = {}
    old.update(data)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    json.dump(old, open(path, "w", encoding="utf-8"), ensure_ascii=False)


def finviz_preload(path, max_age=900):
    """finviz_dump 로 남긴 결과(15분 이내 파일)를 캐시에 올려 둔다"""
    try:
        if time.time() - os.path.getmtime(path) > max_age:
            return 0
        data = json.load(open(path, encoding="utf-8"))
    except Exception:
        return 0
    with _fv_lock:
        for k, v in data.items():
            _fv_cache.setdefault(k, v)
    return len(data)


def fetch_finviz(sym):
    """Finviz 종목 페이지 뉴스 표 (최근 100건, 시각은 미국 동부). 한 번 실행 안에서는 종목당 한 번만 요청."""
    with _fv_lock:
        if sym in _fv_cache:
            return _fv_cache[sym]
    out = []
    try:
        h = ""
        for i in range(3):   # 너무 몰아 요청하면 429 → 잠깐 쉬고 다시
            r = requests.get(f"https://finviz.com/quote.ashx?t={sym}&p=d", headers=BROWSER_HDR, timeout=20)
            if r.status_code == 200:
                h = r.text
                break
            time.sleep(3 * (i + 1))
        if not h:
            print(f"[scanner/finviz] {sym}: HTTP {r.status_code}")
        et, day = ZoneInfo("America/New_York"), None
        for when, url, title, src in FV_ROW.findall(h[h.find('id="news-table"'):]):
            parts = when.split()
            try:
                if len(parts) == 2:
                    day = datetime.strptime(parts[0], "%b-%d-%y").date() if parts[0] != "Today" else datetime.now(et).date()
                if day is None:
                    continue
                ts = int(datetime.combine(day, datetime.strptime(parts[-1], "%I:%M%p").time(), tzinfo=et).timestamp())
            except Exception:
                continue
            title = re.sub(r"<[^>]+>", "", title).replace("&amp;", "&").replace("&#39;", "'").replace("&quot;", '"').strip()
            out.append({"headline": title, "summary": "", "source": src.strip() or "Finviz", "domain": _domain(url),
                        "url": url, "published": ts, "via": f"Finviz ({sym})", "edition": "US"})
    except Exception as ex:
        print(f"[scanner/finviz] {sym}: {ex}")
    with _fv_lock:
        _fv_cache[sym] = out
    return out


EASTMONEY_URL = ("https://np-listapi.eastmoney.com/comm/web/getFastNewsList?client=web&biz=web_724&fastColumn=102"
                 "&pageSize=100&req_trace=1&sortEnd={}")


def fetch_eastmoney(start):
    """동방재부(东方财富) 7x24 속보 — 월스트리트견문처럼 거슬러 올라가며 start 시각까지 (최대 5페이지)."""
    out, cursor = [], ""
    for _ in range(5):
        try:
            data = requests.get(EASTMONEY_URL.format(cursor), timeout=15, headers=BROWSER_HDR).json().get("data") or {}
        except Exception as ex:
            print(f"[scanner/eastmoney] {ex}")
            break
        items = data.get("fastNewsList") or []
        oldest = None
        for it in items:
            try:
                ts = int(datetime.strptime(it["showTime"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())
            except Exception:
                continue
            oldest = ts if oldest is None else min(oldest, ts)
            title = re.sub(r"\s+", " ", it.get("title") or "").strip()
            if not title or DIGEST.search(title):
                continue
            body = re.sub(r"^【[^】]*】", "", it.get("summary") or "").strip()
            out.append({"headline": title[:140], "summary": body[:400], "source": "동방재부", "domain": "eastmoney.com",
                        "url": f"https://finance.eastmoney.com/a/{it.get('code', '')}.html", "published": ts,
                        "via": "동방재부(东方财富) 7x24", "edition": "CN"})
        cursor = data.get("sortEnd")
        if not items or not cursor or (oldest and oldest < start.timestamp()):
            break
    return out


def scan_topic(topic, start, end, finnhub_token, pool, feed_items=()):
    futs = [pool.submit(fetch_gnews, q, ed, start, end) for q, ed in topic["queries"]]
    futs += [pool.submit(fetch_finnhub_company, sym, start, end, finnhub_token) for sym in topic["tickers"]]
    futs += [pool.submit(fetch_webull, sym) for sym in topic["tickers"]]
    futs += [pool.submit(fetch_finviz, sym) for sym in topic["tickers"]]
    raw = []
    for f in futs:
        raw += f.result()
    # 속보 피드는 헤드라인에 토픽별 고유 명칭이 들어간 것만 (본문까지 보면 무관한 언급이 섞임)
    raw += [a for a in feed_items if topic["_feed"].search(a["headline"])
            and (topic["_must"] is None or topic["_must"].search(a["headline"]))]
    if topic["_exclude"] is not None:
        raw = [a for a in raw if not topic["_exclude"].search(a["headline"])]

    s_ts, e_ts = int(start.timestamp()), int(end.timestamp())
    seen, arts = set(), []
    for a in raw:
        if not a["headline"] or not (s_ts <= a["published"] <= e_ts):
            continue
        if a["via"] not in FEED_VIAS and not topic["_kw"].search(a["headline"] + " " + a["summary"]):
            continue
        if topic["_must"] is not None and not topic["_must"].search(a["headline"]):
            continue
        key = (_publisher_key(a["source"]), re.sub(r"\W", "", a["headline"].lower())[:60])
        if key in seen:
            continue
        seen.add(key); arts.append(a)

    stories = []
    for members in cluster(arts):
        fc = fact_check(members)
        lead = fc.pop("lead")
        members.sort(key=lambda m: (-TIER_RANK[m["tier"]], m["published"]))
        first_ts = min(m["published"] for m in members)
        stories.append({
            "headline": lead["headline"],
            "summary": next((m["summary"] for m in members if m["summary"]), ""),
            "url": lead["url"],
            "first_seen": first_ts,
            "first_seen_kst": _fmt_kst(first_ts),
            "last_seen": max(m["published"] for m in members),
            "last_seen_kst": _fmt_kst(max(m["published"] for m in members)),
            "regions": sorted({m["edition"] for m in members}),
            "sentiment": _sentiment(" ".join(m["headline"] for m in members)),
            **fc,
            "sources": [{
                "name": m["source"], "tier": m["tier"], "url": m["url"], "headline": m["headline"],
                "published": m["published"], "time_kst": _fmt_kst(m["published"]), "via": m["via"],
            } for m in members],
        })
    vrank = {"VERIFIED": 2, "SINGLE": 1, "UNVERIFIED": 0}
    stories.sort(key=lambda s: (not s["is_opinion"], vrank[s["verdict"]], s["n_sources"], s["first_seen"]),
                 reverse=True)
    return {"id": topic["id"], "section": topic["section"], "group": topic["group"], "name": topic["name"],
            "count": len(stories), "stories": stories}


def build_briefing(start, end, mode, finnhub_token):
    t0 = time.time()
    # 토픽별 처리는 병렬로, 실제 HTTP 호출은 별도 풀(8개)에서 — 서로 다른 풀이라 교착 없음
    with ThreadPoolExecutor(max_workers=8) as fetch_pool, ThreadPoolExecutor(max_workers=len(TOPICS)) as topic_pool:
        fj, ws = fetch_pool.submit(fetch_financialjuice), fetch_pool.submit(fetch_wallstreetcn, start)
        em = fetch_pool.submit(fetch_eastmoney, start)
        rss = [fetch_pool.submit(fetch_rss, *f) for f in RSS_FEEDS]
        rss_items = [a for f in rss for a in f.result()]
        feed_items = fj.result() + ws.result() + em.result() + rss_items
        print(f"[scanner] feeds: FinancialJuice {len(fj.result())}, 월스트리트견문 {len(ws.result())}, "
              f"동방재부 {len(em.result())}, RSS {len(rss_items)}건/{len(RSS_FEEDS)}곳")
        topics = list(topic_pool.map(lambda t: scan_topic(t, start, end, finnhub_token, fetch_pool, feed_items), TOPICS))
    if os.environ.get("FINVIZ_DUMP"):
        finviz_dump(os.environ["FINVIZ_DUMP"])
    stats = {"VERIFIED": 0, "SINGLE": 0, "UNVERIFIED": 0, "opinion": 0, "rumor": 0, "total": 0}
    for t in topics:
        for s in t["stories"]:
            stats["total"] += 1
            stats[s["verdict"]] += 1
            stats["opinion"] += s["is_opinion"]
            stats["rumor"] += s["is_rumor"]
    print(f"[scanner] {mode} {start:%m-%d %H:%M}~{end:%m-%d %H:%M} KST: "
          f"{stats['total']} stories in {time.time() - t0:.0f}s")
    return {
        "status": "ready",
        "mode": mode,
        "window": {"start": start.isoformat(), "end": end.isoformat(),
                   "label": f"{start:%Y-%m-%d %H:%M} ~ {end:%Y-%m-%d %H:%M} KST"},
        "generated_at": datetime.now(KST).isoformat(timespec="seconds"),
        "stats": stats,
        "topics": topics,
    }


# ─────────────────────────────────────────────────────────────────────
# 스케줄러 + 캐시
# ─────────────────────────────────────────────────────────────────────
class Scanner:
    """Flask 서버 모드: 조회 시점 기준 12시간 창을 15분마다 갱신."""
    def __init__(self, finnhub_token):
        self.token = finnhub_token
        self.live = None
        self.lock = threading.Lock()

    def _build_live(self):
        start, end = live_window(hours=LIVE_SCAN_HOURS)
        data = build_briefing(start, end, "live", self.token)
        with self.lock:
            self.live = data

    def get_live(self):
        with self.lock:
            return self.live or {"status": "building", "mode": "live", "message": "뉴스 스캔 중… (약 1~2분 소요)"}

    def loop(self):
        while True:
            try:
                self._build_live()
            except Exception as ex:
                print(f"[scanner] loop: {ex}")
            time.sleep(LIVE_REFRESH_SEC)
