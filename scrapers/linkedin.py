"""
scrapers/linkedin.py — LinkedIn HK job scraper using Playwright.
Uses URL pagination (start=0, 25, 50...) — more reliable than infinite
scroll which depends on LinkedIn's virtual list lazy-load behaviour.
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
_CANDIDATE_SELECTORS = [
    "li.base-search-card",           # New LinkedIn structure (2024+)
    "li[data-occludable-job-id]",   # Old LinkedIn structure (li tag)
    "[data-occludable-job-id]",     # Generic (any tag, SPA may use div)
    "div.base-search-card",          # Alternative structure
    "[class*='job-card']",          # Generic job card fallback
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


def _count_cards(page) -> int:
    """Count unique job cards on the current page (de-duped by data-occludable-job-id or index)."""
    global _working_selector
    if not _working_selector:
        _working_selector = _detect_selector(page)
    return page.evaluate(f"""() => {{
        const ids = new Set();
        document.querySelectorAll('{_working_selector}').forEach((li, idx) => {{
            const id = li.getAttribute('data-occludable-job-id') || li.getAttribute('data-job-id') || String(idx);
            ids.add(id);
        }});
        return ids.size;
    }}""")


def _extract_card_data(page) -> list[dict]:
    """Pull (title, company, url) tuples for every visible job card."""
    global _working_selector
    if not _working_selector:
        _working_selector = _detect_selector(page)
    
    results = []
    cards = page.query_selector_all(_working_selector)
    
    for idx, card in enumerate(cards):
        try:
            # Get job ID
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
    """Scrape a single keyword using URL pagination (start=0, 25, 50...)."""
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
    url = "https://www.linkedin.com/jobs/search/?" + "&".join(params)
    log.info(f"[LinkedIn] Searching: {kw} | URL: {url}")

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    except Exception as e:
        log.warning(f"[LinkedIn] goto failed: {e}")
        return []

    # Check if redirected to login
    actual_url = page.url
    if "/login" in actual_url or "/checkpoint" in actual_url:
        log.warning(f"[LinkedIn]   Redirected to login! Cookies may be expired.")
        log.warning(f"[LinkedIn]   Please run: python linkedin_login.py")
        debug_dir = Path(__file__).parent.parent / "debug"
        debug_dir.mkdir(exist_ok=True)
        html_path = debug_dir / f"linkedin_redirect_{kw.replace(' ', '_')}.html"
        try:
            html_path.write_text(page.content(), encoding="utf-8")
        except Exception:
            pass
        return []

    # Wait for job card elements to render (not just the container skeleton)
    # Track whether LinkedIn returned 0 results (vs. a detection failure)
    legit_zero_results = False
    try:
        # Prefer waiting for actual job cards — LinkedIn SPA may show
        # the shell ([class*='jobs-search']) before cards are injected.
        # Use state="attached" because cards may be off-screen in a virtual list.
        wait_selectors = ", ".join([
            "li[data-occludable-job-id]",
            "[data-occludable-job-id]",
            "li.base-search-card",
            ".base-search-card",
        ])
        page.wait_for_selector(wait_selectors, timeout=15_000, state="attached")
        log.info(f"[LinkedIn]   Page loaded (job cards found)")
        
        # Detect the correct selector (with retry)
        _working_selector = _detect_selector(page)
    except Exception:
        # Check if page genuinely has 0 results (vs. detection failure)
        no_results_el = page.query_selector('[class*="no-results"], [class*="zero-results"], [class*="empty-state"]')
        result_count_text = page.evaluate("""() => {
            const el = document.querySelector('[class*="results-context"], [class*="jobs-search-results-count"], .results-context-header__job-count, h2');
            return el ? el.textContent.trim() : '';
        }""")
        if no_results_el or "0" in result_count_text:
            log.info(f"[LinkedIn]   0 results for '{kw}' (no matching jobs)")
            legit_zero_results = True
        else:
            log.info(f"[LinkedIn]   No job cards found within 15s, trying generic container...")
            try:
                page.wait_for_selector("[class*='jobs-search'], [class*='results']", timeout=5_000, state="attached")
                log.info(f"[LinkedIn]   Generic container found, retrying card detection...")
                page.wait_for_timeout(3000)
                _working_selector = _detect_selector(page)
            except Exception:
                log.info(f"[LinkedIn]   No results container found")
                # Save debug HTML for unknown failure
                debug_dir = Path(__file__).parent.parent / "debug"
                debug_dir.mkdir(exist_ok=True)
                html_path = debug_dir / f"linkedin_debug_{kw.replace(' ', '_')}.html"
                try:
                    html_path.write_text(page.content(), encoding="utf-8")
                    log.info(f"[LinkedIn]   Debug HTML saved: {html_path.name}")
                except Exception:
                    pass

    # If LinkedIn returned 0 results, skip pagination
    if legit_zero_results:
        log.info(f"[LinkedIn] Searching: {kw} → 0 jobs")
        return []

    # Paginated page loading (start=0, 25, 50...)
    # More reliable than scrolling: LinkedIn virtual list only lazy-loads
    # cards when the viewport passes through intermediate positions.
    jobs_for_kw: list = []
    seen_ids: set[str] = set()
    base_url = page.url.split("&start=")[0]  # strip any existing start param
    page_num = 0
    max_pages = 10  # safety cap (10 pages × 25 = 250 jobs max per keyword)

    while page_num < max_pages:
        start = page_num * 25
        page_url = f"{base_url}&start={start}" if page_num > 0 else base_url

        if page_num > 0:
            try:
                page.goto(page_url, wait_until="domcontentloaded", timeout=30_000)
                page.wait_for_selector(wait_selectors, timeout=15_000, state="attached")
                # Re-detect selector (may change between pages)
                _working_selector = _detect_selector(page)
            except Exception:
                log.info(f"[LinkedIn]   Page {page_num + 1} load failed, stopping")
                break

        # Check stop flag
        try:
            check_stop()
        except InterruptedError:
            log.info('[LinkedIn] Stop requested, exiting...')
            break

        # Extract cards from this page
        cards = _extract_card_data(page)
        if not cards and page_num == 0:
            # Unknown failure on first page (shouldn't happen after wait_for_selector)
            log.info(f"[LinkedIn]   No job cards found for '{kw}'")
            debug_dir = Path(__file__).parent.parent / "debug"
            debug_dir.mkdir(exist_ok=True)
            html_path = debug_dir / f"linkedin_debug_{kw.replace(' ', '_')}.html"
            try:
                html_path.write_text(page.content(), encoding="utf-8")
                log.info(f"[LinkedIn]   Debug HTML saved: {html_path.name}")
            except Exception:
                pass
            break

        # Add new jobs (dedup by id)
        new_on_page = 0
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
                new_on_page += 1

        log.info(f"[LinkedIn]   Page {page_num + 1}: {new_on_page} new jobs (total: {len(jobs_for_kw)})")

        # Stop if page returned fewer than 25 cards (last page)
        if len(cards) < 25:
            break

        page_num += 1

    log.info(f"[LinkedIn] Searching: {kw} → {len(jobs_for_kw)} jobs")
    return jobs_for_kw


def scrape_linkedin(page, keywords: list[str]) -> list:
    """
    Scrape LinkedIn HK jobs, one keyword per search, infinite scroll.
    No page limit — scrolls until LinkedIn stops returning new results.
    Final dedup by (title, company) across all keywords.
    """
    jobs: list = []
    log.info(f"[LinkedIn] Searching {len(keywords)} keywords...")

    for kw in keywords:
        kw_jobs = _scrape_keyword(page, kw)
        jobs.extend(kw_jobs)
        if kw_jobs:
            log.info(f"[LinkedIn]   {kw}: {len(kw_jobs)} jobs (sample: {kw_jobs[0].title[:40]})")

        # Human-like delay between keywords
        from stealth import Stealth
        delay = Stealth.random_delay(3.0, 7.0)
        log.debug(f"[LinkedIn]   Delay after '{kw}': {delay:.1f}s")

        time.sleep(random.uniform(2, 4))

    # Detect if all keywords returned the same jobs (anti-bot sign)
    if len(keywords) > 1 and len(jobs) > 0:
        first_titles = [j.title for j in jobs[:5]]
        all_same = all(j.title == jobs[0].title for j in jobs)
        if all_same:
            log.warning(f"[LinkedIn]   ⚠️  All keywords returned the SAME jobs! This may be anti-bot.")
            log.warning(f"[LinkedIn]   ⚠️  Try re-login: python linkedin_login.py")
        else:
            # Check if >50% of jobs are identical across keywords
            from collections import Counter
            title_counts = Counter(j.title for j in jobs)
            most_common_count = title_counts.most_common(1)[0][1] if title_counts else 0
            if most_common_count > len(keywords):
                log.warning(f"[LinkedIn]   ⚠️  Many duplicate jobs across keywords (most common: {most_common_count} times). Possible anti-bot.")

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
