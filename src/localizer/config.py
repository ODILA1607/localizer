"""Static configuration for Localizer.

Hardcoded by design — see brein concept `pand-aggregator-skelet`:
Thomas should not have to edit a TOML file. If the scope ever shifts
(e.g. a different province), that warrants a new release.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Geographic scope (V1)
# ---------------------------------------------------------------------------
# West-Vlaanderen postcodes start with 8, Oost-Vlaanderen with 9.
POSTCODE_RANGES: tuple[tuple[int, int], ...] = (
    (8000, 8999),
    (9000, 9999),
)


def is_in_scope(postcode: int) -> bool:
    """Return True if `postcode` falls within Localizer's V1 geographic scope."""
    return any(low <= postcode <= high for low, high in POSTCODE_RANGES)


# ---------------------------------------------------------------------------
# HTTP defaults
# ---------------------------------------------------------------------------
# Browser-like User-Agent. Belgian listing sites (Cloudflare-fronted ones
# in particular) immediately 403 unidentified Python clients. We still
# identify ourselves to site owners through the `From` header further
# down the stack — that's the IETF-standard place for operator contact.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/127.0.0.0 Safari/537.36"
)
OPERATOR_CONTACT = "info@odila.be"
DEFAULT_RATE_LIMIT_PER_MIN = 30
HTTP_TIMEOUT_SECONDS = 30

# ---------------------------------------------------------------------------
# Refresh policy
# ---------------------------------------------------------------------------
# How old the data may be before the app suggests a refresh on launch.
STALE_AFTER_HOURS = 24
