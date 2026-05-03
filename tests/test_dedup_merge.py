"""Tests for `dedup.merge_or_insert` — the cross-source merge step."""

from __future__ import annotations

from pathlib import Path

import pytest

from localizer.core import db, dedup
from localizer.core.models import (
    Listing,
    ListingSource,
    PropertyType,
    SourceName,
)


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> Path:
    path = tmp_path / "merge.db"
    db.initialise(path)
    return path


def _listing(
    *,
    source: SourceName,
    source_id: str,
    source_url: str,
    fingerprint: str = "abcdef0123456789",
    **overrides: object,
) -> Listing:
    payload: dict[str, object] = {
        "fingerprint": fingerprint,
        "sources": [
            ListingSource.model_validate(
                {
                    "source_name": source,
                    "source_id": source_id,
                    "source_url": source_url,
                }
            )
        ],
        "postcode": 8500,
        "gemeente": "Kortrijk",
        "type": PropertyType.HUIS,
    }
    payload.update(overrides)
    return Listing.model_validate(payload)


def test_first_listing_is_inserted(tmp_db_path: Path) -> None:
    incoming = _listing(
        source=SourceName.ZIMMO,
        source_id="z-1",
        source_url="https://zimmo.example/1",
    )
    with db.connect(tmp_db_path) as conn:
        stored, inserted = dedup.merge_or_insert(conn, incoming)
    assert inserted is True
    assert stored.id == incoming.id


def test_second_listing_with_same_fingerprint_merges_and_keeps_id(
    tmp_db_path: Path,
) -> None:
    fp = "fingerprint-1234567890ab"
    first = _listing(
        source=SourceName.ZIMMO,
        source_id="z-1",
        source_url="https://zimmo.example/1",
        fingerprint=fp,
        prijs_eur=300000,
    )
    second = _listing(
        source=SourceName.IMMOSCOOP,
        source_id="i-2",
        source_url="https://immoscoop.example/2",
        fingerprint=fp,
        prijs_eur=300000,
    )

    with db.connect(tmp_db_path) as conn:
        _, inserted_first = dedup.merge_or_insert(conn, first)
        merged, inserted_second = dedup.merge_or_insert(conn, second)

    assert inserted_first is True
    assert inserted_second is False
    # The canonical UUID is the *first* one — that's the stable handle.
    assert merged.id == first.id
    assert {s.source_name for s in merged.sources} == {
        SourceName.ZIMMO,
        SourceName.IMMOSCOOP,
    }


def test_merge_fills_null_fields_from_candidate(tmp_db_path: Path) -> None:
    fp = "fingerprint-fill-nulls0"
    sparse = _listing(
        source=SourceName.ZIMMO,
        source_id="z-1",
        source_url="https://zimmo.example/1",
        fingerprint=fp,
        # No prijs_eur, no straat
    )
    rich = _listing(
        source=SourceName.IMMOSCOOP,
        source_id="i-2",
        source_url="https://immoscoop.example/2",
        fingerprint=fp,
        prijs_eur=375000,
        straat="Onbekendstraat 5",
    )

    with db.connect(tmp_db_path) as conn:
        dedup.merge_or_insert(conn, sparse)
        merged, _ = dedup.merge_or_insert(conn, rich)

    assert merged.prijs_eur == 375000
    assert merged.straat == "Onbekendstraat 5"


def test_merge_does_not_overwrite_existing_field(tmp_db_path: Path) -> None:
    fp = "fingerprint-no-overwrite"
    first = _listing(
        source=SourceName.ZIMMO,
        source_id="z-1",
        source_url="https://zimmo.example/1",
        fingerprint=fp,
        prijs_eur=400000,
    )
    later = _listing(
        source=SourceName.IMMOSCOOP,
        source_id="i-2",
        source_url="https://immoscoop.example/2",
        fingerprint=fp,
        prijs_eur=999999,
    )

    with db.connect(tmp_db_path) as conn:
        dedup.merge_or_insert(conn, first)
        merged, _ = dedup.merge_or_insert(conn, later)

    # Existing non-null wins; we don't yet do "latest wins" — see V1.1.
    assert merged.prijs_eur == 400000


def test_merge_dedups_same_source_id(tmp_db_path: Path) -> None:
    """Re-running the same connector against the same listing keeps 1 source row."""
    fp = "fingerprint-same-source"
    listing_v1 = _listing(
        source=SourceName.ZIMMO,
        source_id="z-1",
        source_url="https://zimmo.example/1",
        fingerprint=fp,
    )
    listing_v2 = _listing(
        source=SourceName.ZIMMO,
        source_id="z-1",
        source_url="https://zimmo.example/1",
        fingerprint=fp,
    )

    with db.connect(tmp_db_path) as conn:
        dedup.merge_or_insert(conn, listing_v1)
        merged, _ = dedup.merge_or_insert(conn, listing_v2)

    assert len(merged.sources) == 1
