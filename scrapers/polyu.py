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
from config import check_stop

log = logging.getLogger("hunter")

LOGIN_URL = "https://jobboard-sao.polyu.edu.hk/login?callbackUrl=/"
JOBS_URL = "https://jobboard-sao.polyu.edu.hk/"


def _login(page) -> bool:
    """Login is now handled manually via cookie file. Returns False to signal manual login needed."""
    log.warning("[PolyU] Login required — cookies expired or not logged in.")
    log.warning("[PolyU] Please run `python polyu_login.py` to manually login and save new cookies.")
    return False


def _ensure_on_jobs_page(page) -> bool:
    """Navigate to jobs page. Returns True on success."""
    try:
        log.info(f"[PolyU] Loading: {JOBS_URL}")
        page.goto(JOBS_URL, timeout=30000)
        page.wait_for_load_state("networkidle", timeout=30000)
    except Exception as e:
        log.error(f"[PolyU] Failed to load {JOBS_URL}: {e}")
        return False

    # Check if redirected to login
    if "/login" in page.url:
        log.warning("[PolyU] Redirected to login — cookies may have expired.")
        log.warning("[PolyU] Please run `python polyu_login.py` to update cookies.")
        return False

    log.info(f"[PolyU]   Successfully loaded: {page.url}")
    return True


