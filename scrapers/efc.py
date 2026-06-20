"""
scrapers/efc.py — eFinancialCareers HK scraper.

eFC HK uses clean URL pattern for search:
  https://www.efinancialcareers.hk/jobs/{keyword-slug}
URL built dynamically from config filters (Settings UI).
"""
import logging
import random
import time
import urllib.parse
from pathlib import Path
from .base import BaseScraper
from config import check_stop, config

log = logging.getLogger("hunter")

BASE = "https://www.efinancialcareers.hk"


def _build_url(keyword: str) -> str:
    slug = keyword.lower().replace(" ", "-")
    q = urllib.parse.quote(keyword.lower(), safe="+")  # keep + for spaces
    params = [
        f"q={q}",
        "countryCode=HK",
        "radius=40",
        "radiusUnit=km",
        f"pageSize={config.efc_page_size or '15'}",
        "filters.locationPath=Asia%2FHong+Kong",
        "currencyCode=HKD",
        "language=en",
        "includeUnspecifiedSalary=true",
        "enableVectorSearch=true",
    ]
    if config.efc_exp_level:
        params.append(f"filters.experienceLevel={config.efc_exp_level}")
    if config.efc_posted_within:
        params.append(f"filters.postedWithin={config.efc_posted_within}")
    if config.efc_sort_by:
        params.append(f"sortBy={config.efc_sort_by}")
    return f"{BASE}/jobs/{slug}/in-hong-kong?{'&'.join(params)}"


