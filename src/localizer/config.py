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
USER_AGENT = "Localizer-Internal/0.1 (+contact: info@odila.be)"
DEFAULT_RATE_LIMIT_PER_MIN = 30
HTTP_TIMEOUT_SECONDS = 30

# ---------------------------------------------------------------------------
# Refresh policy
# ---------------------------------------------------------------------------
# How old the data may be before the app suggests a refresh on launch.
STALE_AFTER_HOURS = 24
