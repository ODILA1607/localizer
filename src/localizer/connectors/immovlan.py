"""Immovlan connector — V1 active source.

Live HTML structure (verified 2026-05-04):
  - JSON-LD has multiple blocks per listing page; the useful ones:
      [PostalAddress]   streetAddress, postalCode, addressLocality
      [GeoCoordinates]  latitude, longitude, postalCode
      [House]           description (long text)
  - The price + surface + bedrooms are NOT in any JSON-LD block. They
    live in `<meta name="twitter:description">`, formatted as:
        Huis te koop | Bremkouter 20-28 - 9800 Deinze | 383 011 € |
        Bewoonbare opp. 148m² | 3 Slaapkamers | 1 Badkamer
    plus duplicated in `<meta property="og:description">`.
  - Hero photo via `og:image` (CDN URL with property id in path).
  - URL is reliable for postcode + city + id (already used in V0).

Sitemap layout:
  - /sitemap.xml has 1043+ sub-sitemaps; only nl_property-detail-{N}.xml
    contain individual listings.
  - Listing URL format:
      /nl/detail/{type}/te-koop/{postcode}/{city}/{alphanum-id}
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from typing import ClassVar

from localizer.connectors._parsing import (
    extract_json_ld,
    extract_meta_content,
    find_jsonld_block,
    regex_extract_bedrooms,
    regex_extract_epc_kwh,
    regex_extract_epc_label,
    regex_extract_price,
    regex_extract_surface,
)
from localizer.connectors._sitemaps import fetch_sitemap_text, iter_sitemap_locs
from localizer.connectors.base import (
    Connector,
    HTTPClient,
    ListingRef,
    RawListing,
)
from localizer.connectors.immoscoop import _coerce_float, _epc_from_label_string
from localizer.connectors.zimmo import (
    _classify_condition,
    _classify_property_type,
)
from localizer.core.dedup import compute_fingerprint
from localizer.core.models import (
    Listing,
    ListingSource,
    SourceName,
)

log = logging.getLogger(__name__)

_LISTING_URL = re.compile(
    r"/nl/detail/[^/]+/te-koop/(\d{4})/([^/]+)/([a-z0-9]+)/?$",
    re.IGNORECASE,
)
_SUB_SITEMAP = re.compile(r"/nl_property-detail-\d+\.xml$")


class ImmovlanConnector:
    name: ClassVar[SourceName] = SourceName.IMMOVLAN
    display_name: ClassVar[str] = "Immovlan"
    base_url: ClassVar[str] = "https://www.immovlan.be"

    def discover(self, client: HTTPClient, postcodes: Iterable[int]) -> Iterable[ListingRef]:
        wanted = set(postcodes)
        seen: set[str] = set()
        index_url = f"{self.base_url}/sitemap.xml"

        try:
            index_text = fetch_sitemap_text(client, index_url)
        except Exception as exc:  # pragma: no cover - live path
            log.warning("Immovlan: sitemap index unreachable: %s", exc)
            return

        sub_urls = [u for u in iter_sitemap_locs(index_text) if _SUB_SITEMAP.search(u)]
        log.info("Immovlan: %d listing sub-sitemap(s) to walk", len(sub_urls))

        for sub_url in sub_urls:
            try:
                sub_text = fetch_sitemap_text(client, sub_url)
            except Exception as exc:  # pragma: no cover - live path
                log.warning("Immovlan: sub-sitemap %s failed: %s", sub_url, exc)
                continue
            for url in iter_sitemap_locs(sub_text):
                if url in seen:
                    continue
                m = _LISTING_URL.search(url)
                if m is None:
                    continue
                pc = int(m.group(1))
                if pc not in wanted:
                    continue
                seen.add(url)
                yield ListingRef(url=url)

    @staticmethod
    def _location_from_url(url: str) -> tuple[int, str, str] | None:
        """(postcode, city-titlecased, source_id) from a listing URL."""
        m = _LISTING_URL.search(url)
        if m is None:
            return None
        return int(m.group(1)), m.group(2).replace("-", " ").title(), m.group(3)

    def fetch(self, client: HTTPClient, ref: ListingRef) -> RawListing:
        return client.get(ref.url)

    def parse(self, raw: RawListing) -> Listing:
        text = raw.text()
        blocks = extract_json_ld(text)

        # ---- URL-based ground truth (Immovlan's most reliable source)
        loc = self._location_from_url(str(raw.source_url))
        if loc is None:
            raise ValueError(f"Cannot parse Immovlan URL: {raw.source_url}")
        postcode, city_from_url, source_id = loc

        # ---- PostalAddress block (street is here, not in any House block)
        address_block = find_jsonld_block(blocks, "PostalAddress") or {}
        straat = (address_block.get("streetAddress") or "").strip() or None
        gemeente = (address_block.get("addressLocality") or "").strip() or city_from_url

        # ---- GeoCoordinates block
        geo_block = find_jsonld_block(blocks, "GeoCoordinates") or {}
        lat = _coerce_float(geo_block.get("latitude"))
        lng = _coerce_float(geo_block.get("longitude"))

        # ---- House block: long description (free-text)
        house = find_jsonld_block(blocks, "House") or {}
        description = str(house.get("description") or "")

        # ---- twitter:description has price + surface + bedrooms in one line
        twitter_desc = extract_meta_content(text, key="twitter:description", attr="name") or ""
        og_title = extract_meta_content(text, key="og:title") or ""
        og_image = extract_meta_content(text, key="og:image") or None
        spec_text = " ".join((twitter_desc, og_title))

        prijs = regex_extract_price(spec_text) or regex_extract_price(text)
        opp = regex_extract_surface(spec_text) or regex_extract_surface(text)
        slaapkamers = regex_extract_bedrooms(spec_text) or regex_extract_bedrooms(text)

        # ---- EPC anywhere on the page (often in body text)
        epc_label = _epc_from_label_string(regex_extract_epc_label(text))
        epc_kwh = regex_extract_epc_kwh(text)

        # ---- Type + staat from name + description
        type_text = " ".join((og_title, description, twitter_desc))
        prop_type = _classify_property_type(type_text)
        staat = _classify_condition(description or og_title or twitter_desc)

        fingerprint = compute_fingerprint(source_name=self.name, source_id=source_id)

        return Listing.model_validate(
            {
                "fingerprint": fingerprint,
                "sources": [
                    ListingSource.model_validate(
                        {
                            "source_name": self.name,
                            "source_id": source_id,
                            "source_url": str(raw.source_url),
                        }
                    )
                ],
                "postcode": postcode,
                "gemeente": gemeente,
                "straat": straat,
                "lat": lat,
                "lng": lng,
                "prijs_eur": prijs,
                "type": prop_type,
                "oppervlakte_bewoonbaar_m2": opp,
                "slaapkamers": slaapkamers,
                "epc_label": epc_label,
                "epc_kwh": epc_kwh,
                "staat": staat,
                "titel": og_title.strip(),
                "korte_beschrijving": description.strip()[:500],
                "hoofd_foto_url": og_image,
            }
        )


_: Connector = ImmovlanConnector()
