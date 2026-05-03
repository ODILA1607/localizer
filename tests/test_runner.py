"""Runner tests — exercise orchestration with a fake connector, no network."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import ClassVar

from localizer.connectors.base import (
    HTTPClient,
    ListingRef,
    RawListing,
)
from localizer.core import db
from localizer.core.dedup import compute_fingerprint
from localizer.core.models import (
    Listing,
    ListingSource,
    PropertyType,
    SourceName,
)
from localizer.core.runner import run_refresh


# ---------------------------------------------------------------------------
# A fake connector: returns canned data, never touches the network.
# ---------------------------------------------------------------------------
class _FakeConnector:
    name: ClassVar[SourceName] = SourceName.ZIMMO
    display_name: ClassVar[str] = "Zimmo (fake)"
    base_url: ClassVar[str] = "https://example.test"

    def __init__(self, listings: list[Listing]) -> None:
        self._listings = listings
        self._refs = [ListingRef(url=str(listing.sources[0].source_url)) for listing in listings]
        self._by_url = {ref.url: listing for ref, listing in zip(self._refs, listings, strict=True)}

    def discover(self, client: HTTPClient, postcodes: Iterable[int]) -> Iterable[ListingRef]:
        return list(self._refs)

    def fetch(self, client: HTTPClient, ref: ListingRef) -> RawListing:
        # Body is never inspected because parse() returns a pre-built listing.
        return RawListing(source_url=ref.url, body=b"<html></html>")

    def parse(self, raw: RawListing) -> Listing:
        return self._by_url[raw.source_url]


def _make_listing(
    *,
    source: SourceName,
    source_id: str,
    source_url: str,
    fingerprint: str,
    postcode: int = 8500,
    gemeente: str = "Kortrijk",
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
        "postcode": postcode,
        "gemeente": gemeente,
        "type": PropertyType.HUIS,
    }
    payload.update(overrides)
    return Listing.model_validate(payload)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_runner_inserts_each_unique_listing(tmp_path: Path) -> None:
    listings = [
        _make_listing(
            source=SourceName.ZIMMO,
            source_id="z-1",
            source_url="https://example.test/1",
            fingerprint=compute_fingerprint(
                straat="A 1", postcode=8500, oppervlakte_bewoonbaar_m2=100, slaapkamers=2
            ),
        ),
        _make_listing(
            source=SourceName.ZIMMO,
            source_id="z-2",
            source_url="https://example.test/2",
            fingerprint=compute_fingerprint(
                straat="B 2", postcode=8500, oppervlakte_bewoonbaar_m2=120, slaapkamers=3
            ),
        ),
    ]
    db_path = tmp_path / "loc.db"
    fake_client = HTTPClient()  # never touched by FakeConnector
    try:
        result = run_refresh(
            connectors=[_FakeConnector(listings)],
            client=fake_client,
            db_path=db_path,
            postcodes=[8500],
        )
    finally:
        fake_client.close()

    [cr] = result.per_connector
    assert cr.discovered == 2
    assert cr.fetched == 2
    assert cr.parsed == 2
    assert cr.inserted == 2
    assert cr.merged == 0
    assert cr.errors == []

    with db.connect(db_path) as conn:
        assert len(db.all_listings(conn)) == 2


def test_runner_merges_duplicates_across_connectors(tmp_path: Path) -> None:
    """Same property indexed by Zimmo and Immoscoop merges into 1 canonical row."""
    fp = compute_fingerprint(
        straat="Kerkstraat 12",
        postcode=8500,
        oppervlakte_bewoonbaar_m2=140,
        slaapkamers=3,
    )
    zimmo_listing = _make_listing(
        source=SourceName.ZIMMO,
        source_id="z-100",
        source_url="https://zimmo.example/100",
        fingerprint=fp,
        straat="Kerkstraat 12",
        prijs_eur=275000,
        slaapkamers=3,
        oppervlakte_bewoonbaar_m2=140,
    )
    immoscoop_listing = _make_listing(
        source=SourceName.IMMOSCOOP,
        source_id="i-200",
        source_url="https://immoscoop.example/200",
        fingerprint=fp,
        straat="Kerkstraat 12",
        prijs_eur=275000,
        slaapkamers=3,
        oppervlakte_bewoonbaar_m2=140,
    )

    class _Z(_FakeConnector):
        name: ClassVar[SourceName] = SourceName.ZIMMO

    class _I(_FakeConnector):
        name: ClassVar[SourceName] = SourceName.IMMOSCOOP

    db_path = tmp_path / "loc.db"
    fake_client = HTTPClient()
    try:
        result = run_refresh(
            connectors=[_Z([zimmo_listing]), _I([immoscoop_listing])],
            client=fake_client,
            db_path=db_path,
            postcodes=[8500],
        )
    finally:
        fake_client.close()

    assert result.total_inserted == 1
    assert result.total_merged == 1
    with db.connect(db_path) as conn:
        rows = db.all_listings(conn)
    assert len(rows) == 1
    [merged] = rows
    assert {s.source_name for s in merged.sources} == {
        SourceName.ZIMMO,
        SourceName.IMMOSCOOP,
    }


def test_runner_records_per_listing_errors_without_aborting(tmp_path: Path) -> None:
    listings = [
        _make_listing(
            source=SourceName.ZIMMO,
            source_id="ok",
            source_url="https://example.test/ok",
            fingerprint=compute_fingerprint(
                straat="ok", postcode=8500, oppervlakte_bewoonbaar_m2=100, slaapkamers=2
            ),
        ),
    ]

    class _BoomFetcher(_FakeConnector):
        def fetch(self, client: HTTPClient, ref: ListingRef) -> RawListing:
            if ref.url.endswith("/boom"):
                raise RuntimeError("simulated network failure")
            return super().fetch(client, ref)

    boom_listings = [
        *listings,
        _make_listing(
            source=SourceName.ZIMMO,
            source_id="boom",
            source_url="https://example.test/boom",
            fingerprint=compute_fingerprint(
                straat="boom", postcode=8500, oppervlakte_bewoonbaar_m2=100, slaapkamers=2
            ),
        ),
    ]

    db_path = tmp_path / "loc.db"
    fake_client = HTTPClient()
    try:
        result = run_refresh(
            connectors=[_BoomFetcher(boom_listings)],
            client=fake_client,
            db_path=db_path,
            postcodes=[8500],
        )
    finally:
        fake_client.close()

    [cr] = result.per_connector
    assert cr.discovered == 2
    assert cr.fetched == 1  # the OK one
    assert cr.inserted == 1
    assert any("simulated network failure" in e for e in cr.errors)


def test_runner_handles_not_implemented_gracefully(tmp_path: Path) -> None:
    """A connector that hasn't shipped discover() yet must not crash the runner."""

    class _NotReady:
        name: ClassVar[SourceName] = SourceName.IMMOWEB
        display_name: ClassVar[str] = "Not ready"
        base_url: ClassVar[str] = "https://x.test"

        def discover(self, client: HTTPClient, postcodes: Iterable[int]) -> Iterable[ListingRef]:
            raise NotImplementedError("not yet")

        def fetch(self, client: HTTPClient, ref: ListingRef) -> RawListing:
            raise NotImplementedError("not yet")

        def parse(self, raw: RawListing) -> Listing:
            raise NotImplementedError("not yet")

    db_path = tmp_path / "loc.db"
    fake_client = HTTPClient()
    try:
        result = run_refresh(
            connectors=[_NotReady()],
            client=fake_client,
            db_path=db_path,
            postcodes=[8500],
        )
    finally:
        fake_client.close()

    [cr] = result.per_connector
    assert cr.discovered == 0
    assert "not implemented" in cr.errors[0]
