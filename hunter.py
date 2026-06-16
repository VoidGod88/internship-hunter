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
import time
import random
import logging
import datetime
import json
from pathlib import Path
from typing import Optional

from playwright.sync_api import sync_playwright

from config import config
from models import Job
from database import (
    insert_job, insert_cover_letter, record_application,
    get_jobs_with_cover_letters, get_all_jobs, get_application_history,
    update_job_status, has_been_applied,
)
import scrapers
import jobboard
from ai_writer import generate_cover_letter, generate_batch
from mailer import send_email
from cv_reader import load_cv_profile, format_cv_for_prompt

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


# ── CV Matching ──
CV_MATCH_PROMPT = """You are evaluating whether a candidate's CV matches a job requirement.

CANDIDATE CV PROFILE:
{cv_profile}

JOB DETAILS:
Title: {title}
Company: {company}
Description: {description}
Requirements: {requirements}
Education Required: {education}

Evaluate the match on these dimensions (answer YES/NO for each):
1. Skills Match: Does the candidate have the required technical skills?
2. Education Match: Does the candidate meet the education requirement?
3. Major/Program Match: Is the candidate's major relevant to this role?
4. Final Year: Does the job require "final year" or "fresh graduate" ONLY? 
   (If yes and candidate is NOT final year → mark as mismatch)

Return ONLY a valid JSON object:
{
  "skills_match": true/false,
  "education_match": true/false,
  "major_match": true/false,
  "requires_final_year": true/false,
  "candidate_is_final_year": true/false,
  "overall_match": true/false,
  "reason": "Brief explanation in English (max 80 chars)"
}

Rules:
- overall_match = true ONLY if candidate has a realistic chance of being considered
- Being non-final-year is NOT a reason to reject, unless the job explicitly requires final year
- If skills are somewhat related (e.g. Python for a data role), count as match
- Return ONLY the JSON, no markdown"""


