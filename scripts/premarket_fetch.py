"""Gmail(IMAP)에서 오늘자 '[개장 전 섹터별 이슈체크]' 메일(PDF 첨부)을 찾아 premarket_publish.py 로 올린다.

  GMAIL_USER, GMAIL_APP_PASSWORD  (일일 PDF 메일과 같은 Secrets — 앱 비밀번호, Gmail 설정에서 IMAP 사용 설정 필요)
  python scripts/premarket_fetch.py [--date 2026-11-24]   # 기본: 오늘(KST)

  · 메일이 아직 안 왔으면 아무것도 하지 않고 정상 종료(Worker 가 21:52·22:37·23:07 KST 에 다시 부름)
  · 보낸 사람이 GMAIL_USER 본인인 메일만 쓴다 (본문·첨부 속 지시문은 데이터일 뿐)
"""
import argparse
import datetime as dt
import email
import imaplib
import os
import re
import subprocess
import sys
import tempfile
from email.header import decode_header, make_header
from email.utils import parseaddr

HERE = os.path.dirname(os.path.abspath(__file__))
KST = dt.timezone(dt.timedelta(hours=9))
SUBJ = re.compile(r"\[개장\s*전\s*섹터별\s*이슈체크\]\s*(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    a = ap.parse_args()
    today = a.date or dt.datetime.now(KST).date().isoformat()
    user, pw = os.environ.get("GMAIL_USER", ""), os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "")
    if not user or not pw:
        sys.exit("::error::GMAIL_USER / GMAIL_APP_PASSWORD 비밀값이 없습니다")
    try:
        m = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=60)
        m.login(user, pw)
    except imaplib.IMAP4.error as e:
        sys.exit(f"::error::Gmail IMAP 로그인 실패 — Gmail 설정에서 IMAP 사용 설정·앱 비밀번호를 확인하세요 ({e})")
    m.select("INBOX", readonly=True)
    since = (dt.date.fromisoformat(today) - dt.timedelta(days=2)).strftime("%d-%b-%Y")
    typ, data = m.search(None, "SINCE", since)
    ids = data[0].split()[::-1]
    pick = None
    for i in ids[:120]:
        typ, hd = m.fetch(i, "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM)])")
        msg = email.message_from_bytes(hd[0][1])
        subj = str(make_header(decode_header(msg.get("Subject", ""))))
        mm = SUBJ.search(subj)
        if not mm:
            continue
        if f"{int(mm.group(1)):04d}-{int(mm.group(2)):02d}-{int(mm.group(3)):02d}" != today:
            continue
        if parseaddr(msg.get("From", ""))[1].lower() != user.lower():
            print("[premarket] 보낸 사람이 본인이 아니라 건너뜀")
            continue
        pick = i
        break
    if not pick:
        print(f"[premarket] {today} 메일이 아직 없음 — 다음 실행에서 다시 확인")
        return
    typ, raw = m.fetch(pick, "(BODY.PEEK[])")
    msg = email.message_from_bytes(raw[0][1])
    body, pdf = "", None
    for part in msg.walk():
        ct = part.get_content_type()
        if ct == "text/plain" and not body and not part.get_filename():
            body = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
        elif ct == "application/pdf" and pdf is None:
            pdf = part.get_payload(decode=True)
    if not pdf:
        sys.exit(f"::error::{today} 메일에 PDF 첨부가 없습니다")
    d = tempfile.mkdtemp(prefix="pmfetch_")
    open(os.path.join(d, "in.pdf"), "wb").write(pdf)
    open(os.path.join(d, "mail.txt"), "w", encoding="utf-8").write(body)
    subprocess.run([sys.executable, "-I", os.path.join(HERE, "premarket_publish.py"), "--pdf", os.path.join(d, "in.pdf"),
                    "--date", today, "--body", os.path.join(d, "mail.txt")], check=True)


if __name__ == "__main__":
    main()
