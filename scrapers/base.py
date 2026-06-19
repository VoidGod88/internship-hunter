"""
scrapers/base.py — Shared scraper utilities.
"""
import logging
import os
import random
import time

from models import Job
from stealth import Stealth

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
    def init_page(browser, load_cookies_file: str = None, use_stealth: bool = True):
        """
        Create a new page with realistic browser context + stealth JS.

        load_cookies_file: path to a JSON cookies file saved by
            context.storage_state(path="..."). If given and the file exists,
            the context will be created with those saved cookies (i.e. logged-in
            session, no need to solve Cloudflare again).
        use_stealth: if True, apply comprehensive anti-detection techniques.
        """
        # Pick a random realistic User-Agent
        user_agent = Stealth.get_random_ua()

        launch_kwargs = dict(
            user_agent=user_agent,
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

        # Apply stealth techniques
        if use_stealth:
            Stealth.apply(page)
            log.info(f"[Stealth] Applied (UA: {user_agent[:50]}...)")

        # Stash ctx on page so callers can save cookies later
        page._hunter_ctx = ctx
        return page

    @staticmethod
    def human_delay(min_sec: float = 1.5, max_sec: float = 4.0) -> float:
        """
        Sleep for a random duration (simulates human thinking time).
        Returns: actual delay used (for logging)
        """
        delay = random.uniform(min_sec, max_sec)
        time.sleep(delay)
        return delay
