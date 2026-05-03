"""Zimmo parser tests — runs entirely offline against the saved fixture."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from localizer.connectors.base import RawListing
from localizer.connectors.zimmo import ZimmoConnector
from localizer.core.models import (
    Condition,
    EpcLabel,
    PropertyType,
    SourceName,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


@pytest.fixture
def zimmo_listing_raw() -> RawListing:
    body = (FIXTURES / "zimmo_listing.html").read_bytes()
    return RawListing(
        source_url="https://www.zimmo.be/nl/8500-kortrijk/te-koop/huis/123456789-charmante-rijwoning/",
        body=body,
        fetched_at=datetime(2026, 5, 3, 12, 0, tzinfo=UTC),
    )


def test_zimmo_parses_canonical_fields(zimmo_listing_raw: RawListing) -> None:
    listing = ZimmoConnector().parse(zimmo_listing_raw)

    assert listing.postcode == 8500
    assert listing.gemeente == "Kortrijk"
    assert listing.straat == "Kerkstraat 12"
    assert listing.prijs_eur == 275000
    assert listing.oppervlakte_bewoonbaar_m2 == 140
    assert listing.slaapkamers == 3
    assert listing.epc_label is EpcLabel.D
    assert listing.epc_kwh == 320
    assert listing.staat is Condition.TE_RENOVEREN
    assert listing.type is PropertyType.HUIS
    assert listing.titel == "Charmante rijwoning te renoveren"


def test_zimmo_attaches_correct_source(zimmo_listing_raw: RawListing) -> None:
    listing = ZimmoConnector().parse(zimmo_listing_raw)

    assert len(listing.sources) == 1
    src = listing.sources[0]
    assert src.source_name is SourceName.ZIMMO
    assert src.source_id == "123456789"
    assert "zimmo.be" in str(src.source_url)


def test_zimmo_fingerprint_is_stable(zimmo_listing_raw: RawListing) -> None:
    a = ZimmoConnector().parse(zimmo_listing_raw)
    b = ZimmoConnector().parse(zimmo_listing_raw)
    assert a.fingerprint == b.fingerprint


def test_zimmo_rejects_html_without_jsonld() -> None:
    raw = RawListing(
        source_url="https://www.zimmo.be/nl/example/123456789",
        body=b"<html><head></head><body>nothing</body></html>",
    )
    with pytest.raises(ValueError, match="Residence-like JSON-LD"):
        ZimmoConnector().parse(raw)
