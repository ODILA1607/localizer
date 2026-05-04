"""Immovlan connector — fourth source confirmed by Thomas.

Strategy: bot-protection laag 1 (realistic headers + cookies). Immovlan
publishes structured data as JSON-LD (`Residence` / `RealEstateListing`)
in the listing detail page; `parse()` consumes that. Same pattern as
Zimmo / Immoscoop, separate connector kept for the registry's sake and
to allow per-source tuning later (different sitemap layout, different
field quirks).

discover() and fetch() use the same sitemap-driven approach as Zimmo;
will adjust on first live run.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from typing import ClassVar

from localizer.connectors._parsing import extract_json_ld, find_jsonld_by_type
from localizer.connectors.base import (
    Connector,
    HTTPClient,
    ListingRef,
    RawListing,
)
from localizer.connectors.zimmo import (
    _classify_condition,
    _classify_epc,
    _classify_property_type,
    _coerce_int,
    _iter_sitemap_locs,
    _looks_like_listing_url,
    _postcode_from_url,
)
from localizer.core.dedup import compute_fingerprint
from localizer.core.models import (
    Listing,
    ListingSource,
    SourceName,
)

log = logging.getLogger(__name__)

_SOURCE_ID_FROM_URL = re.compile(r"/(\d{6,})(?:[-/]|$)")


def _is_listing_sitemap(url: str) -> bool:
    """Heuristic: which child-sitemap holds for-sale listings on Immovlan?"""
    lowered = url.lower()
    return any(
        s in lowered for s in ("te-koop", "a-vendre", "for-sale", "huis", "appartement", "listing")
    )


class ImmovlanConnector:
    name: ClassVar[SourceName] = SourceName.IMMOVLAN
    display_name: ClassVar[str] = "Immovlan"
    base_url: ClassVar[str] = "https://www.immovlan.be"

    def discover(self, client: HTTPClient, postcodes: Iterable[int]) -> Iterable[ListingRef]:
        """Sitemap-driven discovery. Same pattern as Zimmo, will adjust live."""
        wanted = set(postcodes)
        seen: set[str] = set()
        index_url = f"{self.base_url}/sitemap.xml"

        try:
            index_xml = client.get(index_url).text()
            sub_sitemaps = list(_iter_sitemap_locs(index_xml))
        except Exception as exc:  # pragma: no cover - live path
            log.warning("Immovlan: sitemap index unreachable (%s)", exc)
            sub_sitemaps = []

        listing_sitemaps = [u for u in sub_sitemaps if _is_listing_sitemap(u)] or sub_sitemaps

        for sitemap_url in listing_sitemaps:
            try:
                sitemap_xml = client.get(sitemap_url).text()
            except Exception as exc:  # pragma: no cover - live path
                log.warning("Immovlan: sitemap %s failed: %s", sitemap_url, exc)
                continue
            for url in _iter_sitemap_locs(sitemap_xml):
                if url in seen:
                    continue
                pc = _postcode_from_url(url)
                if pc is None or pc not in wanted:
                    continue
                if not _looks_like_listing_url(url):
                    continue
                seen.add(url)
                yield ListingRef(url=url)

    def fetch(self, client: HTTPClient, ref: ListingRef) -> RawListing:
        return client.get(ref.url)

    def parse(self, raw: RawListing) -> Listing:
        blocks = extract_json_ld(raw.text())
        residence = (
            find_jsonld_by_type(blocks, "Residence")
            or find_jsonld_by_type(blocks, "RealEstateListing")
            or find_jsonld_by_type(blocks, "Apartment")
            or find_jsonld_by_type(blocks, "House")
            or find_jsonld_by_type(blocks, "Product")
        )
        if residence is None:
            raise ValueError("No Residence-like JSON-LD block found in Immovlan HTML.")

        address = residence.get("address") or {}
        offers = residence.get("offers") or {}
        floor_size = residence.get("floorSize") or {}

        postcode = _coerce_int(address.get("postalCode"))
        if postcode is None:
            raise ValueError("Immovlan listing has no postalCode")

        gemeente = (address.get("addressLocality") or "").strip()
        if not gemeente:
            raise ValueError("Immovlan listing has no addressLocality")

        straat = (address.get("streetAddress") or "").strip() or None
        opp = _coerce_int(floor_size.get("value"))
        slaapkamers = _coerce_int(residence.get("numberOfRooms"))
        prijs = _coerce_int(offers.get("price"))
        type_text = " ".join(str(x or "") for x in (residence.get("@type"), residence.get("name")))
        prop_type = _classify_property_type(type_text)
        staat = _classify_condition(residence.get("description") or residence.get("name"))
        epc = _classify_epc(residence.get("energyEfficiencyScaleMin"))
        epc_kwh = _coerce_int(residence.get("energyConsumption"))

        source_url = residence.get("url") or raw.source_url
        m = _SOURCE_ID_FROM_URL.search(str(source_url))
        if m is None:
            raise ValueError(f"Cannot extract Immovlan source_id from URL: {source_url}")
        source_id = m.group(1)

        image = residence.get("image")
        if isinstance(image, list):
            image = image[0] if image else None

        fingerprint = compute_fingerprint(
            straat=straat,
            postcode=postcode,
            oppervlakte_bewoonbaar_m2=opp,
            slaapkamers=slaapkamers,
        )

        return Listing.model_validate(
            {
                "fingerprint": fingerprint,
                "sources": [
                    ListingSource.model_validate(
                        {
                            "source_name": self.name,
                            "source_id": source_id,
                            "source_url": source_url,
                        }
                    )
                ],
                "postcode": postcode,
                "gemeente": gemeente,
                "straat": straat,
                "prijs_eur": prijs,
                "type": prop_type,
                "oppervlakte_bewoonbaar_m2": opp,
                "slaapkamers": slaapkamers,
                "epc_label": epc,
                "epc_kwh": epc_kwh,
                "staat": staat,
                "titel": (residence.get("name") or "").strip(),
                "korte_beschrijving": (residence.get("description") or "").strip(),
                "hoofd_foto_url": image,
            }
        )


_: Connector = ImmovlanConnector()
