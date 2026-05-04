"""Immoscoop connector — V1 active source.

curl_cffi Chrome impersonation suffices (verified via probe). Sitemap
layout (also verified):
  - Index at `/sitemap/sitemap.xml`
  - Listing sub-sitemaps named `sitemap-properties-nl-{N}.xml.gz`
    (gzipped, ~25k URLs each).
  - Listing URL format: `https://www.immoscoop.be/te-koop/{postcode}-{slug}/{id}`

We filter to:
  - sale-only (URL contains `/te-koop/`),
  - postcode in scope (Belgian 4-digit prefix in path).

Detail pages publish JSON-LD; `parse()` consumes that.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from typing import ClassVar

from localizer.connectors._parsing import extract_json_ld, find_jsonld_by_type
from localizer.connectors._sitemaps import fetch_sitemap_text, iter_sitemap_locs
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
)
from localizer.core.dedup import compute_fingerprint
from localizer.core.models import (
    Listing,
    ListingSource,
    SourceName,
)

log = logging.getLogger(__name__)

# Listing detail URL: e.g. /te-koop/8500-kortrijk/879 → id "879"
_LISTING_URL = re.compile(r"/te-koop/(\d{4})-[^/]+/(\d+)/?$")
_SUB_SITEMAP = re.compile(r"/sitemap-properties-nl-\d+\.xml(?:\.gz)?$")


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
        blocks = extract_json_ld(raw.text())
        residence = (
            find_jsonld_by_type(blocks, "Residence")
            or find_jsonld_by_type(blocks, "RealEstateListing")
            or find_jsonld_by_type(blocks, "Apartment")
            or find_jsonld_by_type(blocks, "House")
        )
        if residence is None:
            raise ValueError("No Residence-like JSON-LD block found in Immoscoop HTML.")

        address = residence.get("address") or {}
        offers = residence.get("offers") or {}
        floor_size = residence.get("floorSize") or {}

        postcode = _coerce_int(address.get("postalCode"))
        if postcode is None:
            raise ValueError("Immoscoop listing has no postalCode")

        gemeente = (address.get("addressLocality") or "").strip()
        if not gemeente:
            raise ValueError("Immoscoop listing has no addressLocality")

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
        m = _LISTING_URL.search(str(source_url))
        if m is None:
            raise ValueError(f"Cannot extract Immoscoop source_id from URL: {source_url}")
        source_id = m.group(2)

        image = residence.get("image")
        if isinstance(image, list):
            image = image[0] if image else None

        fingerprint = compute_fingerprint(source_name=self.name, source_id=source_id)

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


_: Connector = ImmoscoopConnector()
