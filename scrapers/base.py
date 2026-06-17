"""
scrapers/base.py — Shared scraper utilities.
"""
import logging
import os

from models import Job

log = logging.getLogger("hunter")


class BaseScraper:
    """Shared utilities for all Playwright-based scrapers."""

    @staticmethod
    def extract_cards(page, selectors: list[str]) -> list:
        """Try multiple selectors, return the first non-empty list."""
        for sel in selectors:
            cards = page.query_selector_all(sel)
            if cards:
                return cards
        return []

    @staticmethod
    def safe_extract(page, selector: str, fallback: str = "") -> str:
        try:
            el = page.query_selector(selector)
            return el.inner_text().strip() if el else fallback
        except Exception:
            return fallback

    @staticmethod
    def make_job(title: str, company: str, location: str, url: str, source: str) -> Job:
        return Job(
            title=title,
            company=company,
            location=location,
            url=url,
            source=source,
        )

    @staticmethod
    def init_page(browser, load_cookies_file: str = None):
        """Create a new page with realistic browser context + stealth JS.

        load_cookies_file: path to a JSON cookies file saved by
            context.storage_state(path="..."). If given and the file exists,
            the context will be created with those saved cookies (i.e. logged-in
            session, no need to solve Cloudflare again).
        """
        # Stealth JS: mask the most common automation fingerprints
        STEALTH_JS = """
        () => {
          Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
          Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
          Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en','zh-HK']});
          const origGetContext = HTMLCanvasElement.prototype.getContext;
          HTMLCanvasElement.prototype.getContext = function(...a) {
            const c = origGetContext.apply(this, a);
            if (a && a[0]==='2d') c.shadowBlur = 0;
            return c;
          };
        }
        """
        launch_kwargs = dict(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 900},
            locale="en-US",
            timezone_id="Asia/Hong_Kong",
        )
        # If we have saved storage state (cookies + localStorage), reuse it
        if load_cookies_file and os.path.exists(load_cookies_file):
            launch_kwargs["storage_state"] = load_cookies_file
            log.info(f"[Stealth] Loading saved cookies from {load_cookies_file}")

        ctx = browser.new_context(**launch_kwargs)
        page = ctx.new_page()
        page.add_init_script(STEALTH_JS)
        # Stash ctx on page so callers can save cookies later
        page._hunter_ctx = ctx
        return page
