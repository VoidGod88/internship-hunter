"""
scrapers/indeed.py — Indeed HK scraper.
Per-keyword fresh context (matches debug_indeed.py behaviour) to
avoid Indeed's anti-bot detection that flags a shared browser session.
One keyword per search, paginate via Indeed's Next link + start=N fallback.
URL built dynamically from config filters (Settings UI).
"""
import logging
import random
import time
from pathlib import Path

from .base import BaseScraper
from config import config

log = logging.getLogger("hunter")
_COOKIE_PATH = Path(__file__).parent.parent / "cookies" / "indeed.json"

# ── Anti-detection init script (runs on every page navigation) ──
_ANTI_DETECT_JS = """
    () => {
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
        Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
    }
"""

# ── Extraction JS (same logic as debug_indeed.py) ──
_EXTRACT_CARDS = r"""() => {
    const results = [];
    const cards = document.querySelectorAll('[data-jk]');
    for (const card of cards) {
        const jk = card.getAttribute('data-jk');
        if (!jk) continue;

        // ── Title ──
        const spanTitle = card.querySelector('span[title]');
        let title = '';
        if (spanTitle) {
            title = (spanTitle.getAttribute('title') || spanTitle.textContent || '').trim();
            // Strip "Easily apply" suffix
            title = title.replace(/\s*Easily\s*apply\s*$/i, '').trim();
        }
        if (!title) {
            const label = card.getAttribute('aria-label') || '';
            const m = label.match(/details\s+(?:of\s+)?(.+)/i);
            if (m) title = m[1].trim();
        }
        if (!title || title.length < 3) continue;
        const tl = title.toLowerCase();
        if (['sign in','register','create account','upload cv','home',
             'jobs','company reviews','salary guide'].includes(tl)) continue;

        // ── Company ──
        let company = '(unknown)';
        const td = card.closest('td');
        if (td) {
            // Strategy 1: company-specific elements
            const companyEl = td.querySelector(
                '[data-testid="company-name"], [class*="companyName"], [class*="company_name"], '
                + '[class*="CompanyName"], span[class*="css-"][class*="company"]'
            );
            if (companyEl) {
                company = companyEl.textContent.trim();
            }

            // Strategy 2: extract from metadata div (css-u74ql7)
            if (company === '(unknown)') {
                const metaDiv = td.querySelector('div[class*="css-u74ql7"], div[class*="company"]');
                if (metaDiv) {
                    const raw = (metaDiv.textContent || '').trim();
                    const parts = raw.split(/\b(Hong\s*Kong|Kowloon|Kwun\s*Tong|New\s*Territories|Central|Wan\s*Chai|Causeway\s*Bay|Tsim\s*Sha\s*Tsui|Taikoo|TST|Mong\s*Kok|Sha\s*Tin|Tsuen\s*Wan|Yuen\s*Long|Kwai\s*Chung|Ap\s*Lei\s*Chau|Full.?time|Part.?time|Permanent|Contract|Remote|Shift|Hybrid|Temporary)\b/i);
                    if (parts.length > 1) {
                        company = parts[0].trim();
                    } else {
                        company = raw.length <= 60 ? raw : raw.substring(0, 60).trim();
                    }
                }
            }

            // Strategy 3: fallback — first sibling div text
            if (company === '(unknown)') {
                const divs = td.querySelectorAll(':scope > div');
                for (const div of divs) {
                    if (div.contains(card)) continue;
                    const text = (div.textContent || '').trim();
                    if (!text || text.length < 2 || text.length > 60) continue;
                    company = text;
                    break;
                }
            }
        }
        const url = 'https://hk.indeed.com/viewjob?jk=' + jk;
        results.push({jk, title, company, url});
    }
    return results;
}"""


def _dismiss_job_alert_popup(page) -> bool:
    """Detect and dismiss Indeed Job Alert popup.
    Tries: close button → Activate button → Maybe later → skip.
    Returns True if a popup was found and handled.
    """
    try:
        result = page.evaluate(r"""() => {
            const allDivs = document.querySelectorAll(
                '[role="dialog"], [role="alertdialog"], '
                + 'div[class*="modal"], div[class*="popup"], div[class*="overlay"], div[class*="popover"]'
            );
            let popup = null;
            for (const div of allDivs) {
                const text = (div.textContent || '').toLowerCase();
                if (text.includes('job alert') || text.includes('get new jobs')) {
                    popup = div;
                    break;
                }
            }
            if (!popup) return {found: false};

            // 1) Close button
            const closeSelectors = [
                '[aria-label*="close" i]', '[aria-label*="Close"]',
                '[data-testid="close-button"]', '[data-testid="modal-close"]',
                'button[class*="close"]', '[class*="closeButton"]',
                'svg[aria-label*="close" i]',
            ];
            for (const sel of closeSelectors) {
                const el = popup.querySelector(sel);
                if (el) { el.click(); return {found: true, action: 'clicked_close'}; }
            }

            // 2) Activate button
            const buttons = popup.querySelectorAll('button, a[role="button"], [data-testid*="activate"]');
            for (const btn of buttons) {
                const t = (btn.textContent || '').trim().toLowerCase();
                if (t === 'activate' || t === 'submit' || t.includes('activate')) {
                    btn.click();
                    return {found: true, action: 'clicked_activate'};
                }
            }

            // 3) Maybe later / skip link
            const links = popup.querySelectorAll('a, button');
            for (const el of links) {
                const t = (el.textContent || '').trim().toLowerCase();
                if (t.includes('maybe later') || t.includes('not now') || t.includes('skip')) {
                    el.click();
                    return {found: true, action: 'clicked_skip'};
                }
            }

            return {found: true, action: 'no_button_found'};
        }""")
        if result.get("found"):
            log.info(f"[Indeed]   Dismissed popup: {result['action']}")
            page.wait_for_timeout(1500)
            return True
        return False
    except Exception:
        return False


