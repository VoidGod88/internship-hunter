"""
scrapers/jobsdb.py — JobsDB HK scraper using Playwright.
One keyword per search, paginate until no next page.
"""

import time
import random
import logging
import urllib.parse
from .base import BaseScraper

log = logging.getLogger("hunter")

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
    for sel in selectors:
        try:
            elements = page.query_selector_all(sel)
            if elements:
                return elements
        except Exception:
            continue
    return []


def _has_next_page(page) -> bool:
    try:
        next_btn = page.query_selector(
            "a[aria-label='Next'], button[aria-label='Next'], "
            "a[aria-label='下一页'], [class*='next']"
        )
        return bool(next_btn and next_btn.is_visible())
    except Exception:
        return False


def _go_to_next_page(page) -> bool:
    try:
        next_btn = page.query_selector(
            "a[aria-label='Next'], button[aria-label='Next'], "
            "a[aria-label='下一页'], [class*='next']"
        )
        if next_btn and next_btn.is_enabled():
            next_btn.click()
            page.wait_for_timeout(3000)
            return True
    except Exception:
        pass
    return False


def scrape_jobsdb(page, keywords: list[str], max_pages: int = 0) -> list:
    """
    Scrape JobsDB HK for internship jobs.
    One search per keyword, paginate until no next page.
    """
    all_jobs = []
    log.info(f"[JobsDB] Searching {len(keywords)} keywords...")

    for kw in keywords:
        kw_jobs = []
        encoded_kw = urllib.parse.quote(kw)
        base_url = f"https://hk.jobsdb.com/job-search?q={encoded_kw}&l=Hong+Kong"
        page_num = 0

        while True:
            url = base_url
            if page_num > 0:
                url += f"&page={page_num + 1}"

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

                cards = _query_selector_all_multi(page, CARD_SELECTORS)
                if not cards:
                    break

                for card in cards:
                    try:
                        title = ""
                        for sel in TITLE_SELECTORS:
                            title_el = card.query_selector(sel)
                            if title_el:
                                title = title_el.inner_text().strip()
                                break
                        if not title or len(title) < 3:
                            full_text = card.inner_text().strip()
                            lines = [l.strip() for l in full_text.split("\n") if l.strip()]
                            title = lines[0][:120] if lines else ""

                        if not title or len(title) < 3:
                            continue

                        company = ""
                        for sel in COMPANY_SELECTORS:
                            company_el = card.query_selector(sel)
                            if company_el:
                                company = company_el.inner_text().strip()
                                break

                        href = ""
                        link_el = card.query_selector("a[href*='/job/']")
                        if link_el:
                            href = link_el.get_attribute("href") or ""
                        else:
                            href = card.get_attribute("href") or ""

                        if href and not href.startswith("http"):
                            href = "https://hk.jobsdb.com" + href
                        job_url = href or ""

                        if title and len(title) > 3:
                            kw_jobs.append(BaseScraper.make_job(
                                title, company, "Hong Kong", job_url, "JobsDB"
                            ))

                    except Exception:
                        continue

                if max_pages > 0 and page_num + 1 >= max_pages:
                    break
                if not _go_to_next_page(page):
                    break

                page_num += 1
                time.sleep(random.uniform(2, 4))

            except Exception:
                break

        all_jobs.extend(kw_jobs)
        log.info(f"[JobsDB] Searching: {kw} → {len(kw_jobs)} jobs")
        time.sleep(random.uniform(2, 4))

    # Deduplicate by (title, company)
    seen = set()
    unique = []
    for j in all_jobs:
        key = (j.title.strip().lower(), j.company.strip().lower())
        if key not in seen and j.title.strip():
            seen.add(key)
            unique.append(j)

    log.info(f"[JobsDB] Total: {len(unique)} jobs")
    return unique
