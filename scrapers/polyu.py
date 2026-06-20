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
from pathlib import Path
from config import config
from config import check_stop

log = logging.getLogger("hunter")

LOGIN_URL = "https://jobboard-sao.polyu.edu.hk/login?callbackUrl=/"
# Try multiple possible job listing URLs (PolyU changes their URL structure sometimes)
JOBS_URLS = [
    "https://jobboard-sao.polyu.edu.hk/",
    "https://jobboard-sao.polyu.edu.hk/jobs",
    "https://jobboard-sao.polyu.edu.hk/job-posts",
    "https://jobboard-sao.polyu.edu.hk/search",
]


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
    # Try each possible URL until one works (not 404)
    for url in JOBS_URLS:
        try:
            log.info(f"[PolyU] Trying URL: {url}")
            page.goto(url, timeout=30000)
            page.wait_for_load_state("networkidle", timeout=30000)
            
            # Check for 404
            title = page.title().lower()
            if "404" in title or "not found" in title or "error" in title:
                log.warning(f"[PolyU]   {url} returned 404, trying next URL...")
                continue
            
            # Check if redirected to login
            if "/login" in page.url:
                log.info(f"[PolyU]   Redirected to login, will login first")
                if not _login(page):
                    log.warning(f"[PolyU]   Login failed, trying next URL...")
                    continue
                # After login, re-check for 404
                title = page.title().lower()
                if "404" in title or "not found" in title:
                    log.warning(f"[PolyU]   After login, {page.url} is 404, trying next URL...")
                    continue
            
            log.info(f"[PolyU]   Successfully loaded: {page.url}")
            return True
            
        except Exception as e:
            log.warning(f"[PolyU]   Error loading {url}: {e}")
            continue
    
    log.error("[PolyU] All URLs failed!")
    return False


def _search_keyword(page, kw: str) -> list:
    """
    Search a single keyword using the on-page search box.
    Returns list of {title, company, url}.
    """
    # Stay on current page (already navigated to a working URL by _ensure_on_jobs_page)
    try:
        # Just reload current page instead of navigating to hardcoded URL
        page.reload(wait_until="networkidle", timeout=30000)
    except Exception as e:
        log.warning(f"  [PolyU] Failed to reload page for '{kw}': {e}")
        # Try to navigate to a working URL
        for url in JOBS_URLS:
            try:
                page.goto(url, timeout=30000)
                page.wait_for_load_state("networkidle", timeout=30000)
                if "/login" not in page.url and "404" not in page.title():
                    break
            except Exception:
                continue
        else:
            log.warning(f"  [PolyU] Cannot load any working URL for '{kw}'")
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
        items = _scrape_all_and_filter(page, kw)
        # Debug: save HTML if 0 items found
        if not items:
            debug_dir = Path(__file__).parent.parent / "debug"
            debug_dir.mkdir(parents=True, exist_ok=True)
            html_path = debug_dir / f"polyu_debug_{kw.replace(' ', '_')}.html"
            try:
                html_content = page.content()
                html_path.write_text(html_content, encoding="utf-8")
                log.warning(f"  [PolyU] 0 items for '{kw}' — saved debug HTML to: {html_path}")
            except Exception as e:
                log.warning(f"  [PolyU] Failed to save debug HTML: {e}")
        return items

    # Scrape results after search
    return _scrape_current_page(page, kw)


