"""Immoscoop connector — V1 active source.

Live HTML structure (verified 2026-05-04):
  - JSON-LD has TWO blocks per listing page:
      [Product]   name, image[], description, sku (=source_id),
                  offers.price (EUR)
      [House]     address (street/postcode/locality), geo (lat/lng)
  - OpenGraph (`og:title`, `og:image`) duplicates name + hero photo.
  - Specs (slaapkamers, m², EPC) are NOT in JSON-LD on most pages —
    they live in visible HTML body text. We extract via regex from the
    full text (best-effort; some noise tolerated).

Sitemap layout:
  - /sitemap/sitemap.xml is a sitemapindex.
  - Listing sub-sitemaps: sitemap-properties-nl-{N}.xml.gz (gzipped).
  - Listing URL format: /te-koop/{postcode}-{slug}/{numeric-id}.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from typing import Any, ClassVar

from localizer.connectors._parsing import (
    extract_json_ld,
    find_jsonld_block,
    regex_extract_bedrooms,
    regex_extract_epc_kwh,
    regex_extract_epc_label,
    regex_extract_surface,
)
from localizer.connectors._sitemaps import fetch_sitemap_text, iter_sitemap_locs
from localizer.connectors.base import (
    Connector,
    HTTPClient,
    ListingRef,
    RawListing,
)
from localizer.connectors.zimmo import (
    _classify_condition,
    _classify_property_type,
    _coerce_int,
)
from localizer.core.dedup import compute_fingerprint
from localizer.core.models import (
    EpcLabel,
    Listing,
    ListingSource,
    SourceName,
)

log = logging.getLogger(__name__)

_LISTING_URL = re.compile(r"/te-koop/(\d{4})-[^/]+/(\d+)/?$")
_SUB_SITEMAP = re.compile(r"/sitemap-properties-nl-\d+\.xml(?:\.gz)?$")


def _coerce_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", "."))
        except ValueError:
            return None
    return None


def _epc_from_label_string(value: str | None) -> EpcLabel | None:
    if not value:
        return None
    cleaned = value.strip().upper()
    # The regex sometimes captures trailing chars like "C++" — keep first valid letter
    for label in EpcLabel:
        if cleaned == label.value.upper():
            return label
    # Try first character (e.g. "C++" → "C")
    if cleaned and cleaned[0] in "ABCDEF":
        for label in EpcLabel:
            if label.value.upper() == cleaned[0]:
                return label
    return None


class ImmoscoopConnector:
    name: ClassVar[SourceName] = SourceName.IMMOSCOOP
    display_name: ClassVar[str] = "Immoscoop"
    base_url: ClassVar[str] = "https://www.immoscoop.be"

    def discover(self, client: HTTPClient, postcodes: Iterable[int]) -> Iterable[ListingRef]:
        wanted = set(postcodes)
        seen: set[str] = set()
        index_url = f"{self.base_url}/sitemap/sitemap.xml"

        try:
            index_text = fetch_sitemap_text(client, index_url)
        except Exception as exc:  # pragma: no cover - live path
            log.warning("Immoscoop: sitemap index unreachable: %s", exc)
            return

        sub_urls = [u for u in iter_sitemap_locs(index_text) if _SUB_SITEMAP.search(u)]
        log.info("Immoscoop: %d listing sub-sitemap(s) to walk", len(sub_urls))

        for sub_url in sub_urls:
            try:
                sub_text = fetch_sitemap_text(client, sub_url)
            except Exception as exc:  # pragma: no cover - live path
                log.warning("Immoscoop: sub-sitemap %s failed: %s", sub_url, exc)
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

    def fetch(self, client: HTTPClient, ref: ListingRef) -> RawListing:
        return client.get(ref.url)

    def parse(self, raw: RawListing) -> Listing:
        text = raw.text()
        blocks = extract_json_ld(text)
        product = find_jsonld_block(blocks, "Product")
        house = find_jsonld_block(blocks, "House") or find_jsonld_block(blocks, "Residence")
        if product is None and house is None:
            raise ValueError("No Product/House JSON-LD block found in Immoscoop HTML.")

        # ---- Address (House block)
        address = (house or {}).get("address") or {}
        postcode = _coerce_int(address.get("postalCode"))
        gemeente = (address.get("addressLocality") or "").strip()
        straat = (address.get("streetAddress") or "").strip() or None

        if postcode is None or not gemeente:
            # Fallback to URL
            m = _LISTING_URL.search(str(raw.source_url))
            if m is None:
                raise ValueError(f"Immoscoop has no address: {raw.source_url}")
            postcode = postcode or int(m.group(1))
            gemeente = gemeente or "?"

        # ---- Geo (House block)
        geo = (house or {}).get("geo") or {}
        lat = _coerce_float(geo.get("latitude"))
        lng = _coerce_float(geo.get("longitude"))

        # ---- Price (Product.offers)
        offers = (product or {}).get("offers") or {}
        prijs = _coerce_int(offers.get("price"))

        # ---- Hoofd foto (Product.image[0]) or og:image fallback
        hoofd_foto: str | None = None
        images = (product or {}).get("image")
        if isinstance(images, list) and images:
            hoofd_foto = str(images[0])
        elif isinstance(images, str):
            hoofd_foto = images

        # ---- source_id from Product.sku, fall back to URL
        source_url_str = str(offers.get("url") or raw.source_url)
        source_id_raw = (product or {}).get("sku")
        source_id = str(source_id_raw) if source_id_raw else None
        if not source_id:
            m = _LISTING_URL.search(source_url_str)
            if m is None:
                raise ValueError(f"Cannot extract Immoscoop source_id: {source_url_str}")
            source_id = m.group(2)

        # ---- Type + staat from name + description
        name = str((product or house or {}).get("name") or "")
        description = str((product or house or {}).get("description") or "")
        type_text = " ".join((name, description))
        prop_type = _classify_property_type(type_text)
        staat = _classify_condition(description or name)

        # ---- Specs from body regex (Immoscoop doesn't put these in JSON-LD)
        slaapkamers = regex_extract_bedrooms(text)
        opp = regex_extract_surface(text)
        epc_label_str = regex_extract_epc_label(text)
        epc_label = _epc_from_label_string(epc_label_str)
        epc_kwh = regex_extract_epc_kwh(text)

        fingerprint = compute_fingerprint(source_name=self.name, source_id=source_id)

        return Listing.model_validate(
            {
                "fingerprint": fingerprint,
                "sources": [
                    ListingSource.model_validate(
                        {
                            "source_name": self.name,
                            "source_id": source_id,
                            "source_url": source_url_str,
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
                "titel": name.strip(),
                "korte_beschrijving": description.strip()[:500],
                "hoofd_foto_url": hoofd_foto,
            }
        )


_: Connector = ImmoscoopConnector()
