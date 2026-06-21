"""
linkedin_login.py — Interactive LinkedIn manual login helper.

Launches a HEADED Playwright browser, waits for the user to manually
log in (including solving Cloudflare challenges), then saves cookies
after user confirms by pressing Enter.

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


def main():
    print("=" * 60)
    print("  LinkedIn Manual Login Helper")
    print("=" * 60)
    print()
    print("  Instructions:")
    print("  1. A browser window will open")
    print("  2. Manually log in to LinkedIn")
    print("  3. Complete any Cloudflare challenges if prompted")
    print("  4. After successful login, come back to this terminal")
    print("  5. Press Enter to save cookies")
    print()
    print("  " + "=" * 56)
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
        # Note: networkidle will time out because LinkedIn has persistent analytics/ws connections
        page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded", timeout=30_000)
        print(f"  [Opened] Browser opened: {page.url}")

        # Google/Apple/Microsoft login buttons are loaded inside async iframes.
        # Wait up to 15s for the Google sign-in iframe to appear.
        google_loaded = False
        try:
            page.wait_for_selector(
                'iframe[src*="accounts.google.com"], div#credential_picker_container iframe',
                timeout=15_000
            )
            google_loaded = True
            print("  [OK] Google sign-in button detected")
        except Exception:
            log.warning("Google sign-in iframe did not appear within 15s")
            print("  [Note] Google sign-in button not detected (iframe may be slow/blocked)")

        # Extra wait for other social buttons (Apple, Microsoft)
        time.sleep(3)
        print()
        print("  >>> Please log in to LinkedIn in the browser window <<<")
        if not google_loaded:
            print("  Google login button may not be visible — use email+password instead")
        print("  Social login buttons (Google/Apple/Microsoft) are BELOW the email form")
        print()
        print("  " + "-" * 56)

        # Wait for user to press Enter
        input("  Press Enter after you have logged in... ")

        # Verify login was successful — navigate to feed to confirm
        print()
        print("  Verifying login status (navigating to feed)...")
        try:
            page.goto("https://www.linkedin.com/feed/", timeout=15_000, wait_until="domcontentloaded")
            time.sleep(3)
        except Exception as e:
            log.warning(f"Navigation to feed failed: {e}")

        current_url = page.url
        log.info(f"URL after verification: {current_url}")

        # Check if still on login page (not logged in)
        if "/login" in current_url or "/checkpoint" in current_url or "/authwall" in current_url:
            print()
            print("  [Warning] You are NOT logged in.")
            print(f"  Current URL: {current_url}")
            print("  Please make sure you logged in in the PLAYWRIGHT browser window.")
            confirm = input("  Save cookies anyway? (y/n): ")
            if confirm.strip().lower() != "y":
                print("  Aborted. Cookies NOT saved.")
                browser.close()
                return
        else:
            print(f"  [OK] Logged in! URL: {current_url}")

        # Give a moment for session cookies to settle
        print("  Waiting 5 seconds for cookies to settle...\n")
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
