"""Tests for the canonical Listing schema."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from localizer.core.models import (
    Condition,
    EpcLabel,
    Listing,
    ListingSource,
    ListingStatus,
    PropertyType,
    SourceName,
    UserStatus,
)


def _make_source(**overrides: object) -> ListingSource:
    base: dict[str, object] = {
        "source_name": SourceName.ZIMMO,
        "source_id": "abc-123",
        "source_url": "https://www.zimmo.be/nl/example/",
    }
    base.update(overrides)
    return ListingSource.model_validate(base)


def _make_listing(**overrides: object) -> Listing:
    base: dict[str, object] = {
        "fingerprint": "0123456789abcdef",
        "sources": [_make_source()],
        "postcode": 8500,
        "gemeente": "Kortrijk",
    }
    base.update(overrides)
    return Listing.model_validate(base)


def test_listing_minimal_construction() -> None:
    listing = _make_listing()
    assert listing.gemeente == "Kortrijk"
    assert listing.user_status is UserStatus.NIEUW
    assert listing.type is PropertyType.OTHER
    assert listing.sources[0].status is ListingStatus.ACTIVE


def test_listing_postcode_must_be_belgian() -> None:
    with pytest.raises(ValidationError):
        _make_listing(postcode=999)
    with pytest.raises(ValidationError):
        _make_listing(postcode=10000)


def test_listing_must_have_at_least_one_source() -> None:
    with pytest.raises(ValidationError):
        Listing.model_validate(
            {
                "fingerprint": "0123456789abcdef",
                "sources": [],
                "postcode": 8500,
                "gemeente": "Kortrijk",
            }
        )


def test_listing_with_full_payload() -> None:
    listing = _make_listing(
        straat="Kerkstraat 12",
        prijs_eur=325000,
        type=PropertyType.HUIS,
        oppervlakte_bewoonbaar_m2=140,
        slaapkamers=3,
        epc_label=EpcLabel.D,
        epc_kwh=320,
        staat=Condition.TE_RENOVEREN,
        titel="Te renoveren rijwoning",
    )
    assert listing.epc_label is EpcLabel.D
    assert listing.staat is Condition.TE_RENOVEREN
    assert listing.prijs_eur == 325000


def test_listing_source_url_must_be_http() -> None:
    with pytest.raises(ValidationError):
        _make_source(source_url="not-a-url")


def test_listing_negative_price_rejected() -> None:
    with pytest.raises(ValidationError):
        _make_listing(prijs_eur=-1)