def _parse_cards(page) -> list:
    """Extract job title + company + url from eFC Angular SPA cards using Playwright locators."""
    items = []

    # Try efc-job-card first, then fallback to generic selectors
    cards = page.locator("efc-job-card")
    count = cards.count()

    if count == 0:
        # Fallback: try various card selectors
        fallback_selectors = [
            "article[class*='job']",
            "div[class*='JobCard']",
            "div[class*='job-card']",
            "li[class*='job']",
            "[data-cy='job-card']",
            "a[href*='/jobs/']",
        ]
        for sel in fallback_selectors:
            try:
                found = page.locator(sel)
                if found.count() > 0:
                    cards = found
                    count = found.count()
                    log.debug(f"[eFC]   Fallback selector '{sel}' found {count} cards")
                    break
            except Exception:
                continue

    if count == 0:
        # Last resort: find all links to /jobs/ and build cards from them
        try:
            job_links = page.locator("a[href*='/jobs/']")
            link_count = job_links.count()
            if link_count > 0:
                log.debug(f"[eFC]   Using {link_count} raw /jobs/ links as fallback")
                cards = job_links
                count = link_count
        except Exception:
            pass

    log.debug(f"[eFC]   _parse_cards: {count} cards found")

    # ── Fast path: use page.evaluate() for bulk extraction (handles Shadow DOM) ──
    try:
        extracted = page.evaluate("""() => {
            const results = [];
            const cards = document.querySelectorAll('efc-job-card');
            
            if (cards.length > 0) {
                // efc-job-card web components (may have Shadow DOM)
                cards.forEach(card => {
                    try {
                        const root = card.shadowRoot || card;
                        const titleEl = root.querySelector('.job-title, a[class*="title"], h3, h4, a[href*="/jobs/"]') 
                            || card.querySelector('a[href*="/jobs/"]');
                        const title = titleEl ? (titleEl.textContent || '').trim() : '';
                        const href = (titleEl && titleEl.href) ? titleEl.href 
                            : ((card.querySelector('a[href*="/jobs/"]') || {}).href || '');
                        const img = root.querySelector('img[itemprop="image"], img[alt]') 
                            || card.querySelector('img[itemprop="image"], img[alt]');
                        const company = img ? (img.alt || img.title || '').trim() : '(unknown)';
                        if (title.length >= 3) results.push({title, company, href});
                    } catch(e) {
                        const t = (card.innerText || '').trim().split('\\n')[0];
                        if (t.length >= 3) results.push({title: t, company: '(unknown)', href: ''});
                    }
                });
            } else {
                // No efc-job-card elements — try generic selectors
                const selectors = ['article[class*="job"]', 'div[class*="JobCard"]', 'div[class*="job-card"]'];
                for (const sel of selectors) {
                    const found = document.querySelectorAll(sel);
                    if (found.length > 0) {
                        found.forEach(card => {
                            const a = card.querySelector('a[href*="/jobs/"]') || card.querySelector('a');
                            const title = a ? (a.textContent || '').trim() : '';
                            const href = a ? (a.href || '') : '';
                            const img = card.querySelector('img[alt]');
                            const company = img ? (img.alt || '').trim() : '(unknown)';
                            if (title.length >= 3) results.push({title, company, href});
                        });
                        break;
                    }
                }
                // Absolute last resort
                if (results.length === 0) {
                    document.querySelectorAll('a[href*="/jobs/"]').forEach(a => {
                        const t = (a.textContent || '').trim();
                        if (t.length >= 3) results.push({title: t, company: '(unknown)', href: a.href});
                    });
                }
            }
            return results;
        }""")

        if extracted:
            for item in extracted:
                title = str(item.get("title", ""))[:120]
                href = str(item.get("href", ""))
                company = str(item.get("company", "(unknown)"))[:120]
                if href and not href.startswith("http"):
                    href = "https://www.efinancialcareers.hk" + href
                if title:
                    items.append({"title": title, "company": company, "href": href})

            # Dedup by href
            seen = set()
            unique = []
            for x in items:
                if x["href"] and x["href"] not in seen:
                    seen.add(x["href"])
                    unique.append(x)
            return unique

    except Exception as e:
        log.warning(f"[eFC]   evaluate extraction failed: {e}, trying locator loop")

    # ── Slow fallback: locator loop with cap ──
    for i in range(min(count, 50)):
        try:
            card = cards.nth(i)

            tag_name = ""
            try:
                tag_name = card.evaluate("el => el.tagName.lower()")
            except Exception:
                pass

            if tag_name == "a":
                title = (card.inner_text() or "").strip()
                href = card.get_attribute("href") or ""
                if href and not href.startswith("http"):
                    href = "https://www.efinancialcareers.hk" + href
                if title and len(title) >= 3:
                    items.append({"title": title[:120], "company": "(unknown)", "href": href})
                continue

            title = ""
            title_a = card.locator("a.job-title").first
            if title_a:
                h3 = title_a.locator("h3").first
                if h3:
                    title = (h3.inner_text() or "").strip()
                if not title:
                    title = (title_a.inner_text() or "").strip()

            if not title:
                any_a = card.locator("a[href*='/jobs/']").first
                if any_a:
                    title = (any_a.inner_text() or "").strip()
                    if not title_a:
                        title_a = any_a

            if not title or len(title) < 3:
                continue
            
            href = ""
            if title_a:
                href = title_a.get_attribute("href") or ""
            
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

        # Debug: log what selectors are present on the page
        for sel in ["efc-job-card", "a.job-title", "a[href*='/jobs/']", "article", "[class*='job']", "[class*='JobCard']", "[data-cy]", "[class*='card']"]:
            try:
                c = page.locator(sel).count()
                if c > 0:
                    log.info(f"[eFC]   Page has {c} elements matching '{sel}'")
            except Exception:
                pass

        # If no cards found, save debug HTML
        has_cards = page.locator("efc-job-card").count() > 0 or page.locator("a[href*='/jobs/']").count() > 0
        if not has_cards:
            debug_dir = Path(__file__).parent.parent / "debug"
            debug_dir.mkdir(parents=True, exist_ok=True)
            safe_kw = kw.replace(" ", "_")
            debug_path = debug_dir / f"efc_debug_{safe_kw}.html"
            try:
                debug_path.write_text(page.content(), encoding="utf-8")
                log.warning(f"[eFC]   No cards found for '{kw}', saved HTML to {debug_path}")
            except Exception:
                pass

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
