"""
scrapers/base.py — Shared scraper utilities.
"""
from models import Job


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
    def init_page(browser):
        """Create a new page with realistic browser context."""
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 900},
            locale="en-US",
        )
        return ctx.new_page()
