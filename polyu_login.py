"""
polyu_login.py — Interactive PolyU Job Board manual login helper.

Launches a HEADED Chrome browser (system install, not Playwright bundled),
waits for the user to manually log in (including accepting T&C checkboxes),
then saves cookies after confirmation.

Usage:
    python polyu_login.py
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

COOKIE_PATH = Path(__file__).parent / "cookies" / "polyu.json"
LOGIN_URL = "https://jobboard-sao.polyu.edu.hk/login?callbackUrl=/"


def main():
    print("=" * 60)
    print("  PolyU Job Board — Manual Login Helper")
    print("=" * 60)
    print()
    print("  Steps:")
    print("  1. Your system CHROME browser will open at the login page")
    print("  2. Log in with your NetID and password")
    print("  3. If the Terms & Conditions modal appears:")
    print("     → Manually check the two checkboxes")
    print("     → Click 'Continue to access PolyU Job Board'")
    print("  4. After you see the job listings page,")
    print("     COME BACK HERE and press Enter")
    print("  5. Cookies will be saved and browser will close")
    print()
    print("  " + "-" * 54)
    print("  IMPORTANT: Do NOT close the browser manually.")
    print("  Press Enter in THIS terminal when you're ready.")
    print("  " + "-" * 54)
    print()
    print("  Opening browser", end="", flush=True)
    for _ in range(3):
        time.sleep(0.3)
        print(".", end="", flush=True)
    print("\n")

    with sync_playwright() as pw:
        # Use launch() (NOT launch_persistent_context) because we need
        # ignore_default_args to strip --no-sandbox / --enable-automation
        # which some sites detect as automation.
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
        )
        page = ctx.new_page()

        # Add stealth init script
        page.add_init_script("""
            () => {
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
                Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});
                delete navigator.__proto__.webdriver;
            }
        """)

        # Go to PolyU login page (with error handling)
        print("  [Opening] PolyU login page...")
        try:
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30_000)
            print(f"  [OK] Page opened: {page.url}")
        except Exception as e:
            print(f"  [ERROR] Navigation failed: {e}")
            print(f"  Current URL: {page.url}")
            input("  Press Enter to close browser and exit...")
            browser.close()
            return
        log.info(f"Browser opened: {page.url}")
        print(f"  [Opened] {page.url}")
        print()

        # Wait for user to press Enter
        input("  [Waiting] Press Enter HERE after you've logged in and see the job listings...\n")

        # Save cookies
        print("\n  [Saving] Saving cookies...")
        try:
            COOKIE_PATH.parent.mkdir(parents=True, exist_ok=True)
            ctx.storage_state(path=str(COOKIE_PATH))
            print(f"  [Saved] Cookies saved to: {COOKIE_PATH}")
            print("  You can now run the scraper — it will reuse this session")
            print("  and automatically skip the Terms & Conditions modal.")
        except Exception as e:
            log.error(f"Failed to save cookies: {e}")
            print(f"  [Error] Failed to save cookies: {e}")

        try:
            browser.close()
            print("  [Done] Browser closed. You're all set!")
        except Exception:
            print("  [Done] Cookies saved. You can close the browser if still open.")

        print()
        print("=" * 60)


if __name__ == "__main__":
    main()