def check_cv_match(job: Job, cv_profile: dict) -> tuple[bool, str]:
    """
    Use LLM to check if the candidate's CV matches the job.
    Returns (match: bool, reason: str).
    """
    if not config.cv_matching_enabled:
        return True, "CV matching disabled"
    if not cv_profile:
        return True, "No CV profile available"

    if not config.llm_api_key:
        log.warning("[CV Match] No LLM API key, accepting all")
        return True, "LLM unavailable"

    cv_text = format_cv_for_prompt(config.cv_pdf_path)
    if not cv_text:
        return True, "CV text unavailable"

    try:
        from openai import OpenAI
        client = OpenAI(
            api_key=config.llm_api_key,
            base_url=config.llm_base_url,
        )

        user_prompt = CV_MATCH_PROMPT.format(
            cv_profile=json.dumps(cv_profile, ensure_ascii=False, indent=2)[:2000],
            title=job.title[:100],
            company=job.company[:60],
            description=(job.description or "")[:1500],
            requirements=(getattr(job, 'requirements', '') or "")[:500],
            education=(getattr(job, 'education_level', '') or "")[:200],
        )

        response = client.chat.completions.create(
            model=config.llm_model,
            messages=[
                {"role": "system", "content": "You are a CV-job match evaluator. Return only JSON."},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=600,
        )

        raw_content = response.choices[0].message.content or ""
        log.info(f"[CV Match] Raw LLM response ({len(raw_content)} chars): {raw_content[:500]}")

        content = raw_content.strip()

        # ── Step 1: Strip any markdown code block wrappers ──
        # Handles: ```json\n...\n```  |  ```\n...\n```  |  ```json{...}```
        if "```" in content:
            parts = content.split("```")
            for i in range(1, len(parts), 2):
                candidate = parts[i].strip()
                if candidate:
                    # Remove leading "json" / "JSON" label if present
                    candidate = re.sub(r'^(?:json|JSON)\s*', '', candidate).strip()
                    if candidate:
                        content = candidate
                        break
            else:
                # All code-block parts were empty — fall back to stripping all backticks
                content = content.replace("```", "").strip()

        # ── Step 2: Extract the first {...} JSON object ──
        # Try non-greedy first (handles most cases); fall back to greedy
        json_match = re.search(r'\{[\s\S]*?\}', content)
        if not json_match:
            json_match = re.search(r'\{[\s\S]*\}', content)
        if json_match:
            content = json_match.group(0).strip()

        # ── Step 3: Parse JSON with repair for common issues ──
        try:
            result = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            # Try wrapping in braces if the LLM returned bare key-value lines
            if not content.startswith("{"):
                content = "{" + content.rstrip("}") if content.rstrip().endswith("}") else "{" + content + "}"
                try:
                    result = json.loads(content)
                except Exception:
                    log.warning(f"[CV Match] JSON parse failed, raw: {raw_content[:300]}")
                    return True, "LLM parse error (accepted)"
            else:
                log.warning(f"[CV Match] JSON parse failed, raw: {raw_content[:300]}")
                return True, "LLM parse error (accepted)"

        if not isinstance(result, dict):
            log.warning(f"[CV Match] LLM returned non-dict: {type(result).__name__} | value: {str(result)[:100]}")
            return True, "LLM returned unexpected format (accepted)"

        # ── Step 4: Normalize keys (LLM may add leading whitespace) ──
        if isinstance(result, dict):
            result = {k.strip(): v for k, v in result.items()}
        else:
            log.warning(f"[CV Match] LLM returned non-dict type: {type(result).__name__}")
            return True, "LLM returned unexpected format (accepted)"

        # ── Step 5: Extract match fields safely ──
        try:
            match = bool(result.get("overall_match", True))
            reason_parts = []

            if not result.get("skills_match", True):
                reason_parts.append("技能不匹配")
            if not result.get("education_match", True):
                reason_parts.append("学历不符")
            if not result.get("major_match", True):
                reason_parts.append("专业不符")
            if result.get("requires_final_year", False) and not result.get("candidate_is_final_year", False):
                reason_parts.append("要求final year")
                match = False

            reason = str(result.get("reason", "") or "") or ", ".join(reason_parts)
            if not reason:
                reason = "CV 匹配" if match else "CV 不匹配"

            log.info(f"[CV Match] {job.title[:30]}: {match} — {reason}")
            return match, reason
        except Exception as field_err:
            log.warning(f"[CV Match] Field extraction failed: {field_err} | result keys: {list(result.keys())}")
            return True, "LLM parse error (accepted)"

    except Exception as e:
        log.warning(f"[CV Match] LLM failed for {job.title[:30]}: {type(e).__name__}: {e}")
        return True, f"CV check error: {e}"


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
        config.scraper_polyu, config.scraper_linkedin, config.scraper_jobsdb,
        config.scraper_indeed, config.scraper_efc, config.scraper_manual,
    ])
    done = 0

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)

        # ── PolyU Jobboard ──
        if config.scraper_polyu:
            if progress_callback:
                progress_callback(f"Scraping PolyU Jobboard...")
            try:
                page = scrapers.base.BaseScraper.init_page(browser)
                polyu_jobs = jobboard.scrape_polyu(page, keywords=search_keywords)
                all_jobs += polyu_jobs
                log.info(f"PolyU: {len(polyu_jobs)} jobs")
            except Exception as e:
                log.error(f"PolyU scraper error: {e}")
            done += 1

        # ── LinkedIn ──
        if config.scraper_linkedin:
            if progress_callback:
                progress_callback(f"Scraping LinkedIn...")
            try:
                page = scrapers.base.BaseScraper.init_page(browser)
                lj = scrapers.scrape_linkedin(page, keywords, max_per_kw=15)
                all_jobs += lj
                log.info(f"LinkedIn: {len(lj)} jobs")
            except Exception as e:
                log.error(f"LinkedIn error: {e}")
            done += 1

        # ── JobsDB ──
        if config.scraper_jobsdb:
            if progress_callback:
                progress_callback(f"Scraping JobsDB...")
            try:
                page = scrapers.base.BaseScraper.init_page(browser)
                jj = scrapers.scrape_jobsdb(page, keywords, max_per_kw=10)
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
                ij = scrapers.scrape_indeed(page, keywords, max_per_kw=5)
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
                ej = scrapers.scrape_efc(page, max_results=20)
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
    return all_jobs