def _check_page_ok(page) -> bool:
    """Check if page is a valid search results page (not challenge/blocked)."""
    try:
        title = page.title()
        url_lower = (page.url or "").lower()
        if "just a moment" in title.lower():
            return False
        if "attention required" in title.lower():
            return False
        if "captcha" in title.lower():
            return False
        if "request blocked" in title.lower():
            return False
        # Also check body for cloudflare block that doesn't change title
        if "request blocked" in page.content()[:5000].lower():
            return False
        if "/jobs" not in url_lower:
            return False
        return True
    except Exception:
        return True  # give benefit of doubt


def _check_login(page) -> bool:
    """Check if redirected to login page."""
    try:
        return any(s in page.url.lower() for s in ["/signin", "/login", "/account/login"])
    except Exception:
        return False


def _build_url(kw: str) -> str:
    """Build Indeed HK search URL from config filters.

    Indeed HK uses encrypted sc= parameters for ALL filters (education + job types).
    Standard jt= parameter does NOT work on Indeed HK.
    """
    params = [f"q={kw.replace(' ', '+')}"]
    if config.id_date_range:
        params.append(f"fromage={config.id_date_range}")
    if config.id_sort_by:
        params.append(f"sort={config.id_sort_by}")
    if config.id_radius:
        params.append(f"radius={config.id_radius}")
    params.append("l=Hong+Kong")

    # Build sc= param from encrypted filters (education + job types)
    edu_valid = {"HFDVW", "EXSNN", "6QC5F", "MR89S"}
    jt_sc_valid = {"VDTG7", "75GKK", "T9BXE", "CF3CP", "5QWDV",
                   "T65DZ", "7EQCZ", "2X29N", "ZG59D"}
    edu_codes = [c for c in config.id_education if c in edu_valid]
    jt_sc_codes = [c for c in config.id_job_types_sc if c in jt_sc_valid]

    if edu_codes or jt_sc_codes:
        # Only education: simple format
        if edu_codes and not jt_sc_codes:
            if len(edu_codes) == 1:
                sc_val = f"0kf%3Aattr%28{edu_codes[0]}%29%3B"
            else:
                sc_val = f"0kf%3Aattr%28{'%7C'.join(edu_codes)}%252COR%29%3B"
        elif jt_sc_codes and not edu_codes:
            # Only job type(s)
            if len(jt_sc_codes) == 1:
                sc_val = f"0kf%3Aattr%28{jt_sc_codes[0]}%29%3B"
            else:
                sc_val = f"0kf%3Aattr%28{'%7C'.join(jt_sc_codes)}%252COR%29%3B"
        else:
            # Both present: combine as observed in Indeed HK URLs
            edu_part = '%7C'.join(edu_codes) if len(edu_codes) > 1 else edu_codes[0]
            jt_part = '%7C'.join(jt_sc_codes) if len(jt_sc_codes) > 1 else jt_sc_codes[0]
            sc_val = f"0kf%3Aattr%28{jt_part}%29%29attr%28{edu_part}%29%3B"
        params.append(f"sc={sc_val}")

    return "https://hk.indeed.com/jobs?" + "&".join(params)


