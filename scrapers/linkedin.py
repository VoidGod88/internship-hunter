"""
scrapers/linkedin.py — LinkedIn HK job scraper using Playwright.
"""
import time
import random
import logging
from .base import BaseScraper

log = logging.getLogger("hunter")


def scrape_linkedin(page, keywords: list[str], max_per_kw: int = 15) -> list:
    """Scrape LinkedIn HK internship jobs."""
    jobs = []
    log.info("[LinkedIn] Starting...")

    for kw in keywords[:3]:
        log.info(f"  Searching: {kw}")
        url = (
            f"https://www.linkedin.com/jobs/search/?"
            f"keywords={kw.replace(' ', '%20')}"
            f"&location=Hong%20Kong"
            f"&f_JT=I"
        )
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(3000)
            for _ in range(4):
                page.keyboard.press("End")
                page.wait_for_timeout(1500)

            cards = BaseScraper.extract_cards(page, [
                "li[data-occludable-job-id]",
                ".job-search-card",
                "[class*='job-card']",
                "[data-job-id]",
            ])
            log.info(f"  Found {len(cards)} cards")

            for card in cards[:max_per_kw]:
                try:
                    title_el = card.query_selector("h3, [class*='title'], a[href*='/jobs/']")
                    company_el = card.query_selector("h4, [class*='company']")
                    link_el = card.query_selector("a[href*='/jobs/']")
                    title = title_el.inner_text().strip() if title_el else ""
                    company = company_el.inner_text().strip() if company_el else ""
                    href = link_el.get_attribute("href") if link_el else ""
                    job_url = (href if href and href.startswith("http")
                               else f"https://www.linkedin.com{href}" if href
                               else "")
                    if title and len(title) > 3:
                        jobs.append(BaseScraper.make_job(
                            title, company, "Hong Kong", job_url, "LinkedIn"
                        ))
                except Exception:
                    continue

        except Exception as e:
            log.warning(f"  LinkedIn [{kw}] Failed: {e}")
        time.sleep(random.uniform(2, 4))

    log.info(f"[LinkedIn] Total: {len(jobs)}")
    return jobs