def process_jobs(jobs: list[Job], progress_callback=None) -> list[Job]:
    """Apply WIE filter, CV matching, dedup, save to DB. Sorted by job type."""
    total = len(jobs)

    # Load CV profile once
    cv_profile = {}
    if config.cv_matching_enabled and config.cv_pdf_path:
        cv_profile = load_cv_profile(config.cv_pdf_path)
        if cv_profile:
            log.info(f"[CV] Profile loaded: {cv_profile.get('name', 'Unknown')}")

    # WIE filter + CV match + extra docs + classify type
    for i, job in enumerate(jobs):
        try:
            # WIE check
            job.wie_eligible, job.wie_reason = check_wie(job)

            # CV matching (only if WIE passed, to save LLM calls)
            if job.wie_eligible and cv_profile:
                cv_ok, cv_reason = check_cv_match(job, cv_profile)
                if not cv_ok:
                    job.wie_eligible = False
                    job.wie_reason = f"CV不匹配: {cv_reason}"
                    log.info(f"[CV] Rejected: {job.title[:30]} — {cv_reason}")
                else:
                    if cv_reason and cv_reason not in ("CV 匹配", ""):
                        job.wie_reason = f"{job.wie_reason} | CV: {cv_reason}"

            # Extract extra docs
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


def generate_and_send(
    jobs: list[Job],
    dry_run: bool = True,
    progress_callback=None,
    max_emails: int = 10,
) -> list:
    """Generate cover letters and send emails. Returns results list."""
    if progress_callback:
        progress_callback("Generating cover letters via AI...")

    emailable = []
    for j in jobs:
        if not j.wie_eligible:
            continue
        if not j.contact_email:
            continue
        if has_been_applied(j.company, j.title):
            log.debug(f"Skipping (already applied): {j.company}")
            continue
        emailable.append(j)

    emailable = emailable[:max_emails]

    if progress_callback:
        progress_callback(f"Found {len(emailable)} jobs to apply. Generating cover letters...")

    results = []
    for i, job in enumerate(emailable):
        if progress_callback:
            progress_callback(f"Cover letter {i+1}/{len(emailable)}: {job.company}")

        cover_letter = generate_cover_letter(
            job.title, job.company, job.description,
            getattr(job, 'requirements', ''),
            getattr(job, 'education_level', '')
        )

        # Store cover letter in DB for UI retrieval
        if getattr(job, '_db_id', 0) > 0:
            try:
                insert_cover_letter(job._db_id, cover_letter)
                log.debug(f"Cover letter saved to DB for job #{job._db_id}")
            except Exception as e:
                log.warning(f"Failed to save cover letter to DB: {e}")

        sent = False
        if not dry_run:
            sent = send_email(job, cover_letter, dry_run=False)
            if sent:
                record_application(job_id=0, dry_run=False)  # We'll fix job_id later
            time.sleep(config.email_delay_seconds + random.uniform(0, 3))

        results.append({
            "company": job.company,
            "title": job.title,
            "contact_email": job.contact_email,
            "cover_letter": cover_letter,
            "sent": sent,
            "dry_run": dry_run,
        })

    if progress_callback:
        progress_callback(f"Done! {len(results)} cover letters generated.")
    return results


