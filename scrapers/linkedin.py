"""
scrapers/linkedin.py — LinkedIn HK job scraper using Playwright.
Uses proven pagination logic (feed login → &start=N → scroll_into_view → URL dedup).
"""

import time
import random
import logging
from pathlib import Path

from .base import BaseScraper
from config import check_stop, config

log = logging.getLogger("hunter")

# Fixed selector (confirmed working 2026-06-22)
_SELECTOR = "li[data-occludable-job-id]"


def _check_session(page) -> bool:
    """Verify LinkedIn session is valid. If not, warn and return False."""
    try:
        current = page.url.lower()
        if "login" in current:
            log.warning("[LinkedIn]   Not logged in! Cookies may be expired.")
            log.warning("[LinkedIn]   Please run: python linkedin_login.py")
            return False
        if "checkpoint" in current or "challenge" in current:
            log.warning("[LinkedIn]   ⚠️ Security checkpoint detected!")
            log.warning("[LinkedIn]   Session flagged. Please re-acquire cookies:")
            log.warning("[LinkedIn]     python linkedin_login.py")
            log.warning("[LinkedIn]   Then re-run the scraper.")
            return False
    except Exception:
        pass
    return True


def _check_no_results(page) -> bool:
    """Detect 'no results' page (recommendation cards only, not real search results)."""
    try:
        body_text = page.inner_text("body")
        for msg in ["没有符合条件的职位", "No results found", "沒有符合條件的職位"]:
            if msg in body_text:
                return True
    except Exception:
        pass
    return False


def _extract_cards(page, seen_ids: set) -> list[dict]:
    """Extract job data from all cards on current page.
    Scrolls each card into view first (LinkedIn virtual list).
    Uses job URL as primary unique key.
    """
    cards = page.query_selector_all(_SELECTOR)
    new_jobs = []

    for card in cards:
        try:
            # Scroll card into view (required for virtual list rendering)
            try:
                card.scroll_into_view_if_needed(timeout=2000)
                time.sleep(0.15)
            except Exception:
                pass

            # ── Unique ID ──
            # Primary: job URL (most reliable)
            job_url = ""
            link = card.query_selector("a[href*='/jobs/view/']")
            if link:
                href = link.get_attribute("href") or ""
                if href.startswith("/"):
                    href = "https://www.linkedin.com" + href
                job_url = href.split("?")[0]  # remove ?eBP=...

            # Secondary: data-occludable-job-id
            raw_id = card.get_attribute("data-occludable-job-id")

            if job_url:
                unique_key = f"url:{job_url}"
            elif raw_id:
                unique_key = f"id:{raw_id}"
            else:
                unique_key = f"idx:{len(seen_ids)}"

            if unique_key in seen_ids:
                continue

            # ── Title (3-strategy fallback) ──
            title = ""
            for sel in [".base-search-card__title a", "h3 a", "h3"]:
                el = card.query_selector(sel)
                if el:
                    text = el.inner_text().strip()
                    if text and len(text) > 2:
                        title = text
                        break
            if not title:
                links = card.query_selector_all("a")
                for link in links:
                    text = link.inner_text().strip()
                    if text and 5 < len(text) < 200:
                        title = text
                        break
            if not title:
                full_text = card.inner_text().strip()
                lines = [l.strip() for l in full_text.split('\n') if l.strip()]
                if lines:
                    title = lines[0][:100]

            # ── Company ──
            company = ""
            for sel in ["[class*='company']", ".base-search-card__subtitle", "h4"]:
                el = card.query_selector(sel)
                if el:
                    text = el.inner_text().strip()
                    if text:
                        company = text
                        break

            if not title:
                title = f"[No title - ID: {unique_key[:30]}]"

            new_jobs.append({
                "id": unique_key,
                "title": title,
                "company": company,
                "url": job_url,
            })
            seen_ids.add(unique_key)

        except Exception as e:
            log.debug(f"[LinkedIn]   Card extract error: {e}")
            continue

    return new_jobs


