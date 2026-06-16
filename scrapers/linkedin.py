"""
scrapers/linkedin.py — LinkedIn HK job scraper using Playwright.
Supports: one keyword per search (decision A), infinite pagination (decision C).
"""

import time
import random
import logging
from typing import Optional
from .base import BaseScraper

log = logging.getLogger("hunter")

CARDS_PER_PAGE = 25   # LinkedIn shows ~25 jobs per page
MAX_PAGES_DEFAULT = 0   # 0 = no limit, crawl until no next page


def _go_to_next_page(page) -> bool:
    """Click LinkedIn's 'Next' button. Returns True if succeeded."""
    try:
        btn = page.query_selector('button[aria-label="Next"]')
        if btn and btn.is_enabled():
            btn.click()
            page.wait_for_timeout(3000)
            return True
    except Exception:
        pass
    return False


def scrape_linkedin(page, keywords: list[str], max_pages: int = MAX_PAGES_DEFAULT) -> list:
    """
    Scrape LinkedIn HK internship jobs.
    - One search per keyword.
    - Paginate until no Next button or max_pages reached.
    - Deduplicate by (title, company) at the end.
    """
    jobs = []
    log.info("[LinkedIn] Starting (keywords=%d)...", len(keywords))

    for kw in keywords:
        log.info("  Searching: %s", kw)
        start = 0
        page_num = 0

        while True:
            url = (
                f"https://www.linkedin.com/jobs/search/?"
                f"keywords={kw.replace(' ', '%20')}"
                f"&location=Hong%20Kong"
                f"&f_JT=I"
                f"&start={start}"
            )
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                page.wait_for_timeout(3000)

                # Scroll to load all cards on this page
                for _ in range(4):
                    page.keyboard.press("End")
                    page.wait_for_timeout(1500)

                cards = BaseScraper.extract_cards(page, [
                    "li[data-occludable-job-id]",
                    ".job-search-card",
                    "[class*='job-card']",
                    "[data-job-id]",
                ])
                log.info("  Page %d: %d cards", page_num + 1, len(cards))

                if not cards:
                    log.info("  No cards on page %d, stopping pagination.", page_num + 1)
                    break

                for card in cards:
                    try:
                        title_el = card.query_selector("h3, [class*='title']", "a[href*='/jobs/']")
                        company_el = card.query_selector("h4, [class*='company']")
                        link_el = card.query_selector("a[href*='/jobs/']")
                        title = title_el.inner_text().strip() if title_el else ""
                        company = company_el.inner_text().strip() if company_el else ""
                        href = link_el.get_attribute("href") if link_el else ""
                        job_url = (
                            href if href and href.startswith("http")
                            else f"https://www.linkedin.com{href}" if href
                            else ""
                        )
                        if title and len(title) > 3:
                            jobs.append(BaseScraper.make_job(
                                title, company, "Hong Kong", job_url, "LinkedIn"
                            ))
                    except Exception:
                        continue

                # Pagination check
                if max_pages > 0 and page_num + 1 >= max_pages:
                    log.info("  Reached max_pages=%d, stopping.", max_pages)
                    break

                # Try clicking "Next" button
                if not _go_to_next_page(page):
                    # Fallback: increment start= parameter and reload
                    if start > 0 and not page.query_selector("li[data-occludable-job-id]"):
                        break
                    start += CARDS_PER_PAGE
                    page_num += 1
                    time.sleep(random.uniform(2, 4))
                    continue

                start += CARDS_PER_PAGE
                page_num += 1
                time.sleep(random.uniform(2, 4))

            except Exception as e:
                log.warning("  LinkedIn [%s] page %d failed: %s", kw, page_num + 1, e)
                break

        time.sleep(random.uniform(2, 4))

    # Deduplicate by (title, company)
    seen = set()
    unique = []
    for j in jobs:
        key = (j.title.strip().lower(), j.company.strip().lower())
        if key not in seen and j.title.strip():
            seen.add(key)
            unique.append(j)

    log.info("[LinkedIn] Total: %d (deduplicated from %d)", len(unique), len(jobs))
    return unique
