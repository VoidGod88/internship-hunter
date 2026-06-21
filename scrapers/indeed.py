"""
scrapers/indeed.py — Indeed HK scraper using Playwright.
One keyword per search, paginate via start=N URL param.
URL built dynamically from config filters (Settings UI).

New DOM structure (2024+): [data-jk] elements ARE the <a> tags with title <span> inside.
"""
import logging
import random
import time
from pathlib import Path
from .base import BaseScraper
from config import check_stop, config
from stealth import Stealth

log = logging.getLogger("hunter")

# ── Page evaluation JS: bulk-extract all job cards, skip sponsored/recommended ──
_EXTRACT_CARDS_JS = """() => {
    const results = [];
    let inRecommended = false;

    // Indeed's job result list: each card is in a container like <div> > <h3> > <a data-jk>
    // Collect all [data-jk] links
    const cards = document.querySelectorAll('[data-jk]');

    for (const card of cards) {
        // Skip if this card is inside a recommended/sponsored section
        // Check card and ancestors for sponsored indicators
        let node = card;
        let isSponsored = false;
        for (let i = 0; i < 5 && node; i++) {
            const cls = (node.className || '').toString().toLowerCase();
            const txt = (node.textContent || '').slice(0, 200).toLowerCase();
            if (cls.includes('sponsored') || cls.includes('recommend') || cls.includes('promoted')
                || txt.includes('sponsored') || txt.includes('promoted')) {
                isSponsored = true;
                break;
            }
            node = node.parentElement;
        }
        if (isSponsored) continue;

        // Check if we've entered the "recommended jobs" section
        // Look for recent sibling headings that signal recommended results
        let prev = card.previousElementSibling;
        for (let i = 0; i < 3 && prev; i++) {
            const tag = prev.tagName.toLowerCase();
            const txt = (prev.textContent || '').toLowerCase().trim();
            if ((tag === 'h2' || tag === 'h3' || tag === 'h4' || tag === 'div') && 
                (txt.includes('nearby') || txt.includes('regional') || txt.includes('also searched')
                 || txt.includes('similar') || txt.includes('other job') || txt.includes('more job')
                 || txt.includes('recommend') || txt.includes('popular'))) {
                inRecommended = true;
                break;
            }
            prev = prev.previousElementSibling;
        }
        if (inRecommended) continue;

        const jk = card.getAttribute('data-jk');
        if (!jk) continue;

        // Title: inside the <a> there's a <span title="...">
        const spanTitle = card.querySelector('span[title]');
        const title = spanTitle ? (spanTitle.textContent || '').trim() : '';
        if (!title || title.length < 3) continue;

        // Skip nav/footer text
        const tl = title.toLowerCase();
        if (['sign in', 'register', 'create account', 'upload cv', 'home',
             'jobs', 'company reviews', 'salary guide', 'employers'].includes(tl)) continue;

        // Company: find the parent container and look for company name
        // Indeed wraps cards in: <td><div class="resultContent"><h3><a data-jk>...
        let company = '(unknown)';
        let parentDiv = card.closest('div[class*="result"], td.resultContent, div.resultContent');
        if (!parentDiv) parentDiv = card.closest('td');
        if (!parentDiv) parentDiv = card.closest('li');
        
        if (parentDiv) {
            // Try to find company span
            const companyEl = parentDiv.querySelector('span[data-testid="company-name"], span.companyName, span.css-1h7lukg');
            if (companyEl) {
                company = companyEl.textContent.trim();
            } else {
                // Find any span with location-like content pattern (not the title span)
                const spans = parentDiv.querySelectorAll('span');
                for (const s of spans) {
                    const st = s.textContent.trim();
                    // Skip title-like, location-like, or very short
                    if (st.length > 2 && st.length < 60 && st !== title
                        && !st.includes('Hong Kong') && !st.includes('Kowloon')
                        && !st.includes('review') && !st.toLowerCase().includes('day ago')
                        && !st.toLowerCase().includes('hour ago') && !st.toLowerCase().includes('week ago')
                        && !st.match(/^Employer/)) {
                        company = st;
                        break;
                    }
                }
            }
        }

        const url = 'https://hk.indeed.com/viewjob?jk=' + jk;
        results.push({jk, title, company, url});
    }
    return results;
}"""


