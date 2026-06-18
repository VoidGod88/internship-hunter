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

log = logging.getLogger("hunter")

# ── Dynamic card selector (detected at runtime) ──
_working_selector = "li[data-occludable-job-id]"  # default fallback

# ── Tunable scroll behaviour ──
SCROLL_PAUSE_MS = 1500          # wait between scrolls (LinkedIn needs time to lazy-load)
SCROLL_MAX_NO_NEW = 2           # stop after N consecutive scrolls that add 0 new cards
SCROLL_MAX_ROUNDS = 80          # hard cap so a misbehaving page can't loop forever


def _count_cards(page) -> int:
    """Count unique job cards on the current page (de-duped by data-occludable-job-id)."""
    global _working_selector
    return page.evaluate(f"""() => {{
        const ids = new Set();
        document.querySelectorAll('{_working_selector}').forEach(li => {{
            const id = li.getAttribute('data-occludable-job-id') || li.getAttribute('data-job-id');
            if (id) ids.add(id);
        }});
        return ids.size;
    }}""")


def _extract_card_data(page) -> list[dict]:
    """Pull (title, company, url) tuples for every visible job card."""
    global _working_selector
    return page.evaluate(f"""() => {{
        const results = [];
        const seen = new Set();
        document.querySelectorAll('{_working_selector}').forEach(li => {{
            const id = li.getAttribute('data-occludable-job-id') || li.getAttribute('data-job-id');
            if (!id || seen.has(id)) return;
            seen.add(id);
            const titleEl = li.querySelector('h3, [class*="title"], [class*="job-title"]');
            const compEl  = li.querySelector('h4, [class*="company"], [class*="company-name"]');
            const linkEl  = li.querySelector('a[href*="/jobs/"]');
            const title   = titleEl ? titleEl.innerText.trim() : '';
            const company = compEl  ? compEl.innerText.trim()  : '';
            const href    = linkEl ? linkEl.getAttribute('href') : '';
            if (title && title.length > 2) {{
                results.push({{id, title, company, href}});
            }}
        }});
        return results;
    }}""")


def _scroll_to_bottom(page) -> int:
    """Scroll the results pane to the bottom, then return the new card count."""
    global _working_selector
    page.evaluate(f"""() => {{
        const scroller = document.querySelector(
            '.jobs-search-results-list, .scaffold-layout__list, [class*="scaffold"]'
        );
        if (scroller && scroller.scrollHeight > scroller.clientHeight) {{
            scroller.scrollTo(0, scroller.scrollHeight);
            return;
        }}
        window.scrollTo(0, document.body.scrollHeight);
    }}""")
    page.wait_for_timeout(SCROLL_PAUSE_MS)
    return _count_cards(page)


def _scrape_keyword(page, kw: str, max_pages: int) -> list:
    """Scrape a single keyword using infinite scroll. Returns list of Job."""
    url = (
        f"https://www.linkedin.com/jobs/search/?"
        f"keywords={kw.replace(' ', '%20')}"
        f"&location=Hong%20Kong"
        f"&f_JT=I"
    )
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    except Exception:
        log.info(f"[LinkedIn] Searching: {kw} → 0 jobs (goto failed)")
        return []

    # Check if redirected to login
    if "/login" in page.url or "/checkpoint" in page.url:
        log.info(f"[LinkedIn] Searching: {kw} → 0 jobs (not logged in)")
        return []

    page.wait_for_timeout(3000)

    # Try multiple possible card selectors
    card_selectors = [
        "li[data-occludable-job-id]",
        "[data-job-id]",
        ".jobs-search-results__list-item",
        ".scaffold-layout__list-item",
        "ul.jobs-search-results-list > li",
    ]

    cards_found = False
    for sel in card_selectors:
        try:
            count = page.evaluate(f"() => document.querySelectorAll('{sel}').length")
            if count > 0:
                global _working_selector
                _working_selector = sel
                cards_found = True
                break
        except Exception:
            pass

    if not cards_found:
        # Save debug HTML
        debug_dir = Path(__file__).parent.parent / "debug"
        debug_dir.mkdir(exist_ok=True)
        html_path = debug_dir / f"linkedin_debug_{kw.replace(' ', '_')}.html"
        try:
            html_path.write_text(page.content(), encoding="utf-8")
        except Exception:
            pass
        log.info(f"[LinkedIn] Searching: {kw} → 0 jobs")
        return []

    jobs_for_kw: list = []
    seen_ids: set[str] = set()
    no_new_streak = 0
    rounds = 0
    page_count = 0

    while rounds < SCROLL_MAX_ROUNDS:
        rounds += 1
        before = len(seen_ids)

        for card in _extract_card_data(page):
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

        added = len(seen_ids) - before
        if added > 0:
            no_new_streak = 0
            page_count = (len(seen_ids) // 25) + 1
            if max_pages > 0 and page_count >= max_pages:
                break
        else:
            no_new_streak += 1
            if no_new_streak >= SCROLL_MAX_NO_NEW:
                break

        _scroll_to_bottom(page)

    log.info(f"[LinkedIn] Searching: {kw} → {len(jobs_for_kw)} jobs")
    return jobs_for_kw


def scrape_linkedin(page, keywords: list[str], max_pages: int = 0) -> list:
    """
    Scrape LinkedIn HK internship jobs, one keyword per search, infinite scroll.
    - max_pages=0 (default) = scroll until no new cards for SCROLL_MAX_NO_NEW rounds
    - max_pages>0 = cap at that many "pages" (~25 cards each)
    - Final dedup by (title, company) across all keywords.
    """
    jobs: list = []
    log.info(f"[LinkedIn] Searching {len(keywords)} keywords...")

    for kw in keywords:
        jobs.extend(_scrape_keyword(page, kw, max_pages))
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
