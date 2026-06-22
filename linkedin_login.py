"""
linkedin_login.py — Interactive LinkedIn manual login helper.

Launches a HEADED Chrome browser (system install, not Playwright bundled),
waits for the user to manually log in, then saves cookies after confirmation.

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
    print("  1. Your system CHROME browser will open")
    print("  2. Manually log in to LinkedIn")
    print("  3. Complete any Cloudflare challenges if prompted")
    print("  4. After successful login, come back to this terminal")
    print("  5. Press Enter to save cookies")
    print()
    print("  " + "=" * 56)
    print()

    with sync_playwright() as pw:
        # Use launch() (NOT launch_persistent_context) because we need
        # ignore_default_args to strip --no-sandbox / --enable-automation
        # which Google OAuth and security systems detect.
        browser = pw.chromium.launch(
            headless=False,
            channel="chrome",
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
            ],
            ignore_default_args=["--enable-automation", "--no-sandbox"],
        )
        ctx = browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="en-US",
            timezone_id="Asia/Hong_Kong",
        )
        page = ctx.new_page()

        # Add stealth init script (replaces the args approach)
        page.add_init_script("""
            () => {
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
                Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});
                delete navigator.__proto__.webdriver;
            }
        """)

        # Navigate to LinkedIn login with explicit error handling
        print("  [Opening] Navigating to LinkedIn login page...")
        try:
            page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded", timeout=30_000)
            print(f"  [OK] Page opened: {page.url}")
        except Exception as e:
            print(f"  [ERROR] Navigation failed: {e}")
            print(f"  Current URL: {page.url}")
            input("  Press Enter to close browser and exit...")
            browser.close()
            return

        # Wait for login form to be ready
        try:
            page.wait_for_selector('input[name="session_key"], input[type="email"], input[type="text"]', timeout=15_000)
            print("  [OK] Login form loaded!")
        except Exception:
            print("  [Warning] Login form not detected yet (page may still be loading)")

        # Scroll down to find social login buttons (they might be below the fold)
        print("  [Scrolling] Checking if social login buttons are below the fold...")
        page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
        time.sleep(2)

        # Check what login options are available
        page_text = page.content().lower()
        has_google = "google" in page_text and ("sign in" in page_text or "continue" in page_text)
        has_apple = "apple" in page_text and ("sign in" in page_text or "continue" in page_text)

        print()
        if has_google or has_apple:
            print("  [OK] Social login buttons detected!")
            if has_google:
                print("       → You can click 'Sign in with Google'")
                print("       ⚠️  NOTE: Google may block automated browsers.")
                print("          If Google rejects you, use EMAIL + PASSWORD below.")
            if has_apple:
                print("       → 'Sign in with Apple' should work")
            print()
        else:
            print("  [Note] Social login buttons (Google/Apple) not detected.")
            print("       This is normal — LinkedIn may hide them for fresh profiles.")
        print()
        print("  👉 RECOMMENDED: Log in with EMAIL + PASSWORD:")
        print("     1. Enter your email address")
        print("     2. Click 'Next'")
        print("     3. Enter your password")
        print("     4. Complete any verification if asked")
        print("     After logging in once, cookies are saved for future use!")
        print()
        print("  >>> Please log in to LinkedIn in the browser window <<<")
        print()
        print("  " + "-" * 56)

        # Wait for user to press Enter
        input("  Press Enter after you have logged in... ")

        # Verify login was successful
        print()
        print("  Verifying login status (navigating to feed)...")
        try:
            page.goto("https://www.linkedin.com/feed/", timeout=15_000, wait_until="domcontentloaded")
            time.sleep(3)
        except Exception as e:
            log.warning(f"Navigation to feed failed: {e}")

        current_url = page.url
        log.info(f"URL after verification: {current_url}")

        if "/login" in current_url or "/checkpoint" in current_url or "/authwall" in current_url:
            print()
            print("  [Warning] You are NOT logged in.")
            print(f"  Current URL: {current_url}")
            confirm = input("  Save cookies anyway? (y/n): ")
            if confirm.strip().lower() != "y":
                print("  Aborted. Cookies NOT saved.")
                browser.close()
                return
        else:
            print(f"  [OK] Logged in! URL: {current_url}")

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
