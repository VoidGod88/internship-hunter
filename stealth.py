"""
stealth.py — Enhanced anti-detection for Playwright.

Apply comprehensive stealth techniques to mask automation fingerprints.
Usage:
    from stealth import Stealth
    Stealth.apply(page)
"""

import random
import logging

log = logging.getLogger("hunter")

# ── Realistic User-Agent pool (rotate to avoid pattern detection) ──
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]

# ── Comprehensive stealth JS (masks ~20 automation fingerprints) ──
STEALTH_JS = """
() => {
  // 1. Mask navigator.webdriver
  Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

  // 2. Mock plugins (real browsers have plugins)
  Object.defineProperty(navigator, 'plugins', {
    get: () => [1, 2, 3, 4, 5],
  });

  // 3. Mock languages
  Object.defineProperty(navigator, 'languages', {
    get: () => ['en-US', 'en', 'zh-HK', 'zh'],
  });

  // 4. Mask Chrome runtime (Playwright sets this)
  if (window.chrome && window.chrome.runtime) {
    delete window.chrome.runtime;
  }

  // 5. Canvas fingerprint noise
  const origGetContext = HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.getContext = function(...a) {
    const c = origGetContext.apply(this, a);
    if (a && a[0] === '2d') {
      const origFillText = c.fillText.bind(c);
      c.fillText = function(...args) {
        args[1] += Math.random() * 0.05 - 0.025;  // Tiny offset noise
        return origFillText.apply(this, args);
      };
    }
    return c;
  };

  // 6. Mock permissions (real browsers don't have "denied" as default)
  const origQuery = window.navigator.permissions.query;
  window.navigator.permissions.query = (parameters) => (
    parameters.name === 'notifications'
      ? Promise.resolve({ state: Notification.permission })
      : origQuery(parameters)
  );

  // 7. Mask Playwright-specific properties
  delete navigator.__proto__.webdriver;
  if (navigator.webdriver !== undefined) {
    delete navigator.webdriver;
  }

  // 8. Mock screen properties (consistent with viewport)
  Object.defineProperty(screen, 'width', { get: () => window.innerWidth });
  Object.defineProperty(screen, 'height', { get: () => window.innerHeight });
  Object.defineProperty(screen, 'availWidth', { get: () => window.innerWidth });
  Object.defineProperty(screen, 'availHeight', { get: () => window.innerHeight });

  // 9. Hide automation from iframe contentWindow
  Object.defineProperty(HTMLIFrameElement.prototype, 'contentWindow', {
    get() { return window; }
  });

  // 10. Mock Notification permission
  if (!('Notification' in window)) {
    window.Notification = { permission: 'default' };
  }

  // 11. Remove Playwright trace (if present)
  const pwSymbol = Symbol.for('playwright');
  if (window[pwSymbol]) {
    delete window[pwSymbol];
  }

  // 12. Mock connection properties (avoid "effectiveType" mismatch)
  if (navigator.connection) {
    Object.defineProperty(navigator.connection, 'effectiveType', {
      get: () => '4g',
    });
  }

  // 13. Override getBoundingClientRect (anti-fingerprinting)
  const origGetBCR = Element.prototype.getBoundingClientRect;
  Element.prototype.getBoundingClientRect = function() {
    const rect = origGetBCR.call(this);
    return {
      x: rect.x + Math.random() * 0.01,
      y: rect.y + Math.random() * 0.01,
      width: rect.width,
      height: rect.height,
      top: rect.top,
      right: rect.right,
      bottom: rect.bottom,
      left: rect.left,
    };
  };

  // 14. Mock AudioContext fingerprint (optional, can break sites)
  // (Disabled by default to avoid breaking sites)

  // 15. Consistent timezone (already set in context, but double-check)
  // (Handled by Playwright's timezone_id)

  console.debug('[Stealth] Applied 13 masks');
}
"""

# ── Mouse movement simulation (human-like) ──
HUMANIZE_MOUSE_JS = """
() => {
  // Override mouse event properties to look more human
  const origDispatch = EventTarget.prototype.dispatchEvent;
  EventTarget.prototype.dispatchEvent = function(e) {
    if (e instanceof MouseEvent && !e.isTrusted) {
      Object.defineProperty(e, 'isTrusted', { get: () => true });
    }
    return origDispatch.call(this, e);
  };
}
"""


class Stealth:
    """Apply anti-detection techniques to Playwright pages."""

    @staticmethod
    def get_random_ua() -> str:
        """Return a random realistic User-Agent."""
        return random.choice(USER_AGENTS)

    @staticmethod
    def apply(page) -> None:
        """
        Apply all stealth techniques to a Playwright page.
        Call this AFTER creating the page, but BEFORE navigating to target site.
        """
        # Apply comprehensive stealth JS
        page.add_init_script(STEALTH_JS)
        log.debug("[Stealth] Applied comprehensive stealth JS (13 masks)")

    @staticmethod
    def apply_light(page) -> None:
        """
        Apply only the essential stealth techniques (faster, less likely to break sites).
        Use this for sites that break with heavy stealth.
        """
        page.add_init_script("""
        () => {
          Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
          Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
          Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en','zh-HK']});
        }
        """)
        log.debug("[Stealth] Applied light stealth JS (3 masks)")

    @staticmethod
    def random_delay(min_sec: float = 1.5, max_sec: float = 4.0) -> float:
        """
        Return a random delay duration (caller should use time.sleep()).
        Returns: actual delay used (for logging)
        """
        import time
        delay = random.uniform(min_sec, max_sec)
        time.sleep(delay)
        return delay

    @staticmethod
    def human_scroll(page, scroll_pixels: int = 500) -> None:
        """
        Simulate human-like scrolling (gradual, with small random delays).
        Call this instead of instant scrollTo().
        """
        import time
        steps = random.randint(3, 7)
        step_pixels = scroll_pixels // steps
        for _ in range(steps):
            page.evaluate(f"() => window.scrollBy(0, {step_pixels})")
            time.sleep(random.uniform(0.1, 0.3))
        # Small chance to scroll back up a bit (human behavior)
        if random.random() < 0.2:
            page.evaluate(f"() => window.scrollBy(0, {-random.randint(50, 150)})")
            time.sleep(random.uniform(0.2, 0.5))

    @staticmethod
    def human_click(page, selector: str) -> None:
        """
        Simulate human-like clicking (move mouse, pause, then click).
        Requires the selector to be visible.
        """
        import time
        # Hover first (simulates mouse movement)
        page.hover(selector)
        time.sleep(random.uniform(0.3, 0.8))
        # Click
        page.click(selector)
        time.sleep(random.uniform(0.5, 1.2))

    @staticmethod
    def random_mouse_jitter(page) -> None:
        """
        Occasionally move mouse to a random position (simulates human fidgeting).
        Call this occasionally during long scraping sessions.
        """
        import time
        if random.random() < 0.3:  # 30% chance
            x = random.randint(100, 800)
            y = random.randint(100, 600)
            page.mouse.move(x, y)
            time.sleep(random.uniform(0.1, 0.4))
