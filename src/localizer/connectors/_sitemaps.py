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
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterator

from localizer.connectors.base import HTTPClientProtocol

log = logging.getLogger(__name__)

_SITEMAP_NAMESPACE = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
_GZIP_MAGIC = b"\x1f\x8b"

# Chromium renders XML responses with an HTML viewer chrome around them.
# The actual XML lives inside a `<div id="webkit-xml-viewer-source-xml">`
# (or similar) wrapper. The Playwright client returns this rendered HTML;
# we unwrap it before XML parsing.
_XML_FROM_CHROMIUM_VIEWER = re.compile(
    r'<div\s+id="webkit-xml-viewer-source-xml"[^>]*>(.*?)</div>\s*</body>',
    re.DOTALL,
)
_BARE_SITEMAP_BLOCK = re.compile(
    r"<(?:urlset|sitemapindex)[\s\S]*?</(?:urlset|sitemapindex)>",
)


def fetch_sitemap_text(client: HTTPClientProtocol, url: str) -> str:
    """Fetch `url` and decompress if it is gzipped (`.xml.gz` or magic bytes)."""
    raw = client.get(url)
    body = raw.body
    if url.endswith(".gz") or body[:2] == _GZIP_MAGIC:
        try:
            body = gzip.decompress(body)
        except Exception as exc:
            log.warning("gzip decompress failed for %s: %s", url, exc)
    text = body.decode("utf-8", errors="replace")
    return _unwrap_xml_from_html(text)


def _unwrap_xml_from_html(text: str) -> str:
    """Return the raw sitemap XML, stripping Chromium's HTML viewer if present.

    No-op when `text` already starts with raw XML (curl_cffi-fetched
    sources). For Playwright-fetched sources, Chromium has rendered the
    XML inside an HTML viewer; we extract the original XML.
    """
    stripped = text.lstrip()
    if (
        stripped.startswith("<?xml")
        or stripped.startswith("<sitemapindex")
        or stripped.startswith("<urlset")
    ):
        return text

    m = _XML_FROM_CHROMIUM_VIEWER.search(text)
    if m:
        log.debug("Stripped Chromium XML viewer wrapper")
        return m.group(1)

    # Last-resort fallback: hunt for any sitemap block in the rendered HTML.
    m = _BARE_SITEMAP_BLOCK.search(text)
    if m:
        log.debug("Recovered bare <urlset>/<sitemapindex> block from HTML")
        return m.group(0)

    return text


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
