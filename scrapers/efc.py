"""
scrapers/efc.py — eFinancialCareers HK scraper.

eFC HK URL filters:
  keywords=<q>          — search query
  location=<loc>        — city
  jobtype=<id>          — 1=Permanent, 2=Contract, 3=Internship
  pagesize=<n>          — items per page (max 50)
  page=<n>              — pagination (0-indexed)

Example internship search in Hong Kong:
  https://www.efinancialcareers.hk/jobs/search?keywords=AI&location=Hong+Kong&jobtype=3&page=0
"""
import logging
from .base import BaseScraper

log = logging.getLogger("hunter")

BASE = "https://www.efinancialcareers.hk"
JOBTYPE_INTERNSHIP = 3


def _build_url(keyword: str, location: str, page_no: int) -> str:
    return (
        f"{BASE}/jobs/search"
        f"?keywords={keyword.replace(' ', '+')}"
        f"&location={location.replace(' ', '+')}"
        f"&jobtype={JOBTYPE_INTERNSHIP}"
        f"&pagesize=50"
        f"&page={page_no}"
    )


def _parse_cards(page) -> list:
    """Pull every job link + title from the eFC results page."""
    items = page.evaluate("""
        () => {
            const out = [];
            document.querySelectorAll('a[href*="/job/"]').forEach(a => {
                const href = a.getAttribute('href') || '';
                if (!/\\/job\\/[A-Za-z0-9_-]+/.test(href)) return;
                let title = '';
                const h2 = a.querySelector('h2');
                if (h2) title = h2.textContent.trim();
                if (!title) title = (a.getAttribute('aria-label') || a.textContent || '').trim().split('\\n')[0].trim();
                let company = '';
                const card = a.closest('article, li, div[class*="card"]') || a.parentElement;
                if (card) {
                    const emp = card.querySelector('[class*="employer"], [class*="company"], [data-cy="company-name"]');
                    if (emp) company = emp.textContent.trim();
                }
                if (title && title.length > 3) {
                    out.push({title, company, href: href.startsWith('http') ? href : window.location.origin + href});
                }
            });
            const seen = new Set();
            return out.filter(x => seen.has(x.href) ? false : (seen.add(x.href), true));
        }
    """)
    return items or []


def scrape_efc(page, keywords: list[str] = None, max_pages: int = 5,
               location: str = "Hong Kong", jobtype_internship_only: bool = True) -> list:
    """
    Scrape eFinancialCareers HK.
    - keywords: list of search queries
    - max_pages: max pages to paginate per keyword (auto-stops on empty page)
    """
    all_jobs = []
    seen_urls: set = set()

    if not keywords:
        keywords = ["intern", "AI", "software engineer"]

    log.info(f"[eFC] Searching {len(keywords)} keywords...")

    for kw in keywords[:3]:  # limit to 3 keywords for eFC
        kw_jobs = []
        for page_no in range(max_pages):
            url = _build_url(kw, location, page_no)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                page.wait_for_timeout(2500)
            except Exception:
                break

            cards = _parse_cards(page)
            if not cards:
                break

            for c in cards:
                if c["href"] in seen_urls:
                    continue
                seen_urls.add(c["href"])
                kw_jobs.append(BaseScraper.make_job(
                    title=c["title"][:120],
                    company=c.get("company") or "(unknown)",
                    location=location,
                    url=c["href"],
                    source="eFinancialCareers",
                ))

            if len(cards) < 10:  # partial page → likely last
                break

        all_jobs.extend(kw_jobs)
        log.info(f"[eFC] Searching: {kw} → {len(kw_jobs)} jobs")

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
