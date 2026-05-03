"""The Connector contract — central abstraction of the acquisition layer.

Filled in M2. Three methods, three strict rules:

  1. Connectors MUST go through the shared HTTP client provided here.
     The shared client centralises rate-limiting, robots.txt enforcement,
     retries and logging. Individual connectors cannot bypass it.
  2. `parse()` operates on a string/bytes blob, never on live network data.
     This makes every parser unit-testable offline against a fixture.
  3. Fields the source does not provide stay None. No invented data.

Bot-protection ladder (per connector configurable, see brein concept
`pand-aggregator-skelet`):
    1. Realistic headers + cookies (httpx)        — Zimmo, Immoscoop
    2. TLS-fingerprint via curl_cffi              — Immoweb default
    3. Internal JSON endpoints / __NEXT_DATA__    — Immoweb best-case
    4. Playwright headless                        — last resort, NOT
                                                     bundled in the .exe by default
"""

from __future__ import annotations
