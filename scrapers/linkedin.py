"""
scrapers/linkedin.py — LinkedIn HK job scraper using Playwright.
Supports: one keyword per search, infinite scroll pagination (more reliable
than the `start=25` URL parameter, which sometimes hits stale result pages).
"""

import time
import random
import logging
from .base import BaseScraper

log = logging.getLogger("hunter")

# ── Tunable scroll behaviour ──
SCROLL_PAUSE_MS = 1500          # wait between scrolls (LinkedIn needs time to lazy-load)
SCROLL_MAX_NO_NEW = 2           # stop after N consecutive scrolls that add 0 new cards
SCROLL_MAX_ROUNDS = 80          # hard cap so a misbehaving page can't loop forever


def _count_cards(page) -> int:
    """Count unique job cards on the current page (de-duped by data-occludable-job-id)."""
    return page.evaluate("""() => {
        const ids = new Set();
        document.querySelectorAll('li[data-occludable-job-id]').forEach(li => {
            const id = li.getAttribute('data-occludable-job-id');
            if (id) ids.add(id);
        });
        return ids.size;
    }""")


def _extract_card_data(page) -> list[dict]:
    """Pull (title, company, url) tuples for every visible job card."""
    return page.evaluate("""() => {
        const results = [];
        const seen = new Set();
        document.querySelectorAll('li[data-occludable-job-id]').forEach(li => {
            const id = li.getAttribute('data-occludable-job-id');
            if (!id || seen.has(id)) return;
            seen.add(id);
            const titleEl = li.querySelector('h3, [class*="title"]');
            const compEl  = li.querySelector('h4, [class*="company"]');
            const linkEl  = li.querySelector('a[href*="/jobs/"]');
            const title   = titleEl ? titleEl.innerText.trim() : '';
            const company = compEl  ? compEl.innerText.trim()  : '';
            const href    = linkEl ? linkEl.getAttribute('href') : '';
            if (title && title.length > 2) {
                results.push({id, title, company, href});
            }
        });
        return results;
    }""")


def _scroll_to_bottom(page) -> int:
    """Scroll the results pane to the bottom, then return the new card count.

    LinkedIn renders the result list inside a virtualised <ul>; we have to scroll
    the parent scrollable container, not the window. The window scroll trick is
    the most common reason "infinite scroll" scrapers think they're done after
    page 1.
    """
    page.evaluate("""() => {
        // 1. Try the inner results scroller first (LinkedIn's main jobs page)
        const scroller = document.querySelector(
            '.jobs-search-results-list, .scaffold-layout__list, [class*="scaffold"]'
        );
        if (scroller && scroller.scrollHeight > scroller.clientHeight) {
            scroller.scrollTo(0, scroller.scrollHeight);
            return;
        }
        // 2. Fallback: window scroll
        window.scrollTo(0, document.body.scrollHeight);
    }""")
    page.wait_for_timeout(SCROLL_PAUSE_MS)
    return _count_cards(page)


def _scrape_keyword(page, kw: str, max_pages: int) -> list:
    """Scrape a single keyword using infinite scroll. Returns list of Job."""
    log.info("  Searching: %s", kw)
    url = (
        f"https://www.linkedin.com/jobs/search/?"
        f"keywords={kw.replace(' ', '%20')}"
        f"&location=Hong%20Kong"
        f"&f_JT=I"                # f_JT=I = internship filter
    )
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    except Exception as e:
        log.warning("  LinkedIn goto [%s] failed: %s", kw, e)
        return []

    # Wait for the first batch of cards to render
    try:
        page.wait_for_selector("li[data-occludable-job-id]", timeout=15_000)
    except Exception:
        log.info("  No cards rendered for '%s' (Cloudflare or no results).", kw)
        return []

    jobs_for_kw: list = []
    seen_ids: set[str] = set()
    no_new_streak = 0
    rounds = 0
    page_count = 0  # 25 cards ≈ 1 "page" in LinkedIn's UI; useful for max_pages cap

    while rounds < SCROLL_MAX_ROUNDS:
        rounds += 1

        # Snapshot current cards
        before = len(seen_ids)

        # Extract whatever's visible NOW (LinkedIn's virtualisation can drop
        # off-screen nodes from the DOM, so we harvest after every scroll).
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
            log.info("  Scroll %d: +%d new (total %d unique)",
                     rounds, added, len(seen_ids))
        else:
            no_new_streak += 1
            log.info("  Scroll %d: no new cards (streak %d/%d)",
                     rounds, no_new_streak, SCROLL_MAX_NO_NEW)

        # Termination conditions
        if no_new_streak >= SCROLL_MAX_NO_NEW:
            log.info("  Stopping: %d consecutive scrolls with no new cards.",
                     SCROLL_MAX_NO_NEW)
            break
        if max_pages > 0 and page_count >= max_pages:
            log.info("  Stopping: reached max_pages=%d.", max_pages)
            break

        # Try to load more by scrolling
        _scroll_to_bottom(page)

    return jobs_for_kw


def scrape_linkedin(page, keywords: list[str], max_pages: int = 0) -> list:
    """
    Scrape LinkedIn HK internship jobs, one keyword per search, infinite scroll.

    - max_pages=0 (default) = scroll until no new cards for SCROLL_MAX_NO_NEW rounds
    - max_pages>0 = cap at that many "pages" (~25 cards each)
    - Final dedup by (title, company) across all keywords.
    """
    jobs: list = []
    log.info("[LinkedIn] Starting (keywords=%d, infinite-scroll mode)...", len(keywords))

    for kw in keywords:
        jobs.extend(_scrape_keyword(page, kw, max_pages))
        time.sleep(random.uniform(2, 4))   # polite pause between keywords

    # Final cross-keyword dedup
    seen: set = set()
    unique: list = []
    for j in jobs:
        key = (j.title.strip().lower(), j.company.strip().lower())
        if key not in seen and j.title.strip():
            seen.add(key)
            unique.append(j)

    log.info("[LinkedIn] Total: %d (deduplicated from %d raw cards)",
             len(unique), len(jobs))
    return unique
