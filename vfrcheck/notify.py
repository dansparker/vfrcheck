"""Benachrichtigung über Telegram, E-Mail und/oder Signal (CallMeBot). Alles optional via Umgebungsvariablen."""
import os
import smtplib
from email.message import EmailMessage

import requests

env = os.environ.get


def send(subject, text, images=()):
    sent = []
    for name, fn in (("telegram", _telegram), ("email", _email), ("signal", _signal)):
        try:
            if fn(subject, text, images):
                sent.append(name)
        except Exception as e:
            print(f"{name}: Versand fehlgeschlagen: {e}")
    return sent


def email(subject, text, images=()):
    try:
        return _email(subject, text, images)
    except Exception as e:
        print(f"email: Versand fehlgeschlagen: {e}")
        return False


def _telegram(subject, text, images):
    token, chat = env("TELEGRAM_BOT_TOKEN"), env("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return False
    api = f"https://api.telegram.org/bot{token}"
    requests.post(f"{api}/sendMessage", data={"chat_id": chat, "text": f"{subject}\n\n{text}"[:4000]},
                  timeout=30).raise_for_status()
    for name, img in images:
        requests.post(f"{api}/sendPhoto", data={"chat_id": chat, "caption": name},
                      files={"photo": (name, img)}, timeout=60).raise_for_status()
    return True


def _email(subject, text, images):
    host, to = env("SMTP_HOST"), env("MAIL_TO")
    if not (host and to):
        return False
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, env("MAIL_FROM") or env("SMTP_USER"), to
    msg.set_content(text)
    for name, img in images:
        msg.add_attachment(img, maintype="image", subtype="png", filename=name)
    with smtplib.SMTP(host, int(env("SMTP_PORT") or 587), timeout=30) as s:
        s.starttls()
        s.login(env("SMTP_USER"), env("SMTP_PASS"))
        s.send_message(msg)
    return True


def _signal(subject, text, images):
    # CallMeBot: kostenloser Signal-Gateway, nur an die eigene Nummer (siehe README)
    phone, key = env("CALLMEBOT_PHONE"), env("CALLMEBOT_APIKEY")
    if not (phone and key):
        return False
    requests.get("https://signal.callmebot.com/signal/send.php",
                 params={"phone": phone, "apikey": key, "text": f"{subject}\n{text}"[:1500]},
                 timeout=30).raise_for_status()
    return True
