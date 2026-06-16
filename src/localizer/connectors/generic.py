"""Generic connector — parses any third-party listing URL.

Used for the "URL toevoegen" feature: Vergo plakt een URL van een
willekeurige makelaars- of vastgoedsite, en wij proberen er een
`Listing` van te maken zonder voorkennis van de site.

Strategie (in volgorde van betrouwbaarheid):

  1. **JSON-LD** (Schema.org) — veel sites embedden Product,
     RealEstateListing, House, Residence, Place of SingleFamilyResidence.
     Als aanwezig, krijg je ~90% van de velden goed.
  2. **OpenGraph** — `og:title`, `og:image`, `og:description`,
     `og:url`. Bijna élke site heeft dit. Genoeg voor lijstweergave.
  3. **Regex op visible text** — prijs (€-pattern), m², slaapkamers,
     EPC, postcode. Best-effort.

`discover()` levert NIETS op — generic.py wordt nooit door de
sitemap-runner gebruikt. URLs komen via de UI ("URL toevoegen").
`fetch()` en `parse()` worden expliciet aangeroepen door de UI-handler.

Source_id is een MD5-hash van de URL. Stabiel, uniek per pagina, en
voorkomt dedup-collisions met de sitemap-bronnen.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Iterable
from typing import Any, ClassVar
from urllib.parse import urlparse

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
from localizer.connectors.base import (
    Connector,
    HTTPClientProtocol,
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

# JSON-LD types we'll happily extract from, in fallback order.
_JSONLD_TYPES = (
    "RealEstateListing",
    "Product",
    "Residence",
    "House",
    "SingleFamilyResidence",
    "Apartment",
    "Place",
)

# Belgian postcode in free text: 1000-9999, optionally followed by a
# capitalised town name. We require the town-suffix in strict mode to
# avoid catching "9000 EUR" or "1990" (year). Loose-mode is the
# fallback for sites that don't print "<postcode> <town>" together.
_RE_POSTCODE_TOWN = re.compile(r"\b([1-9]\d{3})\s+([A-ZÀ-Ý][\w\-']{2,30})")
_RE_POSTCODE_LOOSE = re.compile(r"\b([1-9]\d{3})\b")


def _extract_postcode_and_town(text: str) -> tuple[int | None, str | None]:
    """Find a Belgian postcode + town pair in `text`.

    Strict pattern first: '<postcode> <Capitalised Town>'. Falls back to
    just the first plausible 4-digit number if no town is adjacent.
    """
    m = _RE_POSTCODE_TOWN.search(text)
    if m:
        return int(m.group(1)), m.group(2).strip()
    m = _RE_POSTCODE_LOOSE.search(text)
    if m:
        return int(m.group(1)), None
    return None, None


def _epc_from_label_string(value: str | None) -> EpcLabel | None:
    if not value:
        return None
    cleaned = value.strip().upper()
    for label in EpcLabel:
        if cleaned == label.value.upper():
            return label
    if cleaned and cleaned[0] in "ABCDEF":
        for label in EpcLabel:
            if label.value.upper() == cleaned[0]:
                return label
    return None


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


def _first_jsonld_match(blocks: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the first JSON-LD block matching any type in `_JSONLD_TYPES`."""
    for type_name in _JSONLD_TYPES:
        block = find_jsonld_block(blocks, type_name)
        if block is not None:
            return block
    return None


def _extract_jsonld_image(block: dict[str, Any]) -> str | None:
    images = block.get("image")
    if isinstance(images, list) and images:
        first = images[0]
        if isinstance(first, dict):
            return first.get("url") or first.get("contentUrl")
        return str(first)
    if isinstance(images, str):
        return images
    if isinstance(images, dict):
        return images.get("url") or images.get("contentUrl")
    return None


def _short_source_id_from_url(url: str) -> str:
    """Stable, short id derived from the full URL. Keeps fingerprint
    collisions impossible across different sites. 16 hex chars =
    plenty of headroom for a personal-use database."""
    return hashlib.md5(url.encode("utf-8")).hexdigest()[:16]


