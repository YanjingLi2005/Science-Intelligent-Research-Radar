"""SMTP email sending helper for Research Radar notifications.

Keeps the SMTP delivery logic in one place so the API test endpoint, scheduled
digests, and future notification channels all behave consistently.
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage
from typing import Any


def send_email(config: dict[str, Any], subject: str, body: str) -> dict[str, Any]:
    """Send one plain-text email using an SMTP configuration dict.

    Returns a small result dict on success. Raises ``ValueError`` when required
    fields are missing and propagates SMTP/network exceptions so callers can
    decide how to record failures.
    """
    smtp_host = str(config.get("smtp_host") or "").strip()
    smtp_port = int(config.get("smtp_port") or 587)
    username = str(config.get("username") or "").strip()
    password = str(config.get("password") or "")
    recipient = str(config.get("recipient") or "").strip()
    subject_prefix = str(config.get("subject_prefix") or "[Research Radar] ")

    required = {
        "smtp_host": smtp_host,
        "username": username,
        "password": password,
        "recipient": recipient,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise ValueError(
            "email notification is missing required fields: " + ", ".join(missing)
        )

    message = EmailMessage()
    message["Subject"] = f"{subject_prefix}{subject}"
    message["From"] = username
    message["To"] = recipient
    message.set_content(body)

    smtp = None
    try:
        if smtp_port == 465:
            smtp = smtplib.SMTP_SSL(smtp_host, smtp_port)
        else:
            smtp = smtplib.SMTP(smtp_host, smtp_port)
            smtp.starttls()
        smtp.login(username, password)
        smtp.sendmail(username, [recipient], message.as_string())
    finally:
        if smtp is not None:
            try:
                smtp.quit()
            except Exception:
                pass
    return {"sent": True, "recipient": recipient}
