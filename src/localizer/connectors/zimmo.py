"""Zimmo connector — V1 pilot bron.

Strategy: bot-protection laag 1 (realistic headers + cookies). Zimmo
publishes structured data as JSON-LD (`Residence` / `RealEstateListing`)
in the listing detail page; `parse()` consumes that.

discover() and fetch() land in M3 once we go live against the real site.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any, ClassVar

from localizer.connectors._parsing import extract_json_ld, find_jsonld_by_type
from localizer.connectors.base import (
    Connector,
    HTTPClient,
    ListingRef,
    RawListing,
)
from localizer.core.dedup import compute_fingerprint
from localizer.core.models import (
    Condition,
    EpcLabel,
    Listing,
    ListingSource,
    PropertyType,
    SourceName,
)

_SOURCE_ID_FROM_URL = re.compile(r"/(\d{6,})(?:[-/]|$)")
_PROPERTY_TYPE_FROM_TEXT = (
    ("appartement", PropertyType.APPARTEMENT),
    ("studio", PropertyType.APPARTEMENT),
    ("huis", PropertyType.HUIS),
    ("woning", PropertyType.HUIS),
    ("villa", PropertyType.HUIS),
    ("grond", PropertyType.GROND),
    ("bouwgrond", PropertyType.GROND),
)
_CONDITION_FROM_TEXT = (
    ("nieuwbouw", Condition.NIEUWBOUW),
    ("renoveren", Condition.TE_RENOVEREN),
    ("renovatie", Condition.TE_RENOVEREN),
    ("instapklaar", Condition.INSTAPKLAAR),
)
_EPC_LABELS = {label.value.upper(): label for label in EpcLabel}


def _coerce_int(value: Any) -> int | None:
    """Best-effort conversion of `value` to a non-negative int. None on failure."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(value) if value >= 0 else None
    if isinstance(value, str):
        digits = re.sub(r"[^\d]", "", value)
        if not digits:
            return None
        try:
            return int(digits)
        except ValueError:
            return None
    return None


def _classify_property_type(text: str | None) -> PropertyType:
    if not text:
        return PropertyType.OTHER
    haystack = text.lower()
    for needle, kind in _PROPERTY_TYPE_FROM_TEXT:
        if needle in haystack:
            return kind
    return PropertyType.OTHER


def _classify_condition(text: str | None) -> Condition | None:
    if not text:
        return None
    haystack = text.lower()
    for needle, cond in _CONDITION_FROM_TEXT:
        if needle in haystack:
            return cond
    return None


def _classify_epc(value: Any) -> EpcLabel | None:
    if not isinstance(value, str):
        return None
    return _EPC_LABELS.get(value.strip().upper())


class ZimmoConnector:
    name: ClassVar[SourceName] = SourceName.ZIMMO
    display_name: ClassVar[str] = "Zimmo"
    base_url: ClassVar[str] = "https://www.zimmo.be"

    def discover(self, client: HTTPClient, postcodes: Iterable[int]) -> Iterable[ListingRef]:
        raise NotImplementedError("Zimmo discover() lands in M3.")

    def fetch(self, client: HTTPClient, ref: ListingRef) -> RawListing:
        raise NotImplementedError("Zimmo fetch() lands in M3.")

    def parse(self, raw: RawListing) -> Listing:
        blocks = extract_json_ld(raw.text())
        residence = (
            find_jsonld_by_type(blocks, "Residence")
            or find_jsonld_by_type(blocks, "RealEstateListing")
            or find_jsonld_by_type(blocks, "SingleFamilyResidence")
            or find_jsonld_by_type(blocks, "Apartment")
            or find_jsonld_by_type(blocks, "House")
        )
        if residence is None:
            raise ValueError("No Residence-like JSON-LD block found in Zimmo HTML.")

        address = residence.get("address") or {}
        offers = residence.get("offers") or {}
        floor_size = residence.get("floorSize") or {}

        postcode = _coerce_int(address.get("postalCode"))
        if postcode is None:
            raise ValueError("Zimmo listing has no postalCode")

        gemeente = (address.get("addressLocality") or "").strip()
        if not gemeente:
            raise ValueError("Zimmo listing has no addressLocality")

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
            raise ValueError(f"Cannot extract Zimmo source_id from URL: {source_url}")
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


# Verify protocol conformance at import time (cheap, mypy + runtime catch).
_: Connector = ZimmoConnector()
