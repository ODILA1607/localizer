"""Immoscoop parser tests — runs entirely offline against the saved fixture."""

from __future__ import annotations

from pathlib import Path

import pytest

from localizer.connectors.base import RawListing
from localizer.connectors.immoscoop import ImmoscoopConnector
from localizer.core.models import (
    Condition,
    EpcLabel,
    PropertyType,
    SourceName,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


@pytest.fixture
def immoscoop_listing_raw() -> RawListing:
    body = (FIXTURES / "immoscoop_listing.html").read_bytes()
    return RawListing(
        source_url="https://www.immoscoop.be/te-koop/9000-gent/987",
        body=body,
    )


def test_immoscoop_parses_canonical_fields(
    immoscoop_listing_raw: RawListing,
) -> None:
    listing = ImmoscoopConnector().parse(immoscoop_listing_raw)

    assert listing.postcode == 9000
    assert listing.gemeente == "Gent"
    assert listing.straat == "Sint-Pietersnieuwstraat 45"
    assert listing.prijs_eur == 365000
    assert listing.oppervlakte_bewoonbaar_m2 == 92
    assert listing.slaapkamers == 2
    assert listing.epc_label is EpcLabel.B
    assert listing.epc_kwh == 95
    assert listing.staat is Condition.INSTAPKLAAR
    assert listing.type is PropertyType.APPARTEMENT


def test_immoscoop_picks_first_image_from_list(
    immoscoop_listing_raw: RawListing,
) -> None:
    listing = ImmoscoopConnector().parse(immoscoop_listing_raw)
    assert listing.hoofd_foto_url is not None
    assert "test-1.jpg" in str(listing.hoofd_foto_url)


def test_immoscoop_extracts_geo_coordinates(
    immoscoop_listing_raw: RawListing,
) -> None:
    listing = ImmoscoopConnector().parse(immoscoop_listing_raw)
    assert listing.lat is not None
    assert listing.lng is not None
    assert 51.0 < listing.lat < 51.1  # Gent is around 51.05
    assert 3.7 < listing.lng < 3.8


def test_immoscoop_attaches_correct_source(
    immoscoop_listing_raw: RawListing,
) -> None:
    listing = ImmoscoopConnector().parse(immoscoop_listing_raw)
    assert listing.sources[0].source_name is SourceName.IMMOSCOOP
    assert listing.sources[0].source_id == "987"


def test_immoscoop_rejects_html_without_jsonld() -> None:
    raw = RawListing(
        source_url="https://www.immoscoop.be/nl/x/9999",
        body=b"<html></html>",
    )
    with pytest.raises(ValueError):
        ImmoscoopConnector().parse(raw)
