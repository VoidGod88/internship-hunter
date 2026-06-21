"""
debug_linkedin.py — Debug LinkedIn job card selector.

Usage:
    cd D:\WorkBuddy\internship_hunter\internship-hunter
    python debug_linkedin.py [keyword]

Default keyword: "Python"
"""
import sys
import json
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

KEYWORD = sys.argv[1] if len(sys.argv) > 1 else "Python"
COOKIE_PATH = Path(__file__).parent / "cookies" / "linkedin.json"
DEBUG_DIR = Path(__file__).parent / "debug"
DEBUG_DIR.mkdir(exist_ok=True)

URL = (
    f"https://www.linkedin.com/jobs/search/"
    f"?f_E=1&f_JT=F%2CP%2CI&f_WT=1&geoId=103291313"
    f"&sortBy=R&keywords={KEYWORD}"
    f"&origin=JOB_SEARCH_PAGE_JOB_FILTER&spellCorrectionEnabled=true"
)

print(f"[Debug] Keyword: {KEYWORD}")
print(f"[Debug] URL: {URL}")
print(f"[Debug] Cookie path: {COOKIE_PATH}")

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=False, slow_mo=100)
    ctx = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
        viewport={"width": 1920, "height": 1080},
    )

    # Load cookies
    if COOKIE_PATH.exists():
        try:
            raw = json.loads(COOKIE_PATH.read_text(encoding="utf-8"))
            # Support both formats
            cookies = raw if isinstance(raw, list) else raw.get("cookies", [])
            ctx.add_cookies(cookies)
            print(f"[Debug] Loaded {len(cookies)} cookies")
        except Exception as e:
            print(f"[Debug] Failed to load cookies: {e}")
    else:
        print("[Debug] WARNING: cookies/linkedin.json not found!")

    page = ctx.new_page()

    print("[Debug] Opening page...")
    page.goto(URL, wait_until="domcontentloaded", timeout=30000)
    time.sleep(3)  # Wait for JS to render

    # Take screenshot
    screenshot_path = DEBUG_DIR / f"linkedin_debug_{KEYWORD}.png"
    page.screenshot(path=str(screenshot_path), full_page=False)
    print(f"[Debug] Screenshot saved: {screenshot_path}")

    # Try multiple selectors
    CANDIDATE_SELECTORS = [
        "ul.jobs-search__results-list div.base-search-card[data-entity-urn]",
        "div.base-search-card[data-entity-urn]",
        "ul.jobs-search__results-list > li",
        "li[data-occludable-job-id]",
        "div[data-job-id]",
        ".job-card-container",
    ]

    print("\n[Debug] Trying selectors...")
    for sel in CANDIDATE_SELECTORS:
        try:
            count = page.locator(sel).count()
            print(f"  [{count:3d}] {sel}")
            if count > 0:
                # Print first match HTML (truncated)
                html = page.locator(sel).first.evaluate("el => el.outerHTML")
                print(f"       Sample HTML: {html[:300]}...")
        except Exception as e:
            print(f"  [ERR] {sel}: {e}")

    # Save page HTML for inspection
    html_path = DEBUG_DIR / f"linkedin_debug_{KEYWORD}.html"
    html_path.write_text(page.content(), encoding="utf-8")
    print(f"\n[Debug] Page HTML saved: {html_path}")
    print(f"[Debug] Page title: {page.title()}")

    # Check if logged in
    try:
        is_logged_in = page.locator("a[href*='feed']").count() > 0 or "feed" in page.url
        print(f"[Debug] Logged in: {is_logged_in}")
        print(f"[Debug] Current URL: {page.url}")
    except Exception as e:
        print(f"[Debug] Could not check login status: {e}")

    browser.close()
    print("\n[Debug] Done. Check the screenshot and HTML file.")
