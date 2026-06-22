"""
indeed_login.py — Interactive Indeed manual login helper.

Launches a headed Chrome browser, waits for user to manually log in,
then saves cookies to `cookies/indeed.json` for reuse by the scraper.

Usage:
    python indeed_login.py
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
    print("  1. A Chrome browser window will open")
    print("  2. Log in manually (Google/Apple/email)")
    print("  3. After login, verify you see your name in the top-right")
    print("  4. Come back to this terminal and press Enter")
    print()
    print("  " + "=" * 56)
    print()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
            ],
        )
        ctx = browser.new_context(
            viewport={"width": 1440, "height": 900},
            locale="en-US",
            timezone_id="Asia/Hong_Kong",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        )
        page = ctx.new_page()

        # Go to Indeed HK login page
        print("  [Opening] Indeed HK login page...")
        try:
            page.goto(
                "https://hk.indeed.com/account/login",
                timeout=60_000,
                wait_until="load",
            )
            print(f"  [OK] Loaded: {page.url}")
        except Exception as e:
            print(f"  [WARN] {e}")
            print(f"  Current URL: {page.url}")

        # Wait for page to settle (Cloudflare / redirects)
        time.sleep(5)
        print(f"  [Status] Current page: {page.title()}")
        print(f"  [Status] URL: {page.url}")

        # Check for Cloudflare
        title_lower = page.title().lower()
        if "just a moment" in title_lower or "attention required" in title_lower:
            print("  [!] Cloudflare / security challenge detected!")
            print("      Please solve the challenge in the browser window.")
            print()

        print()
        print("  >>> Log in to Indeed in the browser <<<")
        print("  (Google, Apple, or email — complete all steps)")
        print()

        # Wait for user
        input("  Press Enter after successful login... ")

        # Verify login by navigating to profile
        print()
        print("  [Verifying] Checking login status...")
        for attempt in range(3):
            try:
                page.goto(
                    "https://profile.indeed.com/",
                    timeout=30_000,
                    wait_until="load",
                )
                time.sleep(3)
                break
            except Exception as e:
                print(f"  [Retry {attempt+1}] {e}")
                time.sleep(2)

        current_url = page.url
        is_logged_in = "/login" not in current_url and "/signin" not in current_url

        if is_logged_in:
            title = page.title()
            print(f"  [OK] Logged in! Page: '{title}'")
            print(f"  [OK] URL: {current_url}")
        else:
            print(f"  [WARN] May not be logged in. URL: {current_url}")
            confirm = input("  Save cookies anyway? (y/n): ")
            if confirm.strip().lower() != "y":
                print("  Aborted.")
                browser.close()
                return

        time.sleep(2)

        # Save cookies
        COOKIE_PATH.parent.mkdir(parents=True, exist_ok=True)
        ctx.storage_state(path=str(COOKIE_PATH))
        print(f"  [Saved] {COOKIE_PATH}")

        browser.close()
        print("  [Done]")
        print("=" * 60)


if __name__ == "__main__":
    main()
