"""
scrapers/polyu.py — PolyU SAO Job Board scraper.

Login: https://jobboard-sao.polyu.edu.hk/login?callbackUrl=/
After login, search keywords via the on-page search box, scrape results.

Cookie-based auth: if cookies/polyu.json exists, reuse saved session.
Run `python polyu_login.py` once to manually log in and save cookies.
"""
import logging
import time
import re
from config import config

log = logging.getLogger("hunter")

LOGIN_URL = "https://jobboard-sao.polyu.edu.hk/login?callbackUrl=/"
JOBS_URL = "https://jobboard-sao.polyu.edu.hk/jobs"


def _login(page) -> bool:
    """Login to PolyU job board. Returns True on success."""
    net_id = config.polyu_net_id.strip()
    password = config.polyu_password.strip()
    if not net_id or not password:
        log.warning("[PolyU] No credentials in .env (POLYU_NET_ID / POLYU_PASSWORD)")
        return False

    log.info("[PolyU] Logging in...")
    try:
        page.goto(LOGIN_URL, timeout=30000)
        page.wait_for_load_state("networkidle", timeout=30000)

        page.fill('input[name="username"], input[name="netId"], input[id*="net"], input[type="text"]', net_id)
        page.fill('input[name="password"], input[name="pwd"], input[type="password"] ', password)
        page.click('button[type="submit"], input[type="submit"] ')
        page.wait_for_load_state("networkidle", timeout=30000)

        if "/login" in page.url:
            log.error("[PolyU] Login failed — still on login page")
            return False

        log.info("[PolyU] Login successful")
        return True
    except Exception as e:
        log.error(f"[PolyU] Login error: {e}")
        return False


def _ensure_on_jobs_page(page) -> bool:
    """Navigate to jobs page, handling login if needed. Returns True on success."""
    need_login = False
    try:
        page.goto(JOBS_URL, timeout=30000)
        page.wait_for_load_state("networkidle", timeout=30000)
        if "/login" in page.url:
            need_login = True
    except Exception:
        need_login = True

    if need_login:
        if not _login(page):
            return False
        try:
            page.goto(JOBS_URL, timeout=30000)
            page.wait_for_load_state("networkidle", timeout=30000)
        except Exception as e:
            log.error(f"[PolyU] Failed to load jobs page after login: {e}")
            return False
    return True


def _search_keyword(page, kw: str) -> list:
    """
    Search a single keyword using the on-page search box.
    Returns list of {title, company, url}.
    """
    # Navigate to jobs page (ensure we're there)
    try:
        page.goto(JOBS_URL, timeout=30000)
        page.wait_for_load_state("networkidle", timeout=30000)
    except Exception as e:
        log.warning(f"  [PolyU] Failed to load jobs page for '{kw}': {e}")
        return []

    # Try to find and use the search box
    search_selectors = [
        'input[type="search"] ',
        'input[placeholder*="Search"] ',
        'input[placeholder*="search"] ',
        'input[name*="search"] ',
        'input[name*="keyword"] ',
        'input[name*="q"] ',
        'input[class*="search"] ',
    ]

    searched = False
    for sel in search_selectors:
        try:
            box = page.query_selector(sel)
            if box and box.is_visible():
                box.fill(kw)
                page.wait_for_timeout(500)
                box.press("Enter")
                page.wait_for_load_state("networkidle", timeout=15000)
                searched = True
                log.debug(f"  [PolyU] Searched '{kw}' using selector: {sel}")
                break
        except Exception:
            continue

    if not searched:
        # No search box found — scrape all jobs and filter locally
        log.info(f"  [PolyU] No search box found, scraping all jobs for '{kw}' (local filter)")
        return _scrape_all_and_filter(page, kw)

    # Scrape results after search
    return _scrape_current_page(page, kw)


def _scrape_current_page(page, kw: str) -> list:
    """Scrape job cards from current page. Returns list of {title, company, url}."""
    items = page.evaluate("""
        (kw) => {
            const out = [];
            const lowerKw = (kw || '').toLowerCase();
            // Try multiple selectors for job cards/links
            const cardSelectors = [
                'a[href*="/job-posts"]',
                'a[href*="/job/"]',
                'a[href*="/jobs/"]',
                '[class*="job-card"] a',
                '[class*="jobCard"] a',
                'article a',
                '.job-item a',
            ];
            let links = [];
            for (const sel of cardSelectors) {
                const found = document.querySelectorAll(sel);
                if (found.length > 0) {
                    links = Array.from(found);
                    break;
                }
            }
            // Fallback: all links with meaningful text
            if (links.length === 0) {
                links = Array.from(document.querySelectorAll('a')).filter(a => {
                    const txt = a.textContent.trim();
                    return txt.length > 5 && txt.length < 200;
                });
            }
            links.forEach(a => {
                const href = a.getAttribute('href') || '';
                if (!href || href === '#') return;
                const fullUrl = href.startsWith('http') ? href : window.location.origin + href;
                // Extract title
                let title = '';
                const h3 = a.querySelector('h3');
                const h2 = a.querySelector('h2');
                const h4 = a.querySelector('h4');
                if (h3) title = h3.textContent.trim();
                else if (h2) title = h2.textContent.trim();
                else if (h4) title = h4.textContent.trim();
                else title = a.textContent.trim().split('\\n')[0].trim();
                // Extract company
                let company = '';
                const card = a.closest('article, li, div[class*="card"], div[class*="item"]') || a.parentElement;
                if (card) {
                    const comp = card.querySelector('[class*="company"], [class*="employer"], [data-cy*="company"]');
                    if (comp) company = comp.textContent.trim();
                }
                if (title && title.length > 3) {
                    out.push({title, company, url: fullUrl});
                }
            });
            // Dedup by url
            const seen = new Set();
            return out.filter(x => seen.has(x.url) ? false : (seen.add(x.url), true));
        }
    """, kw)

    # Visit detail pages to get full description for keyword matching
    if items and kw:
        items = _enrich_with_details(page, items, kw)

    return items or []


