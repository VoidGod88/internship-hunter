"""
scrapers/linkedin.py — LinkedIn HK job scraper using Playwright.
Supports: one keyword per search, infinite scroll pagination (more reliable
than the `start=25` URL parameter, which sometimes hits stale result pages).
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
    "li[data-occludable-job-id]",   # Old LinkedIn structure
    "div.base-search-card",          # Alternative structure
    "[class*='job-card']",          # Generic job card
]
_working_selector = None  # Will be detected at runtime

# ── Tunable scroll behaviour ──
SCROLL_PAUSE_MS = 1500          # wait between scrolls (LinkedIn needs time to lazy-load)
SCROLL_MAX_NO_NEW = 4           # stop after N consecutive scrolls that add 0 new cards
SCROLL_MAX_ROUNDS = 200         # hard cap so a misbehaving page can't loop forever


def _detect_selector(page) -> str:
    """Detect the correct job card selector for the current LinkedIn page."""
    for sel in _CANDIDATE_SELECTORS:
        try:
            count = page.locator(sel).count()
            if count > 0:
                log.info(f"[LinkedIn]   Detected job card selector: '{sel}' ({count} cards)")
                return sel
        except Exception:
            continue
    log.warning("[LinkedIn]   No job card selector detected, using fallback")
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


def _scroll_to_bottom(page) -> int:
    """Scroll the results pane to the bottom (human-like), then return the new card count."""
    global _working_selector
    # Use human-like scroll (gradual, not instant)
    from stealth import Stealth
    Stealth.human_scroll(page, scroll_pixels=800)
    page.wait_for_timeout(SCROLL_PAUSE_MS)
    return _count_cards(page)


def _scrape_keyword(page, kw: str) -> list:
    """Scrape a single keyword using infinite scroll."""
    import urllib.parse
    # Reset selector detection for each keyword
    global _working_selector
    _working_selector = None
    encoded_kw = urllib.parse.quote(kw)
    # LinkedIn search with filters: entry-level, F/P/I job types, on-site, Hong Kong, sorted by relevance
    url = (
        f"https://www.linkedin.com/jobs/search/"
        f"?f_E=1&f_JT=F%2CP%2CI&f_WT=1&geoId=103291313"
        f"&keywords={encoded_kw}&origin=JOB_SEARCH_PAGE_JOB_FILTER"
        f"&sortBy=R&spellCorrectionEnabled=true"
    )
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

    # Wait for job results container to load (up to 10s)
    try:
        # Try multiple selectors for job cards
        wait_selectors = ", ".join([
            "li.base-search-card",
            "li[data-occludable-job-id]",
            "[class*='job-card']",
            "[class*='jobs-search']",
            ".base-search-card"
        ])
        page.wait_for_selector(wait_selectors, timeout=10_000)
        log.info(f"[LinkedIn]   Page loaded (job container found)")
        
        # Detect the correct selector
        _working_selector = _detect_selector(page)
    except Exception:
        log.info(f"[LinkedIn]   No job container found within 10s, will retry after scroll...")
        page.wait_for_timeout(5000)

    # Infinite scroll to load all jobs
    jobs_for_kw: list = []
    seen_ids: set[str] = set()
    prev_count = 0
    no_new_rounds = 0
    scroll_round = 0

    while scroll_round < SCROLL_MAX_ROUNDS:
        scroll_round += 1

        # Scroll down to trigger lazy-load
        from stealth import Stealth
        Stealth.human_scroll(page, scroll_pixels=800)
        page.wait_for_timeout(SCROLL_PAUSE_MS)

        # Extract cards after scroll
        cards = _extract_card_data(page)
        if not cards and scroll_round == 1:
            # Save debug HTML if no cards found on first scroll
            debug_dir = Path(__file__).parent.parent / "debug"
            debug_dir.mkdir(exist_ok=True)
            html_path = debug_dir / f"linkedin_debug_{kw.replace(' ', '_')}.html"
            try:
                html_path.write_text(page.content(), encoding="utf-8")
                log.info(f"[LinkedIn]   Debug HTML saved: {html_path.name}")
            except Exception:
                pass
            log.info(f"[LinkedIn]   No job cards found for '{kw}'")
            break

        # Check stop flag
        try:
            check_stop()
        except InterruptedError:
            log.info('[LinkedIn] Stop requested, exiting...')
            break

        # Add new jobs
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

        total_now = len(jobs_for_kw)

        # Check if no new cards for too long (page fully loaded)
        if total_now == prev_count:
            no_new_rounds += 1
            if no_new_rounds >= SCROLL_MAX_NO_NEW:
                break
        else:
            no_new_rounds = 0
        prev_count = total_now

        # Log progress every 5 rounds
        if scroll_round % 5 == 0:
            log.info(f"[LinkedIn]   Scroll #{scroll_round}: {total_now} jobs so far...")

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