def _extract_polyu_cards(page) -> list:
    """
    Extract job cards from PolyU job board page.
    
    PolyU job cards have this structure (from screenshot analysis):
    - Company logo (img tag)
    - Job title (heading text)
    - Company name
    - "Internship Position" / "Graduate Position" tag
    - Posted On / Closing On dates
    
    Strategy:
    1. Try specific PolyU card selectors first
    2. Fallback: find links that LOOK like jobs (exclude footer/nav links)
    """
    items = page.evaluate("""
        () => {
            const out = [];
            
            // ── Known non-job link texts to EXCLUDE (footer, nav, etc.) ──
            const excludeTexts = [
                'facebook', 'instagram', 'linkedin', 'twitter', 'youtube',
                'privacy policy', 'web accessibility', 'personal information',
                'return home', 'terms and conditions', 'user guide',
                'cookie', 'sitemap', 'copyright', 'all rights reserved',
                'the hong kong polytechnic university', 'polyu',
                'job seeker', 'employer',
            ];
            
            // Known URL patterns to EXCLUDE
            const excludeUrlPatterns = [
                '/facebook', '/instagram', '/linkedin', '/twitter',
                'privacy-policy', 'web-accessibility', 'terms',
                'cookie', 'sitemap',
            ];
            
            function shouldExclude(linkEl) {
                const text = (linkEl.textContent || '').trim().toLowerCase();
                const href = (linkEl.getAttribute('href') || '').toLowerCase();
                
                // Exclude if text matches known non-job content
                for (const ex of excludeTexts) {
                    if (text.includes(ex) && text.length < 100) return true;
                }
                
                // Exclude if href matches known non-job URLs
                for (const pat of excludeUrlPatterns) {
                    if (href.includes(pat)) return true;
                }
                
                // Exclude if it's just an icon/image-only link with no meaningful text
                if (text.length < 5) return true;
                
                // Exclude if it looks like a social media icon
                if (linkEl.querySelector('svg') && text.length < 10) return true;
                
                return false;
            }
            
            // ── Try specific PolyU job card selectors first ──
            // Based on screenshot analysis, cards are likely in a grid/list layout
            const cardContainerSelectors = [
                '[class*="job-post"]',        // job post container
                '[class*="jobCard"]',          // camelCase variant
                '[class*="job_card"]',         // snake_case variant
                '[class*="jobItem"]',          // item variant
                '[class*="job-item"]',         // kebab variant
                '[data-testid*="job"]',        // test ID based
                'article[class*="job"]',       // article element
            ];
            
            let candidateCards = [];
            for (const sel of cardContainerSelectors) {
                const found = document.querySelectorAll(sel);
                if (found.length > 0) {
                    candidateCards = Array.from(found);
                    break;
                }
            }
            
            // If found structured cards, extract from them
            if (candidateCards.length > 0) {
                for (const card of candidateCards) {
                    // Find the main link within the card
                    const link = card.querySelector('a[href*="/job-posts"], a[href*="/job/"], a[href*="detail"]');
                    
                    // Extract title: try heading tags first
                    let title = '';
                    const titleEl = card.querySelector('h2, h3, h4, [class*="title"], [class*="Title"]');
                    if (titleEl) title = titleEl.textContent.trim();
                    
                    // Extract company name
                    let company = '';
                    const compEl = card.querySelector('[class*="company"], [class*="employer"], [class*="Company"], [class*="Employer"]');
                    if (compEl) company = compEl.textContent.trim();
                    
                    // If no title from heading, use first meaningful text
                    if (!title || title.length < 3) {
                        const allText = card.innerText.split('\\n').map(t => t.trim()).filter(t => t.length > 3);
                        title = allText[0] || '';
                    }
                    
                    // Get URL
                    let url = '';
                    if (link) {
                        url = link.getAttribute('href') || '';
                    } else {
                        // Maybe the whole card is clickable
                        const anyLink = card.querySelector('a[href]');
                        if (anyLink) url = anyLink.getAttribute('href') || '';
                    }
                    
                    if (!url || url === '#') continue;
                    if (!url.startsWith('http')) url = window.location.origin + url;
                    if (!title || title.length < 3) continue;
                    
                    out.push({title, company: company || 'PolyU Employer', url});
                }
                
                // If we got results from structured cards, return them
                if (out.length > 0) return out;
            }
            
            // ── Fallback: extract from ALL links but filter aggressively ──
            const allLinks = document.querySelectorAll('a');
            for (const a of allLinks) {
                if (shouldExclude(a)) continue;
                
                const href = a.getAttribute('href') || '';
                if (!href || href === '#' || href.startsWith('javascript:')) continue;
                
                const fullUrl = href.startsWith('http') ? href : window.location.origin + href;
                
                // Extract title
                let title = '';
                const h3 = a.querySelector('h3');
                const h2 = a.querySelector('h2');
                const h4 = a.querySelector('h4');
                const h5 = a.querySelector('h5');
                const h6 = a.querySelector('h6');
                if (h3) title = h3.textContent.trim();
                else if (h2) title = h2.textContent.trim();
                else if (h4) title = h4.textContent.trim();
                else if (h5) title = h5.textContent.trim();
                else if (h6) title = h6.textContent.trim();
                else title = a.textContent.trim().split('\\n')[0].trim();
                
                if (!title || title.length < 3) continue;
                
                // Extract company from parent container
                let company = '';
                const card = a.closest('article, li, div[class*="card"], div[class*="item"], [class*="post"]') || a.parentElement;
                if (card) {
                    const comp = card.querySelector('[class*="company"], [class*="employer"], [class*="Company"], [class*="Employer"]');
                    if (comp) company = comp.textContent.trim();
                }
                
                out.push({title, company: company || '', url: fullUrl});
            }
            
            // Dedup by url
            const seen = new Set();
            return out.filter(x => seen.has(x.url) ? false : (seen.add(x.url), true));
        }
    """)
    return items or []


