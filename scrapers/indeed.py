"""
scrapers/indeed.py — Indeed HK scraper using Playwright.
One keyword per search, paginate until no Next button.
"""

import time
import random
import logging
from .base import BaseScraper

log = logging.getLogger("hunter")

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
    for sel in selectors:
        try:
            elements = page.query_selector_all(sel)
            if elements:
                return elements
        except Exception:
            continue
    return []


def _go_to_next_page(page) -> bool:
    try:
        btn = page.query_selector('a[aria-label="Next"], a:has-text("Next")')
        if btn and btn.is_enabled():
            btn.click()
            page.wait_for_timeout(3000)
            return True
    except Exception:
        pass
    return False


def scrape_indeed(page, keywords: list[str], max_pages: int = 0) -> list:
    """
    Scrape Indeed HK for internship jobs.
    One search per keyword, paginate until no Next button.
    """
    all_jobs = []
    log.info(f"[Indeed] Searching {len(keywords)} keywords...")

    for kw in keywords:
        kw_jobs = []
        base_url = (
            f"https://hk.indeed.com/jobs?"
            f"q={kw.replace(' ', '+')}"
            f"&l=Hong+Kong"
            f"&jt=internship"
        )
        start = 0
        page_num = 0

        while True:
            url = base_url + f"&start={start}" if start > 0 else base_url
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=20_000)
                try:
                    page.wait_for_selector("[data-jk], .job_seen_beacon, .resultContent", timeout=10_000)
                except Exception:
                    break

                page.wait_for_timeout(3000)
                cards = _query_selector_all_multi(page, CARD_SELECTORS)
                if not cards:
                    break

                for card in cards:
                    try:
                        title = ""
                        for sel in TITLE_SELECTORS:
                            try:
                                title_el = card.query_selector(sel)
                                if title_el:
                                    title = title_el.inner_text().strip()
                                    break
                            except Exception:
                                continue

                        if not title or len(title) < 3:
                            txt = card.inner_text()
                            lines = [l.strip() for l in txt.split("\n") if l.strip()]
                            if lines:
                                title = lines[0][:120]

                        if not title or len(title) < 3:
                            continue

                        company = ""
                        for sel in COMPANY_SELECTORS:
                            try:
                                company_el = card.query_selector(sel)
                                if company_el:
                                    company = company_el.inner_text().strip()
                                    break
                            except Exception:
                                continue

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
                            kw_jobs.append(BaseScraper.make_job(
                                title, company, "Hong Kong", job_url, "Indeed"
                            ))

                    except Exception:
                        continue

                if max_pages > 0 and page_num + 1 >= max_pages:
                    break
                if not _go_to_next_page(page):
                    break

                start += 10
                page_num += 1
                time.sleep(random.uniform(2, 4))

            except Exception:
                break

        all_jobs.extend(kw_jobs)
        log.info(f"[Indeed] Searching: {kw} → {len(kw_jobs)} jobs")
        time.sleep(random.uniform(1, 2))

    # Deduplicate by (title, company)
    seen = set()
    unique = []
    for j in all_jobs:
        key = (j.title.strip().lower(), j.company.strip().lower())
        if key not in seen and j.title.strip():
            seen.add(key)
            unique.append(j)

    log.info(f"[Indeed] Total: {len(unique)} jobs")
    return unique
