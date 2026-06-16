"""
jobboard.py — PolyU SAO Jobboard login + scraper via Playwright.
Handles NetID SSO (ADFS) login, session persistence, and full job detail extraction.

Actual page structure (Next.js app with Tailwind):
- Job list: https://jobboard-sao.polyu.edu.hk/  (after login)
- Job cards: a[data-job-post-id] with Tailwind classes
- Title: div.text-xl.font-bold
- Company: div.text-sm.font-light
- Position type: span.rounded-full inside div.flex.flex-wrap
- Detail page: /job-posts?id=XXXX&t=j
"""
import json
import logging
import re
import time
from pathlib import Path

from models import Job
from config import config

log = logging.getLogger("hunter")

COOKIE_FILE = Path(__file__).parent / "cookies" / "polyu_cookies.json"
COOKIE_FILE.parent.mkdir(exist_ok=True)

HOME_URL = "https://jobboard-sao.polyu.edu.hk/"
LOGIN_URL = "https://jobboard-sao.polyu.edu.hk/login?callbackUrl=/"
JOB_POSTS_URL = "https://jobboard-sao.polyu.edu.hk/job-posts"


def _save_cookies(context) -> None:
    cookies = context.cookies()
    COOKIE_FILE.write_text(json.dumps(cookies, indent=2), encoding="utf-8")
    log.info("[PolyU] Cookies saved")


def _load_cookies(context) -> bool:
    if not COOKIE_FILE.exists():
        return False
    try:
        cookies = json.loads(COOKIE_FILE.read_text(encoding="utf-8"))
        context.add_cookies(cookies)
        log.info("[PolyU] Cookies loaded from cache")
        return True
    except Exception:
        return False


def login(page, net_id: str = "", password: str = "") -> bool:
    """
    Login to PolyU Jobboard via ADFS SSO.
    1. Go to homepage → redirected to /login
    2. Click SAML2 / SSO link → ADFS login page
    3. Fill UserName / Password, submit
    4. Redirect back to jobboard home
    """
    net_id = net_id or config.polyu_net_id
    password = password or config.polyu_password

    # Step 1: Go to home, check if already logged in
    page.goto(HOME_URL, wait_until="networkidle", timeout=20_000)
    page.wait_for_timeout(2000)

    current_url = page.url
    if "login" not in current_url:
        log.info("[PolyU] Already logged in (cookie valid)")
        _save_cookies(page.context)
        return True

    # Step 2: Need to login
    if not net_id or not password:
        log.error("[PolyU] No NetID/password configured.")
        return False

    log.info("[PolyU] Logging in via ADFS SSO...")
    try:
        # Click SSO login button on jobboard login page
        sso_link = page.query_selector("a[href*='saml2'], a:has-text('Log in as')")
        if sso_link:
            sso_link.click()
            page.wait_for_timeout(5000)
        else:
            log.error("[PolyU] Could not find SSO login link")
            return False

        # Now on ADFS page (adfs.polyu.edu.hk)
        log.info(f"[PolyU] On SSO page: {page.title()}")

        # Fill UserName (NetID)
        username_el = page.query_selector("input#userNameInput, input[name='UserName']")
        if username_el:
            username_el.fill(net_id)
            log.info("[PolyU] Filled NetID")
        else:
            log.error("[PolyU] Could not find NetID input on SSO page")
            return False

        # Fill Password
        pwd_el = page.query_selector("input#passwordInput, input[name='Password']")
        if pwd_el:
            pwd_el.fill(password)
            log.info("[PolyU] Filled password")
        else:
            log.error("[PolyU] Could not find password input on SSO page")
            return False

        # Submit by pressing Enter (ADFS form submits on Enter)
        pwd_el.press("Enter")
        page.wait_for_timeout(8000)

        # Check result
        current_url = page.url
        if "login" in current_url or "adfs" in current_url:
            log.error(f"[PolyU] Login failed — still on {current_url}")
            return False

        log.info("[PolyU] Login successful")
        _save_cookies(page.context)

        # ── Handle Terms & Conditions modal (if present) ──
        _accept_terms_and_conditions(page)

        return True

    except Exception as e:
        log.error(f"[PolyU] Login error: {e}")
        return False


