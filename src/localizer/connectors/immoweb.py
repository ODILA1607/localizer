"""Immoweb connector — third source (V1.1) wegens Cloudflare-bot-protection.

Strategy: bot-protection laag 2 (curl_cffi TLS-fingerprint) + laag 3
(`__NEXT_DATA__` JSON-blob in de HTML, schoonst voor Immoweb). Playwright
is een laatste redmiddel en wordt niet standaard gebundeld.

discover() and fetch() land in M3 / V1.1; parse() is already wired so
fixtures captured today validate the architecture.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, ClassVar

from localizer.connectors._parsing import extract_next_data
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


class ImmowebConnector:
    name: ClassVar[SourceName] = SourceName.IMMOWEB
    display_name: ClassVar[str] = "Immoweb"
    base_url: ClassVar[str] = "https://www.immoweb.be"

    def discover(self, client: HTTPClient, postcodes: Iterable[int]) -> Iterable[ListingRef]:
        raise NotImplementedError("Immoweb discover() lands in V1.1.")

    def fetch(self, client: HTTPClient, ref: ListingRef) -> RawListing:
        raise NotImplementedError("Immoweb fetch() lands in V1.1.")

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
