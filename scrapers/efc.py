"""
scrapers/efc.py — eFinancialCareers HK scraper using Playwright.
"""
import logging
from .base import BaseScraper

log = logging.getLogger("hunter")


def scrape_efc(page, max_results: int = 20) -> list:
    jobs = []
    log.info("[eFC] Starting...")
    url = "https://www.efinancialcareers.hk/jobs/search?keywords=AI+intern&location=Hong+Kong"
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_timeout(4000)
        cards = BaseScraper.extract_cards(page, [
            "[data-cy='job-card']",
            "article",
            ".job-card",
            "[class*='job']",
        ])
        for card in cards[:max_results]:
            try:
                title_el = card.query_selector("h3, a[href*='/job']")
                company_el = card.query_selector("[class*='company'], .employer")
                link_el = card.query_selector("a[href*='/job']")
                title = title_el.inner_text().strip() if title_el else ""
                company = company_el.inner_text().strip() if company_el else ""
                href = link_el.get_attribute("href") if link_el else ""
                job_url = (href if href and href.startswith("http")
                           else f"https://www.efinancialcareers.hk{href}" if href
                           else "")
                if title and len(title) > 3:
                    jobs.append(BaseScraper.make_job(
                        title, company, "Hong Kong", job_url, "eFinancialCareers"
                    ))
            except Exception:
                continue
    except Exception as e:
        log.warning(f"  eFC Failed: {e}")
    log.info(f"[eFinancialCareers] Total: {len(jobs)}")
    return jobs