def _accept_terms_and_conditions(page) -> None:
    """
    Detect and accept the Terms & Conditions modal that appears after login.
    Modal has two checkboxes and a 'Continue to access PolyU Job Board' button.

    Strategy:
    1. Wait for possible modal to appear (up to 5s)
    2. Look for checkbox input elements
    3. Use page.locator() (auto-wait, visible-only) instead of query_selector
    4. Force-click if normal click fails
    """
    try:
        # Wait a bit for modal animation
        page.wait_for_timeout(2500)

        # Try to detect modal by looking for visible checkboxes
        checkboxes = page.locator("input[type='checkbox']").all()
        if not checkboxes:
            log.info("[PolyU] No T&C modal detected, proceeding...")
            return

        # Check if any checkbox is visible
        visible_checkboxes = [cb for cb in checkboxes if cb.is_visible()]
        if not visible_checkboxes:
            log.info("[PolyU] Checkboxes found but not visible (modal not shown), proceeding...")
            return

        log.info(f"[PolyU] T&C modal detected ({len(visible_checkboxes)} checkboxes) — accepting terms...")

        # Check all visible checkboxes using force click (bypasses visibility check if needed)
        for cb in visible_checkboxes:
            try:
                cb.click(force=True)
                page.wait_for_timeout(300)
            except Exception:
                # Fallback: evaluate click via JS
                try:
                    cb.evaluate("el => el.click()")
                except Exception:
                    pass

        page.wait_for_timeout(500)

        # Click "Continue to access PolyU Job Board" button
        # Try multiple strategies
        clicked = False

        # Strategy 1: locator with text match (auto-waits for visible)
        try:
            btn = page.locator("button").filter(has_text="Continue").first
            if btn.is_visible(timeout=3000):
                btn.click()
                clicked = True
                log.info("[PolyU] Clicked Continue (strategy 1: locator filter)")
        except Exception:
            pass

        # Strategy 2: query_selector + evaluate (bypass visibility)
        if not clicked:
            try:
                btn_el = page.query_selector("button[type='submit'], button:has-text('Continue')")
                if btn_el:
                    btn_el.evaluate("el => el.click()")
                    clicked = True
                    log.info("[PolyU] Clicked Continue (strategy 2: JS evaluate)")
            except Exception:
                pass

        # Strategy 3: press Enter
        if not clicked:
            log.warning("[PolyU] Could not click Continue button, trying Enter key")
            page.keyboard.press("Enter")
            page.wait_for_timeout(2000)

        # Wait for modal to disappear
        page.wait_for_timeout(3000)
        log.info("[PolyU] T&C modal handled")

    except Exception as e:
        log.warning(f"[PolyU] T&C handling error: {e}")


