"""
scrapers/linkedin.py — LinkedIn HK job scraper using Playwright.
Uses URL pagination (start=0, 25, 50...) — confirmed working (no duplicates).
"""

import time
import random
import logging
from pathlib import Path

from .base import BaseScraper
from config import check_stop

log = logging.getLogger("hunter")

# ── Dynamic card selector (detected at runtime) ──
# Try these selectors in order, use the first one that matches
# Based on LinkedIn diagnostic (2026-06): New structure uses
#   UL.jobs-search__results-list > div.base-search-card[data-entity-urn]
# IMPORTANT: Only capture search results, NOT recommended cards in sidebar
_CANDIDATE_SELECTORS = [
    "ul.jobs-search__results-list div.base-search-card[data-entity-urn]",  # Precise: only search results
    "ul.jobs-search__results-list li[data-occludable-job-id]",           # Old structure, search results only
    "div.base-search-card[data-entity-urn]",  # New structure (may include recommended)
    "li[data-occludable-job-id]",            # Old LinkedIn structure
    "[data-occludable-job-id]",              # Generic old structure
    "[class*='job-card']",                   # Generic fallback
]
_working_selector = None  # Will be detected at runtime

def _detect_selector(page) -> str:
    """Detect the correct job card selector for the current LinkedIn page.
    Retries up to 3 times (2s apart) — LinkedIn SPA may not have rendered
    job cards immediately after the container appears."""
    for attempt in range(3):
        for sel in _CANDIDATE_SELECTORS:
            try:
                count = page.locator(sel).count()
                if count > 0:
                    log.info(f"[LinkedIn]   Detected job card selector: '{sel}' ({count} cards)")
                    return sel
            except Exception:
                continue
        if attempt < 2:
            log.info(f"[LinkedIn]   No cards yet, retrying ({attempt+2}/3)...")
            page.wait_for_timeout(2000)
    log.warning("[LinkedIn]   No job card selector detected after 3 retries, using fallback")
    return _CANDIDATE_SELECTORS[0]  # fallback


def _extract_card_data(page) -> list[dict]:
    """Pull (title, company, url) tuples for every visible job card."""
    global _working_selector
    if not _working_selector:
        _working_selector = _detect_selector(page)
    
    results = []
    cards = page.query_selector_all(_working_selector)
    
    for idx, card in enumerate(cards):
        try:
            # Get job ID from data-entity-urn (new structure) or fallback to old attributes
            job_id = ""
            
            # New LinkedIn structure: data-entity-urn="urn:li:jobPosting:XXXX"
            entity_urn = card.get_attribute("data-entity-urn")
            if entity_urn and "jobPosting:" in entity_urn:
                job_id = entity_urn.split("jobPosting:")[-1]
            
            # Fallback to old attributes
            if not job_id:
                job_id = (
                    card.get_attribute("data-occludable-job-id") or
                    card.get_attribute("data-job-id") or
                    card.get_attribute("id") or
                    str(idx)
                )
            
            # Extract title
            title = ""
            for sel in [".base-search-card__title", "h3", '[class*="title"]', '[class*="job-title"]']:
                el = card.query_selector(sel)
                if el:
                    text = el.inner_text().strip()
                    if text:
                        title = text
                        break
            
            # Extract company
            company = ""
            for sel in [".base-search-card__subtitle", "h4", '[class*="company"]', '[class*="subtitle"]']:
                el = card.query_selector(sel)
                if el:
                    text = el.inner_text().strip()
                    if text:
                        company = text
                        break
            
            # Extract URL
            href = ""
            for sel in ['a[href*="/jobs/view/"]', 'a[href*="/jobs/"]', 'a[href*="linkedin.com/jobs/"]']:
                el = card.query_selector(sel)
                if el:
                    link = el.get_attribute("href")
                    if link:
                        href = link
                        break
            
            if title and len(title) > 2:
                results.append({
                    "id": job_id,
                    "title": title,
                    "company": company,
                    "href": href
                })
        except Exception as e:
            log.debug(f"[LinkedIn]   Error extracting card: {e}")
            continue
    
    return results


