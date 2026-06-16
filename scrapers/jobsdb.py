"""
scrapers/jobsdb.py — JobsDB HK scraper using Playwright.
"""
import time
import random
import logging
from .base import BaseScraper

log = logging.getLogger("hunter")


def scrape_jobsdb(page, keywords: list[str], max_per_kw: int = 10) -> list:
    jobs = []
    log.info("[JobsDB] Starting...")

    for kw in keywords[:3]:
        log.info(f"  Searching: {kw}")
        url = f"https://hk.jobsdb.com/job-search/{kw.replace(' ', '-')}-jobs/in-hong-kong"
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(5000)
            try:
                page.click("button:has-text('Accept')", timeout=2000)
            except Exception:
                pass
            page.keyboard.press("End")
            page.wait_for_timeout(2000)

            cards = BaseScraper.extract_cards(page, [
                "[data-automation='job-list-item']",
                "article",
                "[class*='job-card']",
                "[class*='JobCard']",
                "div[class*='job']",
            ])
            log.info(f"  Found {len(cards)} cards")

            for card in cards[:max_per_kw]:
                try:
                    title_el = card.query_selector(
                        "[data-automation='job-title'], h3, a[href*='/job/'], [class*='title']"
                    )
                    company_el = card.query_selector(
                        "[data-automation='company-name'], h4, [class*='company']"
                    )
                    link_el = card.query_selector("a[href*='/job/'], a[href*='jobsdb.com']")
                    title = title_el.inner_text().strip() if title_el else ""
                    company = company_el.inner_text().strip() if company_el else ""
                    href = link_el.get_attribute("href") if link_el else ""
                    job_url = (href if href and href.startswith("http")
                               else f"https://hk.jobsdb.com{href}" if href
                               else "")
                    if title and len(title) > 3:
                        jobs.append(BaseScraper.make_job(
                            title, company, "Hong Kong", job_url, "JobsDB"
                        ))
                except Exception:
                    continue

        except Exception as e:
            log.warning(f"  JobsDB [{kw}] Failed: {e}")
        time.sleep(random.uniform(2, 4))

    log.info(f"[JobsDB] Total: {len(jobs)}")
    return jobs
