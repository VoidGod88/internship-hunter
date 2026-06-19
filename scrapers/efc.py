"""
scrapers/efc.py — eFinancialCareers HK scraper.

eFC HK uses clean URL pattern for search:
  https://www.efinancialcareers.hk/jobs/{keyword-slug}

Example:
  https://www.efinancialcareers.hk/jobs/llm
"""
import logging
import random
import time
import urllib.parse
from .base import BaseScraper
from config import check_stop

log = logging.getLogger("hunter")

BASE = "https://www.efinancialcareers.hk"


def _build_url(keyword: str) -> str:
    slug = keyword.lower().replace(" ", "-")
    q = urllib.parse.quote(keyword.lower(), safe="+")  # keep + for spaces
    return (
        f"{BASE}/jobs/{slug}/in-hong-kong"
        f"?q={q}"
        f"&countryCode=HK"
        f"&radius=40"
        f"&radiusUnit=km"
        f"&pageSize=15"
        f"&filters.experienceLevel=NO_EXPERIENCE"
        f"&filters.locationPath=Asia%2FHong+Kong"
        f"&currencyCode=HKD"
        f"&language=en"
        f"&includeUnspecifiedSalary=true"
        f"&enableVectorSearch=true"
    )


def _parse_cards(page) -> list:
    """Extract job title + company + url from eFC Angular SPA cards using Playwright locators."""
    items = []
    cards = page.locator("efc-job-card")
    count = cards.count()
    
    for i in range(count):
        try:
            card = cards.nth(i)
            # Title: <a class="job-title"> or <h3> inside it
            title = ""
            title_a = card.locator("a.job-title").first
            if title_a:
                h3 = title_a.locator("h3").first
                if h3:
                    title = (h3.inner_text() or "").strip()
                if not title:
                    title = (title_a.inner_text() or "").strip()
            
            if not title or len(title) < 3:
                continue
            
            # Link
            href = ""
            if title_a:
                href = title_a.get_attribute("href") or ""
            
            # Company: <img itemprop="image" alt="..."> or <div class="company">
            company = ""
            img = card.locator("img[itemprop='image']").first
            if img:
                company = (img.get_attribute("alt") or img.get_attribute("title") or "").strip()
            if not company:
                comp_div = card.locator(".company, [class*='company']").first
                if comp_div:
                    company = (comp_div.inner_text() or "").strip()
            
            if not company:
                company = "(unknown)"
            
            if href and not href.startswith("http"):
                href = "https://www.efinancialcareers.hk" + href
            
            items.append({
                "title": title[:120],
                "company": company[:120],
                "href": href or "",
            })
        except Exception:
            continue
    
    # Dedup by href
    seen = set()
    unique = []
    for x in items:
        if x["href"] and x["href"] not in seen:
            seen.add(x["href"])
            unique.append(x)
    
    return unique


def scrape_efc(page, keywords: list[str] = None, max_pages: int = 5,
               location: str = "Hong Kong", jobtype_internship_only: bool = True) -> list:
    """
    Scrape eFinancialCareers HK.
    URL format: /jobs/{keyword}/in-hong-kong?q=...&pageSize=15
    Uses infinite scroll to load more results.
    max_pages = max scroll rounds (default 5).
    """
    all_jobs = []
    seen_hrefs: set = set()

    if not keywords:
        keywords = ["intern", "AI", "software engineer"]

    log.info(f"[eFC] Searching {len(keywords)} keywords...")

    for kw in keywords:
        kw_jobs = []
        url = _build_url(kw)
        log.info(f"[eFC] Searching: {kw} | URL: {url}")

        try:
            page.goto(url, wait_until="networkidle", timeout=60_000)
            page.wait_for_timeout(3000)
        except Exception as e:
            log.warning(f"[eFC]   Failed to load: {e}")
            continue

        # Scroll to load more results
        for scroll_round in range(max_pages):
            # Parse current cards
            cards = _parse_cards(page)
            new_count = 0
            for c in cards:
                if c["href"] in seen_hrefs:
                    continue
                seen_hrefs.add(c["href"])
                kw_jobs.append(BaseScraper.make_job(
                    title=c["title"][:120],
                    company=c.get("company") or "(unknown)",
                    location=location,
                    url=c["href"],
                    source="eFinancialCareers",
                ))
                new_count += 1

            log.info(f"[eFC]   Scroll {scroll_round+1}/{max_pages}: +{new_count} jobs (total: {len(kw_jobs)})")

            # Try scrolling to bottom to trigger lazy load
            if scroll_round + 1 >= max_pages:
                break
            try:
                page.keyboard.press("End")
                page.wait_for_timeout(3000)
            except Exception:
                break

        all_jobs.extend(kw_jobs)
        log.info(f"[eFC] Searching: {kw} → {len(kw_jobs)} jobs")
        time.sleep(random.uniform(2, 4))

    # Deduplicate
    seen = set()
    unique = []
    for j in all_jobs:
        key = (j.title.strip().lower(), j.company.strip().lower())
        if key not in seen and j.title.strip():
            seen.add(key)
            unique.append(j)

    log.info(f"[eFC] Total: {len(unique)} jobs")
    return unique
