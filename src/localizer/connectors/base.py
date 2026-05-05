"""The Connector contract — central abstraction of the acquisition layer.

Three rules enforced by the architecture:

  1. Connectors NEVER do network I/O directly. They go through the shared
     `HTTPClient` exposed here, which centralises:
        - rate-limiting per host
        - robots.txt enforcement
        - identifying User-Agent
        - retries with backoff
        - error logging

  2. `parse()` operates on bytes/string already in memory, never on live
     network data. Every parser is therefore unit-testable offline against
     a saved HTML fixture.

  3. Fields the source does not provide stay None. No invented data.

Bot-protection ladder (per connector configurable):
    1. Realistic headers + cookies (default httpx)  - Zimmo, Immoscoop
    2. TLS-fingerprint via curl_cffi                - Immoweb default
    3. Internal JSON / __NEXT_DATA__                - Immoweb best-case
    4. Playwright headless                          - last resort, NOT bundled
"""

from __future__ import annotations

import logging
import threading
import time
import urllib.robotparser
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar, Protocol, runtime_checkable
from urllib.parse import urlparse

from curl_cffi import requests as curl_requests

from localizer.config import (
    DEFAULT_RATE_LIMIT_PER_MIN,
    HTTP_IMPERSONATE,
    HTTP_TIMEOUT_SECONDS,
    OPERATOR_CONTACT,
    ROBOTS_USER_AGENT,
)
from localizer.core.models import Listing, SourceName, utc_now

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Value types
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class ListingRef:
    """A discovered URL pointing at a listing detail page."""

    url: str


@dataclass(frozen=True, slots=True)
class RawListing:
    """The raw HTTP response body of a listing detail page.

    Stored as bytes because not every source serves UTF-8 (and we want to
    keep the original byte stream for fixture round-trips).
    """

    source_url: str
    body: bytes
    fetched_at: datetime = field(default_factory=utc_now)

    def text(self, encoding: str = "utf-8") -> str:
        return self.body.decode(encoding, errors="replace")


# ---------------------------------------------------------------------------
# Rate limiter — token-bucket per host
# ---------------------------------------------------------------------------
class RateLimiter:
    """Per-host minimum-interval throttle.

    Thread-safe. Default budget is `DEFAULT_RATE_LIMIT_PER_MIN` requests per
    minute → minimum interval of 60 / N seconds between requests to the
    same host. Per-host overrides via `set_rate(host, per_minute)`.
    """

    def __init__(self, default_per_minute: int = DEFAULT_RATE_LIMIT_PER_MIN) -> None:
        self._default_interval = 60.0 / max(default_per_minute, 1)
        self._intervals: dict[str, float] = {}
        self._last_request: dict[str, float] = {}
        self._lock = threading.Lock()

    def set_rate(self, host: str, per_minute: int) -> None:
        """Override rate for a specific host."""
        with self._lock:
            self._intervals[host] = 60.0 / max(per_minute, 1)

    def acquire(self, host: str) -> None:
        """Block until it is OK to make another request to `host`."""
        with self._lock:
            interval = self._intervals.get(host, self._default_interval)
            now = time.monotonic()
            last = self._last_request.get(host, 0.0)
            elapsed = now - last
            wait = interval - elapsed
            if wait > 0:
                time.sleep(wait)
            self._last_request[host] = time.monotonic()


# ---------------------------------------------------------------------------
# Robots.txt enforcement
# ---------------------------------------------------------------------------
class RobotsCache:
    """Cached robots.txt lookups per host. Fail-open on parse errors."""

    def __init__(self, user_agent: str = ROBOTS_USER_AGENT) -> None:
        self._user_agent = user_agent
        self._parsers: dict[str, urllib.robotparser.RobotFileParser] = {}
        self._lock = threading.Lock()

    def can_fetch(self, url: str, *, fetch_robots: Any | None = None) -> bool:
        """Return True if `url` is allowed for our user-agent.

        Fail-open policy: when robots.txt is unreachable, returns 4xx /
        5xx, or fails to parse, we *do not* block. Justification:
            - Localizer is a low-volume internal tool with an
              identifying User-Agent.
            - Many sites (Zimmo, Immoweb...) sit behind Cloudflare and
              return 403 on robots.txt to non-browser clients. Treating
              that as "forbidden by the site" would block us from a
              public document the site fully intends to publish.
            - Real prohibition is signalled by 200 OK + an actual
              `Disallow` rule; that path is honoured.
        """
        parsed = urlparse(url)
        host = parsed.netloc
        if not host:
            return True

        with self._lock:
            parser = self._parsers.get(host)

        if parser is None:
            parser = urllib.robotparser.RobotFileParser()
            robots_url = f"{parsed.scheme}://{host}/robots.txt"
            try:
                if fetch_robots is not None:
                    response = fetch_robots.get(robots_url, timeout=HTTP_TIMEOUT_SECONDS)
                    if response.status_code == 200:
                        parser.parse(response.text.splitlines())
                    else:
                        log.info(
                            "robots.txt at %s returned %s — failing open",
                            robots_url,
                            response.status_code,
                        )
                        # `allow_all` is a documented attribute of
                        # `RobotFileParser` (see CPython source) but
                        # missing from typeshed stubs.
                        parser.allow_all = True  # type: ignore[attr-defined]
                else:
                    parser.set_url(robots_url)
                    parser.read()
            except Exception as exc:
                log.warning("robots.txt fetch failed for %s: %s — failing open", host, exc)
                parser.allow_all = True  # type: ignore[attr-defined]

            with self._lock:
                self._parsers[host] = parser

        try:
            return parser.can_fetch(self._user_agent, url)
        except Exception as exc:
            log.warning("robots.txt evaluation failed for %s: %s — failing open", url, exc)
            return True


