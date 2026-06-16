"""
scrapers/indeed.py — Indeed HK scraper using Playwright.
"""
import time
import random
import logging
from .base import BaseScraper

log = logging.getLogger("hunter")

# Multiple card selectors to try (in order of reliability)
CARD_SELECTORS = [
    "[data-jk]",
    ".job_seen_beacon",
    "[class*='jobContainer']",
    ".resultContent",
    "article",
]

TITLE_SELECTORS = [
    "h2 a",
    "[data-jk] h2 a",
    "[class*='title'] a",
    "a[href*='/job/']",
    "span[title]",
]

COMPANY_SELECTORS = [
    "[data-jk] span",
    "[class*='company']",
    "span[class*='company']",
]


def _query_selector_all_multi(page, selectors: list[str]) -> list:
    """
    Try multiple selectors, return the first non-empty result.
    Playwright's query_selector_all only accepts a single string selector.
    """
    for sel in selectors:
        try:
            elements = page.query_selector_all(sel)
            if elements:
                log.debug(f"[Indeed] Found {len(elements)} cards with selector: {sel}")
                return elements
        except Exception as e:
            log.debug(f"[Indeed] Selector '{sel}' failed: {e}")
            continue
    return []


def scrape_indeed(page, keywords: list[str], max_per_kw: int = 5) -> list:
    """
    Scrape Indeed HK for internship jobs.
    URL format: https://hk.indeed.com/jobs?q={kw}&l=Hong+Kong&jt=internship
    """
    jobs = []
    log.info("[Indeed] Starting (%d keywords)...", len(keywords))

    for i, kw in enumerate(keywords):
        log.info("  [%d/%d] Searching: %s", i + 1, len(keywords), kw[:60])
        url = (
            f"https://hk.indeed.com/jobs?"
            f"q={kw.replace(' ', '+')}"
            f"&l=Hong+Kong"
            f"&jt=internship"
        )
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20_000)
            # Wait for job cards to appear
            try:
                page.wait_for_selector("[data-jk], .job_seen_beacon, .resultContent", timeout=10_000)
            except Exception:
                log.warning("    No job cards found, skipping...")
                page.wait_for_timeout(1000)
                continue

            page.wait_for_timeout(3000)

            # Use multi-selector helper
            cards = _query_selector_all_multi(page, CARD_SELECTORS)
            log.info("    Found %d cards", len(cards))

            for card in cards[:max_per_kw]:
                try:
                    # Try to get title from card
                    title = ""
                    title_el = None
                    for sel in TITLE_SELECTORS:
                        try:
                            title_el = card.query_selector(sel)
                            if title_el:
                                title = title_el.inner_text().strip()
                                break
                        except Exception:
                            continue

                    # Fallback: get title from card text
                    if not title or len(title) < 3:
                        txt = card.inner_text()
                        lines = [l.strip() for l in txt.split("\n") if l.strip()]
                        if lines:
                            title = lines[0][:120]

                    if not title or len(title) < 3:
                        continue

                    # Company
                    company = ""
                    for sel in COMPANY_SELECTORS:
                        try:
                            company_el = card.query_selector(sel)
                            if company_el:
                                company = company_el.inner_text().strip()
                                break
                        except Exception:
                            continue

                    # URL
                    href = ""
                    try:
                        link_el = card.query_selector("a[href*='/job/']")
                        if link_el:
                            href = link_el.get_attribute("href") or ""
                    except Exception:
                        pass

                    if href and not href.startswith("http"):
                        href = "https://hk.indeed.com" + href
                    job_url = href or ""

                    if title and len(title) > 3:
                        jobs.append(BaseScraper.make_job(
                            title, company, "Hong Kong", job_url, "Indeed"
                        ))

                except Exception as e:
                    log.debug("  [Indeed] Card parse error: %s", e)
                    continue

        except Exception as e:
            log.warning("    Indeed [%s] Failed: %s", kw[:40], e)

        # Short delay between keywords
        time.sleep(random.uniform(1, 2))

    log.info("[Indeed] Total: %d", len(jobs))
    return jobs