def scrape_job_list(page) -> list[Job]:
    """Scrape job cards from homepage ('Latest Job Posts')."""
    jobs = []
    log.info("[PolyU] Scraping homepage job cards...")

    try:
        # Navigate to home
        page.goto(HOME_URL, wait_until="networkidle", timeout=30_000)
        page.wait_for_timeout(3000)

        # Handle T&C modal if it appears on homepage
        _accept_terms_and_conditions(page)

        # Scroll to load all visible cards
        for _ in range(5):
            page.keyboard.press("End")
            page.wait_for_timeout(1000)

        # Use the exact card selector: <a data-job-post-id="...">
        cards = page.query_selector_all("a[data-job-post-id]")
        log.info(f"[PolyU] Found {len(cards)} job cards on homepage")

        if not cards:
            log.warning("[PolyU] No job cards found on homepage!")
            return jobs

        for card in cards:
            try:
                # Title: try multiple selectors
                title_el = card.query_selector("div.text-xl, h3, [class*='title'], a[data-job-post-id]")
                title = title_el.inner_text().strip() if title_el else ""
                # Fallback: use the card's text if title not found
                if not title or len(title) < 2:
                    full_text = card.inner_text().strip()
                    lines = [l.strip() for l in full_text.split("\n") if l.strip()]
                    title = lines[0] if lines else ""
                if not title or len(title) < 2:
                    log.debug(f"[PolyU] Card skipped: title='{title}', card_text={card.inner_text().strip()[:80]}")
                    continue

                # Company: try multiple selectors
                company_el = card.query_selector("div.text-sm.font-light, [class*='company'], span.text-sm")
                company = company_el.inner_text().strip() if company_el else ""

                # Position type
                type_el = card.query_selector("span.rounded-full, [class*='badge'], span.bg-")
                job_type = type_el.inner_text().strip() if type_el else ""

                # URL — use card's own href
                href = card.get_attribute("href") or ""
                if href and not href.startswith("http"):
                    href = "/" + href.lstrip("/")
                url = f"https://jobboard-sao.polyu.edu.hk{href}" if href else ""

                # data-job-post-id
                post_id = card.get_attribute("data-job-post-id") or ""

                # Deadline from card text
                card_text = card.inner_text()
                close_match = re.search(r'Closing\s*(?:On|on)?\s*[:\s]?\s*([\d\-/]{8,20})', card_text)
                deadline = close_match.group(1) if close_match else ""

                job = Job(
                    title=title,
                    company=company,
                    location="Hong Kong",
                    url=url,
                    source="PolyU Jobboard",
                    deadline=deadline,
                )
                jobs.append(job)
            except Exception as e:
                log.warning(f"[PolyU] Card parse error: {e}")
                continue

        log.info(f"[PolyU] Extracted {len(jobs)} jobs from homepage")

    except Exception as e:
        log.error(f"[PolyU] Homepage scrape error: {e}")

    return jobs


def scrape_job_posts_page(page) -> list[Job]:
    """Scrape from the full /job-posts listing page (View All)."""
    jobs = []
    log.info("[PolyU] Scraping /job-posts page...")

    try:
        page.goto(JOB_POSTS_URL, wait_until="networkidle", timeout=30_000)
        page.wait_for_timeout(3000)

        # Same card structure
        cards = page.query_selector_all("a[data-job-post-id]")
        log.info(f"[PolyU] Found {len(cards)} job cards on /job-posts")

        for card in cards:
            try:
                title_el = card.query_selector("div.text-xl, h3, [class*='title'], a[data-job-post-id]")
                title = title_el.inner_text().strip() if title_el else ""
                if not title or len(title) < 2:
                    full_text = card.inner_text().strip()
                    lines = [l.strip() for l in full_text.split("\n") if l.strip()]
                    title = lines[0] if lines else ""
                if not title or len(title) < 2:
                    log.debug(f"[PolyU] Card skipped: title='{title}', card_text={card.inner_text().strip()[:80]}")
                    continue

                company_el = card.query_selector("div.text-sm.font-light, [class*='company'], span.text-sm")
                company = company_el.inner_text().strip() if company_el else ""

                type_el = card.query_selector("span.rounded-full, [class*='badge'], span.bg-")
                job_type = type_el.inner_text().strip() if type_el else ""

                href = card.get_attribute("href") or ""
                if href and not href.startswith("http"):
                    href = "/" + href.lstrip("/")
                url = f"https://jobboard-sao.polyu.edu.hk{href}" if href else ""

                post_id = card.get_attribute("data-job-post-id") or ""

                card_text = card.inner_text()
                close_match = re.search(r'Closing\s*(?:On|on)?\s*[:\s]?\s*([\d\-/]{8,20})', card_text)
                deadline = close_match.group(1) if close_match else ""

                job = Job(
                    title=title,
                    company=company,
                    location="Hong Kong",
                    url=url,
                    source="PolyU Jobboard",
                    deadline=deadline,
                )
                jobs.append(job)
            except Exception as e:
                log.warning(f"[PolyU] Card parse error: {e}")
                continue

        log.info(f"[PolyU] Extracted {len(jobs)} jobs from /job-posts")

    except Exception as e:
        log.error(f"[PolyU] /job-posts scrape error: {e}")

    return jobs


