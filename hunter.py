"""
hunter.py — Core orchestration engine for WIE Internship Hunter.
Handles: scraping -> filtering (WIE) -> CV matching -> sorting -> cover letter -> email.
Refactored v4 with modular architecture.
"""
import os as _os
import sys as _sys
_os.environ["PYTHONWARNINGS"] = "ignore"
# Suppress all warnings at Python level (before any imports)
import warnings
warnings.filterwarnings("ignore")

import re
import logging
import datetime
import json
from pathlib import Path
from typing import Optional

from playwright.sync_api import sync_playwright

from config import config
from models import Job
from database import (
    insert_job, get_all_jobs, get_application_history,
    update_job_status,
)
import scrapers
from mailer import send_email

log = logging.getLogger("hunter")

# ─────────────────────────────────────────────
# WIE Filter & CV Matching
# ─────────────────────────────────────────────

WIE_NEGATIVE = [
    "accounting only", "warehouse", "driver", "cleaner", "cook",
    "nurse", "doctor", "lawyer", "social work", "sales only",
    "marketing only", "customer service only",
]

# STEM Internship Scheme jobs do NOT count toward WIE hours
STEM_SCHEME_PATTERNS = [
    r"\bstem\b\s*[-–—]\s*",      # "STEM - AI Engineer"
    r"^\bstem\b",                  # "STEM Intern"
    r"\bstem\s+internship\b",      # "STEM internship"
    r"\bstem\s+scheme\b",          # "STEM scheme"
    r"stem\s+internship\s+scheme",
]

CS_KEYWORDS = [
    "software", "developer", "engineer", "ai", "machine learning", "deep learning",
    "data science", "data analyst", "nlp", "computer vision", "llm", "python",
    "backend", "frontend", "full stack", "cloud", "devops", "mlops", "research",
    "robotics", "algorithm", "database", "security", "networking", "it",
    "technology", "tech", "coding", "programming", "automation", "analytics",
    "information technology", "system", "platform", "product", "digital",
    "intern", "internship", "trainee", "attachment", "placement", "student",
]

# ── Job Type Classification (for sorting) ──
JOB_TYPE_PATTERNS = {
    "summer": [
        r"\bsummer\b", r"\bs ummer\s*intern", r"\bs ummer\s*program",
        r"\b暑期\b", r"summer\s*placement", r"\bsi\b",
    ],
    "internship": [
        r"\bintern\b", r"\binternship\b", r"\btrainee\b",
        r"\battachment\b", r"\bplacement\b", r"\bapprentice\b",
    ],
    "parttime": [
        r"\bpart[- ]?time\b", r"\b兼职\b", r"\bpart time\b",
        r"\bfreelance\b", r"\bcontract\b", r"\btemp\b",
    ],
}


def classify_job_type(job: Job) -> str:
    """Classify job into: 'summer', 'internship', 'parttime', 'other'."""
    text = (job.title + " " + job.description).lower()
    for jtype, patterns in JOB_TYPE_PATTERNS.items():
        for pat in patterns:
            if re.search(pat, text):
                return jtype
    return "other"


def sort_jobs_by_type(jobs: list[Job]) -> list[Job]:
    """Sort jobs by priority: summer > internship > parttime > other."""
    priority = {"summer": 0, "internship": 1, "parttime": 2, "other": 3}
    return sorted(jobs, key=lambda j: priority.get(getattr(j, "_job_type", "other"), 3))


# ── CV matching moved to app.py (UI handler, on-demand LLM call) ──


def normalize_company(name: str) -> str:
    n = name.lower().strip()
    n = re.sub(r"[\(\)\[\]\{\}（）【】]", " ", n)
    n = re.sub(r"[^\w\s]", " ", n)
    n = re.sub(r"\s+", " ", n)
    for suffix in ["limited", "ltd", "inc", "corp", "group", "company", "co",
                   "hk", "hong kong", "-"]:
        n = n.replace(suffix, "")
    return re.sub(r"\s+", " ", n).strip()


