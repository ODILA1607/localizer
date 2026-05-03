"""Immoweb parser tests — runs entirely offline against the saved fixture."""

from __future__ import annotations

from pathlib import Path

import pytest

from localizer.connectors.base import RawListing
from localizer.connectors.immoweb import ImmowebConnector
from localizer.core.models import (
    Condition,
    EpcLabel,
    PropertyType,
    SourceName,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


@pytest.fixture
def immoweb_listing_raw() -> RawListing:
    body = (FIXTURES / "immoweb_listing.html").read_bytes()
    return RawListing(
        source_url="https://www.immoweb.be/nl/classified/villa/te-koop/brugge/8000/11223344",
        body=body,
    )


def test_immoweb_parses_canonical_fields(immoweb_listing_raw: RawListing) -> None:
    listing = ImmowebConnector().parse(immoweb_listing_raw)

    assert listing.postcode == 8000
    assert listing.gemeente == "Brugge"
    assert listing.straat == "Lindenlaan 27"
    assert listing.prijs_eur == 510000
    assert listing.oppervlakte_bewoonbaar_m2 == 220
    assert listing.slaapkamers == 4
    assert listing.epc_label is EpcLabel.F
    assert listing.epc_kwh == 480
    assert listing.staat is Condition.TE_RENOVEREN
    assert listing.type is PropertyType.HUIS  # subType "villa" → HUIS


def test_immoweb_attaches_correct_source(immoweb_listing_raw: RawListing) -> None:
    listing = ImmowebConnector().parse(immoweb_listing_raw)
    assert listing.sources[0].source_name is SourceName.IMMOWEB
    assert listing.sources[0].source_id == "11223344"


def test_immoweb_uses_first_photo_url(immoweb_listing_raw: RawListing) -> None:
    listing = ImmowebConnector().parse(immoweb_listing_raw)
    assert listing.hoofd_foto_url is not None
    assert "11223344" in str(listing.hoofd_foto_url)


def test_immoweb_rejects_page_without_next_data() -> None:
    raw = RawListing(
        source_url="https://www.immoweb.be/nl/classified/villa/x/8000/0",
        body=b"<html><body>no next data here</body></html>",
    )
    with pytest.raises(ValueError, match="__NEXT_DATA__"):
        ImmowebConnector().parse(raw)