def scrape_indeed(page, keywords: list[str], max_pages: int = 0) -> list:
    """
    Scrape Indeed HK for jobs.
    One search per keyword, paginate via start=N URL param.
    max_pages: 0 = unlimited, else stop after N pages.
    """
    all_jobs = []
    log.info(f"[Indeed] Searching {len(keywords)} keywords...")

    for kw in keywords:
        kw_jobs = []
        seen_jk = set()
        # Build URL from config filters
        params = [f"q={kw.replace(' ', '+')}"]
        if config.id_date_range:
            params.append(f"fromage={config.id_date_range}")
        if config.id_job_type:
            params.append(f"jt={config.id_job_type}")
        if config.id_sort_by:
            params.append(f"sort={config.id_sort_by}")
        if config.id_radius:
            params.append(f"radius={config.id_radius}")
        params.append("l=Hong+Kong")
        # Indeed education filter: encrypted sc= parameter
        # Codes: HFDVW=Bachelor, EXSNN=Master, 6QC5F=PhD, MR89S=Diploma
        if config.id_education:
            valid = {"HFDVW","EXSNN","6QC5F","MR89S"}
            codes = [c for c in config.id_education if c in valid]
            if codes:
                if len(codes) == 1:
                    sc_val = f"0kf%3Aattr%28{codes[0]}%29%3B"
                else:
                    sc_val = f"0kf%3Aattr%28{'%7C'.join(codes)}%252COR%29%3B"
                params.append(f"sc={sc_val}")
                log.info(f"[Indeed]   education sc={sc_val}")
        base_url = "https://hk.indeed.com/jobs?" + "&".join(params)
        log.info(f"[Indeed] Searching: {kw} | URL: {base_url}")
        start = 0
        page_num = 0

        while True:
            url = base_url + f"&start={start}" if start > 0 else base_url

            # ── Navigate with Cloudflare retry ──
            page_ok = False
            for cf_try in range(2):
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                    page.wait_for_timeout(3000)

                    _title = page.title().lower()
                    if "just a moment" in _title or "blocked" in _title:
                        log.warning(f"[Indeed]   Cloudflare challenge (try {cf_try+1}/2), waiting 15s...")
                        page.wait_for_timeout(15_000)
                        continue
                    page_ok = True
                    break
                except Exception as e:
                    log.warning(f"[Indeed]   goto error: {e}")
                    break

            if not page_ok:
                log.error(f"[Indeed]   Page blocked after retries, skipping '{kw}'")
                break

            # ── Detect homepage redirect ──
            current_url = page.url
            if "/jobs" not in current_url or current_url.rstrip("/").endswith("/hk"):
                log.warning(f"[Indeed]   Redirected to homepage! URL: {current_url}")
                break

            # ── Detect "no results" ──
            try:
                body_text = page.inner_text("body")
                no_result_keywords = [
                    "找不到", "no jobs found", "No matching jobs",
                    "沒有相關", "没有找到", "0 jobs in", "no results",
                ]
                if any(kw_no in body_text for kw_no in no_result_keywords):
                    log.info(f"[Indeed]   No results page for '{kw}' (start={start})")
                    break
            except Exception:
                pass

            # ── Extract cards ──
            cards = page.evaluate(_EXTRACT_CARDS_JS)
            if not cards:
                if start == 0:
                    debug_dir = Path(__file__).parent.parent / "debug"
                    debug_dir.mkdir(parents=True, exist_ok=True)
                    debug_path = debug_dir / f"indeed_debug_{kw.replace(' ', '_')}.html"
                    try:
                        debug_path.write_text(page.content(), encoding="utf-8")
                        log.warning(f"[Indeed]   No cards on first page, debug HTML: {debug_path.name}")
                    except Exception:
                        pass
                else:
                    log.info(f"[Indeed]   No more jobs at start={start}, stopping")
                break

            new_count = 0
            kw_parts = [p.lower() for p in kw.split() if len(p) > 1]
            for c in cards:
                jk = c.get("jk", "")
                if jk in seen_jk:
                    continue
                seen_jk.add(jk)
                title = c.get("title", "")
                company = c.get("company", "(unknown)")
                url = c.get("url", "")
                if not title or len(title) < 3:
                    continue
                title_lower = title.lower()
                if kw_parts and not any(p in title_lower for p in kw_parts):
                    continue
                if any(skip in title_lower for skip in [
                    "sign in", "register", "create account", "upload cv",
                    "home", "jobs", "company reviews", "salary guide"
                ]):
                    continue
                kw_jobs.append(BaseScraper.make_job(
                    title, company, "Hong Kong", url, "Indeed"
                ))
                new_count += 1

            if new_count == 0:
                log.info(f"[Indeed]   No new jobs at start={start}, stopping")
                break

            if max_pages > 0 and page_num + 1 >= max_pages:
                break

            start += 10
            page_num += 1
            time.sleep(random.uniform(3, 6))

        all_jobs.extend(kw_jobs)
        log.info(f"[Indeed] Searching: {kw} → {len(kw_jobs)} jobs")
        # Long delay between keywords to avoid Cloudflare rate-limiting
        time.sleep(random.uniform(5, 10))

    # Deduplicate by (title, company)
    seen = set()
    unique = []
    for j in all_jobs:
        key = (j.title.strip().lower(), j.company.strip().lower())
        if key not in seen and j.title.strip():
            seen.add(key)
            unique.append(j)

    log.info(f"[Indeed] Total: {len(unique)} jobs")
    return unique