def check_wie(job: Job) -> tuple[bool, str]:
    if not config.wie_enabled:
        return True, "WIE filter disabled"

    text = (job.title + " " + job.description + " " + job.location).lower()
    title_lower = job.title.lower()

    # ── Rule 1: STEM Internship Scheme → NOT WIE eligible ──
    for pattern in STEM_SCHEME_PATTERNS:
        if re.search(pattern, title_lower):
            return False, "STEM Internship Scheme (not WIE eligible)"

    # Also check raw job_type if available
    raw = getattr(job, 'raw_data', None) or {}
    job_type = raw.get('job_type', '') if isinstance(raw, dict) else ''
    if 'stem' in title_lower[:20] and 'intern' in job_type.lower():
        return False, "STEM Internship Scheme (not WIE eligible)"

    if config.wie_require_hk:
        hk_locs = ["hong kong", "hk ", " hk", "kowloon", "tuen mun",
                   "sha tin", "kwun tong", "causeway bay", "central",
                   "cyberport", "hkstp", "science park", "shatin"]
        if not any(loc in text for loc in hk_locs):
            if job.location and "hong kong" not in job.location.lower():
                return False, "Not in HK"

    if config.wie_exclude_non_cs:
        for neg in WIE_NEGATIVE:
            if neg in text:
                return False, f"Negative: {neg}"
        if not any(k in text for k in CS_KEYWORDS):
            return False, "Not CS/IT"

    is_internship = any(k in text for k in ["intern", "internship", "trainee", "attachment", "placement"])
    is_summer = any(k in text for k in ["summer", "暑期", "summer program"])
    if not is_internship and not is_summer:
        return False, "Not an internship"

    if config.wie_exclude_final_year:
        if "final year" in text and "required" in text:
            return False, "Requires final year"

    return True, "CS internship in HK"


def parse_extra_docs(text: str) -> str:
    """Extract additional required documents from job description.
    Looks for: transcript, application form, cover letter, portfolio, etc."""
    if not text:
        return ""
    text_lower = text.lower()
    found = []
    doc_patterns = {
        "transcript": [r"transcript", r"成績單", r"成绩单", r"academic\s*record"],
        "application form": [r"application\s*form", r"申請表", r"申请表", r"standard\s*form"],
        "portfolio": [r"portfolio", r"作品集"],
        "cover letter": [r"cover\s*letter", r"求職信", r"求职信"],
        "reference letter": [r"reference\s*letter", r"推薦信", r"推荐信"],
        "ID copy": [r"hkid|id\s*(card|copy)", r"身份證", r"身份证"],
        "expected salary": [r"expected\s*salary|期望薪", r"current\s*salary"],
        "DSE cert": [r"dse\s*(cert|result)|hkdse|文憑試"],
        "exam results": [r"exam\s*result|public\s*exam"],
        "writing sample": [r"writing\s*sample", r"寫作樣本"],
    }
    for label, patterns in doc_patterns.items():
        for pat in patterns:
            if re.search(pat, text_lower):
                found.append(label)
                break
    return ", ".join(found) if found else ""


# ─────────────────────────────────────────────
# Core Pipeline
# ─────────────────────────────────────────────

