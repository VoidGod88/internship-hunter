"""
scrapers/jobsdb.py — JobsDB HK scraper using Playwright.
"""
import time
import random
import logging
from pathlib import Path
from .base import BaseScraper

log = logging.getLogger("hunter")


def scrape_jobsdb(page, keywords: list[str], max_per_kw: int = 10) -> list:
    """
    Scrape JobsDB HK for internship jobs.
    URL format: https://hk.jobsdb.com/job-search/{kw}-jobs/in-hong-kong
    """
    jobs = []
    log.info("[JobsDB] Starting...")

    for kw in keywords[:3]:
        log.info(f"  Searching: {kw}")
        # Build URL — spaces to %20, not hyphens (JobsDB uses query params)
        import urllib.parse
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

            # Try multiple card selectors (JobsDB changes DOM frequently)
            cards = page.query_selector_all([
                "[data-automation='job-list-item']",
                "article",
                "[class*='job-card']",
                "[class*='JobCard']",
                "div[class*='job']",
                "a[href*='/job/']",
            ])
            # Flatten if nested
            if cards and isinstance(cards[0], list):
                cards = [c for sublist in cards for c in (sublist if isinstance(sublist, list) else [sublist])]

            # Fallback: if query_selector_all with list didn't work, try one by one
            if not cards:
                for sel in ["[data-automation='job-list-item']", "article",
                            "[class*='job-card']", "a[href*='/job/']"]:
                    cards = page.query_selector_all(sel)
                    if cards:
                        log.debug(f"[JobsDB] Found cards with selector: {sel}")
                        break

            log.info(f"  Found {len(cards)} cards")

            for card in cards[:max_per_kw]:
                try:
                    # Try multiple title selectors
                    title_el = card.query_selector([
                        "[data-automation='job-title']",
                        "h3", "h2",
                        "a[href*='/job/']",
                        "[class*='title']",
                    ]) if not isinstance(card, str) else None
                    # Handle case where card itself is an <a>
                    if not title_el and hasattr(card, "inner_text"):
                        title = card.inner_text().strip()
                        href = card.get_attribute("href") or ""
                        company = ""
                        job_url = href if href.startswith("http") else f"https://hk.jobsdb.com{href}"
                        if title and len(title) > 3:
                            jobs.append(BaseScraper.make_job(
                                title, company, "Hong Kong", job_url, "JobsDB"
                            ))
                        continue

                    title = title_el.inner_text().strip() if title_el else ""
                    company_el = card.query_selector("[data-automation='company-name'], h4, [class*='company']")
                    company = company_el.inner_text().strip() if company_el else ""
                    link_el = card.query_selector("a[href*='/job/']")
                    href = link_el.get_attribute("href") if link_el else ""
                    job_url = (
                        href if href and href.startswith("http")
                        else f"https://hk.jobsdb.com{href}" if href
                        else ""
                    )
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
