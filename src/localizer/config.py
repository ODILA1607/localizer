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
# Cloudflare-fronted Belgian listing sites (Zimmo, Immoweb...) inspect
# TLS handshakes and immediately 403 anything that is not a real
# browser. Use curl_cffi to reproduce a Chrome 120 fingerprint end-to-end:
# TLS handshake, ALPN, ALPS, Sec-CH-UA headers, Accept ordering — the
# whole package. The User-Agent we send is whatever curl_cffi assigns
# to that browser version.
#
# `ROBOTS_USER_AGENT` is *only* used to evaluate robots.txt rules, not
# sent over the wire. "*" picks the catch-all rule set, which is the
# right thing for a desktop tool that is not registered as a named bot.
HTTP_IMPERSONATE = "chrome131"
ROBOTS_USER_AGENT = "*"
OPERATOR_CONTACT = "info@odila.be"
DEFAULT_RATE_LIMIT_PER_MIN = 60
HTTP_TIMEOUT_SECONDS = 30

# ---------------------------------------------------------------------------
# Refresh policy
# ---------------------------------------------------------------------------
# How old the data may be before the app suggests a refresh on launch.
STALE_AFTER_HOURS = 24
