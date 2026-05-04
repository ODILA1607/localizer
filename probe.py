"""Round 3: pull one real listing sub-sitemap from each source so we can
see the URL format of an actual listing. Handles gzip for Immoscoop.

Run:  python probe.py
"""

from __future__ import annotations

import gzip
import re

from curl_cffi import requests

_LOC = re.compile(r"<loc>(.*?)</loc>", re.DOTALL)


def show(title: str) -> None:
    bar = "=" * len(title)
    print(f"\n{bar}\n{title}\n{bar}")


def fetch(s: requests.Session, url: str) -> tuple[int, bytes]:
    try:
        r = s.get(url, timeout=20, allow_redirects=True)
        return r.status_code, r.content
    except Exception as exc:
        return -1, repr(exc).encode()


def decode(body: bytes, url: str) -> str:
    """Auto-decompress if .gz; otherwise decode utf-8."""
    if url.endswith(".gz"):
        try:
            body = gzip.decompress(body)
        except Exception as exc:
            return f"GZIP DECODE FAILED: {exc}"
    return body.decode("utf-8", errors="replace")


def main() -> None:
    s = requests.Session(impersonate="chrome131")

    # ---- Immoscoop: real listing sub-sitemap (gzipped)
    show("Immoscoop: contents of sitemap-properties-nl-1.xml.gz")
    url = "https://www.immoscoop.be/sitemap-properties-nl-1.xml.gz"
    code, body = fetch(s, url)
    text = decode(body, url)
    print(f"status: {code}, decompressed length: {len(text)}")
    locs = _LOC.findall(text)
    print(f"URL count: {len(locs)}")
    for loc in locs[:10]:
        print(f"  {loc.strip()}")

    # ---- Immovlan: real listing sub-sitemap (plain)
    show("Immovlan: contents of nl_property-detail-1.xml")
    url = "https://immovlan.be/sitemaps/nl_property-detail-1.xml"
    code, body = fetch(s, url)
    text = decode(body, url)
    print(f"status: {code}, length: {len(text)}")
    locs = _LOC.findall(text)
    print(f"URL count: {len(locs)}")
    for loc in locs[:10]:
        print(f"  {loc.strip()}")


if __name__ == "__main__":
    main()
