"""
scrapers/jobsdb.py — JobsDB HK scraper using Playwright.
One keyword per search, paginate until no next page.
URL built dynamically from config filters (Settings UI).
"""

import time
import random
import logging
from .base import BaseScraper
from config import config

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
            page.wait_for_load_state("domcontentloaded")
            return True
    except Exception:
        pass
    return False


def _check_page_6(page, base_url: str) -> bool:
    """
    Quick probe: check if page 6 has job cards.
    Navates to {base_url}?page=6 and checks for cards.
    Returns True if cards found (page 6 exists).
    """
    try:
        sep = "&" if "?" in base_url else "?"
        url = base_url + sep + "page=6"
        log.info(f"[JobsDB]   Probing page 6: {url}")
        page.goto(url, wait_until="domcontentloaded", timeout=15_000)
        page.wait_for_timeout(3000)
        cards = _query_selector_all_multi(page, CARD_SELECTORS)
        has_page_6 = len(cards) > 0
        log.info(f"[JobsDB]   Page 6 probe: {'found ' + str(len(cards)) + ' cards' if has_page_6 else 'no cards (no page 6)'}")
        return has_page_6
    except Exception as e:
        log.info(f"[JobsDB]   Page 6 probe failed: {e}")
        return False


def scrape_jobsdb(page, keywords: list[str], max_pages: int = 0, max_jobs: int = 0) -> list:
    """
    Scrape JobsDB HK for internship jobs.
    URL format from config: /{keyword}-jobs-in-{category}/{work_type}[?daterange=N&workarrangement=...][&page=N]
    """
    all_jobs = []
    log.info(f"[JobsDB] Searching {len(keywords)} keywords...")

    # JobsDB ID mapping
    WT_IDS = {"242": "Full time", "243": "Part time", "244": "Contract/Temp", "245": "Casual/Vacation"}
    WA_IDS = {"1": "On-site", "2": "Hybrid", "3": "Remote"}

    for kw in keywords:
        kw_slug = kw.lower().replace(" ", "-")
        category = config.jd_category or "information-communication-technology"

        # Work type: list of IDs → comma-separated string (or empty for all)
        if config.jd_work_type:
            wt_ids = [str(v) for v in config.jd_work_type]
            wt_str = ",".join(wt_ids)
            wt_labels = [WT_IDS.get(v, v) for v in wt_ids]
            log.info(f"[JobsDB]   {kw}: work_type={', '.join(wt_labels)}")
        else:
            wt_str = ""
            log.info(f"[JobsDB]   {kw}: work_type=all")

        base_url = f"https://hk.jobsdb.com/{kw_slug}-jobs-in-{category}/in-hong-kong"
        if wt_str:
            base_url += f"?worktype={wt_str}"  # JobsDB uses /in-hong-kong for location
        else:
            base_url += ""

        # Build query params
        params = []

        # Work arrangement (remote options): list → comma-separated
        if config.jd_work_arrangement:
            wa_ids = [str(v) for v in config.jd_work_arrangement]
            wa_str = ",".join(wa_ids)
            wa_labels = [WA_IDS.get(v, v) for v in wa_ids]
            params.append(f"workarrangement={wa_str}")
            log.info(f"[JobsDB]   {kw}: work_arrangement={', '.join(wa_labels)}")

        # Date range
        if config.jd_daterange:
            params.append(f"daterange={config.jd_daterange}")
            log.info(f"[JobsDB]   {kw}: daterange={config.jd_daterange}")

        # Build final URL (handle case where base_url already has query string)
        if params:
            sep = "&" if "?" in base_url else "?"
            scrape_url = base_url + sep + "&".join(params)
        elif not config.jd_daterange:
            # No daterange configured — probe page 6 to decide
            has_page_6 = _check_page_6(page, base_url)
            if has_page_6:
                log.info(f"[JobsDB]   {kw}: page 6 exists, using daterange=7")
                scrape_url = base_url + "?daterange=7"
            else:
                log.info(f"[JobsDB]   {kw}: no page 6, using normal URL")
                scrape_url = base_url
        else:
            scrape_url = base_url

        log.info(f"[JobsDB] Searching: {kw} | URL: {scrape_url}")
        kw_jobs = _scrape_jobsdb_keyword(page, kw, scrape_url, max_pages=0, max_jobs=0)
        all_jobs.extend(kw_jobs)
        log.info(f"[JobsDB] {kw}: {len(kw_jobs)} jobs")
        time.sleep(random.uniform(1, 2))

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


def _scrape_jobsdb_keyword(page, kw: str, base_url: str, max_pages: int = 0, max_jobs: int = 0) -> list:
    """Scrape one keyword with a given base URL. Returns list of jobs."""
    kw_jobs = []
    page_num = 0

    while True:
        url = base_url
        if page_num > 0:
            sep = "&" if "?" in base_url else "?"
            url += f"{sep}page={page_num + 1}"

        try:
            log.info(f"[JobsDB]   Fetching page {page_num + 1}: {url}")
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(1500)

            cards = _query_selector_all_multi(page, CARD_SELECTORS)
            if not cards:
                break

            before_count = len(kw_jobs)
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
                        # Stop if max_jobs reached
                        if max_jobs > 0 and len(kw_jobs) >= max_jobs:
                            break

                except Exception:
                    continue

            log.info(f"[JobsDB]   Page {page_num + 1}: {len(cards)} cards → +{len(kw_jobs) - before_count} new ({len(kw_jobs)} total)")

            if max_jobs > 0 and len(kw_jobs) >= max_jobs:
                break

            if max_pages > 0 and page_num + 1 >= max_pages:
                break
            if not _go_to_next_page(page):
                break

            page_num += 1
            time.sleep(random.uniform(0.5, 1))

        except Exception:
            break

    return kw_jobs