def _scrape_keyword(browser, kw: str, max_pages: int) -> list:
    """Scrape a single keyword — creates fresh context on shared non-headless browser."""
    kw_jobs: list = []
    seen_jk = set()
    url = _build_url(kw)
    log.info(f"[Indeed] Searching: {kw} | URL: {url}")

    # ── Create fresh context per keyword (key anti-detection measure) ──
    ctx_kwargs = dict(
        viewport={"width": 1280, "height": 900},
        locale="en-US",
    )
    if _COOKIE_PATH.exists():
        try:
            ctx_kwargs["storage_state"] = str(_COOKIE_PATH)
        except Exception as e:
            log.warning(f"[Indeed]   Failed to load storage_state: {e}")

    ctx = browser.new_context(**ctx_kwargs)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.add_init_script(_ANTI_DETECT_JS)

    try:
        start = 0
        page_num = 0
        empty_pages = 0

        while True:
            page_num += 1
            if 0 < max_pages < page_num:
                break

            if page_num == 1:
                next_url = url
            else:
                # Try Indeed's Next link first
                try:
                    next_link = page.evaluate(r"""() => {
                        const el = document.querySelector(
                            'a[data-testid="pagination-page-next"], a[aria-label*="Next"], a[aria-label*="\u4e0b\u4e00"]'
                        );
                        return el ? el.href : null;
                    }""")
                except Exception:
                    next_link = None

                if next_link:
                    next_url = next_link
                    log.info(f"[Indeed]   Page {page_num}: next link → {next_url}")
                else:
                    next_url = f"{url}&start={start}"
                    log.info(f"[Indeed]   Page {page_num}: start={start} → {next_url}")

            try:
                page.goto(next_url, timeout=30_000, wait_until="domcontentloaded")
                time.sleep(0.5)
            except Exception as e:
                log.warning(f"[Indeed]   goto error: {e}")
                break

            # ── Check login redirect ──
            if _check_login(page):
                log.warning("[Indeed]   Redirected to login!")
                break

            # ── Dismiss popup ──
            _dismiss_job_alert_popup(page)

            # ── Check for anti-bot page ──
            if not _check_page_ok(page):
                log.warning(f"[Indeed]   Blocked by anti-bot challenge!")
                break

            # ── Extract cards ──
            cards = page.evaluate(_EXTRACT_CARDS)
            if not cards:
                if start == 0:
                    debug_dir = Path(__file__).parent.parent / "debug"
                    debug_dir.mkdir(parents=True, exist_ok=True)
                    debug_path = debug_dir / f"indeed_debug_{kw.replace(' ', '_')}.html"
                    try:
                        debug_path.write_text(page.content(), encoding="utf-8")
                        log.warning(f"[Indeed]   No cards — debug: {debug_path.name}")
                    except Exception:
                        pass
                else:
                    log.info(f"[Indeed]   No more jobs at start={start}, stopping")
                break

            # ── Dedup & make Job objects ──
            new_count = 0
            for c in cards:
                jk = c.get("jk", "")
                if not jk or jk in seen_jk:
                    continue
                seen_jk.add(jk)
                title = c.get("title", "")
                company = c.get("company", "(unknown)")
                job_url = c.get("url", "")
                if not title or len(title) < 3:
                    continue
                tl = title.lower()
                if any(skip in tl for skip in [
                    "sign in", "register", "create account", "upload cv",
                    "home", "jobs", "company reviews", "salary guide",
                ]):
                    continue
                kw_jobs.append(BaseScraper.make_job(
                    title, company, "Hong Kong", job_url, "Indeed"
                ))
                new_count += 1

            log.info(f"[Indeed]   Page {page_num}: {len(cards)} cards → +{new_count} new ({len(kw_jobs)} total)")

            if new_count == 0:
                empty_pages += 1
                if empty_pages >= 2:
                    log.info(f"[Indeed]   No new jobs for 2 pages, stopping")
                    break
                start += 10
                time.sleep(random.uniform(0.5, 1))
                continue

            empty_pages = 0
            start += 10
            time.sleep(random.uniform(2, 4))

    finally:
        # Always close the per-keyword context (browser stays alive for next keywords)
        try:
            ctx.close()
        except Exception:
            pass

    return kw_jobs


def scrape_indeed(browser, keywords: list[str], max_pages: int = 0) -> list:
    """
    Scrape Indeed HK for jobs.
    Creates a fresh context per keyword on a shared non-headless browser
    to bypass Cloudflare anti-bot detection.
    The `browser` must be launched with headless=False.
    """
    all_jobs: list = []
    log.info(f"[Indeed] Searching {len(keywords)} keywords...")

    for idx, kw in enumerate(keywords, 1):
        try:
            kw_jobs = _scrape_keyword(browser, kw, max_pages)
        except Exception as e:
            log.error(f"[Indeed]   Error scraping '{kw}': {e}")
            kw_jobs = []

        all_jobs.extend(kw_jobs)
        log.info(f"[Indeed] {kw}: {len(kw_jobs)} jobs")

        # Delay between keywords
        if idx < len(keywords):
            time.sleep(random.uniform(1, 2))

    # ── Global dedup by (title, company) ──
    seen = set()
    unique = []
    for j in all_jobs:
        key = (j.title.strip().lower(), j.company.strip().lower())
        if key not in seen and j.title.strip():
            seen.add(key)
            unique.append(j)

    log.info(f"[Indeed] Total: {len(unique)} jobs")
    return unique
