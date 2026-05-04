"""Immovlan connector — V1 active source.

curl_cffi Chrome impersonation suffices (verified via probe). Sitemap
layout (also verified):
  - Index at `/sitemap.xml` listing 1043+ sub-sitemaps (most are
    search-page indexes, not individual listings).
  - Listing sub-sitemaps named `nl_property-detail-{1..13}.xml` (plain
    XML, ~2500 URLs each).
  - Listing URL format:
      `https://immovlan.be/nl/detail/{type}/te-koop/{postcode}/{city}/{id}`
    where `{id}` is alphanumeric (e.g. `vbe15512`, `rbv78631`).

We filter to:
  - sale-only (URL contains `/te-koop/`),
  - postcode in scope (Belgian 4-digit segment).
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

# Listing detail URL:
#   /nl/detail/{type}/te-koop/{postcode}/{city}/{alphanum-id}
# Group 1 = postcode, group 2 = city slug, group 3 = source_id.
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
        # END for sub_url

    @staticmethod
    def _location_from_url(url: str) -> tuple[int, str, str] | None:
        """(postcode, city, source_id) from a listing URL, or None if the
        URL does not match the expected pattern.

        Immovlan's JSON-LD often omits address.postalCode, so the URL is
        the authoritative source for location.
        """
        m = _LISTING_URL.search(url)
        if m is None:
            return None
        return int(m.group(1)), m.group(2).replace("-", " ").title(), m.group(3)

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

        # URL is authoritative for location + id on Immovlan: their
        # JSON-LD omits address.postalCode and addressLocality is sparse.
        source_url = residence.get("url") or raw.source_url
        loc = self._location_from_url(str(source_url))
        if loc is None:
            raise ValueError(f"Cannot parse Immovlan URL: {source_url}")
        postcode, gemeente, source_id = loc

        # Optional fields fall back to JSON-LD where available.
        address = residence.get("address") or {}
        if isinstance(address, dict):
            jsonld_locality = (address.get("addressLocality") or "").strip()
            if jsonld_locality:
                gemeente = jsonld_locality
            straat = (address.get("streetAddress") or "").strip() or None
        else:
            straat = None

        offers = residence.get("offers") or {}
        floor_size = residence.get("floorSize") or {}
        opp = _coerce_int(floor_size.get("value"))
        slaapkamers = _coerce_int(residence.get("numberOfRooms"))
        prijs = _coerce_int(offers.get("price"))
        type_text = " ".join(str(x or "") for x in (residence.get("@type"), residence.get("name")))
        prop_type = _classify_property_type(type_text)
        staat = _classify_condition(residence.get("description") or residence.get("name"))
        epc = _classify_epc(residence.get("energyEfficiencyScaleMin"))
        epc_kwh = _coerce_int(residence.get("energyConsumption"))

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
