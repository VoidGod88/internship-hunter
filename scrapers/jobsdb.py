"""
scrapers/jobsdb.py — JobsDB HK scraper using Playwright.
"""
import time
import random
import logging
import urllib.parse
from .base import BaseScraper

log = logging.getLogger("hunter")

# Multiple card selectors to try (in order of reliability)
CARD_SELECTORS = [
    "[data-automation='job-list-item']",
    "article",
    "[class*='job-card']",
    "[class*='JobCard']",
    "a[href*='/job/']",
]

TITLE_SELECTORS = [
    "[data-automation='job-title']",
    "h3", "h2",
    "a[href*='/job/']",
    "[class*='title']",
]

COMPANY_SELECTORS = [
    "[data-automation='company-name']",
    "h4",
    "[class*='company']",
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
                log.debug(f"[JobsDB] Found {len(elements)} cards with selector: {sel}")
                return elements
        except Exception as e:
            log.debug(f"[JobsDB] Selector '{sel}' failed: {e}")
            continue
    return []


def scrape_jobsdb(page, keywords: list[str], max_per_kw: int = 10) -> list:
    """
    Scrape JobsDB HK for internship jobs.
    URL format: https://hk.jobsdb.com/job-search?q={kw}&l=Hong+Kong
    """
    jobs = []
    log.info("[JobsDB] Starting...")

    for kw in keywords[:3]:
        log.info(f"  Searching: {kw}")
        encoded_kw = urllib.parse.quote(kw)
        url = f"https://hk.jobsdb.com/job-search?q={encoded_kw}&l=Hong+Kong"
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(5000)

            # Dismiss cookie/privacy popup
            try:
                page.click("button:has-text('Accept'), button:has-text('同意')", timeout=3000)
            except Exception:
                pass

            page.keyboard.press("End")
            page.wait_for_timeout(3000)

            # Use multi-selector helper
            cards = _query_selector_all_multi(page, CARD_SELECTORS)
            log.info(f"  Found {len(cards)} cards")

            for card in cards[:max_per_kw]:
                try:
                    # Try to get title from card
                    title = ""
                    title_el = None
                    for sel in TITLE_SELECTORS:
                        title_el = card.query_selector(sel)
                        if title_el:
                            title = title_el.inner_text().strip()
                            break

                    # Fallback: use card's own text
                    if not title or len(title) < 3:
                        full_text = card.inner_text().strip()
                        lines = [l.strip() for l in full_text.split("\n") if l.strip()]
                        title = lines[0][:120] if lines else ""

                    if not title or len(title) < 3:
                        continue

                    # Company
                    company = ""
                    for sel in COMPANY_SELECTORS:
                        company_el = card.query_selector(sel)
                        if company_el:
                            company = company_el.inner_text().strip()
                            break

                    # URL
                    href = ""
                    link_el = card.query_selector("a[href*='/job/']")
                    if link_el:
                        href = link_el.get_attribute("href") or ""
                    else:
                        # Card itself might be an <a>
                        href = card.get_attribute("href") or ""

                    if href and not href.startswith("http"):
                        href = "https://hk.jobsdb.com" + href
                    job_url = href or ""

                    if title and len(title) > 3:
                        jobs.append(BaseScraper.make_job(
                            title, company, "Hong Kong", job_url, "JobsDB"
                        ))

                except Exception as e:
                    log.debug(f"[JobsDB] Card parse error: {e}")
                    continue

        except Exception as e:
            log.warning(f"  JobsDB [{kw}] Failed: {e}")
        time.sleep(random.uniform(2, 4))

    log.info(f"[JobsDB] Total: {len(jobs)}")
    return jobs
