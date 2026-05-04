"""Shared sitemap fetch + parse helpers used by every connector.

Two responsibilities:
  - `fetch_sitemap_text()` retrieves a sitemap URL via the shared HTTP
    client and transparently decompresses gzipped sitemap files
    (`.xml.gz`, common on big sites like Immoscoop).
  - `iter_sitemap_locs()` yields `<loc>` strings from a sitemap or
    sitemapindex XML document, namespace-aware and tolerant of malformed
    XML.

Private module — implementation detail of the `connectors` package.
"""

from __future__ import annotations

import gzip
import logging
import xml.etree.ElementTree as ET
from collections.abc import Iterator

from localizer.connectors.base import HTTPClient

log = logging.getLogger(__name__)

_SITEMAP_NAMESPACE = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
_GZIP_MAGIC = b"\x1f\x8b"


def fetch_sitemap_text(client: HTTPClient, url: str) -> str:
    """Fetch `url` and decompress if it is gzipped (`.xml.gz` or magic bytes)."""
    raw = client.get(url)
    body = raw.body
    if url.endswith(".gz") or body[:2] == _GZIP_MAGIC:
        try:
            body = gzip.decompress(body)
        except Exception as exc:
            log.warning("gzip decompress failed for %s: %s", url, exc)
    return body.decode("utf-8", errors="replace")


def iter_sitemap_locs(xml_text: str) -> Iterator[str]:
    """Yield trimmed `<loc>` values. Malformed XML logs and yields nothing."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        log.warning("sitemap XML parse failed: %s", exc)
        return
    for loc in root.iter(f"{_SITEMAP_NAMESPACE}loc"):
        text = (loc.text or "").strip()
        if text:
            yield text
