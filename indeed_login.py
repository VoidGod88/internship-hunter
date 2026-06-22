"""
indeed_login.py — Interactive Indeed manual login helper.

Launches a HEADED Chrome browser (system install, not Playwright bundled),
waits for the user to manually log in, then saves cookies to
`cookies/indeed.json` for reuse by the scraper.

Usage:
    python indeed_login.py
    # Or triggered from the Web UI button
"""
import time
import logging
from pathlib import Path
from playwright.sync_api import sync_playwright

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("indeed_login")

COOKIE_PATH = Path(__file__).parent / "cookies" / "indeed.json"


def main():
    print("=" * 60)
    print("  Indeed Manual Login Helper")
    print("=" * 60)
    print()
    print("  Instructions:")
    print("  1. Your system CHROME browser will open")
    print("  2. Log in manually (Google/Apple/email all supported)")
    print("  3. Complete any CAPTCHA / Cloudflare challenge if prompted")
    print("  4. After successful login, come back to this terminal")
    print("  5. Press Enter to save cookies")
    print()
    print("  " + "=" * 56)
    print()

    with sync_playwright() as pw:
        # Use launch() (NOT launch_persistent_context) because we need
        # ignore_default_args to strip --no-sandbox / --enable-automation
        # which Cloudflare detects as automation.
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

        # Go to Indeed HK login page (with error handling)
        print("  [Opening] Indeed HK login page...")
        try:
            page.goto("https://hk.indeed.com/account/login", timeout=30_000, wait_until="domcontentloaded")
            print(f"  [OK] Page opened: {page.url}")
        except Exception as e:
            print(f"  [ERROR] Navigation failed: {e}")
            print(f"  Current URL: {page.url}")
            input("  Press Enter to close browser and exit...")
            browser.close()
            return
        except Exception:
            pass

        time.sleep(3)

        # Check for Cloudflare / security challenges
        page_text = ""
        try:
            page_text = page.content().lower()
        except Exception:
            pass

        if "cloudflare" in page_text or "additional verification" in page_text or "just a moment" in page.title():
            print("  [!] Cloudflare verification detected!")
            print("      Please solve the CAPTCHA/verification in the browser.")
            print("      After passing, click 'Return home' or wait to be redirected.")
            print()
        elif "/login" not in page.url and "account/login" not in page.url:
            print(f"  [OK] Navigated to: {page.url}")
        else:
            print(f"  [OK] Login page loaded: {page.url}")

        print()
        print("  >>> Please log in to Indeed in the browser window <<<")
        print("  (If you see a Cloudflare challenge, solve it FIRST, then log in)")
        print()
        print("  " + "-" * 56)

        # Wait for user to press Enter
        input("  Press Enter after you have logged in... ")

        # Verify login
        print()
        print("  Verifying login status (navigating to profile page)...")
        try:
            page.goto("https://profile.indeed.com/", timeout=15_000, wait_until="domcontentloaded")
            time.sleep(3)
        except Exception as e:
            log.warning(f"Navigation after login failed: {e}")

        current_url = page.url
        log.info(f"URL after verification: {current_url}")

        if "/login" in current_url or "/signin" in current_url:
            print()
            print("  [Warning] You do NOT appear to be logged in.")
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
        print("  The scraper will now load these cookies automatically.")
        print()

        browser.close()
        print("  [Done] Browser closed. You're all set!")
        print("=" * 60)


if __name__ == "__main__":
    main()