def run_full_pipeline(
    keywords: Optional[list[str]] = None,
    dry_run: bool = True,
    max_emails: int = 10,
    progress_callback=None,
) -> dict:
    """Run the full pipeline: scrape -> process -> generate -> send."""
    if not keywords:
        keywords = config.search_keywords

    # Step 1: Scrape
    if progress_callback:
        progress_callback("Phase 1/4: Scraping jobs...")
    raw_jobs = run_scrapers(keywords, progress_callback)

    # Step 2: Process
    if progress_callback:
        progress_callback(f"Phase 2/4: Processing {len(raw_jobs)} jobs...")
    processed = process_jobs(raw_jobs, progress_callback)

    wie_count = sum(1 for j in processed if j.wie_eligible)
    if progress_callback:
        progress_callback(f"Phase 2 done: {len(processed)} unique, {wie_count} WIE-eligible")

    # Step 3: Generate cover letters & send
    if progress_callback:
        progress_callback(f"Phase 3/4: Sending emails...")
    results = generate_and_send(processed, dry_run, progress_callback, max_emails)

    sent_count = sum(1 for r in results if r["sent"])

    summary = {
        "total_raw": len(raw_jobs),
        "total_processed": len(processed),
        "wie_eligible": wie_count,
        "emails_sent": sent_count,
        "results": results,
    }

    if progress_callback:
        progress_callback(
            f"Done! {sent_count} emails {'(dry run)' if dry_run else 'sent'} "
            f"| {wie_count} WIE-eligible | {len(raw_jobs)} raw"
        )

    return summary


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
    parser.add_argument("--dry-run", action="store_true", default=None, help="Dry run mode")
    parser.add_argument("--max-emails", type=int, default=10, help="Max emails per run")
    parser.add_argument("--status-file", type=str, default="", help="Status JSON file for UI polling")

    # Scraper toggles
    parser.add_argument("--scraper-polyu", action="store_true", default=None)
    parser.add_argument("--scraper-linkedin", action="store_true", default=None)
    parser.add_argument("--scraper-jobsdb", action="store_true", default=None)
    parser.add_argument("--scraper-indeed", action="store_true", default=None)
    parser.add_argument("--scraper-efc", action="store_true", default=None)
    parser.add_argument("--scraper-manual", action="store_true", default=None)
    parser.add_argument("--cover-letter", action="store_true", default=None)

    args = parser.parse_args()

    # Apply args to config (only override if explicitly set)
    if args.dry_run is not None:
        config.dry_run = args.dry_run
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
    if args.cover_letter is not None:
        config.cover_letter_enabled = args.cover_letter
    if args.max_emails:
        config.max_emails_per_run = args.max_emails

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("hunter.log", encoding="utf-8"),
        ],
    )

    log.info("=" * 60)
    log.info("WIE Internship Hunter v4 (subprocess mode)")
    log.info(f"  Dry run: {config.dry_run} | Max emails: {config.max_emails_per_run}")
    log.info(f"  PolyU: {config.scraper_polyu} | LinkedIn: {config.scraper_linkedin}")
    log.info("=" * 60)

    status_file = args.status_file
    _write_status(status_file, {"status": "running", "phase": "init", "message": "Starting..."})

    _phase = "init"

    def progress_cb(msg):
        log.info(msg)
        # Auto-detect phase from message for progress bar
        nonlocal _phase
        if "Phase 1" in msg or "Scraping" in msg:
            _phase = "scraping"
        elif "Phase 2" in msg or "Processing" in msg:
            _phase = "processing"
        elif "Phase 3" in msg or "Generating" in msg or "Cover letter" in msg:
            _phase = "generating"
        elif "Complete!" in msg or "Done!" in msg:
            _phase = "done"
        _write_status(status_file, {"status": "running", "phase": _phase, "message": msg})

    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()] if args.keywords else None

    try:
        summary = run_full_pipeline(
            keywords=keywords,
            dry_run=config.dry_run,
            max_emails=config.max_emails_per_run,
            progress_callback=progress_cb,
        )

        _write_status(status_file, {
            "status": "done",
            "phase": "done",
            "message": f"Complete! {summary['wie_eligible']} WIE eligible, {summary['emails_sent']} emails",
            "summary": {
                "total_raw": summary["total_raw"],
                "total_processed": summary["total_processed"],
                "wie_eligible": summary["wie_eligible"],
                "emails_sent": summary["emails_sent"],
            }
        })

        log.info("\n" + "=" * 60)
        log.info(f"Done! Emails: {summary['emails_sent']} | WIE: {summary['wie_eligible']}")
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
