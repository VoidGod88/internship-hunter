"""
scrapers/indeed.py — Indeed HK scraper using Playwright.
"""
import time
import random
import logging
from .base import BaseScraper

log = logging.getLogger("hunter")


def scrape_indeed(page, keywords: list[str], max_per_kw: int = 5) -> list:
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

            page.wait_for_timeout(2000)

            cards = BaseScraper.extract_cards(page, [
                "[data-jk]",
                ".job_seen_beacon",
                "[class*='jobContainer']",
                ".resultContent",
                "article",
            ])
            log.info("    Found %d cards", len(cards))

            for card in cards[:max_per_kw]:
                try:
                    title_el = card.query_selector(
                        "[data-jk] h2 a, h2 a, [class*='title'] a, a[href*='/job/']"
                    )
                    company_el = card.query_selector("[data-jk] span, [class*='company']")
                    link_el = card.query_selector("a[href*='/job/']")
                    title = title_el.inner_text().strip() if title_el else ""
                    company = company_el.inner_text().strip() if company_el else ""
                    href = link_el.get_attribute("href") if link_el else ""
                    job_url = (href if href and href.startswith("http")
                               else f"https://hk.indeed.com{href}" if href
                               else "")
                    if title and len(title) > 3:
                        jobs.append(BaseScraper.make_job(
                            title, company, "Hong Kong", job_url, "Indeed"
                        ))
                except Exception:
                    continue

        except Exception as e:
            log.warning("    Indeed [%s] Failed: %s", kw[:40], e)

        # Short delay between keywords
        time.sleep(random.uniform(1, 2))

    log.info("[Indeed] Total: %d", len(jobs))
    return jobs