class GenericConnector:
    """Best-effort parser for arbitrary listing URLs.

    Never appears in the registry's `DEFAULT_ENABLED` set — only invoked
    explicitly by the URL-paste UI flow.
    """

    name: ClassVar[SourceName] = SourceName.DIVERSEN
    display_name: ClassVar[str] = "Diverse"
    base_url: ClassVar[str] = ""
    requires_browser: ClassVar[bool] = False

    def discover(
        self, client: HTTPClientProtocol, postcodes: Iterable[int]
    ) -> Iterable[ListingRef]:
        # No sitemap — URLs are user-supplied.
        return iter(())

    def fetch(self, client: HTTPClientProtocol, ref: ListingRef) -> RawListing:
        return client.get(ref.url)

    def parse(self, raw: RawListing) -> Listing:
        text = raw.text()
        url = str(raw.source_url)

        # ---- Step 1: JSON-LD (best quality if present)
        blocks = extract_json_ld(text)
        ld = _first_jsonld_match(blocks) or {}
        # House/Residence-type blocks may also be present alongside the
        # primary block (mirror of Immoscoop's layout). Pull address/geo
        # from any structurally-rich block, even if it's not the primary.
        ld_address: dict[str, Any] = {}
        ld_geo: dict[str, Any] = {}
        for candidate in (ld, *blocks):
            if not isinstance(candidate, dict):
                continue
            addr = candidate.get("address")
            if isinstance(addr, dict) and not ld_address:
                ld_address = addr
            geo = candidate.get("geo")
            if isinstance(geo, dict) and not ld_geo:
                ld_geo = geo

        # ---- Step 2: OpenGraph (universal fallback)
        og_title = extract_meta_content(text, key="og:title")
        og_description = extract_meta_content(text, key="og:description")
        og_image = extract_meta_content(text, key="og:image")

        # ---- Compose name + description
        name = (ld.get("name") or og_title or "").strip()
        description = (ld.get("description") or og_description or "").strip()

        # ---- Address (postcode + town)
        postcode = _coerce_int(ld_address.get("postalCode"))
        gemeente = (ld_address.get("addressLocality") or "").strip() or None
        straat = (ld_address.get("streetAddress") or "").strip() or None

        if postcode is None or not gemeente:
            # Fall back to free-text scan over title + description +
            # body. Order matters: structured fields first, then prose.
            haystack = " ".join(filter(None, [name, description, text]))
            pc_text, town_text = _extract_postcode_and_town(haystack)
            if postcode is None and pc_text is not None:
                postcode = pc_text
            if not gemeente and town_text:
                gemeente = town_text

        if postcode is None:
            raise ValueError(f"Generic parser could not find a Belgian postcode in {url}")
        if not gemeente:
            gemeente = "?"  # surface visible to user — they can fix later

        # ---- Geo
        lat = _coerce_float(ld_geo.get("latitude"))
        lng = _coerce_float(ld_geo.get("longitude"))

        # ---- Price (JSON-LD offers, then regex on body)
        prijs: int | None = None
        offers = ld.get("offers") or {}
        if isinstance(offers, dict):
            prijs = _coerce_int(offers.get("price"))
        elif isinstance(offers, list) and offers and isinstance(offers[0], dict):
            prijs = _coerce_int(offers[0].get("price"))
        if prijs is None:
            prijs = regex_extract_price(text)

        # ---- Hoofdfoto (JSON-LD image, then og:image)
        hoofd_foto = _extract_jsonld_image(ld) or og_image

        # ---- Type + staat (classify on combined text)
        type_text = " ".join(filter(None, [name, description]))
        prop_type = _classify_property_type(type_text)
        staat = _classify_condition(description or name)

        # ---- Specs from body regex (most third-party sites don't put
        #      these in JSON-LD — same situation as Immoscoop)
        slaapkamers = _coerce_int(ld.get("numberOfRooms")) or regex_extract_bedrooms(text)
        opp_jsonld = ld.get("floorSize") or {}
        opp = (
            _coerce_int(opp_jsonld.get("value") if isinstance(opp_jsonld, dict) else None)
            or regex_extract_surface(text)
        )
        epc_label_str = regex_extract_epc_label(text)
        epc_label = _epc_from_label_string(epc_label_str)
        epc_kwh = regex_extract_epc_kwh(text)

        # ---- Identity: hash the URL to keep fingerprints unique across
        # sites without relying on the site exposing a clean ID.
        source_id = _short_source_id_from_url(url)
        fingerprint = compute_fingerprint(source_name=self.name, source_id=source_id)

        return Listing.model_validate(
            {
                "fingerprint": fingerprint,
                "sources": [
                    ListingSource.model_validate(
                        {
                            "source_name": self.name,
                            "source_id": source_id,
                            "source_url": url,
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
                # `titel` and `korte_beschrijving` are non-nullable in
                # the Listing model — fall back to empty string instead
                # of None when the page yielded no name/description.
                "titel": name[:200] if name else "",
                "korte_beschrijving": description[:500] if description else "",
                "hoofd_foto_url": hoofd_foto,
            }
        )


_: Connector = GenericConnector()


# ---------------------------------------------------------------------------
# URL → Connector dispatch
# ---------------------------------------------------------------------------
# Domain-prefix matching. Order is irrelevant; longest-match isn't
# necessary because every entry below is a unique top-level pair.
_DOMAIN_TO_SOURCE: dict[str, SourceName] = {
    "immoscoop.be": SourceName.IMMOSCOOP,
    "immovlan.be": SourceName.IMMOVLAN,
    "zimmo.be": SourceName.ZIMMO,
    "immoweb.be": SourceName.IMMOWEB,
}


def source_for_url(url: str) -> SourceName:
    """Return the SourceName that should handle `url`.

    Known portals route to their native connector (better data quality);
    anything else falls back to `DIVERSEN` (the generic parser).
    """
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return SourceName.DIVERSEN
    if not host:
        return SourceName.DIVERSEN
    # Strip leading "www."
    if host.startswith("www."):
        host = host[4:]
    for domain, source in _DOMAIN_TO_SOURCE.items():
        if host == domain or host.endswith("." + domain):
            return source
    return SourceName.DIVERSEN