def scrape_job_detail(page, job: Job) -> Job:
    """Visit a job detail page (/job-posts?id=XXXX) and extract full info."""
    if not job.url or "job-posts?id=" not in job.url:
        return job

    try:
        page.goto(job.url, wait_until="networkidle", timeout=15_000)
        page.wait_for_timeout(2000)

        # Get full page content
        body_text = page.inner_text("body") if hasattr(page, "inner_text") else page.content()

        # Description - grab main content area
        desc_parts = []
        # Try to find the job description content (Next.js page with prose/article)
        for sel in ["article", "[class*='prose']", "main .container", ".job-detail", "[class*='description']"]:
            el = page.query_selector(sel)
            if el:
                text = el.inner_text().strip()
                if len(text) > 100:
                    desc_parts.append(text)
                    break
        if not desc_parts:
            # Fallback: get all text from body
            desc_parts.append(body_text)

        full_text = "\n\n".join(desc_parts)
        job.description = full_text

        # Extract requirements
        job.requirements = _extract_section(full_text, [
            "Requirements", "Qualification", "requirement",
            "Minimum Qualification", "Preferred Qualification",
            "What you need", "You should have", "You will need",
        ])

        # Extract education requirement
        job.education_level = _extract_section(full_text, [
            "Education", "education level", "Degree", "Academic",
        ])

        # Extract final year requirement
        job.is_final_year = _extract_section(full_text, [
            "Final year", "final year", "graduating", "final-year",
        ])

        # Extract contact email
        emails = re.findall(r'[\w\.-]+@[\w\.-]+\.\w+', full_text)
        if emails:
            job.contact_email = emails[0]

        # Extract location if available
        loc_match = re.search(r'(?:Location|Venue|Address)[:\s]*([^\n]{5,80})', full_text, re.I)
        if loc_match:
            job.location = loc_match.group(1).strip()

        # Extract deadline
        dl_match = re.search(r'(?:Deadline|Closing\s*Date|Application\s*Deadline)[:\s]*([^\n]{5,30})', full_text, re.I)
        if dl_match:
            job.deadline = dl_match.group(1).strip()

    except Exception as e:
        log.debug(f"[PolyU] Detail fetch error for {job.title[:30]}: {e}")

    return job


def _extract_section(text: str, keywords: list[str], max_chars: int = 500) -> str:
    """Extract a section from text based on heading keywords."""
    if not text:
        return ""
    text_lower = text.lower()
    for kw in keywords:
        idx = text_lower.find(kw.lower())
        if idx >= 0:
            section = text[idx:idx + max_chars]
            lines = section.split("\n")
            result_lines = []
            for line in lines:
                stripped = line.strip()
                if len(stripped) > 5 and not stripped.isupper():
                    result_lines.append(stripped)
                if len(result_lines) >= 4:
                    break
            return "; ".join(result_lines[:3])
    return ""


def scrape_polyu(page, net_id: str = "", password: str = "") -> list[Job]:
    """Main entry: login + scrape homepage + /job-posts + fetch details."""
    if not login(page, net_id, password):
        log.warning("[PolyU] Login failed, skipping PolyU scraper")
        return []

    # Scrape both homepage and full listing
    home_jobs = scrape_job_list(page)
    posts_jobs = scrape_job_posts_page(page)

    # Deduplicate by post_id or URL
    seen = set()
    all_jobs = []
    for job in home_jobs + posts_jobs:
        key = job.url or job.title
        if key not in seen:
            seen.add(key)
            all_jobs.append(job)

    log.info(f"[PolyU] Combined unique jobs: {len(all_jobs)} (home: {len(home_jobs)}, posts: {len(posts_jobs)})")

    # Fetch details for each job
    max_details = min(len(all_jobs), 30)
    detailed = []
    for i, job in enumerate(all_jobs[:max_details]):
        log.debug(f"[PolyU] Detail {i+1}/{max_details}: {job.title[:40]}")
        detailed.append(scrape_job_detail(page, job))
        time.sleep(0.3)  # Rate limit

    detailed.extend(all_jobs[max_details:])  # Add remaining without details
    log.info(f"[PolyU] Done: {len(detailed)} total jobs ({max_details} with details)")
    return detailed