def _search_keyword(page, kw: str) -> list:
    """
    Search a single keyword using the on-page search box.
    Returns list of {title, company, url}.
    """
    # Stay on current page (already navigated to a working URL by _ensure_on_jobs_page)
    try:
        # Just reload current page instead of navigating to hardcoded URL
        page.reload(wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        log.warning(f"  [PolyU] Failed to reload page for '{kw}': {e}")
        # Try to navigate back to jobs page
        try:
            page.goto(JOBS_URL, timeout=30000)
            page.wait_for_load_state("networkidle", timeout=30000)
            if "/login" in page.url:
                log.warning(f"  [PolyU] Redirected to login for '{kw}' — cookies expired")
                return []
        except Exception as e:
            log.warning(f"  [PolyU] Cannot load {JOBS_URL} for '{kw}': {e}")
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
                page.wait_for_timeout(200)
                box.fill(kw)
                page.wait_for_timeout(200)
                box.press("Enter")
                page.wait_for_timeout(500)
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
                page.wait_for_timeout(300)
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
                page.wait_for_load_state("domcontentloaded", timeout=10000)
                navigated = True
        except Exception:
            pass

        # Fallback: click > / Next button
        if not navigated:
            try:
                next_btn = page.query_selector('a[rel="next"], a:has-text(">"):not(:has-text(">>"))')
                if next_btn and next_btn.is_visible():
                    next_btn.click()
                    page.wait_for_load_state("domcontentloaded", timeout=10000)
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


def _radix_goto_next_page(page) -> bool:
    """Use Radix UI combobox to navigate to the next page. Returns True if navigated."""
    try:
        combobox = page.query_selector('[role="combobox"]')
        if not combobox:
            return False
        # Read current page number from combobox
        current_text = (combobox.inner_text() or "").strip()
        current_page_num = int(current_text) if current_text.isdigit() else 1

        combobox.click()
        page.wait_for_timeout(300)
        page.wait_for_selector('[role="option"]', timeout=5000)
        options = page.query_selector_all('[role="option"]')
        next_page = current_page_num + 1
        for opt in options:
            opt_text = (opt.inner_text() or "").strip()
            if opt_text == str(next_page):
                opt.click()
                page.wait_for_timeout(500)
                page.wait_for_load_state("networkidle", timeout=10000)
                return True
        # No next page found — close dropdown
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        return False
    except Exception as e:
        log.debug(f"[PolyU]   Radix goto next page failed: {e}")
        return False


def _extract_all_pages(page, kw: str) -> list:
    """Extract job cards from current page and paginate through all result pages."""
    all_items = []
    seen_urls = set()
    current_page = 1
    max_pages = 50

    while current_page <= max_pages:
        items = _extract_polyu_cards(page)
        new_count = 0
        for item in items:
            url = item.get("url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                all_items.append(item)
                new_count += 1

        log.info(f"[PolyU]   Page {current_page}: {len(items)} cards → +{new_count} new ({len(all_items)} total)")

        # Stop if this page yielded 0 new items (no more pages)
        if new_count == 0:
            log.info(f"[PolyU]   No new cards on page {current_page}, stopping.")
            break

        # Try to go to next page
        if not _radix_goto_next_page(page):
            break
        current_page += 1

    return all_items


def _find_and_use_search_box(page, kw: str) -> bool:
    """Find the search input on the job-posts page, type keyword, press Enter."""
    search_selectors = [
        'input[placeholder*="Search" i]',
        'input[placeholder*="search" i]',
        'input[type="search"]',
        'input[name*="search" i]',
        'input[name*="keyword" i]',
        'input[class*="search" i]',
        'input[type="text"]',
    ]
    for sel in search_selectors:
        try:
            box = page.query_selector(sel)
            if box and box.is_visible():
                box.fill("")
                page.wait_for_timeout(200)
                box.fill(kw)
                page.wait_for_timeout(200)
                box.press("Enter")
                page.wait_for_timeout(500)
                page.wait_for_load_state("networkidle", timeout=15000)
                return True
        except Exception:
            continue
    return False


def scrape_polyu(page, keywords: list[str] = None, max_pages: int = 3) -> list:
    """
    Scrape PolyU job board.
    Strategy: Click "View All" → for each keyword, search → paginate results.
    Uses Radix UI combobox for pagination.
    """
    from models import Job

    if not keywords:
        keywords = ["intern"]

    # Navigate to jobs page
    log.info("[PolyU] Loading jobs page...")
    if not _ensure_on_jobs_page(page):
        log.error("[PolyU] Failed to load jobs page!")
        return []

    # ── Click "View All" to go to the full paginated list page ──
    try:
        for sel in ['a:has-text("View All")', 'a:has-text("VIEW ALL")', 'a:has-text("View all")',
                     'button:has-text("View All")']:
            try:
                btn = page.query_selector(sel)
                if btn and btn.is_visible():
                    log.info(f'[PolyU] Clicking "View All" (matched: {sel})...')
                    btn.click()
                    page.wait_for_load_state("networkidle", timeout=30000)
                    page.wait_for_timeout(500)
                    log.info(f"[PolyU]   Now on: {page.url}")
                    break
            except Exception:
                continue
    except Exception as e:
        log.warning(f"[PolyU] Error clicking View All: {e}")

    # ── For each keyword: search → paginate → collect ──
    all_items = []
    seen_urls = set()

    for kw in keywords:
        from config import check_stop
        try:
            check_stop()
        except InterruptedError:
            log.info("[PolyU] Stop requested, exiting...")
            return []

        log.info(f"[PolyU] Searching: {kw}")
        searched = _find_and_use_search_box(page, kw)

        if searched:
            kw_items = _extract_all_pages(page, kw)
        else:
            log.info(f"[PolyU]   No search box found, scraping all pages (local filter)")
            kw_items = _extract_all_pages(page, kw)
            # Local filter
            kw_words = [w.lower() for w in kw.split() if len(w) > 2]
            if kw_words:
                kw_items = [item for item in kw_items
                            if any(w in (item.get("title") or "").lower() or
                                   w in (item.get("company") or "").lower()
                                   for w in kw_words)]

        for item in kw_items:
            url = item.get("url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                all_items.append(item)

        log.info(f"[PolyU] {kw}: {len(kw_items)} jobs")

    # Convert to Job objects
    all_jobs = [Job(
        title=item.get("title", ""),
        company=item.get("company", "PolyU Job Board"),
        location="Hong Kong",
        url=item.get("url", ""),
        source="PolyU",
    ) for item in all_items]

    log.info(f"[PolyU] Total: {len(all_jobs)} jobs")
    return all_jobs
