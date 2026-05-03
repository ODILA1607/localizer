"""Immoweb connector. Laatste bron (V1.1) wegens Cloudflare-bot-protection.

Strategy: start met laag 2 (curl_cffi TLS-fingerprint) + laag 3
(`__NEXT_DATA__` JSON-blob in de HTML). Playwright pas als beide falen.
"""

from __future__ import annotations
