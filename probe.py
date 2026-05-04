"""Round 4: dump JSON-LD + OpenGraph meta + __NEXT_DATA__ from one real
listing per source so we can see where price/surface/EPC/photos live.

Run:  python probe.py > probe-output.txt
(piping helps — output is large)
"""

from __future__ import annotations

import json
import re
import sys

from curl_cffi import requests
from selectolax.parser import HTMLParser

# Force UTF-8 stdout so we can pipe to a file on Windows without crashing
# on euro signs / m² / etc.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


_LISTINGS = {
    "immoscoop": "https://www.immoscoop.be/te-koop/8500-kortrijk/879",
    "immovlan": "https://immovlan.be/nl/detail/huis/te-koop/9800/deinze/rbv78636",
}


def banner(title: str) -> None:
    print("\n" + "=" * len(title))
    print(title)
    print("=" * len(title))


def show_meta_tags(tree: HTMLParser) -> None:
    """Dump OpenGraph + Twitter + standard meta tags relevant to listings."""
    print("\n--- meta tags ---")
    for node in tree.css("meta[property], meta[name]"):
        prop = node.attributes.get("property") or node.attributes.get("name") or ""
        content = (node.attributes.get("content") or "").strip()
        # Filter to interesting ones, skip viewport/charset/etc.
        if any(
            keyword in prop.lower()
            for keyword in (
                "og:",
                "twitter:",
                "price",
                "geo",
                "lat",
                "long",
                "area",
                "room",
                "bedroom",
                "epc",
                "energy",
                "image",
                "address",
            )
        ):
            print(f"  {prop:<40} {content[:100]}")


def show_json_ld(tree: HTMLParser) -> None:
    print("\n--- JSON-LD blocks ---")
    for i, node in enumerate(tree.css('script[type="application/ld+json"]')):
        text = node.text() or ""
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            print(f"  [{i}] (parse error, {len(text)} chars)")
            continue
        if isinstance(data, list):
            for j, sub in enumerate(data):
                print(f"  [{i}.{j}] {json.dumps(sub, indent=2)[:1200]}")
        else:
            print(f"  [{i}] {json.dumps(data, indent=2)[:1500]}")


def show_next_data(tree: HTMLParser) -> None:
    print("\n--- __NEXT_DATA__ (truncated) ---")
    node = tree.css_first("script#__NEXT_DATA__")
    if node is None:
        print("  (none)")
        return
    text = node.text() or ""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        print(f"  (parse error, {len(text)} chars)")
        return
    # Walk to find a 'classified' / 'property' / 'listing' key anywhere
    found_keys = set()

    def walk(obj, path=""):  # type: ignore[no-untyped-def]
        if isinstance(obj, dict):
            for k, v in obj.items():
                kl = k.lower()
                if any(
                    needle in kl
                    for needle in (
                        "price",
                        "epc",
                        "bedroom",
                        "surface",
                        "address",
                        "image",
                        "photo",
                    )
                ):
                    found_keys.add(path + "." + k)
                if isinstance(v, (dict, list)):
                    walk(v, path + "." + k)
        elif isinstance(obj, list):
            for i, item in enumerate(obj[:5]):
                walk(item, path + f"[{i}]")

    walk(data)
    print("  Interesting keys found:")
    for k in sorted(found_keys)[:30]:
        print(f"    {k}")


def show_visible_text_snippets(tree: HTMLParser) -> None:
    """Look for euros, m², EPC labels in raw text — quick sanity check."""
    print("\n--- Text snippets ---")
    body = tree.body
    if body is None:
        return
    text = body.text(separator=" ", strip=True)
    for label, pattern in (
        ("price", r"€\s*[\d.,]+"),
        ("m²", r"\d+\s*m[²2]"),
        ("EPC", r"EPC[^.]{0,40}"),
        ("slaapkamers", r"\d+\s*slaapkamer"),
    ):
        matches = re.findall(pattern, text)[:5]
        if matches:
            print(f"  {label}: {matches}")


def probe(name: str, url: str) -> None:
    banner(f"{name}: {url}")
    s = requests.Session(impersonate="chrome131")
    try:
        # Warmup
        host = url.split("/")[2]
        s.get(f"https://{host}/", timeout=15, allow_redirects=True)
        r = s.get(url, timeout=20, allow_redirects=True)
    except Exception as exc:
        print(f"  FETCH FAILED: {exc!r}")
        return
    print(f"status: {r.status_code}, length: {len(r.text)}")
    tree = HTMLParser(r.text)
    show_meta_tags(tree)
    show_json_ld(tree)
    show_next_data(tree)
    show_visible_text_snippets(tree)


def main() -> None:
    for name, url in _LISTINGS.items():
        probe(name, url)


if __name__ == "__main__":
    main()