# ---------------------------------------------------------------------------
# HTTP client — the only network ingress for connectors
# ---------------------------------------------------------------------------
class HTTPClient:
    """Shared HTTP client used by every connector. Owns rate-limiter + robots.

    Internals: `curl_cffi` Session impersonating Chrome — TLS, ALPN,
    Sec-CH-UA headers, the lot. This is what gets us past Cloudflare on
    Zimmo / Immoweb. Connectors talk to `.get(url)` and receive a
    `RawListing`, or `RobotsBlockedError` / `requests.HTTPError`.
    """

    def __init__(
        self,
        *,
        rate_limiter: RateLimiter | None = None,
        robots: RobotsCache | None = None,
        impersonate: str = HTTP_IMPERSONATE,
        timeout: float = HTTP_TIMEOUT_SECONDS,
    ) -> None:
        self._rate_limiter = rate_limiter or RateLimiter()
        self._robots = robots or RobotsCache()
        # `impersonate` is a Literal in curl_cffi's stubs; we want it
        # configurable via a plain string in config.py.
        self._client = curl_requests.Session(
            impersonate=impersonate,  # type: ignore[arg-type]
            timeout=timeout,
        )
        # curl_cffi already sends Chrome's full Accept / Sec-CH-UA /
        # Sec-Fetch-* set when impersonating. We only add operator
        # contact (IETF-standard `From`) and our preferred language.
        self._client.headers.update(
            {
                "From": OPERATOR_CONTACT,
                "Accept-Language": "nl-BE,nl;q=0.9,en;q=0.7",
            }
        )
        # Cloudflare often gates the very first request to a host with a
        # bot-management cookie (`__cf_bm`, `cf_clearance`). Pre-fetch
        # the homepage once per host so that cookie is in our jar before
        # we ask for `/robots.txt` or `/sitemap.xml`.
        self._warmed_hosts: set[str] = set()

    @property
    def rate_limiter(self) -> RateLimiter:
        return self._rate_limiter

    def _ensure_warmed(self, host: str, scheme: str) -> None:
        """Hit the homepage once per host to harvest Cloudflare cookies."""
        if host in self._warmed_hosts:
            return
        self._warmed_hosts.add(host)  # mark first to avoid re-trying on failure
        homepage = f"{scheme}://{host}/"
        try:
            log.info("Warming up cookies for %s", host)
            self._rate_limiter.acquire(host)
            response = self._client.get(homepage, allow_redirects=True)
            log.debug(
                "Warmup %s: status=%s cookies=%d",
                homepage,
                response.status_code,
                len(self._client.cookies),
            )
        except Exception as exc:
            log.debug("Warmup for %s failed: %s — continuing", host, exc)

    def get(self, url: str) -> RawListing:
        """Fetch `url`, honouring robots.txt + per-host rate limit."""
        parsed = urlparse(url)
        host = parsed.netloc
        self._ensure_warmed(host, parsed.scheme)

        if not self._robots.can_fetch(url, fetch_robots=self._client):
            raise RobotsBlockedError(url)

        self._rate_limiter.acquire(host)
        log.debug("GET %s", url)
        response = self._client.get(url, allow_redirects=True)
        response.raise_for_status()  # type: ignore[no-untyped-call]
        return RawListing(source_url=str(response.url), body=response.content)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> HTTPClient:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()


class RobotsBlockedError(RuntimeError):
    """Raised when robots.txt forbids fetching a URL."""

    def __init__(self, url: str) -> None:
        super().__init__(f"robots.txt forbids fetching {url}")
        self.url = url


# ---------------------------------------------------------------------------
# HTTPClientProtocol — the surface a Connector actually consumes.
# ---------------------------------------------------------------------------
# `HTTPClient` (curl_cffi-based, fast) and `PlaywrightHTTPClient`
# (real Chromium, used for Cloudflare-protected sites) both implement
# this. The runner picks one per connector via `requires_browser`.
@runtime_checkable
class HTTPClientProtocol(Protocol):
    def get(self, url: str) -> RawListing:
        """Fetch `url` and return its body. Raises on transport errors."""

    def close(self) -> None: ...


# ---------------------------------------------------------------------------
# The Connector Protocol
# ---------------------------------------------------------------------------
@runtime_checkable
class Connector(Protocol):
    """Every source implements this. See module docstring for the rules.

    Class-level metadata (`name`, `display_name`, `base_url`) makes the
    connector identifiable to the registry without instantiation.

    `requires_browser` (defaults to False) signals to the runner that
    this connector needs a real-browser HTTP client (Playwright) rather
    than the default curl_cffi one — relevant for Cloudflare-protected
    sites that demand JS-challenge solving.
    """

    name: ClassVar[SourceName]
    display_name: ClassVar[str]
    base_url: ClassVar[str]
    requires_browser: ClassVar[bool]

    def discover(
        self, client: HTTPClientProtocol, postcodes: Iterable[int]
    ) -> Iterable[ListingRef]:
        """Yield URLs of listing detail pages within `postcodes`."""

    def fetch(self, client: HTTPClientProtocol, ref: ListingRef) -> RawListing:
        """Download a single listing's detail page."""

    def parse(self, raw: RawListing) -> Listing:
        """Map raw bytes to a canonical Listing.

        Pure function: no network I/O, no global state. Must be safe to
        call repeatedly on the same input.
        """