def _enrich_with_details(page, items: list, kw: str) -> list:
    """
    Visit detail pages for items that don't obviously match the keyword.
    Keep items where title or detail page contains the keyword (fuzzy: any word matches).
    """
    kw_words = [w.lower() for w in kw.split() if len(w) > 2]
    if not kw_words:
        return items

    matched = []
    for item in items:
        title = (item.get("title") or "").lower()
        company = (item.get("company") or "").lower()
        url = item.get("url") or ""

        # Quick check: title or company already matches
        if any(w in title or w in company for w in kw_words):
            matched.append(item)
            continue

        # Visit detail page to check description
        if url and url.startswith("http"):
            try:
                page.goto(url, timeout=10000)
                page.wait_for_load_state("domcontentloaded", timeout=10000)
                page.wait_for_timeout(1000)
                desc = (page.evaluate("() => document.body.innerText") or "").lower()
                if any(w in desc for w in kw_words):
                    matched.append(item)
            except Exception:
                # If can't load detail page, keep the item (might be a false negative)
                matched.append(item)
        else:
            matched.append(item)

    log.debug(f"  [PolyU] Keyword '{kw}': {len(matched)}/{len(items)} matched after detail check")
    return matched


def _scrape_all_and_filter(page, kw: str) -> list:
    """
    Scrape ALL jobs from the board (paginate), then filter by keyword locally.
    Used as fallback when no search box is found.
    """
    all_items = []
    max_pages = 5

    for page_no in range(max_pages):
        items = page.evaluate("""
            () => {
                const out = [];
                const selectors = [
                    'a[href*="/job-posts"]', 'a[href*="/job/"]', 'a[href*="/jobs/"]',
                    '[class*="job-card"] a', '[class*="jobCard"] a', 'article a', '.job-item a',
                ];
                let links = [];
                for (const sel of selectors) {
                    const found = document.querySelectorAll(sel);
                    if (found.length > 0) { links = Array.from(found); break; }
                }
                if (links.length === 0) {
                    links = Array.from(document.querySelectorAll('a')).filter(a => {
                        const t = a.textContent.trim(); return t.length > 5 && t.length < 200;
                    });
                }
                links.forEach(a => {
                    const href = a.getAttribute('href') || '';
                    if (!href || href === '#') return;
                    const fullUrl = href.startsWith('http') ? href : window.location.origin + href;
                    let title = '';
                    const h3 = a.querySelector('h3'); const h2 = a.querySelector('h2'); const h4 = a.querySelector('h4');
                    if (h3) title = h3.textContent.trim();
                    else if (h2) title = h2.textContent.trim();
                    else if (h4) title = h4.textContent.trim();
                    else title = a.textContent.trim().split('\\n')[0].trim();
                    let company = '';
                    const card = a.closest('article, li, div[class*="card"], div[class*="item"]') || a.parentElement;
                    if (card) {
                        const comp = card.querySelector('[class*="company"], [class*="employer"]');
                        if (comp) company = comp.textContent.trim();
                    }
                    if (title && title.length > 3) out.push({title, company, url: fullUrl});
                });
                const seen = new Set();
                return out.filter(x => seen.has(x.url) ? false : (seen.add(x.url), true));
            }
        """)
        if not items:
            break
        all_items += items

        # Try next page
        try:
            next_btn = page.query_selector('a[rel="next"], button:has-text("Next"), a:has-text("Next"), [aria-label="Next"]')
            if next_btn:
                next_btn.click()
                page.wait_for_load_state("networkidle", timeout=10000)
            else:
                break
        except Exception:
            break

    # Filter locally by keyword
    if kw:
        kw_words = [w.lower() for w in kw.split() if len(w) > 2]
        filtered = []
        for item in all_items:
            title = (item.get("title") or "").lower()
            company = (item.get("company") or "").lower()
            if any(w in title or w in company for w in kw_words):
                filtered.append(item)
        log.debug(f"  [PolyU] Local filter '{kw}': {len(filtered)}/{len(all_items)} matched")
        return filtered

    return all_items


def scrape_polyu(page, keywords: list[str] = None, max_pages: int = 3) -> list:
    """
    Scrape PolyU job board, one keyword per search using the on-page search box.
    - keywords: list of search keywords
    - max_pages: max pages per keyword (not used currently, searches all results)
    Returns list of Job objects.
    """
    from models import Job

    if not keywords:
        keywords = ["intern"]  # default fallback

    # Ensure we're logged in before searching
    if not _ensure_on_jobs_page(page):
        return []

    all_jobs = []
    for kw in keywords:
        log.info(f"[PolyU] Searching: {kw}")
        items = _search_keyword(page, kw)
        # Convert to Job objects
        for item in items:
            all_jobs.append(Job(
                title=item.get("title", ""),
                company=item.get("company", "PolyU Job Board"),
                location="Hong Kong",
                url=item.get("url", ""),
                source="PolyU",
            ))
        log.info(f"[PolyU] Searching: {kw} → {len(items)} jobs")

    # Deduplicate by (title, company)
    seen = set()
    unique = []
    for j in all_jobs:
        key = (j.title.strip().lower(), j.company.strip().lower())
        if key not in seen and j.title.strip():
            seen.add(key)
            unique.append(j)

    log.info(f"[PolyU] Total: {len(unique)} jobs")
    return unique
