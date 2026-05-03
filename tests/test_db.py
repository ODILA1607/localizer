"""Tests for the SQLite storage layer."""

from __future__ import annotations

from pathlib import Path

import pytest

from localizer.core import db
from localizer.core.models import (
    Listing,
    ListingSource,
    ListingStatus,
    PropertyType,
    SourceName,
)


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    db.initialise(path)
    return path


def _make_listing(
    fingerprint: str = "abcdef0123456789",
    *,
    sources: list[ListingSource] | None = None,
    **overrides: object,
) -> Listing:
    payload: dict[str, object] = {
        "fingerprint": fingerprint,
        "sources": sources
        or [
            ListingSource.model_validate(
                {
                    "source_name": SourceName.ZIMMO,
                    "source_id": "z-1",
                    "source_url": "https://www.zimmo.be/listing/1",
                }
            )
        ],
        "postcode": 8500,
        "gemeente": "Kortrijk",
        "type": PropertyType.HUIS,
    }
    payload.update(overrides)
    return Listing.model_validate(payload)


def test_initialise_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "x.db"
    db.initialise(path)
    db.initialise(path)  # second call must not raise
    assert path.exists()


def test_upsert_then_fetch_round_trip(tmp_db_path: Path) -> None:
    listing = _make_listing(prijs_eur=275000, slaapkamers=3)
    with db.connect(tmp_db_path) as conn:
        db.upsert_listing(conn, listing)
        fetched = db.find_listing_by_fingerprint(conn, listing.fingerprint)

    assert fetched is not None
    assert fetched.id == listing.id
    assert fetched.prijs_eur == 275000
    assert fetched.slaapkamers == 3
    assert len(fetched.sources) == 1
    assert fetched.sources[0].source_name is SourceName.ZIMMO


def test_upsert_replaces_sources_on_update(tmp_db_path: Path) -> None:
    """Re-upserting a listing must keep one row per (source_name, source_id)."""
    first = _make_listing()
    with db.connect(tmp_db_path) as conn:
        db.upsert_listing(conn, first)

    updated = first.model_copy(
        update={
            "sources": [
                *first.sources,
                ListingSource.model_validate(
                    {
                        "source_name": SourceName.IMMOSCOOP,
                        "source_id": "i-9",
                        "source_url": "https://www.immoscoop.be/9",
                    }
                ),
            ]
        }
    )
    with db.connect(tmp_db_path) as conn:
        db.upsert_listing(conn, updated)
        fetched = db.find_listing_by_fingerprint(conn, updated.fingerprint)

    assert fetched is not None
    assert {s.source_name for s in fetched.sources} == {
        SourceName.ZIMMO,
        SourceName.IMMOSCOOP,
    }


def test_mark_source_gone_flips_status(tmp_db_path: Path) -> None:
    listing = _make_listing()
    src = listing.sources[0]
    with db.connect(tmp_db_path) as conn:
        db.upsert_listing(conn, listing)
        db.mark_source_gone(conn, src.source_name, src.source_id)
        fetched = db.find_listing_by_fingerprint(conn, listing.fingerprint)

    assert fetched is not None
    assert fetched.sources[0].status is ListingStatus.GONE


def test_all_listings_returns_every_row(tmp_db_path: Path) -> None:
    a = _make_listing(fingerprint="aaaaaaaaaaaaaaaa")
    b = _make_listing(
        fingerprint="bbbbbbbbbbbbbbbb",
        sources=[
            ListingSource.model_validate(
                {
                    "source_name": SourceName.IMMOSCOOP,
                    "source_id": "i-2",
                    "source_url": "https://www.immoscoop.be/2",
                }
            )
        ],
    )
    with db.connect(tmp_db_path) as conn:
        db.upsert_listing(conn, a)
        db.upsert_listing(conn, b)
        rows = db.all_listings(conn)

    assert {str(r.fingerprint) for r in rows} == {a.fingerprint, b.fingerprint}
