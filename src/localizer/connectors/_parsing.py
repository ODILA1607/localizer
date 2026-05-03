"""Shared HTML-to-JSON extraction helpers used by every connector.

Private module (leading underscore) — implementation detail of the
connectors package. Connector code calls these instead of touching
selectolax / json directly so that the parse logic stays uniform.
"""

from __future__ import annotations

import json
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
