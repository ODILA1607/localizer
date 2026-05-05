"""Immoweb connector — V1.1 active source (Cloudflare bypass via Playwright).

Strategy: `requires_browser=True` so the runner hands us a
`PlaywrightHTTPClient`. Real Chromium solves Cloudflare's JS challenge,
the cf_clearance cookie persists in the browser context, then every
listing page loads normally. Immoweb's listing detail pages embed the
full property in a `__NEXT_DATA__` JSON-LD-style script tag — the
cleanest data source on any of the four bronnen.

Sitemap layout (best-effort assumption; will adjust on first live run):
  - /sitemap.xml is a sitemapindex.
  - Listing detail URLs: /nl/classified/{type}/te-koop/{city}/{postcode}/{id}
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from typing import Any, ClassVar

from localizer.connectors._parsing import extract_next_data
from localizer.connectors._sitemaps import fetch_sitemap_text, iter_sitemap_locs
from localizer.connectors.base import (
    Connector,
    HTTPClientProtocol,
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
    Condition,
    Listing,
    ListingSource,
    PropertyType,
    SourceName,
)

_PROPERTY_TYPE_MAPPING: dict[str, PropertyType] = {
    "house": PropertyType.HUIS,
    "villa": PropertyType.HUIS,
    "apartment": PropertyType.APPARTEMENT,
    "studio": PropertyType.APPARTEMENT,
    "ground": PropertyType.GROND,
    "land": PropertyType.GROND,
}

# Immoweb's `buildingCondition` enum (English values) → our Condition.
_BUILDING_CONDITION_MAPPING: dict[str, Condition] = {
    "as_new": Condition.INSTAPKLAAR,
    "just_renovated": Condition.INSTAPKLAAR,
    "good": Condition.INSTAPKLAAR,
    "to_renovate": Condition.TE_RENOVEREN,
    "to_restore": Condition.TE_RENOVEREN,
    "to_be_done_up": Condition.TE_RENOVEREN,
    "new": Condition.NIEUWBOUW,
    "under_construction": Condition.NIEUWBOUW,
}


def _find_classified(data: dict[str, Any]) -> dict[str, Any] | None:
    """Locate the classified payload inside Next.js page-state.

    Immoweb's __NEXT_DATA__ wraps the listing under
    `props.pageProps.classified` (or similar). We walk the standard
    Next.js path and fall back to a shallow search if the layout drifts.
    """
    page_props = (
        data.get("props", {}).get("pageProps", {}) if isinstance(data.get("props"), dict) else {}
    )
    if isinstance(page_props, dict):
        classified = page_props.get("classified")
        if isinstance(classified, dict):
            return classified
    # Fallback: flat search
    for key in ("classified", "listing", "property"):
        v = data.get(key)
        if isinstance(v, dict):
            return v
    return None


log = logging.getLogger(__name__)

# Listing URL pattern (best-effort assumption; adjust after first live run).
# Example: /nl/classified/villa/te-koop/brugge/8000/11223344
_LISTING_URL = re.compile(
    r"/nl/classified/[^/]+/te-koop/[^/]+/(\d{4})/(\d+)/?$",
    re.IGNORECASE,
)
# Heuristic for sub-sitemap names that hold listings. Will adjust live.
_SUB_SITEMAP_HINT = re.compile(
    r"classified|te-koop|listing|nl",
    re.IGNORECASE,
)


class ImmowebConnector:
    name: ClassVar[SourceName] = SourceName.IMMOWEB
    display_name: ClassVar[str] = "Immoweb"
    base_url: ClassVar[str] = "https://www.immoweb.be"
    requires_browser: ClassVar[bool] = True  # Cloudflare JS-challenge

    def discover(
        self, client: HTTPClientProtocol, postcodes: Iterable[int]
    ) -> Iterable[ListingRef]:
        """Sitemap-driven discovery; will be calibrated on first live run."""
        wanted = set(postcodes)
        seen: set[str] = set()
        index_url = f"{self.base_url}/sitemap.xml"

        try:
            index_text = fetch_sitemap_text(client, index_url)
        except Exception as exc:  # pragma: no cover - live path
            log.warning("Immoweb: sitemap index unreachable: %s", exc)
            return

        sub_urls = [
            u for u in iter_sitemap_locs(index_text) if _SUB_SITEMAP_HINT.search(u)
        ] or list(iter_sitemap_locs(index_text))
        log.info("Immoweb: %d listing sub-sitemap(s) to walk", len(sub_urls))

        for sub_url in sub_urls:
            try:
                sub_text = fetch_sitemap_text(client, sub_url)
            except Exception as exc:  # pragma: no cover - live path
                log.warning("Immoweb: sub-sitemap %s failed: %s", sub_url, exc)
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

    def fetch(self, client: HTTPClientProtocol, ref: ListingRef) -> RawListing:
        return client.get(ref.url)

    def parse(self, raw: RawListing) -> Listing:
        next_data = extract_next_data(raw.text())
        if next_data is None:
            raise ValueError("No __NEXT_DATA__ block found in Immoweb HTML.")

        classified = _find_classified(next_data)
        if classified is None:
            raise ValueError("__NEXT_DATA__ does not contain a classified payload.")

        address = classified.get("address") or {}
        price = classified.get("price") or {}
        epc = classified.get("epc") or classified.get("certificates") or {}

        postcode = _coerce_int(address.get("postalCode"))
        if postcode is None:
            raise ValueError("Immoweb classified has no postalCode")

        gemeente = (address.get("city") or address.get("locality") or "").strip()
        if not gemeente:
            raise ValueError("Immoweb classified has no city")

        street = address.get("street") or ""
        house_number = address.get("houseNumber") or address.get("number") or ""
        straat_pieces = [p for p in (street, house_number) if p]
        straat = " ".join(str(p).strip() for p in straat_pieces).strip() or None

        # Geo: Immoweb stores lat/lng on the classified.location block
        location = classified.get("location") or {}
        try:
            lat = float(location["latitude"]) if location.get("latitude") is not None else None
        except (TypeError, ValueError):
            lat = None
        try:
            lng = float(location["longitude"]) if location.get("longitude") is not None else None
        except (TypeError, ValueError):
            lng = None

        opp = _coerce_int(classified.get("habitableSurface") or classified.get("livingArea"))
        slaapkamers = _coerce_int(classified.get("bedrooms") or classified.get("bedroomCount"))
        prijs = _coerce_int(price.get("mainValue") or price.get("amount") or price.get("value"))

        sub_type = (classified.get("subType") or classified.get("type") or "").lower()
        prop_type = _PROPERTY_TYPE_MAPPING.get(sub_type, PropertyType.OTHER)
        if prop_type is PropertyType.OTHER:
            # Fallback to text-classifier on title
            prop_type = _classify_property_type(
                str(classified.get("title") or classified.get("name") or "")
            )

        # buildingCondition is Immoweb's own enum (English). Map directly;
        # fall back to text-classifying the Dutch description.
        bc_raw = classified.get("buildingCondition")
        bc_key = str(bc_raw).strip().lower() if isinstance(bc_raw, str) else ""
        staat = _BUILDING_CONDITION_MAPPING.get(bc_key) or _classify_condition(
            str(classified.get("description") or "")
        )
        epc_label = _classify_epc(epc.get("label") or epc.get("score"))
        epc_kwh = _coerce_int(epc.get("consumption") or epc.get("kWhPerSquareMeter"))

        source_id_raw = classified.get("id") or classified.get("classifiedId")
        source_id = str(source_id_raw) if source_id_raw is not None else None
        if not source_id:
            raise ValueError("Immoweb classified has no id")

        source_url = classified.get("url") or raw.source_url

        media = classified.get("media") or {}
        photos = media.get("photos") if isinstance(media, dict) else None
        if isinstance(photos, list) and photos:
            first_photo = photos[0]
            if isinstance(first_photo, dict):
                image = first_photo.get("url") or first_photo.get("large")
            else:
                image = first_photo
        else:
            image = classified.get("mainPhoto") or classified.get("image")

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
                "lat": lat,
                "lng": lng,
                "prijs_eur": prijs,
                "type": prop_type,
                "oppervlakte_bewoonbaar_m2": opp,
                "slaapkamers": slaapkamers,
                "epc_label": epc_label,
                "epc_kwh": epc_kwh,
                "staat": staat,
                "titel": str(classified.get("title") or classified.get("name") or "").strip(),
                "korte_beschrijving": str(classified.get("description") or "").strip(),
                "hoofd_foto_url": image,
            }
        )


_: Connector = ImmowebConnector()
