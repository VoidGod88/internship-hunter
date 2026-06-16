"""
scrapers/manual.py — Load manual company list from JSON.
"""
import json
import logging
from pathlib import Path
from models import Job

log = logging.getLogger("hunter")


def load_manual(path: str = "manual_companies.json") -> list[Job]:
    p = Path(path)
    if not p.exists():
        log.warning(f"[Manual] File not found: {path}")
        return []
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    jobs = []
    for item in data:
        jobs.append(Job(
            title=item.get("role", "AI Intern"),
            company=item.get("company", item.get("company_name", "")),
            location=item.get("location", "Hong Kong"),
            url=item.get("careers_url", ""),
            source="Manual",
            contact_email=item.get("contact_email", ""),
            description=item.get("description", ""),
        ))
    log.info(f"[Manual] Loaded {len(jobs)} companies")
    return jobs