def run_scrapers(
    keywords: Optional[list[str]] = None,
    progress_callback=None,
) -> list[Job]:
    """Run all enabled scrapers and return jobs."""
    if keywords is None:
        keywords = config.search_keywords

    all_jobs = []
    total_scrapers = sum([
        config.scraper_polyu,
        config.scraper_linkedin, config.scraper_jobsdb,
        config.scraper_indeed, config.scraper_efc, config.scraper_manual,
    ])
    done = 0

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)

        # ── LinkedIn ──
        if config.scraper_linkedin:
            if progress_callback:
                progress_callback(f"Scraping LinkedIn...")
            _cookie_path = Path(__file__).parent / "cookies" / "linkedin.json"
            try:
                page = scrapers.base.BaseScraper.init_page(
                    browser,
                    load_cookies_file=str(_cookie_path) if _cookie_path.exists() else None,
                )
                if _cookie_path.exists():
                    log.info(f"[LinkedIn] Loaded cookies from {_cookie_path}")
                lj = scrapers.scrape_linkedin(page, keywords, max_pages=0)
                all_jobs += lj
                log.info(f"LinkedIn: {len(lj)} jobs")
                # Save cookies for next run (even if 0 jobs, cookies may have been refreshed)
                try:
                    _cookie_path.parent.mkdir(parents=True, exist_ok=True)
                    page._hunter_ctx.storage_state(path=str(_cookie_path))
                    log.info(f"[LinkedIn] Saved cookies to {_cookie_path}")
                except Exception as _e:
                    log.warning(f"[LinkedIn] Failed to save cookies: {_e}")
            except Exception as e:
                log.error(f"LinkedIn error: {e}")
            done += 1

        # ── PolyU Job Board ──
        if config.scraper_polyu:
            if progress_callback:
                progress_callback("Scraping PolyU Job Board...")
            try:
                page = scrapers.base.BaseScraper.init_page(browser)
                pj = scrapers.scrape_polyu(page, keywords, max_pages=3)
                all_jobs += pj
                log.info(f"PolyU: {len(pj)} jobs")
            except Exception as e:
                log.error(f"PolyU error: {e}")
            done += 1

        # ── JobsDB ──
        if config.scraper_jobsdb:
            if progress_callback:
                progress_callback(f"Scraping JobsDB...")
            try:
                page = scrapers.base.BaseScraper.init_page(browser)
                jj = scrapers.scrape_jobsdb(page, keywords, max_pages=0)
                all_jobs += jj
                log.info(f"JobsDB: {len(jj)} jobs")
            except Exception as e:
                log.error(f"JobsDB error: {e}")
            done += 1

        # ── Indeed ──
        if config.scraper_indeed:
            if progress_callback:
                progress_callback(f"Scraping Indeed ({len(keywords)} keywords)...")
            try:
                page = scrapers.base.BaseScraper.init_page(browser)
                ij = scrapers.scrape_indeed(page, keywords, max_pages=0)
                all_jobs += ij
                log.info(f"Indeed: {len(ij)} jobs")
            except Exception as e:
                log.error(f"Indeed error: {e}")
            done += 1

        # ── eFinancialCareers ──
        if config.scraper_efc:
            if progress_callback:
                progress_callback(f"Scraping eFinancialCareers...")
            try:
                page = scrapers.base.BaseScraper.init_page(browser)
                ej = scrapers.scrape_efc(page, keywords, max_pages=5)
                all_jobs += ej
                log.info(f"eFC: {len(ej)} jobs")
            except Exception as e:
                log.error(f"eFC error: {e}")
            done += 1

        browser.close()

    # ── Manual companies ──
    if config.scraper_manual:
        if progress_callback:
            progress_callback(f"[{done+1}/{total_scrapers}] Loading manual companies...")
        manual_path = Path(__file__).parent / "manual_companies.json"
        all_jobs += scrapers.load_manual(str(manual_path))
        done += 1

    log.info(f"Raw total: {len(all_jobs)}")

    # ── Diagnose: if all external scrapers returned 0, surface a clear warning ──
    external_count = len(all_jobs) - (
        len(scrapers.load_manual(str(Path(__file__).parent / "manual_companies.json")))
        if config.scraper_manual else 0
    )
    if external_count == 0 and (config.scraper_polyu or config.scraper_linkedin or config.scraper_jobsdb
                                or config.scraper_indeed or config.scraper_efc):
        warn = (
            "⚠️ All external scrapers returned 0 jobs. "
            "Possible causes: Cloudflare block (LinkedIn/JobsDB/Indeed/eFC), "
            "or login failed (PolyU — check POLYU_NET_ID/PASSWORD in .env). "
            "Try using 'LinkedIn Login' to save cookies. "
            "Pipeline is using manual_companies.json only."
        )
        log.warning(warn)
        if progress_callback:
            progress_callback(warn)

    return all_jobs


def process_jobs(jobs: list[Job], progress_callback=None) -> list[Job]:
    """Apply WIE rule filter, extract extra docs, classify type, dedup, save to DB.
    Sorted by job type. NO LLM calls — CV matching is done on-demand from the UI.
    """
    total = len(jobs)

    # WIE filter + extra docs + classify type
    for i, job in enumerate(jobs):
        try:
            # WIE rule check (no LLM)
            job.wie_eligible, job.wie_reason = check_wie(job)

            # Extract extra docs (regex only)
            job.extra_docs = parse_extra_docs(job.description)

            # Classify job type for sorting
            job._job_type = classify_job_type(job)
        except Exception as e:
            log.error(f"Error processing job {job.title[:30]}: {e}")
            job.wie_eligible = False
            job.wie_reason = f"Error: {e}"

        if progress_callback and i % 10 == 0:
            progress_callback(f"Processing {i+1}/{total}: {job.title[:40]}...")

    # Dedup
    seen = set()
    unique = []
    for j in jobs:
        norm_company = normalize_company(j.company)
        norm_title = j.title.lower().strip()[:30]
        k = (norm_company, norm_title)
        if k not in seen:
            seen.add(k)
            unique.append(j)

    # Sort by job type priority: summer > internship > parttime > other
    unique = sort_jobs_by_type(unique)

    # Save to DB
    saved = 0
    for job in unique:
        jid = insert_job(job.to_dict())
        if jid > 0:
            job._db_id = jid
            saved += 1

    if progress_callback:
        progress_callback(f"Saved {saved} new jobs to database (duplicates skipped)")

    log.info(f"After dedup: {len(unique)} (saved: {saved} new)")
    return unique


