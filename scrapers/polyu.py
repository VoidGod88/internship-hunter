"""
scrapers/polyu.py — PolyU SAO Job Board scraper.

Login: https://jobboard-sao.polyu.edu.hk/login?callbackUrl=/
After login, scrape internship listings from the job board.

Requires config.polyu_net_id and config.polyu_password set in .env:
    POLYU_NET_ID=your_net_id
    POLYU_PASSWORD=your_password
"""
import logging
import time
from config import config

log = logging.getLogger("hunter")

LOGIN_URL = "https://jobboard-sao.polyu.edu.hk/login?callbackUrl=/"
JOBS_URL = "https://jobboard-sao.polyu.edu.hk/jobs"


def _accept_terms(page) -> bool:
    """Accept the Terms & Conditions modal (2 checkboxes + Continue button)."""
    try:
        # Check if T&C modal exists
        tc_modal = page.query_selector('text=Terms and Conditions')
        if not tc_modal:
            # Try alternative selectors
            tc_modal = page.query_selector('[class*="modal"], [class*="dialog"], [role="dialog"]')

        if not tc_modal:
            log.info("[PolyU] No T&C modal found, proceeding...")
            return True

        log.info("[PolyU] Found T&C modal, accepting terms...")

        # Wait a moment for modal to fully render
        page.wait_for_timeout(1000)

        # Click both checkboxes
        checkbox_selectors = [
            'input[type="checkbox"]',
            '[role="checkbox"]',
            'label:has-text("I have read and agree")',
            'label:has-text("I declare that")',
            '[class*="checkbox"]',
        ]

        clicked = 0
        for sel in checkbox_selectors:
            try:
                checkboxes = page.query_selector_all(sel)
                for cb in checkboxes:
                    # Check if it's already checked
                    is_checked = cb.evaluate("el => el.checked || el.getAttribute('aria-checked') === 'true'")
                    if not is_checked:
                        cb.click()
                        page.wait_for_timeout(300)
                        clicked += 1
                    else:
                        clicked += 1
            except Exception:
                continue

        log.info("[PolyU] Clicked %d checkbox elements", clicked)

        if clicked < 2:
            log.warning("[PolyU] Expected 2 checkboxes but only found/clicked %d", clicked)
            # Try clicking by text/position as fallback
            try:
                page.click('label:has-text("agree")', timeout=3000)
                page.wait_for_timeout(500)
                page.click('label:has-text("declare")', timeout=3000)
                page.wait_for_timeout(500)
                log.info("[PolyU] Used fallback label click")
            except Exception as e:
                log.warning("[PolyU] Fallback click failed: %s", e)

        # Click "Continue to access PolyU Job Board" button
        continue_selectors = [
            'button:has-text("Continue")',
            'button:has-text("Continue to access")',
            'a:has-text("Continue to access")',
            'button[type="submit"]:has-text("Continue")',
            '[class*="btn"]:has-text("Continue")',
        ]

        for sel in continue_selectors:
            try:
                btn = page.query_selector(sel)
                if btn:
                    btn.click()
                    log.info("[PolyU] Clicked Continue button")
                    page.wait_for_load_state("networkidle", timeout=15000)
                    return True
            except Exception:
                continue

        log.error("[PolyU] Could not find Continue button")
        return False

    except Exception as e:
        log.error("[PolyU] T&C acceptance error: %s", e)
        return False


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

        # Fill NET ID
        page.fill('input[name="username"], input[name="netId"], input[id*="net"], input[type="text"]', net_id)
        # Fill password
        page.fill('input[name="password"], input[name="pwd"], input[type="password"]', password)
        # Submit
        page.click('button[type="submit"], input[type="submit"]')
        page.wait_for_load_state("networkidle", timeout=30000)

        # Check if login succeeded (URL no longer contains /login)
        current_url = page.url
        if "/login" in current_url:
            log.error("[PolyU] Login failed — still on login page")
            return False

        log.info("[PolyU] Login successful")

        # Accept Terms & Conditions modal
        if not _accept_terms(page):
            log.warning("[PolyU] Failed to accept T&C, but continuing...")

        return True
    except Exception as e:
        log.error(f"[PolyU] Login error: {e}")
        return False


def _parse_job_cards(page) -> list:
    """Extract job listings from current page."""
    items = page.evaluate("""
        () => {
            const out = [];
            // Try multiple selectors for job cards
            const selectors = [
                'a[href*="/job/"]',
                'a[href*="/jobs/"]',
                '[class*="job-card"] a',
                '[class*="jobCard"] a',
                'article a',
                '.job-item a',
            ];
            let links = [];
            for (const sel of selectors) {
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
                    return txt.length > 5 && txt.length < 200 &&
                           (txt.toLowerCase().includes('intern') ||
                            txt.toLowerCase().includes('job') ||
                            txt.toLowerCase().includes('position'));
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
            // Dedupe by url
            const seen = new Set();
            return out.filter(x => seen.has(x.url) ? false : (seen.add(x.url), true));
        }
    """)
    return items or []


def scrape_polyu(page, keywords: list[str] = None, max_pages: int = 3) -> list:
    """
    Scrape PolyU job board.
    - keywords: search keywords (currently not used — PolyU board shows all eligible jobs)
    - max_pages: max pages to paginate
    Returns list of dicts: {title, company, url, source: "PolyU"}
    """
    from models import Job

    # Login first
    if not _login(page):
        return []

    # Navigate to jobs listing
    log.info("[PolyU] Navigating to job listings...")
    try:
        page.goto(JOBS_URL, timeout=30000)
        page.wait_for_load_state("networkidle", timeout=30000)

        # T&C modal might appear again on first visit
        _accept_terms(page)
    except Exception as e:
        log.warning(f"[PolyU] Failed to load jobs page: {e}")
        # Try current page (might already be on jobs page after login)
        pass

    all_items = []
    for page_no in range(max_pages):
        log.info(f"[PolyU] Scraping page {page_no + 1}...")
        items = _parse_job_cards(page)
        if not items:
            log.info(f"[PolyU] No more jobs found on page {page_no + 1}")
            break
        all_items += items
        log.info(f"[PolyU] Page {page_no + 1}: {len(items)} jobs")

        # Try to click "Next" button
        try:
            next_btn = page.query_selector('a[rel="next"], button:has-text("Next"), a:has-text("Next"), [aria-label="Next"]')
            if next_btn:
                next_btn.click()
                page.wait_for_load_state("networkidle", timeout=10000)
            else:
                break
        except Exception:
            break

    # Convert to Job objects
    jobs = []
    for item in all_items:
        jobs.append(Job(
            title=item.get("title", ""),
            company=item.get("company", "PolyU Job Board"),
            url=item.get("url", ""),
            source="PolyU",
        ))
        log.info(f"[PolyU] {item.get('title', '')}")

    log.info(f"[PolyU] Total: {len(jobs)} jobs")
    return jobs
