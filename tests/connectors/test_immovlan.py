"""Immovlan parser tests — runs entirely offline against the saved fixture."""

from __future__ import annotations

from pathlib import Path

import pytest

from localizer.connectors.base import RawListing
from localizer.connectors.immovlan import ImmovlanConnector
from localizer.core.models import (
    Condition,
    EpcLabel,
    PropertyType,
    SourceName,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


@pytest.fixture
def immovlan_listing_raw() -> RawListing:
    body = (FIXTURES / "immovlan_listing.html").read_bytes()
    return RawListing(
        source_url="https://immovlan.be/nl/detail/huis/te-koop/8000/brugge/vbe12345",
        body=body,
    )


def test_immovlan_parses_canonical_fields(immovlan_listing_raw: RawListing) -> None:
    listing = ImmovlanConnector().parse(immovlan_listing_raw)

    assert listing.postcode == 8000
    assert listing.gemeente == "Brugge"
    assert listing.straat == "Maria-Theresiastraat 8"
    assert listing.prijs_eur == 415000
    assert listing.oppervlakte_bewoonbaar_m2 == 175
    assert listing.slaapkamers == 4
    assert listing.epc_label is EpcLabel.C
    assert listing.epc_kwh == 210
    assert listing.staat is Condition.INSTAPKLAAR
    assert listing.type is PropertyType.HUIS  # title "...woning..." → HUIS


def test_immovlan_attaches_correct_source(immovlan_listing_raw: RawListing) -> None:
    listing = ImmovlanConnector().parse(immovlan_listing_raw)
    assert listing.sources[0].source_name is SourceName.IMMOVLAN
    assert listing.sources[0].source_id == "vbe12345"


def test_immovlan_rejects_html_without_jsonld() -> None:
    raw = RawListing(
        source_url="https://www.immovlan.be/nl/detail/0",
        body=b"<html></html>",
    )
    with pytest.raises(ValueError):
        ImmovlanConnector().parse(raw)