def run_full_pipeline(
    keywords: Optional[list[str]] = None,
    progress_callback=None,
) -> dict:
    """Run the scrape + rule-based processing pipeline.
    NO LLM calls. CV matching and cover letter generation are now done
    on-demand from the UI.
    """
    if not keywords:
        keywords = config.search_keywords

    # Step 1: Scrape
    if progress_callback:
        progress_callback("Phase 1/2: Scraping jobs...")
    raw_jobs = run_scrapers(keywords, progress_callback)

    # Step 2: Process (rule-based WIE filter, no LLM)
    if progress_callback:
        progress_callback(f"Phase 2/2: Processing {len(raw_jobs)} jobs...")
    processed = process_jobs(raw_jobs, progress_callback)

    wie_count = sum(1 for j in processed if j.wie_eligible)
    if progress_callback:
        progress_callback(
            f"Done! {wie_count} WIE-eligible / {len(processed)} unique / {len(raw_jobs)} raw"
        )

    return {
        "total_raw": len(raw_jobs),
        "total_processed": len(processed),
        "wie_eligible": wie_count,
    }


# ─────────────────────────────────────────────
# CLI Entry Point (for subprocess mode)
# ─────────────────────────────────────────────

import argparse
import json
import sys
import os

def _write_status(status_file: str, data: dict):
    """Write pipeline status to a JSON file for UI polling."""
    if not status_file:
        return
    try:
        path = Path(status_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False))
    except Exception as e:
        log.warning(f"Failed to write status file: {e}")


def main():
    parser = argparse.ArgumentParser(description="WIE Internship Hunter v4 (CLI)")
    parser.add_argument("--keywords", type=str, default="", help="Comma-separated search keywords")
    parser.add_argument("--status-file", type=str, default="", help="Status JSON file for UI polling")

    # Scraper toggles
    parser.add_argument("--scraper-polyu", action="store_true", default=None)
    parser.add_argument("--scraper-linkedin", action="store_true", default=None)
    parser.add_argument("--scraper-jobsdb", action="store_true", default=None)
    parser.add_argument("--scraper-indeed", action="store_true", default=None)
    parser.add_argument("--scraper-efc", action="store_true", default=None)
    parser.add_argument("--scraper-manual", action="store_true", default=None)

    args = parser.parse_args()

    # Apply args to config (only override if explicitly set)
    if args.scraper_polyu is not None:
        config.scraper_polyu = args.scraper_polyu
    if args.scraper_linkedin is not None:
        config.scraper_linkedin = args.scraper_linkedin
    if args.scraper_jobsdb is not None:
        config.scraper_jobsdb = args.scraper_jobsdb
    if args.scraper_indeed is not None:
        config.scraper_indeed = args.scraper_indeed
    if args.scraper_efc is not None:
        config.scraper_efc = args.scraper_efc
    if args.scraper_manual is not None:
        config.scraper_manual = args.scraper_manual

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("hunter.log", encoding="utf-8"),
        ],
    )

    log.info("=" * 60)
    log.info("WIE Internship Hunter v4 (subprocess mode — scrape + filter only)")
    log.info(f"  PolyU: {config.scraper_polyu} | LinkedIn: {config.scraper_linkedin} | JobsDB: {config.scraper_jobsdb} | Indeed: {config.scraper_indeed}")
    log.info(f"  eFC: {config.scraper_efc} | Manual: {config.scraper_manual}")
    log.info("=" * 60)

    status_file = args.status_file
    _write_status(status_file, {"status": "running", "phase": "init", "message": "Starting..."})

    _phase = "init"

    def progress_cb(msg):
        log.info(msg)
        nonlocal _phase
        if "Phase 1" in msg or "Scraping" in msg:
            _phase = "scraping"
        elif "Phase 2" in msg or "Processing" in msg:
            _phase = "processing"
        elif "Done!" in msg or "Complete!" in msg:
            _phase = "done"
        _write_status(status_file, {"status": "running", "phase": _phase, "message": msg})

    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()] if args.keywords else None

    try:
        summary = run_full_pipeline(
            keywords=keywords,
            progress_callback=progress_cb,
        )

        _write_status(status_file, {
            "status": "done",
            "phase": "done",
            "message": f"Done! {summary['wie_eligible']} WIE-eligible / {summary['total_processed']} unique",
            "summary": {
                "total_raw": summary["total_raw"],
                "total_processed": summary["total_processed"],
                "wie_eligible": summary["wie_eligible"],
            }
        })

        log.info("\n" + "=" * 60)
        log.info(f"Done! WIE: {summary['wie_eligible']} / Total: {summary['total_processed']}")
        log.info("=" * 60)

    except Exception as e:
        log.exception("Pipeline failed")
        _write_status(status_file, {
            "status": "error",
            "phase": "error",
            "message": str(e),
        })
        sys.exit(1)


if __name__ == "__main__":
    main()
