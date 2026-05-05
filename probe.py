"""Round 6: dump the FULL Immoscoop pageProps.property dict from the
Next.js inline script — that's where the real coordinates live.

1. Browse to https://www.immoscoop.be/ in a normal browser.
2. Click any listing in West-/Oost-Vlaanderen that loads (200 OK).
3. Copy that URL into the URL constant below.
4. Run: python probe.py > probe-output.txt
"""

from __future__ import annotations

import json
import re
import sys

from curl_cffi import requests
from selectolax.parser import HTMLParser

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


# ⚠️  REPLACE with a fresh, working Immoscoop listing URL.
URL = "https://www.immoscoop.be/te-koop/8200-sint-michiels/1119591"


_NEXT_PROPS = re.compile(r'(\{"props"\s*:\s*\{"pageProps".*?\})\s*</script', re.DOTALL)


def find_property_payload(html: str) -> dict | None:
    """Locate the inline JS blob that holds Next.js's pageProps and
    return its `pageProps.property` dict if present."""
    tree = HTMLParser(html)
    candidates: list[str] = []
    for node in tree.css("script"):
        text = node.text() or ""
        if '"pageProps"' in text and '"property"' in text:
            candidates.append(text)

    print(f"\nFound {len(candidates)} candidate <script> blocks with pageProps.property")
    for idx, text in enumerate(candidates):
        # Try parsing the whole script tag as JSON first (works for
        # `<script type="application/json">…</script>` cases).
        try:
            data = json.loads(text)
            print(f"  [{idx}] parsed as direct JSON ({len(text)} chars)")
            return _extract_property(data)
        except json.JSONDecodeError:
            pass
        # Otherwise: regex out the JSON object.
        m = re.search(r'\{"props"\s*:\s*\{.*\}\s*\}', text, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(0))
                print(f"  [{idx}] parsed via regex extract ({len(m.group(0))} chars)")
                return _extract_property(data)
            except json.JSONDecodeError as exc:
                print(f"  [{idx}] regex match did not parse: {exc}")
    return None


def _extract_property(data: dict) -> dict | None:
    if not isinstance(data, dict):
        return None
    page_props = data.get("props", {}).get("pageProps")
    if not isinstance(page_props, dict):
        return None
    prop = page_props.get("property")
    return prop if isinstance(prop, dict) else None


def main() -> None:
    print(f"Probing: {URL}")
    s = requests.Session(impersonate="chrome131")
    s.get("https://www.immoscoop.be/", timeout=15)  # warmup
    r = s.get(URL, timeout=20, allow_redirects=True)
    print(f"status: {r.status_code}, length: {len(r.text)}")
    if r.status_code != 200:
        print("\n⚠️  Non-200 response — pick a fresh listing URL.")
    html = r.text

    prop = find_property_payload(html)
    if prop is None:
        print("\nNo `pageProps.property` dict found.")
        return

    print(f"\nProperty dict: {len(prop)} top-level keys.")
    print("\n--- All top-level keys ---")
    for k, v in prop.items():
        type_name = type(v).__name__
        if isinstance(v, (str, int, float, bool)) or v is None:
            preview = repr(v)[:80]
        elif isinstance(v, list):
            preview = f"list[{len(v)}]"
        elif isinstance(v, dict):
            preview = f"dict({list(v.keys())[:8]})"
        else:
            preview = type_name
        print(f"  {k:<25} {type_name:<10} {preview}")

    print("\n--- Any keys mentioning location/coords (full values) ---")
    interesting = ("lat", "lng", "long", "coord", "geo", "location", "position")
    _dump_matching(prop, interesting, prefix="property")


def _dump_matching(obj, keywords, prefix=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = k.lower()
            if any(kw in kl for kw in keywords):
                print(f"  {prefix}.{k} = {json.dumps(v, indent=2)[:600]}")
            if isinstance(v, (dict, list)):
                _dump_matching(v, keywords, prefix + "." + k)
    elif isinstance(obj, list):
        for i, item in enumerate(obj[:5]):  # first 5 only
            _dump_matching(item, keywords, prefix + f"[{i}]")


if __name__ == "__main__":
    main()
