"""
Diagnostic script v3: Full SSO login flow + snapshot real job listing page.
"""
import json, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config import config
from playwright.sync_api import sync_playwright

OUT_DIR = Path(__file__).parent / "debug_snapshots"
OUT_DIR.mkdir(exist_ok=True)
COOKIE_FILE = Path(__file__).parent / "cookies" / "polyu_cookies.json"

def main():
    net_id = config.polyu_net_id
    password = config.polyu_password

    if not net_id or not password:
        print("[DIAG] ERROR: Set POLYU_NET_ID and POLYU_PASSWORD in .env")
        return

    with sync_playwright() as p:
        # Use non-headless so we can see and approve if MFA needed
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
        )
        page = context.new_page()

        # Step 1: Homepage -> redirects to login
        print("[DIAG] Step 1: Homepage")
        page.goto("https://jobboard-sao.polyu.edu.hk/", wait_until="networkidle", timeout=30_000)
        page.wait_for_timeout(2000)
        print(f"  URL: {page.url}")
        print(f"  Title: {page.title()}")

        # Step 2: Click SSO login
        print("\n[DIAG] Step 2: Click SSO login")
        sso_link = page.query_selector("a[href*='saml2']")
        if not sso_link:
            sso_link = page.query_selector("a:has-text('Log in as')")
        if sso_link:
            sso_link.click()
            page.wait_for_timeout(5000)
            print(f"  After SSO click URL: {page.url}")
            print(f"  Title: {page.title()}")
        else:
            print("  Could not find SSO link!")
            (OUT_DIR / "login_page.html").write_text(page.content(), encoding="utf-8")

        # Step 3: Fill NetID + password (we're now on PolyU SSO page)
        print("\n[DIAG] Step 3: Fill credentials on SSO page")
        page.wait_for_timeout(3000)

        # Save SSO page for analysis
        (OUT_DIR / "sso_page.html").write_text(page.content(), encoding="utf-8")
        page.screenshot(path=str(OUT_DIR / "sso_page.png"), full_page=True)
        print(f"  SSO page saved. Title: {page.title()}")

        # Find all inputs on SSO page
        inputs = page.query_selector_all("input")
        print(f"  Found {len(inputs)} input fields:")
        for inp in inputs:
            name = inp.get_attribute("name") or ""
            id_ = inp.get_attribute("id") or ""
            type_ = inp.get_attribute("type") or ""
            placeholder = inp.get_attribute("placeholder") or ""
            label = inp.get_attribute("aria-label") or ""
            print(f"    name={name}, id={id_}, type={type_}, placeholder={placeholder}, aria-label={label}")

        # Try common SSO field patterns
        # PolyU typically uses iptNetID / iptPassword on their SSO portal
        username_selectors = [
            "input[name='iptNetID']",
            "input[name='username']",
            "input[name='UserName']",
            "input#userNameInput",
            "input#i0116",  # Microsoft login
            "input[type='email']",
            "input[name='loginfmt']",  # MS login
        ]
        password_selectors = [
            "input[name='iptPassword']",
            "input[name='password']",
            "input[name='Password']",
            "input#passwordInput",
            "input#i0118",  # Microsoft login
            "input[type='password']",
            "input[name='passwd']",  # MS login
        ]

        username_el = None
        for sel in username_selectors:
            username_el = page.query_selector(sel)
            if username_el:
                print(f"  Found username field: {sel}")
                break

        password_el = None
        for sel in password_selectors:
            password_el = page.query_selector(sel)
            if password_el:
                print(f"  Found password field: {sel}")
                break

        if username_el:
            username_el.fill(net_id)
            print(f"  Filled NetID: {net_id}")
        else:
            print("  WARNING: Could not find username field!")

        if password_el:
            password_el.fill(password)
            print("  Filled password")
        else:
            print("  WARNING: Could not find password field!")

        # Step 4: Click submit
        print("\n[DIAG] Step 4: Submit login")
        submit_selectors = [
            "button[type='submit']",
            "input[type='submit']",
            "input#idSIButton9",  # MS login
            "button:has-text('Sign in')",
            "button:has-text('登入')",
            "button:has-text('Login')",
            "input[name='submit']",
        ]
        submit_btn = None
        for sel in submit_selectors:
            submit_btn = page.query_selector(sel)
            if submit_btn:
                print(f"  Found submit: {sel}")
                break

        if submit_btn:
            submit_btn.click()
            print("  Clicked submit, waiting for redirect...")
        else:
            print("  WARNING: No submit button found, pressing Enter...")
            page.keyboard.press("Enter")

        # Wait for redirect back to jobboard
        page.wait_for_timeout(10000)
        print(f"  After submit URL: {page.url}")
        print(f"  Title: {page.title()}")

        # Check for MFA
        if "mfa" in page.url.lower() or "verify" in page.url.lower() or "authenticator" in page.url.lower():
            print("\n[DIAG] === MFA REQUIRED! ===")
            print(f"  URL: {page.url}")
            (OUT_DIR / "mfa_page.html").write_text(page.content(), encoding="utf-8")
            page.screenshot(path=str(OUT_DIR / "mfa_page.png"), full_page=True)
            print("  Saved MFA page. Waiting 60s for manual MFA...")
            time.sleep(60)
            print(f"  After waiting URL: {page.url}")

        # Step 5: Snapshot job listing page
        print("\n[DIAG] Step 5: Snapshot job listing")
        page.wait_for_timeout(3000)
        current_url = page.url
        print(f"  Final URL: {current_url}")
        print(f"  Title: {page.title()}")

        (OUT_DIR / "after_login.html").write_text(page.content(), encoding="utf-8")
        page.screenshot(path=str(OUT_DIR / "after_login.png"), full_page=True)
        print("  Saved after_login.html + screenshot")

        # Check if we got to the job listing
        if "login" not in current_url:
            print("\n[DIAG] === LOGIN SUCCESSFUL ===")
            # Save cookies
            cookies = context.cookies()
            COOKIE_FILE.parent.mkdir(exist_ok=True)
            COOKIE_FILE.write_text(json.dumps(cookies, indent=2), encoding="utf-8")
            print(f"  Saved {len(cookies)} cookies")

            # Analyze links on job listing page
            all_links = page.query_selector_all("a[href]")
            job_links = []
            for link in all_links:
                href = link.get_attribute("href") or ""
                text = link.inner_text().strip()
                if text and len(text) > 3:
                    job_links.append((href, text[:80]))
            print(f"\n  Found {len(job_links)} meaningful links:")
            for href, text in job_links[:30]:
                print(f"    href='{href}' | text='{text}'")

            # Try to find job cards/rows
            print("\n  --- Job card structure ---")
            # Dump a snippet of HTML around potential job entries
            body_text = page.inner_text("body")[:3000]
            print(f"  Body text (first 3000 chars):\n{body_text}")

        else:
            print(f"\n[DIAG] === LOGIN FAILED (still on login page) ===")
            body_text = page.inner_text("body")[:500]
            print(f"  Body: {body_text}")

        print("\n[DIAG] Keeping browser open for 10s. Examine and close manually.")
        time.sleep(10)
        browser.close()


if __name__ == "__main__":
    main()
