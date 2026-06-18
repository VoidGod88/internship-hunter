"""
polyu_login.py — Interactive PolyU Job Board manual login helper.

Launches a HEADED Playwright browser, waits for the user to manually
log in (including accepting the Terms & Conditions checkboxes),
then saves cookies after user confirms by pressing Enter in the terminal.

After running this once, the main scraper will reuse the saved cookies
and automatically skip the T&C modal.

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

# Global flag for user confirmation
_user_confirmed = False


def _check_login_success(page) -> bool:
    """Quick check: is user likely logged in? (Used for status display only)"""
    url = page.url
    if "/login" in url:
        return False
    # Check for common post-login URLs
    if "/jobs" in url or url.rstrip("/") == "https://jobboard-sao.polyu.edu.hk":
        return True
    # Check for logout/user menu (indicates logged-in state)
    try:
        logout = page.query_selector("text=/logout/i, text=/sign out/i, [href*='logout']")
        if logout:
            return True
    except Exception:
        pass
    return False


def main():
    global _user_confirmed

    print("=" * 60)
    print("  PolyU Job Board — Manual Login Helper")
    print("=" * 60)
    print()
    print("  Steps:")
    print("  1. A browser window will open at the login page")
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
        browser = pw.chromium.launch(headless=False)
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

        # Go to PolyU login page
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30_000)
        log.info(f"Browser opened: {page.url}")
        print(f"  [Opened] {page.url}")
        print()

        # Start a background thread to wait for Enter key
        import threading
        input_received = threading.Event()

        def _wait_for_enter():
            global _user_confirmed
            try:
                input("  [Waiting] Press Enter HERE after you've logged in and see the job listings...\n")
                _user_confirmed = True
                input_received.set()
            except (EOFError, KeyboardInterrupt):
                input_received.set()

        waiter = threading.Thread(target=_wait_for_enter, daemon=True)
        waiter.start()

        # While waiting, periodically check if browser is still open
        browser_gone = False
        while not input_received.is_set():
            time.sleep(1)
            try:
                # Check if browser is still open
                _ = page.url  # This will raise if browser is closed
            except Exception:
                print("\n  [Notice] Browser was closed manually.")
                browser_gone = True
                break

        if browser_gone:
            # Try to save cookies anyway (if context still accessible)
            try:
                COOKIE_PATH.parent.mkdir(parents=True, exist_ok=True)
                ctx.storage_state(path=str(COOKIE_PATH))
                print(f"  [Saved] Cookies saved to: {COOKIE_PATH}")
                print("  You can now run the scraper — it will reuse this session.")
            except Exception as e:
                print(f"  [Error] Could not save cookies: {e}")
                print("  Please run this script again and press Enter (don't close browser manually).")
            return

        if not _user_confirmed:
            return

        # User pressed Enter — save cookies
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

        # Close browser
        try:
            browser.close()
            print("  [Done] Browser closed. You're all set!")
        except Exception:
            print("  [Done] Cookies saved. You can close the browser if still open.")

        print()
        print("=" * 60)


if __name__ == "__main__":
    main()
