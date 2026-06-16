"""
mailer.py — Gmail SMTP email sender for internship applications.
"""

import smtplib
import time
import random
import datetime
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path
from config import config
from models import Job

log = logging.getLogger("hunter")


def send_email(job: Job, cover_letter: str, dry_run: bool = True) -> bool:
    """
    Send an application email.
    Returns True if sent (or dry_run simulated), False on failure.
    """
    if not job.contact_email:
        log.warning(f"No contact email for {job.company} - {job.title}")
        return False

    sender = config.email
    password = config.email_password
    cv_path = config.cv_pdf_path

    subject = config.email_subject_template.format(
        title=job.title,
        name="Yip Fung Ming"
    )

    if dry_run:
        log.info(f"[DRY RUN] → {job.contact_email} ({job.company})")
        log.info(f"  Subject: {subject}")
        job.status = "Applied (Dry Run)"
        job.applied_date = datetime.date.today().isoformat()
        job.cover_letter_sent = cover_letter
        return True

    if not sender or not password:
        log.error("Email credentials not configured")
        return False

    try:
        msg = MIMEMultipart()
        msg["From"] = sender
        msg["To"] = job.contact_email
        msg["Subject"] = subject
        msg.attach(MIMEText(cover_letter, "plain", "utf-8"))

        if cv_path and config.email_attach_cv:
            cv = Path(cv_path)
            if cv.exists():
                with open(cv, "rb") as f:
                    part = MIMEBase("application", "octet-stream")
                    part.set_payload(f.read())
                encoders.encode_base64(part)
                part.add_header(
                    "Content-Disposition", "attachment",
                    filename=cv.name
                )
                msg.attach(part)

        with smtplib.SMTP("smtp.gmail.com", 587) as s:
            s.starttls()
            s.login(sender, password)
            s.sendmail(sender, job.contact_email, msg.as_string())

        log.info(f"Sent -> {job.contact_email} ({job.company})")
        job.status = "Applied"
        job.applied_date = datetime.date.today().isoformat()
        job.cover_letter_sent = cover_letter
        return True

    except Exception as e:
        log.error(f"Send failed {job.contact_email}: {e}")
        return False


def send_batch(jobs_with_cl: list, dry_run: bool = True) -> dict:
    """
    Send emails for a batch of jobs with cover letters.
    jobs_with_cl: list of {"job": Job, "cover_letter": str}
    Returns: {"sent": int, "failed": int, "results": list}
    """
    sent = 0
    failed = 0
    results = []

    for i, item in enumerate(jobs_with_cl):
        job = item["job"]
        cl = item["cover_letter"]

        success = send_email(job, cl, dry_run)
        results.append({
            "job": job,
            "success": success
        })

        if success:
            sent += 1
        else:
            failed += 1

        if i < len(jobs_with_cl) - 1:
            delay = config.email_delay_seconds + random.uniform(0, 3)
            log.debug(f"Delay {delay:.1f}s before next email...")
            time.sleep(delay)

    log.info(f"Email batch complete: {sent} sent, {failed} failed")
    return {"sent": sent, "failed": failed, "results": results}