def _scrape_current_page(page, kw: str) -> list:
    """Scrape job cards from current page. Returns list of {title, company, url}."""
    items = _extract_polyu_cards(page)

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
    Scrape ALL jobs from the board (paginate via <select> dropdown), then filter by keyword locally.
    Used as fallback when no search box is found.
    """
    all_items = []
    seen_urls = set()
    current_page = 1
    max_pages = 20

    # Detect total pages from <select>
    total_pages = max_pages
    try:
        page_select = page.query_selector('select')
        if page_select:
            options = page.evaluate("""(sel) => {
                const opts = sel.querySelectorAll('option');
                return Array.from(opts).map(o => parseInt(o.value) || parseInt(o.textContent)).filter(v => v > 0);
            }""", page_select)
            if options:
                total_pages = max(options)
    except Exception:
        pass

    while current_page <= min(total_pages, max_pages):
        items = _extract_polyu_cards(page)

        for item in items:
            url = item.get("url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                all_items.append(item)

        if not items or current_page >= min(total_pages, max_pages):
            break

        # Try select dropdown first
        navigated = False
        try:
            page_select = page.query_selector('select')
            if page_select:
                page_select.select_option(str(current_page + 1))
                page.wait_for_load_state("networkidle", timeout=10000)
                navigated = True
        except Exception:
            pass

        # Fallback: click > / Next button
        if not navigated:
            try:
                next_btn = page.query_selector('a[rel="next"], a:has-text(">"):not(:has-text(">>"))')
                if next_btn and next_btn.is_visible():
                    next_btn.click()
                    page.wait_for_load_state("networkidle", timeout=10000)
                    navigated = True
            except Exception:
                pass

        if not navigated:
            break

        current_page += 1

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
    Scrape PolyU job board.
    Strategy: Visit /jobs, click "View All" if present, infinite scroll to load all jobs,
    then filter locally by keywords.
    - keywords: list of search keywords (matched against title, company, description)
    - max_pages: NOT USED (we scrape all jobs via infinite scroll)
    Returns list of Job objects.
    """
    from models import Job

    if not keywords:
        keywords = ["intern"]  # default fallback

    # Navigate to jobs page (try multiple URLs, handle login)
    log.info("[PolyU] Loading jobs page...")
    if not _ensure_on_jobs_page(page):
        log.error("[PolyU] Failed to load any jobs page!")
        return []

    # ── Click "View All" to go to the full paginated list page ──
    view_all_clicked = False
    try:
        view_all_selectors = [
            'a:has-text("View All")',
            'a:has-text("VIEW ALL")',
            'a:has-text("View all")',
            'button:has-text("View All")',
            '[class*="view-all"] a',
            '[class*="viewAll"] a',
            'a[href*="view-all"]',
            'a[href*="viewAll"]',
            'a[href*="all-jobs"]',
        ]
        for sel in view_all_selectors:
            try:
                btn = page.query_selector(sel)
                if btn and btn.is_visible():
                    log.info(f'[PolyU] Clicking "View All" (matched: {sel})...')
                    btn.click()
                    page.wait_for_load_state("networkidle", timeout=30000)
                    page.wait_for_timeout(3000)
                    log.info(f"[PolyU]   Now on: {page.url}")
                    view_all_clicked = True
                    break
            except Exception:
                continue
        
        if not view_all_clicked:
            log.warning("[PolyU] No 'View All' button found! Scraping current page (limited results)")
            # Save debug HTML
            try:
                debug_dir = Path(__file__).parent.parent / "debug"
                debug_dir.mkdir(parents=True, exist_ok=True)
                html_path = debug_dir / "polyu_homepage_no_view_all.html"
                html_path.write_text(page.content(), encoding="utf-8")
                log.warning(f"[PolyU]   Saved homepage HTML to: {html_path}")
            except Exception:
                pass
    except Exception as e:
        log.warning(f"[PolyU] Error clicking View All: {e}")

    # Pagination: use <select> dropdown or > / >> buttons
    log.info("[PolyU] Loading jobs with pagination...")
    all_items = []
    seen_urls = set()
    current_page = 1
    max_pages = 50

    # First, detect total pages from the <select> dropdown
    total_pages = 0
    try:
        page_select = page.query_selector('select')
        if page_select:
            options = page.evaluate("""(sel) => {
                const opts = sel.querySelectorAll('option');
                return Array.from(opts).map(o => parseInt(o.value) || parseInt(o.textContent)).filter(v => v > 0);
            }""", page_select)
            if options:
                total_pages = max(options)
                log.info(f"[PolyU]   Detected {total_pages} pages from select dropdown")
    except Exception as e:
        log.debug(f"[PolyU]   Could not detect total pages: {e}")

    # If no select found, cap at max_pages
    if total_pages == 0:
        total_pages = max_pages

    while current_page <= min(total_pages, max_pages):
        # Extract current page items
        items = _extract_polyu_cards(page)

        # Add new items
        for item in items:
            url = item.get("url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                all_items.append(item)

        log.info(f"[PolyU]   Page {current_page}: {len(all_items)} total jobs")

        if current_page >= min(total_pages, max_pages):
            break

        # Try strategy 1: use <select> dropdown to jump to next page
        navigated = False
        try:
            page_select = page.query_selector('select')
            if page_select:
                next_page = current_page + 1
                page_select.select_option(str(next_page))
                page.wait_for_load_state("networkidle", timeout=10000)
                page.wait_for_timeout(2000)
                navigated = True
                log.debug(f"[PolyU]   Selected page {next_page} via dropdown")
        except Exception as e:
            log.debug(f"[PolyU]   Select dropdown failed: {e}")

        # Try strategy 2: click > or Next button
        if not navigated:
            try:
                next_btn = page.query_selector(
                    'a[rel="next"], button:has-text(">"):not(:has-text(">>")), '
                    'a:has-text("Next"), [aria-label*="next"]'
                )
                if next_btn and next_btn.is_visible():
                    is_disabled = next_btn.get_attribute("disabled") or next_btn.get_attribute("aria-disabled")
                    if not is_disabled:
                        next_btn.click()
                        page.wait_for_load_state("networkidle", timeout=10000)
                        page.wait_for_timeout(2000)
                        navigated = True
            except Exception as e:
                log.debug(f"[PolyU]   Next button failed: {e}")

        if not navigated:
            log.info("[PolyU] No more pages")
            break

        current_page += 1
    
    log.info(f"[PolyU] Loaded {len(all_items)} total jobs from {current_page} pages")
    
    # Filter locally by keywords
    # Filter locally by keywords
    if keywords and keywords != ["intern"]:
        filtered = []
        for kw in keywords:
            kw_words = [w.lower() for w in kw.split() if len(w) > 2]
            for item in all_items:
                title = (item.get("title") or "").lower()
                company = (item.get("company") or "").lower()
                if any(w in title or w in company for w in kw_words):
                    if item not in filtered:
                        filtered.append(item)
        log.info(f"[PolyU] After keyword filter: {len(filtered)} jobs")
        # If all filtered out, ignore filter and return all items
        if filtered:
            all_items = filtered
        else:
            log.warning("[PolyU] All jobs filtered out! Ignoring keyword filter, returning all jobs")
    else:
        log.info(f"[PolyU] No keyword filter applied, returning all {len(all_items)} jobs")

    # Convert to Job objects
    all_jobs = []
    for item in all_items:
        all_jobs.append(Job(
            title=item.get("title", ""),
            company=item.get("company", "PolyU Job Board"),
            location="Hong Kong",
            url=item.get("url", ""),
            source="PolyU",
        ))

    log.info(f"[PolyU] Total: {len(all_jobs)} jobs")
    return all_jobs