def _scrape_keyword(page, kw: str) -> list:
    """Scrape a single keyword using URL pagination (start=0, 25, 50...).
    
    Strategy:
    1. Build base URL with filters
    2. Loop: add &start=N, load page, extract cards
    3. Stop when a page returns 0 new jobs (or < 25 jobs, indicating last page)
    4. Max 10 pages (250 jobs) safety cap
    """
    import urllib.parse
    from config import config
    
    # Reset selector detection for each keyword
    global _working_selector
    _working_selector = None
    
    encoded_kw = urllib.parse.quote(kw)
    # LinkedIn search with filters from config (customizable via Settings UI)
    params = []
    if config.li_exp_level:
        params.append(f"f_E={config.li_exp_level}")
    if config.li_job_types:
        encoded_jt = urllib.parse.quote(config.li_job_types)
        params.append(f"f_JT={encoded_jt}")
    if config.li_work_types:
        params.append(f"f_WT={config.li_work_types}")
    if config.li_geo_id:
        params.append(f"geoId={config.li_geo_id}")
    if config.li_sort_by:
        params.append(f"sortBy={config.li_sort_by}")
    if config.li_posted_within:
        params.append(f"f_TPR={config.li_posted_within}")
    params.append(f"keywords={encoded_kw}")
    params.append("origin=JOB_SEARCH_PAGE_JOB_FILTER")
    params.append("spellCorrectionEnabled=true")
    base_url = "https://www.linkedin.com/jobs/search/?" + "&".join(params)
    
    log.info(f"[LinkedIn] Searching: {kw} | URL: {base_url}")
    
    jobs_for_kw = []
    seen_ids = set()
    start = 0
    page_size = 25
    max_pages = 10  # Safety cap: 10 pages = 250 jobs max per keyword
    empty_pages = 0
    max_empty_pages = 2  # Stop after 2 consecutive empty pages
    
    for page_num in range(max_pages):
        try:
            check_stop()
        except InterruptedError:
            break
        
        # Build paginated URL
        if start > 0:
            url = base_url + f"&start={start}"
        else:
            url = base_url
        
        log.debug(f"[LinkedIn]   Page {page_num+1}: {url[-80:]}")
        
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        except Exception as e:
            log.warning(f"[LinkedIn]   goto failed: {e}")
            break
        
        # Check if redirected to login
        actual_url = page.url
        if "/login" in actual_url or "/checkpoint" in actual_url:
            log.warning(f"[LinkedIn]   Redirected to login! Cookies may be expired.")
            log.warning(f"[LinkedIn]   Please run: python linkedin_login.py")
            # Save debug HTML
            debug_dir = Path(__file__).parent.parent / "debug"
            debug_dir.mkdir(exist_ok=True)
            html_path = debug_dir / f"linkedin_redirect_{kw.replace(' ', '_')}.html"
            try:
                html_path.write_text(page.content(), encoding="utf-8")
            except Exception:
                pass
            break
        
        # Wait for job cards to render
        try:
            wait_selectors = ", ".join([
                "div.base-search-card[data-entity-urn]",
                "li[data-occludable-job-id]",
                "[data-entity-urn*='jobPosting:']",
            ])
            page.wait_for_selector(wait_selectors, timeout=15_000, state="attached")
            log.debug(f"[LinkedIn]   Page {page_num+1} loaded (cards found)")
        except Exception:
            # Check if genuinely 0 results
            no_results_el = page.query_selector('[class*="no-results"], [class*="zero-results"], [class*="empty-state"]')
            if no_results_el:
                log.info(f"[LinkedIn]   Page {page_num+1}: 0 results (no matching jobs)")
                empty_pages += 1
                if empty_pages >= max_empty_pages:
                    break
                start += page_size
                continue
            else:
                # Maybe slow network — wait a bit more
                page.wait_for_timeout(3000)
        
        # Detect selector (first page only)
        if _working_selector is None:
            _working_selector = _detect_selector(page)
        
        # Extract cards
        cards = _extract_card_data(page)
        
        # Filter new jobs
        new_count = 0
        for card in cards:
            if card["id"] in seen_ids:
                continue
            seen_ids.add(card["id"])
            href = card["href"] or ""
            job_url = (
                href if href.startswith("http")
                else f"https://www.linkedin.com{href}" if href
                else ""
            )
            if card["title"] and len(card["title"]) > 3:
                jobs_for_kw.append(BaseScraper.make_job(
                    card["title"], card["company"], "Hong Kong", job_url, "LinkedIn"
                ))
                new_count += 1
        
        log.info(f"[LinkedIn]   Page {page_num+1}: {new_count} new jobs (total: {len(jobs_for_kw)})")
        
        # Stop conditions
        if new_count == 0:
            empty_pages += 1
            if empty_pages >= max_empty_pages:
                log.info(f"[LinkedIn]   No more results after {page_num+1} pages")
                break
        else:
            empty_pages = 0  # Reset on successful page
            
        # If this page returned < page_size jobs, it's likely the last page
        if len(cards) < page_size:
            log.info(f"[LinkedIn]   Last page reached ({len(cards)} < {page_size} cards)")
            break
        
        # Next page
        start += page_size
        
        # Human-like delay between pages
        time.sleep(random.uniform(1.5, 3.0))
    
    log.info(f"[LinkedIn] Searching: {kw} → {len(jobs_for_kw)} jobs")
    return jobs_for_kw


def scrape_linkedin(page, keywords: list[str]) -> list:
    """Scrape LinkedIn HK jobs using URL pagination (start=0, 25, 50...).
    One keyword per search. Final dedup by (title, company)."""
    jobs: list = []
    log.info(f"[LinkedIn] Searching {len(keywords)} keywords...")
    
    for kw in keywords:
        try:
            kw_jobs = _scrape_keyword(page, kw)
        except Exception as e:
            log.error(f"[LinkedIn]   Error scraping '{kw}': {e}")
            kw_jobs = []
        jobs.extend(kw_jobs)
        if kw_jobs:
            log.info(f"[LinkedIn]   {kw}: {len(kw_jobs)} jobs (sample: {kw_jobs[0].title[:40]})")
        
        # Human-like delay between keywords
        time.sleep(random.uniform(2, 4))
    
    # Final cross-keyword dedup
    seen: set = set()
    unique: list = []
    for j in jobs:
        key = (j.title.strip().lower(), j.company.strip().lower())
        if key not in seen and j.title.strip():
            seen.add(key)
            unique.append(j)
    
    log.info(f"[LinkedIn] Total: {len(unique)} jobs")
    return unique
