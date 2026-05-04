"""Shared HTML-to-JSON extraction helpers used by every connector.

Private module (leading underscore) — implementation detail of the
connectors package. Connector code calls these instead of touching
selectolax / json directly so that the parse logic stays uniform.
"""

from __future__ import annotations

import json
import re
from typing import Any

from selectolax.parser import HTMLParser


def extract_json_ld(html: str) -> list[dict[str, Any]]:
    """Return every parsable `<script type="application/ld+json">` block.

    Lists inside a single block are flattened into individual entries.
    Malformed JSON blocks are skipped silently — connectors should treat
    "no useful blocks found" as a hard parse failure themselves.
    """
    tree = HTMLParser(html)
    results: list[dict[str, Any]] = []
    for node in tree.css('script[type="application/ld+json"]'):
        text = node.text() or ""
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(data, list):
            results.extend(d for d in data if isinstance(d, dict))
        elif isinstance(data, dict):
            results.append(data)
    return results


def find_jsonld_by_type(blocks: list[dict[str, Any]], wanted: str) -> dict[str, Any] | None:
    """Return the first JSON-LD block whose `@type` equals `wanted` (case-insensitive).

    Some sites nest the wanted type inside `@graph`; we also search there.
    """
    wanted_lower = wanted.lower()
    for block in blocks:
        block_type = block.get("@type")
        if isinstance(block_type, str) and block_type.lower() == wanted_lower:
            return block
        # @graph: list of subnodes
        graph = block.get("@graph")
        if isinstance(graph, list):
            for node in graph:
                if (
                    isinstance(node, dict)
                    and isinstance(node.get("@type"), str)
                    and node["@type"].lower() == wanted_lower
                ):
                    return node
    return None


def extract_next_data(html: str) -> dict[str, Any] | None:
    """Return the parsed `__NEXT_DATA__` JSON blob, or None if absent.

    Next.js sites embed their entire page-state here as JSON. For Immoweb
    this is the cleanest path to listing data.
    """
    tree = HTMLParser(html)
    node = tree.css_first("script#__NEXT_DATA__")
    if node is None:
        return None
    text = node.text() or ""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def extract_meta_content(html: str, *, key: str, attr: str = "property") -> str | None:
    """Return content of a `<meta {attr}="{key}">` tag, or None if absent.

    Examples:
        extract_meta_content(html, key="og:image")              # OpenGraph image
        extract_meta_content(html, key="twitter:description", attr="name")
    """
    tree = HTMLParser(html)
    node = tree.css_first(f'meta[{attr}="{key}"]')
    if node is None:
        return None
    content = node.attributes.get("content")
    return content.strip() if content else None


def find_jsonld_block(blocks: list[dict[str, Any]], wanted_type: str) -> dict[str, Any] | None:
    """Alias for `find_jsonld_by_type` — kept for naming clarity in callers."""
    return find_jsonld_by_type(blocks, wanted_type)


# ---------------------------------------------------------------------------
# Best-effort regex extraction from visible text
# ---------------------------------------------------------------------------
# Property listing pages embed specs in HTML rather than structured data
# almost everywhere. These patterns are intentionally generous; they
# may miss edge cases or pull a noisy match — `merge_or_insert` will
# tolerate a None or even an occasional wrong value, but the user's
# filtering benefits hugely from any extraction at all.

_RE_BEDROOMS = re.compile(r"(\d+)\s*[Ss]laapkamer", re.IGNORECASE)
_RE_SURFACE = re.compile(r"(\d{2,4})\s*m\s*[²2]", re.IGNORECASE)

# Two euro orderings: prefix ("€ 415.000") and suffix ("415 000 €" — the
# Belgian convention used in twitter:description on Immovlan).
_RE_PRICE_EUR_PREFIX = re.compile(r"(?:€|\bEUR\b)\s*([\d][\d\s.,]{2,15})", re.IGNORECASE)
_RE_PRICE_EUR_SUFFIX = re.compile(r"([\d][\d\s.,]{2,15})\s*(?:€|\bEUR\b)", re.IGNORECASE)

# `[^A-Z]` (any non-uppercase) lets the regex skim past words like
# "label", "score", "klasse" between "EPC" and the actual letter.
_RE_EPC_LABEL = re.compile(r"\bEPC[^A-Z]{0,30}([A-F](?:\+\+?)?)\b")
_RE_EPC_KWH = re.compile(r"(\d{2,4})\s*kWh\s*/\s*m", re.IGNORECASE)


def parse_int_eu(raw: str) -> int | None:
    """Convert a European-formatted number string ('383 011', '275.000') to int."""
    digits = re.sub(r"[^\d]", "", raw)
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def find_first_int(pattern: re.Pattern[str], text: str) -> int | None:
    m = pattern.search(text)
    if m is None:
        return None
    return parse_int_eu(m.group(1))


def find_first_str(pattern: re.Pattern[str], text: str) -> str | None:
    m = pattern.search(text)
    return m.group(1).strip() if m else None


def regex_extract_bedrooms(text: str) -> int | None:
    return find_first_int(_RE_BEDROOMS, text)


def regex_extract_surface(text: str) -> int | None:
    """Largest plausible m² number in text (avoids picking '16 m²' for a bathroom)."""
    matches = [parse_int_eu(m.group(1)) for m in _RE_SURFACE.finditer(text)]
    plausible = [v for v in matches if v is not None and 20 <= v <= 2000]
    return max(plausible) if plausible else None


def regex_extract_price(text: str) -> int | None:
    """First plausible euro amount (≥ 1000) in text. Tries suffix-€ first
    (Belgian convention) then prefix-€."""
    for pattern in (_RE_PRICE_EUR_SUFFIX, _RE_PRICE_EUR_PREFIX):
        for m in pattern.finditer(text):
            v = parse_int_eu(m.group(1))
            if v is not None and 1000 <= v <= 100_000_000:
                return v
    return None


def regex_extract_epc_label(text: str) -> str | None:
    """Return the EPC label (A..F, possibly with '+') or None."""
    return find_first_str(_RE_EPC_LABEL, text)


def regex_extract_epc_kwh(text: str) -> int | None:
    """Return EPC kWh/m²/year as int or None."""
    return find_first_int(_RE_EPC_KWH, text)
