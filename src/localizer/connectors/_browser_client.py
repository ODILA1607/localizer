"""Playwright-based HTTP client — used for Cloudflare-protected sources.

The default `HTTPClient` (curl_cffi) is fine for Immoscoop and Immovlan
but cannot solve Cloudflare's JS-challenge that Zimmo and Immoweb gate
their pages with. `PlaywrightHTTPClient` runs a real Chromium under the
hood: the JS challenge runs, the cookie ends up in our jar, and from
then on every page on that host loads normally.

Trade-offs the runner accepts when picking this client:
  - Slow. Each `goto()` is 2-10s vs 0.5-1s for curl_cffi.
  - Heavy: Chromium needs to be installed once via
    `playwright install chromium` (~150MB).
  - Same `.get(url) -> RawListing` shape as `HTTPClient`, so connectors
    don't care which one they're handed.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from localizer.config import HTTP_TIMEOUT_SECONDS, OPERATOR_CONTACT
from localizer.connectors.base import (
    RateLimiter,
    RawListing,
    RobotsBlockedError,
    RobotsCache,
)

if TYPE_CHECKING:
    from playwright.sync_api import Browser, BrowserContext, Playwright


log = logging.getLogger(__name__)


class BrowserUnavailableError(RuntimeError):
    """Raised when Chromium is not installed or fails to start."""


class PlaywrightHTTPClient:
    """Headless Chromium acting as an HTTP client.

    Same surface as `HTTPClient` (`.get()`, `.close()`, context-manager).
    One Browser + one BrowserContext are reused across requests so that
    Cloudflare's clearance cookies persist for the session.
    """

    def __init__(
        self,
        *,
        rate_limiter: RateLimiter | None = None,
        robots: RobotsCache | None = None,
        timeout_seconds: float = HTTP_TIMEOUT_SECONDS,
    ) -> None:
        # Importing inside __init__ keeps `import localizer` free of the
        # heavyweight Playwright dep when no browser-required connector
        # is enabled.
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - dep missing in tests
            raise BrowserUnavailableError(f"Playwright not installed: {exc}") from exc

        self._rate_limiter = rate_limiter or RateLimiter()
        self._robots = robots or RobotsCache()
        self._timeout_ms = int(timeout_seconds * 1000)
        self._warmed_hosts: set[str] = set()

        try:
            self._pw: Playwright = sync_playwright().start()
            self._browser: Browser = self._pw.chromium.launch(headless=True)
            self._context: BrowserContext = self._browser.new_context(
                locale="nl-BE",
                viewport={"width": 1366, "height": 768},
                extra_http_headers={
                    "From": OPERATOR_CONTACT,
                    "Accept-Language": "nl-BE,nl;q=0.9,en;q=0.7",
                },
            )
            self._context.set_default_timeout(self._timeout_ms)
        except Exception as exc:
            raise BrowserUnavailableError(
                f"Could not launch Chromium ({exc}). Install it with: playwright install chromium"
            ) from exc

    @property
    def rate_limiter(self) -> RateLimiter:
        return self._rate_limiter

    def get(self, url: str) -> RawListing:
        """Navigate Chromium to `url` and return the rendered HTML."""
        parsed = urlparse(url)
        host = parsed.netloc

        if not self._robots.can_fetch(url):
            raise RobotsBlockedError(url)

        self._rate_limiter.acquire(host)
        self._ensure_warmed(parsed.scheme, host)

        log.debug("Browser GET %s", url)
        page = self._context.new_page()
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=self._timeout_ms)
            try:
                page.wait_for_load_state("networkidle", timeout=10_000)
            except Exception as exc:
                log.debug("networkidle timeout for %s: %s", url, exc)
            if response is not None and response.status >= 400:
                raise RuntimeError(f"HTTP {response.status} on {url}")
            content = page.content()
            final_url = page.url
        finally:
            page.close()

        # `page.content()` returns the rendered HTML. For XML responses
        # (sitemap.xml) Chromium wraps them in an HTML viewer; the
        # `_sitemaps` helper strips that wrapper before XML parsing.
        return RawListing(source_url=final_url, body=content.encode("utf-8"))

    def _ensure_warmed(self, scheme: str, host: str) -> None:
        """Visit the homepage once via a real Page so Cloudflare's JS
        challenge runs and `cf_clearance` lands in the context cookie
        jar. Subsequent calls reuse `context.request` for raw HTTP.
        """
        if host in self._warmed_hosts:
            return
        self._warmed_hosts.add(host)  # mark first to avoid loops on failure
        homepage = f"{scheme}://{host}/"
        log.info("Warming up Cloudflare cookies for %s via Chromium", host)
        page = self._context.new_page()
        try:
            page.goto(homepage, wait_until="domcontentloaded", timeout=self._timeout_ms)
            try:
                page.wait_for_load_state("networkidle", timeout=10_000)
            except Exception as exc:
                log.debug("networkidle timeout during warmup for %s: %s", host, exc)
        except Exception as exc:
            log.warning("Browser warmup for %s failed: %s — continuing", host, exc)
        finally:
            page.close()

    def close(self) -> None:
        """Tear the Chromium instance down. Idempotent."""
        try:
            self._context.close()
        except Exception as exc:
            log.debug("BrowserContext close error: %s", exc)
        try:
            self._browser.close()
        except Exception as exc:
            log.debug("Browser close error: %s", exc)
        try:
            self._pw.stop()
        except Exception as exc:
            log.debug("Playwright stop error: %s", exc)

    def __enter__(self) -> PlaywrightHTTPClient:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()
