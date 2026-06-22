"""
scrapers/indeed.py — Indeed HK scraper using Playwright.
One keyword per search, paginate via start=N URL param.
URL built dynamically from config filters (Settings UI).

New DOM structure (2024+): [data-jk] elements ARE the <a> tags with title <span> inside.
"""
import logging
import random
import time
import json
from pathlib import Path
from .base import BaseScraper
from config import check_stop, config
from stealth import Stealth

log = logging.getLogger("hunter")
_COOKIE_PATH = Path(__file__).parent.parent / "cookies" / "indeed.json"

# ── Page evaluation JS: bulk-extract all job cards (Indeed 2026) ──
# Key insight: Indeed mixes organic results, sponsored jobs, and "jobs similar to
# what you browsed" on the same page.  Text-based filtering ("Easily apply") is
# unreliable.  Instead we locate the PRIMARY results container and only extract
# [data-jk] cards that live inside it.
#
# Organic results are inside:
#   <ul id="jobsearch-SerpJobList"> or <div data-mosaic-id="provider"> or
#   the first <tbody> of the main mosaic table.
# Recommended/similar sections live OUTSIDE this container.
_EXTRACT_CARDS_JS = r"""() => {
    const results = [];

    // ── Step 1: locate the primary organic-results container ──
    let primaryContainer = null;

    // Strategy A: Indeed's own SerpJobList <ul> (most reliable in 2026)
    primaryContainer = document.getElementById('jobsearch-SerpJobList');

    // Strategy B: mosaic provider div
    if (!primaryContainer) {
        primaryContainer = document.querySelector(
            'div[data-mosaic-id="provider"]'
        );
    }

    // Strategy C: first tbody inside the main results table
    if (!primaryContainer) {
        const table = document.querySelector(
            'table.mosaic-container, table[id*="results"], '
            + 'table[class*="jobsearch"]'
        );
        if (table) {
            const tbodies = table.querySelectorAll('tbody');
            if (tbodies.length > 0) {
                primaryContainer = tbodies[0];
            } else {
                primaryContainer = table;
            }
        }
    }

    // Fallback: just use the <ul> that holds most job cards
    if (!primaryContainer) {
        const uls = document.querySelectorAll('ul');
        for (const ul of uls) {
            const jks = ul.querySelectorAll('[data-jk]');
            if (jks.length >= 3) { primaryContainer = ul; break; }
        }
    }

    // Only look for cards INSIDE the primary container.
    // Cards outside are recommended/sponsored/sidebar content.
    const cards = primaryContainer
        ? primaryContainer.querySelectorAll('[data-jk]')
        : document.querySelectorAll('[data-jk]');

    for (const card of cards) {
        const jk = card.getAttribute('data-jk');
        if (!jk) continue;

        // ── Title: span[title] attribute or aria-label fallback ──
        const spanTitle = card.querySelector('span[title]');
        let title = '';
        if (spanTitle) {
            title = (spanTitle.getAttribute('title') || spanTitle.textContent || '').trim();
        }
        if (!title) {
            const label = card.getAttribute('aria-label') || '';
            const m = label.match(/details\s+(?:of\s+)?(.+)/i);
            if (m) title = m[1].trim();
        }
        if (!title || title.length < 3) continue;

        const tl = title.toLowerCase();
        if (['sign in', 'register', 'create account', 'upload cv', 'home',
             'jobs', 'company reviews', 'salary guide', 'employers / post job',
             'employers'].includes(tl)) continue;

        // ── Company name extraction ──
        // DOM: TD.resultContent > (DIV.jobCardContainer) >
        //       (H3.jobTitle > A[data-jk]) + (div.company line)
        // The company name is usually a sibling link near the title,
        // often inside a [data-testid="company-name"] or similar.
        let company = '(unknown)';
        const td = card.closest('td[class*="resultContent"]')
                 || card.closest('td')
                 || card.closest('[class*="result"]')
                 || card.closest('li')
                 || card.closest('[class*="card"]');

        if (td) {
            // Try specific Indeed selectors first (fastest)
            const companyEl = td.querySelector(
                '[data-testid="company-name"], '
                + '[class*="companyName"], '
                + '[class*="company-name"], '
                + 'a[data-toggle="jobdetail-company"]'
            );
            if (companyEl) {
                const ct = (companyEl.textContent || '').trim();
                if (ct && ct.length >= 2 && ct.length <= 60) company = ct;
            }

            // Fallback: scan for the first plausible text node / link
            // that isn't the title, location, date, or metadata
            if (company === '(unknown)') {
                const candidates = td.querySelectorAll('a, span, div');
                for (const el of candidates) {
                    if (el === spanTitle || el.contains(card) || card.contains(el)) continue;
                    const st = (el.textContent || '').trim();
                    if (!st || st.length < 2 || st.length > 60) continue;
                    if (st === title) continue;
                    // Skip location strings
                    if (/^(Hong Kong|Kowloon|New Territories|Central|Wan Chai|TST|Kwai Chung|Sha Tin|Tsuen Wan)/i.test(st)) continue;
                    // Skip metadata badges
                    if (/^(Full.?time|Permanent|Contract|Part.?time|Shift|Remote|\d+\+?$)/i.test(st)) continue;
                    if (/Easy\s*apply|[0-9]+\s*(day|hour|week|month)s?\s*ago|New/i.test(st)) continue;
                    // Skip rating like "3.5 ★"
                    if (/^\d[\d.]*\s*[★☆]/.test(st)) continue;
                    // Skip heavily-styled utility elements (CSS framework)
                    const cls = (el.className || '').toString();
                    if ((cls.match(/css-\w+/g) || []).length >= 4) continue;
                    company = st;
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
    Tries: Activate button → close button → Maybe later → ESC.
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


def scrape_indeed(page, keywords: list[str], max_pages: int = 0) -> list:
    """
    Scrape Indeed HK for jobs.
    One search per keyword, paginate via start=N URL param.
    max_pages: 0 = unlimited, else stop after N pages.
    """
    # ── Load Indeed cookies (if available) ──────────────────────────────
    if _COOKIE_PATH.exists():
        try:
            with open(_COOKIE_PATH) as f:
                storage = json.load(f)
            page.context.add_cookies(storage.get("cookies", []))
            log.info(f"[Indeed] Loaded cookies from {_COOKIE_PATH}")
        except Exception as e:
            log.warning(f"[Indeed] Failed to load cookies: {e}")
    else:
        log.info(f"[Indeed] No cookie file found at {_COOKIE_PATH} (run indeed_login.py first)")

    all_jobs = []
    log.info(f"[Indeed] Searching {len(keywords)} keywords...")

    for kw in keywords:
        kw_jobs = []
        seen_jk = set()
        kw_parts = [p.strip().lower() for p in kw.replace('+', ' ').split() if p.strip()]
        # ── 请求监控：记录所有网络请求（帮助调试"点到apply"问题） ──
        suspicious_requests = []
        import re
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
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                page.wait_for_timeout(3000)
            except Exception as e:
                log.warning(f"[Indeed]   goto error: {e}")
                break

            # ── Dismiss Job Alert popup ──
            _dismiss_job_alert_popup(page)

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

            # ── 去重：按 jk 去重，同时统计新职位数量 ──
            new_count = 0
            for c in cards:
                jk = c.get("jk", "")
                if jk and jk not in seen_jk:
                    seen_jk.add(jk)
                    title = c.get("title", "")
                    company = c.get("company", "(unknown)")
                    url = c.get("url", "")
                    if not title or len(title) < 3:
                        continue
                    # 放宽标题过滤：只过滤多词关键词，且只要求任一词出现在标题或公司名中
                    title_lower = title.lower()
                    company_lower = company.lower()
                    if kw_parts and len(kw_parts) >= 2:
                        if not any(p in title_lower or p in company_lower for p in kw_parts):
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
            # 检查是否跳转到登录页（Indeed 会在翻页时要求登录）
            try:
                curr_url = page.url.lower()
                if any(s in curr_url for s in ["/signin", "/login", "/account"]):
                    log.info("[Indeed]   Redirected to login page, stopping pagination")
                    break
            except Exception:
                pass
            time.sleep(random.uniform(3, 6))

        all_jobs.extend(kw_jobs)
        log.info(f"[Indeed] Searching: {kw} → {len(kw_jobs)} jobs")
        # Delay between keywords to avoid rate-limiting
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
