"""
linkedin_login.py — Interactive LinkedIn manual login helper.

Launches a HEADED Playwright browser, waits for the user to manually
log in (including solving Cloudflare challenges), then automatically
detects successful login and saves cookies to cookies/linkedin.json.

Usage:
    python linkedin_login.py
    # Or triggered from the Web UI button
"""
import time
import logging
from pathlib import Path
from playwright.sync_api import sync_playwright

# ── Logging ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("hunter")

COOKIE_PATH = Path(__file__).parent / "cookies" / "linkedin.json"
POLL_INTERVAL = 3       # seconds between login checks
LOGIN_TIMEOUT = 600      # 10 minutes max to log in


def _is_logged_in(page) -> bool:
    """Check if the current page shows a logged-in LinkedIn session."""
    url = page.url
    # Not logged in: still on auth pages
    if any(p in url for p in ["/login", "/checkpoint", "/authwall", "/challenge", "/signup"]):
        return False
    # Logged in: URL contains feed / mynetwork / jobs / notifications / dashboard etc.
    logged_in_urls = ["/feed", "/mynetwork", "/jobs", "/notifications", "/dashboard", "/in/", "/messaging"]
    if any(p in url for p in logged_in_urls):
        return True
    # Check for logged-in UI elements (global nav bar) — more reliable than URL
    try:
        # LinkedIn's global nav has specific data attributes
        nav = page.query_selector("[data-test-global-nav], nav.global-nav, .c-global-nav")
        if nav:
            return True
        # Check for "Start a post" button (only visible when logged in)
        post_btn = page.query_selector("button[aria-label*='Start a post'], [data-control-name='share.post']")
        if post_btn:
            return True
    except Exception:
        pass
    return False


def main():
    print("=" * 60)
    print("  LinkedIn Manual Login Helper")
    print("=" * 60)
    print()
    print("  A browser window will open.")
    print("  1. Manually log in to LinkedIn")
    print("  2. Complete any Cloudflare challenges if prompted")
    print("  3. After successful login, wait 5-10 seconds for auto-detection")
    print("  4. The browser will close automatically after cookies are saved")
    print()
    print("  If auto-detection fails: close the browser manually,")
    print("  and cookies will be saved on next run.")
    print("=" * 60)
    print()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)   # HEADED — you can see the window
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )
        page = ctx.new_page()

        # Stealth JS (same as BaseScraper)
        page.add_init_script("""
            () => {
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
                Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});
            }
        """)

        # Go to LinkedIn login page
        page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded", timeout=30_000)
        log.info(f"Browser opened: {page.url}")
        print(f"  [Opened] {page.url}")
        print("  ... waiting for you to log in ...")
        print("  (Check the browser window — you may need to solve a Cloudflare challenge)")
        print()

        start = time.time()
        logged_in = False
        last_url = ""

        while time.time() - start < LOGIN_TIMEOUT:
            time.sleep(POLL_INTERVAL)
            try:
                current_url = page.url
                # Log URL changes for debugging
                if current_url != last_url:
                    log.info(f"URL changed: {current_url}")
                    last_url = current_url
                if _is_logged_in(page):
                    print(f"\n  [Detected] Logged in! URL={page.url}")
                    log.info("LinkedIn login detected — saving cookies")
                    logged_in = True
                    break
            except Exception as e:
                log.debug(f"Login check error: {e}")
            # Still waiting
            elapsed = int(time.time() - start)
            print(f"  ... waiting ({elapsed}s / {LOGIN_TIMEOUT}s) — URL: {page.url[:80]} ...", end="\r")

        if not logged_in:
            print("\n\n  [Timeout] Login was not detected within 10 minutes.")
            print("  Please try again, or manually log in and THEN run the scraper.")
            print("  (The scraper will try to use any existing session cookies.)\n")
            browser.close()
            return

        # Give a moment for session cookies to settle
        print("\n  Logging in detected! Waiting 5 seconds for cookies to settle...\n")
        time.sleep(5)

        # Save cookies + localStorage + sessionStorage
        COOKIE_PATH.parent.mkdir(parents=True, exist_ok=True)
        ctx.storage_state(path=str(COOKIE_PATH))
        print(f"  [Saved] Cookies saved to: {COOKIE_PATH}")
        print("  You can now run the scraper — it will reuse this session.")
        print()

        browser.close()
        print("  [Done] Browser closed. You're all set!")
        print("=" * 60)


if __name__ == "__main__":
    main()