def _scrape_keyword(page, kw: str) -> list:
    """Scrape a single keyword using URL pagination (&start=0,25,50...)."""
    import urllib.parse

    encoded_kw = urllib.parse.quote(kw)
    params = []
    
    # Experience Level: list → comma-separated (e.g., [1,2,6] → "1,2,6")
    if config.li_exp_level and len(config.li_exp_level) > 0:
        exp_str = ",".join(str(v) for v in config.li_exp_level)
        params.append(f"f_E={exp_str}")
    
    # Job Types: list → comma-separated (e.g., ["F","P","I"] → "F,P,I")
    if config.li_job_types and len(config.li_job_types) > 0:
        jt_str = ",".join(str(v) for v in config.li_job_types)
        encoded_jt = urllib.parse.quote(jt_str)
        params.append(f"f_JT={encoded_jt}")
    
    # Work Types: list → comma-separated (e.g., [1,2] → "1,2")
    if config.li_work_types and len(config.li_work_types) > 0:
        wt_str = ",".join(str(v) for v in config.li_work_types)
        params.append(f"f_WT={wt_str}")
    
    if config.li_geo_id:
        params.append(f"geoId={config.li_geo_id}")
    if config.li_sort_by:
        params.append(f"sortBy={config.li_sort_by}")
    if config.li_posted_within:
        params.append(f"f_TPR={config.li_posted_within}")
    
    params.append(f"keywords={encoded_kw}")
    params.append("origin=JOB_SEARCH_PAGE_JOB_FILTER")
    params.append("spellCorrectionEnabled=true")
    base_url = "https://www.linkedin.com/jobs/search/?" + "&".join(params)

    log.info(f"[LinkedIn] Searching: {kw} | URL: {base_url}")

    jobs_for_kw = []
    seen_ids = set()
    page_num = 0
    page_size = 25
    max_pages = 10
    empty_pages = 0

    while page_num < max_pages and empty_pages < 2:
        try:
            check_stop()
        except InterruptedError:
            break

        start = page_num * page_size
        url = base_url + f"&start={start}" if start > 0 else base_url

        log.debug(f"[LinkedIn]   Page {page_num+1}: &start={start}")

        try:
            page.goto(url, timeout=30000)
            time.sleep(0.5)

            # Scroll to trigger lazy loading
            for _ in range(3):
                page.keyboard.press("End")
                page.evaluate("window.scrollBy(0, 800)")
                time.sleep(0.5)

            # Wait for cards to appear
            deadline = time.time() + 5
            while page.locator(_SELECTOR).count() < 10 and time.time() < deadline:
                time.sleep(0.5)
            page.evaluate("window.scrollBy(0, 500)")
            time.sleep(0.3)

            cards = page.query_selector_all(_SELECTOR)
            count = len(cards)

            # Check "no results" on first page
            if page_num == 0:
                if _check_no_results(page):
                    log.info(f"[LinkedIn]   '{kw}': no matching jobs (recommendations only), skipping")
                    break

            log.debug(f"[LinkedIn]   Page {page_num+1}: {count} cards")

            # Extract jobs
            new_jobs = _extract_cards(page, seen_ids)
            jobs_for_kw.extend(new_jobs)
            log.info(f"[LinkedIn]   Page {page_num+1}: {count} cards → +{len(new_jobs)} new ({len(jobs_for_kw)} total)")

            # Stop conditions
            if count > 0 and count < page_size:
                log.info(f"[LinkedIn]   Last page ({count} < {page_size}), stopping")
                break
            if count == 0:
                empty_pages += 1
                log.info(f"[LinkedIn]   Empty page (consecutive: {empty_pages})")
                if empty_pages >= 2:
                    break
                page_num += 1
                continue

            empty_pages = 0
            page_num += 1

            # Human-like delay
            time.sleep(random.uniform(0.5, 1))

        except Exception as e:
            log.warning(f"[LinkedIn]   Page {page_num+1} error: {e}")
            empty_pages += 1
            page_num += 1

    log.info(f"[LinkedIn] {kw}: {len(jobs_for_kw)} jobs")
    return jobs_for_kw


def scrape_linkedin(page, keywords: list[str]) -> list:
    """Scrape LinkedIn HK jobs. One browser session for all keywords."""
    # Ensure LinkedIn session is established (cookies need feed/ visit to activate)
    current = page.url
    if "feed" not in current and "linkedin.com" not in current:
        log.info("[LinkedIn]   Establishing session (navigating to feed/)")
        try:
            page.goto("https://www.linkedin.com/feed/", timeout=30000)
            page.wait_for_url("**/feed/**", timeout=15000)
        except Exception as e:
            log.warning(f"[LinkedIn]   Feed navigation timeout (may already be logged in): {e}")

    jobs: list = []
    log.info(f"[LinkedIn] Searching {len(keywords)} keywords...")

    # Verify session
    if not _check_session(page):
        log.error("[LinkedIn] Cannot scrape: not logged in")
        return []

    for kw in keywords:
        try:
            kw_jobs = _scrape_keyword(page, kw)
        except Exception as e:
            log.error(f"[LinkedIn]   Error scraping '{kw}': {e}")
            kw_jobs = []

        # Convert to Job namedtuple
        for j in kw_jobs:
            jobs.append(BaseScraper.make_job(
                j["title"], j["company"], "Hong Kong", j["url"], "LinkedIn"
            ))

        time.sleep(random.uniform(1, 2))

    # Final cross-keyword dedup
    seen: set = set()
    unique: list = []
    for j in jobs:
        key = (j.title.strip().lower(), j.company.strip().lower())
        if key not in seen and j.title.strip():
            seen.add(key)
            unique.append(j)

    log.info(f"[LinkedIn] Total: {len(unique)} jobs")
    if len(unique) == 0:
        log.warning("[LinkedIn] ⚠️ 0 jobs returned — session may be flagged.")
        log.warning("[LinkedIn]   Try re-acquiring cookies:")
        log.warning("[LinkedIn]     python linkedin_login.py")
    return unique
