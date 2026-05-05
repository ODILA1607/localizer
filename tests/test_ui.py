"""UI smoke tests — boot the FastAPI app against a tmp DB and hit routes."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from localizer.connectors.base import RawListing
from localizer.connectors.immoscoop import ImmoscoopConnector
from localizer.core import db, dedup
from localizer.ui import server as ui_server

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def populated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A tmp DB seeded with one parsed listing from the Immoscoop fixture."""
    path = tmp_path / "ui.db"
    db.initialise(path)

    raw = RawListing(
        source_url="https://www.immoscoop.be/te-koop/9000-gent/987",
        body=(FIXTURES / "immoscoop_listing.html").read_bytes(),
    )
    listing = ImmoscoopConnector().parse(raw)
    with db.connect(path) as conn:
        dedup.merge_or_insert(conn, listing)

    # Redirect the UI's `default_db_path()` to our tmp DB.
    monkeypatch.setattr(ui_server, "default_db_path", lambda: path)
    return path


def test_index_renders_with_seeded_listing(populated_db: Path) -> None:
    with TestClient(ui_server.app) as client:
        response = client.get("/")
    assert response.status_code == 200
    body = response.text
    assert "Localizer" in body
    assert "9000" in body  # postcode of seeded listing
    assert "Gent" in body  # gemeente


def test_index_filters_by_postcode(populated_db: Path) -> None:
    with TestClient(ui_server.app) as client:
        match = client.get("/?postcode=9000").text
        miss = client.get("/?postcode=8000").text
    assert "Gent" in match
    assert "9000" in match
    # 8000 returns the empty-state message, not the seeded listing
    assert "Geen listings" in miss


def test_index_accepts_blank_int_filters(populated_db: Path) -> None:
    """Browsers submit blank number inputs as `?prijs_min=&...`. Empty
    strings must coerce to None — without it, FastAPI's int parser
    raises 422 and the filter form breaks."""
    with TestClient(ui_server.app) as client:
        r = client.get(
            "/?gemeente=&postcode=&prijs_min=&prijs_max=&slaapkamers_min=&opp_min="
        )
    assert r.status_code == 200
    assert "Gent" in r.text  # seeded listing visible


def test_map_accepts_blank_int_filters(populated_db: Path) -> None:
    with TestClient(ui_server.app) as client:
        r = client.get("/map?prijs_min=&prijs_max=&slaapkamers_min=&opp_min=&postcode=")
    assert r.status_code == 200


def test_set_status_updates_user_status(populated_db: Path) -> None:
    with db.connect(populated_db) as conn:
        rows = db.all_listings(conn)
    listing_id = str(rows[0].id)

    with TestClient(ui_server.app) as client:
        r = client.post(
            f"/listings/{listing_id}/status",
            data={"status": "interessant", "next": "/"},
            follow_redirects=False,
        )
    assert r.status_code == 303

    with db.connect(populated_db) as conn:
        rows = db.all_listings(conn)
    assert rows[0].user_status.value == "interessant"


def test_set_status_rejects_invalid_value(populated_db: Path) -> None:
    with db.connect(populated_db) as conn:
        rows = db.all_listings(conn)
    listing_id = str(rows[0].id)

    with TestClient(ui_server.app) as client:
        client.post(
            f"/listings/{listing_id}/status",
            data={"status": "not-a-real-status"},
            follow_redirects=False,
        )

    with db.connect(populated_db) as conn:
        rows = db.all_listings(conn)
    # Status unchanged
    assert rows[0].user_status.value == "nieuw"
