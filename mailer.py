"""
mailer.py — Gmail SMTP email sender for internship applications.
Provides a shared send_email() function used by both
Test Email and Send Application features.
"""

import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from pathlib import Path

from config import config

log = logging.getLogger("hunter")


try:
    from cv_reader import load_cv_profile
    _has_cv_reader = True
except Exception:
    _has_cv_reader = False


# ── Public API ─────────────────────────────────────────────────────────────

def get_sender_name() -> str:
    """Get sender name from CV profile, or return placeholder."""
    if _has_cv_reader and config.cv_pdf_path:
        try:
            profile = load_cv_profile(config.cv_pdf_path)
            name = profile.get("name", "").strip()
            if name:
                return name
        except Exception:
            pass
    return ""


def send_email(
    to_addr: str,
    subject: str,
    body: str,
    cv_path: str = "",
    cfg=None,
) -> dict:
    """
    Send an email via Gmail SMTP.
    Returns {"success": True} or {"success": False, "error": "..."}.

    Args:
        to_addr: Recipient email address
        subject: Email subject
        body: Email body (plain text)
        cv_path: Optional path to CV PDF to attach
        cfg: Optional config object (uses global config if None)
    """
    if cfg is None:
        cfg = config

    email_addr = cfg.email or ""
    email_pw = cfg.email_password or ""

    if not email_addr or not email_pw:
        return {"success": False, "error": "Gmail email or app password not configured"}

    try:
        msg = MIMEMultipart()
        msg["From"] = email_addr
        msg["To"] = to_addr
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))

        # Attach CV PDF if provided
        if cv_path and Path(cv_path).exists():
            cv_file = Path(cv_path)
            with open(cv_file, "rb") as f:
                part = MIMEApplication(f.read(), Name=cv_file.name)
            part["Content-Disposition"] = f'attachment; filename="{cv_file.name}"'
            msg.attach(part)
            log.info(f"[mailer] Attached CV: {cv_file.name}")

        # Send via Gmail SMTP
        server = smtplib.SMTP("smtp.gmail.com", 587, timeout=30)
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(email_addr, email_pw)
        server.sendmail(email_addr, [to_addr], msg.as_string())
        server.quit()

        log.info(f"[mailer] Email sent to {to_addr}, subject: {subject[:50]}")
        return {"success": True}

    except Exception as e:
        log.exception(f"[mailer] Failed to send email to {to_addr}")
        return {"success": False, "error": str(e)}
