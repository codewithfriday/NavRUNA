"""Alerts: in-app list, e-mail, signed webhook and Telegram (FR-CORE-020..024).

Messages leaving the platform carry only public data: no callsigns, registrations or operator names (SEC-002).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import smtplib
from datetime import timedelta
from email.message import EmailMessage

import httpx
from sqlalchemy.orm import Session

from .. import config
from ..models import Alert, AppSetting, now

log = logging.getLogger("navruna.alerts")
CHANNELS = ("email", "webhook", "telegram")
ALERT_TYPES = ("SPI", "GA", "TDM", "EWS", "COVERAGE", "JOB")
DEFAULT_ROUTING = {t: {"email": True, "webhook": True, "telegram": t in ("SPI", "GA", "EWS")} for t in ALERT_TYPES}


def routing(db: Session) -> dict:
    s = db.get(AppSetting, "alert_routing")
    r = {k: dict(v) for k, v in DEFAULT_ROUTING.items()}
    if s:
        for t, ch in (s.value or {}).items():
            if t in r:
                r[t].update({c: bool(v) for c, v in ch.items() if c in CHANNELS})
    return r


def channel_status() -> dict:
    return {
        "email": {"configured": bool(config.SMTP_HOST and config.ALERT_EMAILS), "host": config.SMTP_HOST,
                  "recipients": len(config.ALERT_EMAILS)},
        "webhook": {"configured": bool(config.WEBHOOK_URL), "url": config.WEBHOOK_URL.split("?")[0],
                    "signed": bool(config.WEBHOOK_SECRET)},
        "telegram": {"configured": bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID),
                     "token": config.masked(config.TELEGRAM_BOT_TOKEN), "chat": config.masked(config.TELEGRAM_CHAT_ID)},
    }


def raise_alert(db: Session, source: str, ref: str, level: int, title: str, message: str, public_message: str,
                breakdown_key: str = "", link: str = "", data: dict | None = None, quiet_hours: float = 24) -> Alert:
    """Create or update the open alert for (source, ref, breakdown); notify when new, when the level changes,
    or after the quiet period (FR-CORE-022)."""
    a = (db.query(Alert).filter_by(source=source, ref=ref, breakdown_key=breakdown_key)
         .filter(Alert.status != "closed").order_by(Alert.id.desc()).first())
    t = now()
    if a is None:
        a = Alert(source=source, ref=ref, breakdown_key=breakdown_key, level=level, title=title, message=message,
                  public_message=public_message, link=link, data=data or {}, first_seen=t, last_seen=t)
        db.add(a)
        db.flush()
    else:
        a.last_seen, a.title, a.message, a.public_message, a.data = t, title, message, public_message, data or a.data
        if level != a.level:
            a.level = level
            if a.status == "acknowledged" and level > a.sent_level:
                a.status = "open"
    send = a.last_sent is None or a.level != a.sent_level or (t - a.last_sent) > timedelta(hours=quiet_hours)
    if send:
        deliver(db, a)
    db.commit()
    return a


def close_alert(db: Session, source: str, ref: str, breakdown_key: str = ""):
    for a in db.query(Alert).filter_by(source=source, ref=ref, breakdown_key=breakdown_key).filter(Alert.status != "closed"):
        a.status = "closed"
    db.commit()


def deliver(db: Session, a: Alert):
    r = routing(db).get(a.source, {})
    text = f"[NavRUNA] {a.public_message or a.title}\n{config.PUBLIC_URL.rstrip('/')}{a.link}"
    results = []
    for ch in CHANNELS:
        if not r.get(ch):
            continue
        try:
            ok = {"email": _email, "webhook": _webhook, "telegram": _telegram}[ch](a, text)
            if ok is not None:
                results.append({"channel": ch, "at": now().isoformat(timespec="seconds"), "ok": ok})
        except Exception as e:  # noqa: BLE001 - a failing channel must not stop processing (NFR-011)
            log.warning("alert delivery via %s failed: %s", ch, e)
            results.append({"channel": ch, "at": now().isoformat(timespec="seconds"), "ok": False, "error": str(e)[:200]})
    a.delivery = (a.delivery or [])[-20:] + results
    a.last_sent, a.sent_level = now(), a.level


def _email(a: Alert, text: str):
    if not (config.SMTP_HOST and config.ALERT_EMAILS):
        return None
    msg = EmailMessage()
    msg["Subject"] = f"NavRUNA alert (level {a.level}): {a.public_message or a.title}"[:200]
    msg["From"] = config.SMTP_FROM
    msg["To"] = ", ".join(config.ALERT_EMAILS)
    msg.set_content(text + "\n\n" + config.DISCLAIMER)
    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=20) as s:
        if config.SMTP_TLS:
            s.starttls()
        if config.SMTP_USER:
            s.login(config.SMTP_USER, config.SMTP_PASSWORD)
        s.send_message(msg)
    return True


def sign(body: bytes) -> str:
    return hmac.new(config.WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()


def _webhook(a: Alert, text: str):
    if not config.WEBHOOK_URL:
        return None
    payload = {"source": "NavRUNA", "alert_id": a.id, "type": a.source, "ref": a.ref, "level": a.level,
               "title": a.public_message or a.title, "first_seen": a.first_seen.isoformat(), "status": a.status,
               "link": f"{config.PUBLIC_URL.rstrip('/')}{a.link}", "breakdown": a.breakdown_key}
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if config.WEBHOOK_SECRET:
        headers["X-NavRUNA-Signature"] = "sha256=" + sign(body)   # FR-CORE-024
    r = httpx.post(config.WEBHOOK_URL, content=body, headers=headers, timeout=15)
    return r.status_code < 300


def _telegram(a: Alert, text: str):
    if not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID):
        return None
    r = httpx.post(f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
                   json={"chat_id": config.TELEGRAM_CHAT_ID, "text": text, "disable_web_page_preview": True}, timeout=15)
    return r.status_code < 300


def test_channel(channel: str) -> dict:
    a = Alert(id=0, source="JOB", ref="test", level=1, title="Test alert", public_message="Test alert from NavRUNA",
              link="/alerts", first_seen=now(), status="open")
    try:
        ok = {"email": _email, "webhook": _webhook, "telegram": _telegram}[channel](a, "[NavRUNA] Test alert")
        return {"ok": bool(ok), "configured": ok is not None}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "configured": True, "error": str(e)[:300]}
