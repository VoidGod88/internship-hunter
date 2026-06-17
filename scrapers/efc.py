"""
scrapers/efc.py — eFinancialCareers HK scraper.

eFC HK URL filters (all optional, combined via &):
  keywords=<q>          — search query
  location=<loc>        — city
  sector=<id>           — sector ID (e.g. 1=IT, 2=Finance)
  jobtype=<id>          — 1=Permanent, 2=Contract, 3=Internship
  pagesize=<n>          — items per page (max 50)
  page=<n>              — pagination (0-indexed)

Example internship search in Hong Kong:
  https://www.efinancialcareers.hk/jobs/search?keywords=AI&location=Hong+Kong&jobtype=3&page=0
"""
import logging
import re
from .base import BaseScraper

log = logging.getLogger("hunter")

BASE = "https://www.efinancialcareers.hk"
JOBTYPE_INTERNSHIP = 3  # eFC: 1=Perm, 2=Contract, 3=Intern


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
    """Pull every job link + title from the eFC results page.

    eFC renders a list of <a href="/job/..."> blocks, each containing a <h2> title.
    Returns list of dicts: {title, company, url}
    """
    items = page.evaluate("""
        () => {
            const out = [];
            document.querySelectorAll('a[href*="/job/"]').forEach(a => {
                const href = a.getAttribute('href') || '';
                // Skip non-job anchors like /job-search or /jobs/...
                if (!/\\/job\\/[A-Za-z0-9_-]+/.test(href)) return;
                // Title from h2 inside the link, or aria-label, or visible text
                let title = '';
                const h2 = a.querySelector('h2');
                if (h2) title = h2.textContent.trim();
                if (!title) title = (a.getAttribute('aria-label') || a.textContent || '').trim().split('\\n')[0].trim();
                // Company from sibling / parent
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
            // Dedupe by href
            const seen = new Set();
            return out.filter(x => seen.has(x.href) ? false : (seen.add(x.href), true));
        }
    """)
    return items or []


def scrape_efc(page, keywords: list[str] = None, max_pages: int = 5,
               location: str = "Hong Kong", jobtype_internship_only: bool = True) -> list:
    """
    Scrape eFinancialCareers HK.
    - keywords: list of search queries (default: top CV-derived keywords)
    - max_pages: max pages to paginate per keyword (auto-stops on empty page)
    - jobtype_internship_only: hard-filter to internship postings via URL
    """
    jobs: list = []
    seen_urls: set = set()

    if not keywords:
        keywords = ["intern", "AI", "software engineer"]

    jobtype = JOBTYPE_INTERNSHIP if jobtype_internship_only else None
    log.info(f"[eFC] Starting — {len(keywords)} keyword(s), max {max_pages} pages each")

    for kw in keywords[:3]:
        for page_no in range(max_pages):
            url = _build_url(kw, location, page_no)
            if jobtype_internship_only:
                # already in _build_url
                pass
            log.info(f"  eFC page {page_no+1}: {kw}")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                page.wait_for_timeout(2500)
            except Exception as e:
                log.warning(f"  eFC [{kw}] page {page_no} load failed: {e}")
                break

            cards = _parse_cards(page)
            if not cards:
                log.info(f"  eFC [{kw}] page {page_no+1} empty — stop")
                break

            added = 0
            for c in cards:
                if c["href"] in seen_urls:
                    continue
                seen_urls.add(c["href"])
                jobs.append(BaseScraper.make_job(
                    title=c["title"][:120],
                    company=c.get("company") or "(unknown)",
                    location=location,
                    url=c["href"],
                    source="eFinancialCareers",
                ))
                added += 1
            log.info(f"  eFC [{kw}] p{page_no+1} added {added} (total {len(jobs)})")

            if len(cards) < 10:  # partial page → likely last page
                break

    log.info(f"[eFinancialCareers] Total: {len(jobs)} unique jobs")
    return jobs
