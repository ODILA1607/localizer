"""Tests for the shared connector infrastructure (RateLimiter, Robots, registry).

Network tests for HTTPClient itself are deferred to M3 integration; these
tests cover the pure-logic pieces that we can verify offline.
"""

from __future__ import annotations

import time

from localizer.connectors.base import RateLimiter
from localizer.connectors.registry import (
    ALL_CONNECTORS,
    DEFAULT_ENABLED,
    enabled_connectors,
)
from localizer.core.models import SourceName


def test_rate_limiter_enforces_minimum_interval() -> None:
    # 600/min → 0.1s minimum interval. Two back-to-back acquires must
    # span at least the interval (allowing a small margin for timing
    # noise on Windows).
    rl = RateLimiter(default_per_minute=600)
    rl.acquire("example.com")  # warm up

    start = time.monotonic()
    rl.acquire("example.com")
    elapsed = time.monotonic() - start

    assert elapsed >= 0.05  # tolerant lower-bound


def test_rate_limiter_per_host_independent() -> None:
    # Two distinct hosts should not block each other.
    rl = RateLimiter(default_per_minute=60)
    rl.acquire("a.example")
    start = time.monotonic()
    rl.acquire("b.example")
    elapsed = time.monotonic() - start
    assert elapsed < 0.5  # b shouldn't wait for a


def test_rate_limiter_set_rate_overrides_default() -> None:
    rl = RateLimiter(default_per_minute=60)
    rl.set_rate("fast.example", per_minute=6000)
    rl.acquire("fast.example")
    start = time.monotonic()
    rl.acquire("fast.example")
    elapsed = time.monotonic() - start
    # 6000/min → ~0.01s minimum; allow up to 0.2s for OS noise
    assert elapsed < 0.2


def test_registry_lists_all_four_connectors() -> None:
    assert set(ALL_CONNECTORS.keys()) == {
        SourceName.ZIMMO,
        SourceName.IMMOSCOOP,
        SourceName.IMMOVLAN,
        SourceName.IMMOWEB,
    }


def test_default_enabled_is_all_four_sources() -> None:
    """All four bronnen are now in default — Cloudflare ones use Playwright."""
    assert DEFAULT_ENABLED == frozenset(
        {
            SourceName.ZIMMO,
            SourceName.IMMOSCOOP,
            SourceName.IMMOVLAN,
            SourceName.IMMOWEB,
        }
    )


def test_enabled_connectors_returns_all_four_instances() -> None:
    instances = enabled_connectors()
    assert {c.name for c in instances} == {
        SourceName.ZIMMO,
        SourceName.IMMOSCOOP,
        SourceName.IMMOVLAN,
        SourceName.IMMOWEB,
    }


def test_browser_required_flag_is_set_correctly() -> None:
    """Cloudflare-protected sources opt into the Playwright client."""
    by_name = {c.name: c for c in enabled_connectors()}
    assert by_name[SourceName.ZIMMO].requires_browser is True
    assert by_name[SourceName.IMMOWEB].requires_browser is True
    assert by_name[SourceName.IMMOSCOOP].requires_browser is False
    assert by_name[SourceName.IMMOVLAN].requires_browser is False


def test_enabled_connectors_can_be_overridden() -> None:
    selection = frozenset({SourceName.ZIMMO, SourceName.IMMOSCOOP})
    instances = enabled_connectors(selection)
    assert {c.name for c in instances} == selection
