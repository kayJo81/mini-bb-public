"""
일일 PDF 메일 발송 (Gmail SMTP)

  python scripts/send_mail.py reports

  필요한 저장소 Secrets: GMAIL_USER(보내는 Gmail 주소), GMAIL_APP_PASSWORD(앱 비밀번호 16자리)
  선택: MAIL_TO(받는 주소, 쉼표로 여러 개) — 없으면 GMAIL_USER 로 보냄
"""
import glob
import os
import smtplib
import sys
from email.message import EmailMessage


def main(folder):
    user, pw = os.environ.get("GMAIL_USER", ""), os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "")
    if not user or not pw:
        print("::warning::GMAIL_USER / GMAIL_APP_PASSWORD 비밀값이 없어 메일을 보내지 않습니다 (PDF는 실행 결과 Artifacts 에 있음)")
        return
    to = [x.strip() for x in (os.environ.get("MAIL_TO") or user).split(",") if x.strip()]
    files = sorted(glob.glob(os.path.join(folder, "*.pdf")))
    sub = open(os.path.join(folder, "summary.txt"), encoding="utf-8").read().strip()
    msg = EmailMessage()
    msg["Subject"] = f"[미니 블벅터] {sub.split('·')[1].strip()} 뉴스 PDF"
    msg["From"], msg["To"] = user, ", ".join(to)
    msg.set_content(f"{sub}\n\n첨부 {len(files)}개:\n" + "\n".join("· " + os.path.basename(f) for f in files)
                    + "\n\n사이트: https://mini-bbterminal.pages.dev")
    for f in files:
        with open(f, "rb") as fh:
            msg.add_attachment(fh.read(), maintype="application", subtype="pdf", filename=os.path.basename(f))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as s:
        s.login(user, pw)
        s.send_message(msg)
    print(f"[mail] {len(files)}개 PDF → {', '.join(to)}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "reports")
