"""SQLite storage layer.

One file, one connection-per-call (cheap given single-user desktop app).
Schema is created on first connect; future migrations get a `schema_version`
mechanism here.

Production location on Windows:  `%LOCALAPPDATA%\\Localizer\\localizer.db`
Test location:                    any tmp_path the caller provides.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from localizer.core.models import (
    Listing,
    ListingSource,
    ListingStatus,
    SourceName,
)

SCHEMA_VERSION = 3


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------
def default_db_path() -> Path:
    """`%LOCALAPPDATA%\\Localizer\\localizer.db` on Windows; ~/.localizer/ elsewhere.

    Created on first call.

    Hosted deployments (e.g. the Render demo) set `LOCALIZER_DB_PATH` to
    point at a committed read-only snapshot instead of the per-user app
    folder. On Thomas' desktop the env var is unset and we fall back to
    the OS-appropriate location below.
    """
    override = os.environ.get("LOCALIZER_DB_PATH")
    if override:
        path = Path(override)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path.home() / ".local" / "share"
    folder = base / "Localizer"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "localizer.db"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS listing (
    id                          TEXT PRIMARY KEY,
    fingerprint                 TEXT NOT NULL,
    postcode                    INTEGER NOT NULL,
    gemeente                    TEXT NOT NULL,
    straat                      TEXT,
    lat                         REAL,
    lng                         REAL,
    prijs_eur                   INTEGER,
    type                        TEXT NOT NULL,
    oppervlakte_bewoonbaar_m2   INTEGER,
    slaapkamers                 INTEGER,
    epc_label                   TEXT,
    epc_kwh                     INTEGER,
    staat                       TEXT,
    titel                       TEXT NOT NULL DEFAULT '',
    korte_beschrijving          TEXT NOT NULL DEFAULT '',
    hoofd_foto_url              TEXT,
    user_status                 TEXT NOT NULL DEFAULT 'nieuw',
    created_at                  TEXT NOT NULL,
    updated_at                  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_listing_fingerprint ON listing (fingerprint);
CREATE INDEX IF NOT EXISTS ix_listing_postcode    ON listing (postcode);
CREATE INDEX IF NOT EXISTS ix_listing_user_status ON listing (user_status);

CREATE TABLE IF NOT EXISTS listing_source (
    listing_id    TEXT NOT NULL REFERENCES listing(id) ON DELETE CASCADE,
    source_name   TEXT NOT NULL,
    source_id     TEXT NOT NULL,
    source_url    TEXT NOT NULL,
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'active',
    PRIMARY KEY (source_name, source_id)
);

CREATE INDEX IF NOT EXISTS ix_listing_source_listing_id ON listing_source (listing_id);

CREATE TABLE IF NOT EXISTS app_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------
@contextmanager
def connect(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """Open a SQLite connection with sane defaults; commit + close on exit."""
    path = db_path or default_db_path()
    conn = sqlite3.connect(path, isolation_level=None, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
    finally:
        conn.close()


def init_schema(conn: sqlite3.Connection) -> None:
    """Create tables if absent + run migrations forward to SCHEMA_VERSION."""
    conn.executescript(SCHEMA_SQL)
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    if row is None:
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
        return

    current = int(row["version"])
    if current < 2:
        # v2: add lat/lng columns to listing for the map view.
        existing_cols = {r["name"] for r in conn.execute("PRAGMA table_info(listing)").fetchall()}
        if "lat" not in existing_cols:
            conn.execute("ALTER TABLE listing ADD COLUMN lat REAL")
        if "lng" not in existing_cols:
            conn.execute("ALTER TABLE listing ADD COLUMN lng REAL")
        conn.execute("UPDATE schema_version SET version = ?", (2,))
    if current < 3:
        # v3: Immoscoop's JSON-LD `House.geo` block turned out to be
        # the agent's office, not the property. The new parser uses
        # `pageProps.property.address.geo` instead. Wipe existing
        # Immoscoop coordinates so the next refresh refills them
        # from the reliable source. Other sources keep their geo.
        conn.execute(
            """
            UPDATE listing
            SET lat = NULL, lng = NULL
            WHERE id IN (
                SELECT listing_id FROM listing_source
                WHERE source_name = 'immoscoop'
            )
            """
        )
        conn.execute("UPDATE schema_version SET version = ?", (3,))


def initialise(db_path: Path | None = None) -> Path:
    """Open + initialise + close. Returns the resolved path."""
    resolved = db_path or default_db_path()
    with connect(resolved) as conn:
        init_schema(conn)
    return resolved


# ---------------------------------------------------------------------------
# Read / write helpers
# ---------------------------------------------------------------------------
def upsert_listing(conn: sqlite3.Connection, listing: Listing) -> None:
    """Insert or update a single canonical listing + all its source rows."""
    payload = json.loads(listing.model_dump_json())
    sources = payload.pop("sources")

    columns = list(payload.keys())
    placeholders = ", ".join(f":{c}" for c in columns)
    update_assignments = ", ".join(f"{c}=excluded.{c}" for c in columns if c != "id")

    sql = (
        f"INSERT INTO listing ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT(id) DO UPDATE SET {update_assignments}"
    )
    conn.execute(sql, payload)

    # Replace source rows for this listing in one shot — simplest correct
    # behaviour for V1; volume is small enough that this never hurts.
    conn.execute("DELETE FROM listing_source WHERE listing_id = ?", (str(listing.id),))
    conn.executemany(
        """
        INSERT INTO listing_source (
            listing_id, source_name, source_id, source_url,
            first_seen, last_seen, status
        ) VALUES (
            :listing_id, :source_name, :source_id, :source_url,
            :first_seen, :last_seen, :status
        )
        """,
        [
            {
                "listing_id": str(listing.id),
                "source_name": s["source_name"],
                "source_id": s["source_id"],
                "source_url": s["source_url"],
                "first_seen": s["first_seen"],
                "last_seen": s["last_seen"],
                "status": s["status"],
            }
            for s in sources
        ],
    )


def find_listing_by_fingerprint(conn: sqlite3.Connection, fingerprint: str) -> Listing | None:
    """Return the canonical listing for `fingerprint`, or None if absent."""
    row = conn.execute("SELECT * FROM listing WHERE fingerprint = ?", (fingerprint,)).fetchone()
    if row is None:
        return None
    return _row_to_listing(conn, row)


def all_listings(conn: sqlite3.Connection) -> list[Listing]:
    """Return every canonical listing in insertion-stable order."""
    rows = conn.execute("SELECT * FROM listing ORDER BY created_at").fetchall()
    return [_row_to_listing(conn, r) for r in rows]


def mark_source_gone(conn: sqlite3.Connection, source: SourceName, source_id: str) -> None:
    """Soft-delete: flag a source-reference as no longer advertised."""
    conn.execute(
        "UPDATE listing_source SET status = ? WHERE source_name = ? AND source_id = ?",
        (ListingStatus.GONE.value, source.value, source_id),
    )


# ---------------------------------------------------------------------------
# UI-side helpers: filtered query, status update, app-meta
# ---------------------------------------------------------------------------
def _build_filter_clause(
    *,
    gemeente: str | None = None,
    postcode: int | None = None,
    prijs_min: int | None = None,
    prijs_max: int | None = None,
    slaapkamers_min: int | None = None,
    opp_min: int | None = None,
    epc_label_in: list[str] | None = None,
    type_in: list[str] | None = None,
    staat_in: list[str] | None = None,
    user_status_in: list[str] | None = None,
    exclude_user_status: list[str] | None = None,
    source_in: list[str] | None = None,
    text: str | None = None,
) -> tuple[str, list[object]]:
    """Build the WHERE clause + params for the listing-filter shape used by
    `query_listings` and `count_listings`. Returns ('', []) when there are
    no constraints."""
    where: list[str] = []
    params: list[object] = []

    if gemeente:
        where.append("LOWER(gemeente) LIKE ?")
        params.append(f"%{gemeente.lower()}%")
    if postcode is not None:
        where.append("postcode = ?")
        params.append(postcode)
    if prijs_min is not None:
        where.append("prijs_eur >= ?")
        params.append(prijs_min)
    if prijs_max is not None:
        where.append("prijs_eur <= ?")
        params.append(prijs_max)
    if slaapkamers_min is not None:
        where.append("slaapkamers >= ?")
        params.append(slaapkamers_min)
    if opp_min is not None:
        where.append("oppervlakte_bewoonbaar_m2 >= ?")
        params.append(opp_min)
    if epc_label_in:
        placeholders = ",".join("?" for _ in epc_label_in)
        where.append(f"epc_label IN ({placeholders})")
        params.extend(epc_label_in)
    if type_in:
        placeholders = ",".join("?" for _ in type_in)
        where.append(f"type IN ({placeholders})")
        params.extend(type_in)
    if staat_in:
        placeholders = ",".join("?" for _ in staat_in)
        where.append(f"staat IN ({placeholders})")
        params.extend(staat_in)
    if user_status_in:
        placeholders = ",".join("?" for _ in user_status_in)
        where.append(f"user_status IN ({placeholders})")
        params.extend(user_status_in)
    if exclude_user_status:
        placeholders = ",".join("?" for _ in exclude_user_status)
        where.append(f"user_status NOT IN ({placeholders})")
        params.extend(exclude_user_status)
    if source_in:
        placeholders = ",".join("?" for _ in source_in)
        where.append(
            f"id IN (SELECT listing_id FROM listing_source WHERE source_name IN ({placeholders}))"
        )
        params.extend(source_in)
    if text:
        where.append("(LOWER(titel) LIKE ? OR LOWER(korte_beschrijving) LIKE ?)")
        like = f"%{text.lower()}%"
        params.extend([like, like])

    clause = (" WHERE " + " AND ".join(where)) if where else ""
    return clause, params


def query_listings(
    conn: sqlite3.Connection,
    *,
    gemeente: str | None = None,
    postcode: int | None = None,
    prijs_min: int | None = None,
    prijs_max: int | None = None,
    slaapkamers_min: int | None = None,
    opp_min: int | None = None,
    epc_label_in: list[str] | None = None,
    type_in: list[str] | None = None,
    staat_in: list[str] | None = None,
    user_status_in: list[str] | None = None,
    exclude_user_status: list[str] | None = None,
    source_in: list[str] | None = None,
    text: str | None = None,
    limit: int | None = None,
    offset: int | None = None,
) -> list[Listing]:
    """Filtered listing query. Empty filter args mean 'no constraint'.

    `created_at` alone isn't a stable sort key (a single refresh batch
    shares one timestamp), so we tie-break on `id DESC` to keep
    pagination consistent across page hops.
    """
    clause, params = _build_filter_clause(
        gemeente=gemeente,
        postcode=postcode,
        prijs_min=prijs_min,
        prijs_max=prijs_max,
        slaapkamers_min=slaapkamers_min,
        opp_min=opp_min,
        epc_label_in=epc_label_in,
        type_in=type_in,
        staat_in=staat_in,
        user_status_in=user_status_in,
        exclude_user_status=exclude_user_status,
        source_in=source_in,
        text=text,
    )
    sql = "SELECT * FROM listing" + clause + " ORDER BY created_at DESC, id DESC"
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    if offset is not None and offset > 0:
        # OFFSET requires LIMIT in SQLite — supply a sentinel if absent.
        if limit is None:
            sql += " LIMIT -1"
        sql += f" OFFSET {int(offset)}"

    rows = conn.execute(sql, params).fetchall()
    return [_row_to_listing(conn, r) for r in rows]


def count_listings(
    conn: sqlite3.Connection,
    *,
    gemeente: str | None = None,
    postcode: int | None = None,
    prijs_min: int | None = None,
    prijs_max: int | None = None,
    slaapkamers_min: int | None = None,
    opp_min: int | None = None,
    epc_label_in: list[str] | None = None,
    type_in: list[str] | None = None,
    staat_in: list[str] | None = None,
    user_status_in: list[str] | None = None,
    exclude_user_status: list[str] | None = None,
    source_in: list[str] | None = None,
    text: str | None = None,
) -> int:
    """Count listings matching the same filter shape as `query_listings`."""
    clause, params = _build_filter_clause(
        gemeente=gemeente,
        postcode=postcode,
        prijs_min=prijs_min,
        prijs_max=prijs_max,
        slaapkamers_min=slaapkamers_min,
        opp_min=opp_min,
        epc_label_in=epc_label_in,
        type_in=type_in,
        staat_in=staat_in,
        user_status_in=user_status_in,
        exclude_user_status=exclude_user_status,
        source_in=source_in,
        text=text,
    )
    sql = "SELECT COUNT(*) FROM listing" + clause
    return int(conn.execute(sql, params).fetchone()[0])


def query_listing_pins(
    conn: sqlite3.Connection,
    *,
    gemeente: str | None = None,
    postcode: int | None = None,
    prijs_min: int | None = None,
    prijs_max: int | None = None,
    slaapkamers_min: int | None = None,
    opp_min: int | None = None,
    epc_label_in: list[str] | None = None,
    type_in: list[str] | None = None,
    staat_in: list[str] | None = None,
    user_status_in: list[str] | None = None,
    exclude_user_status: list[str] | None = None,
    source_in: list[str] | None = None,
    text: str | None = None,
    limit: int | None = None,
) -> list[dict[str, object]]:
    """Flat single-query lookup for the map view.

    Returns lightweight dicts (just what the map template needs) by
    JOINing `listing` with `listing_source` in one round-trip. Avoids
    the N+1 problem of `query_listings` → `_row_to_listing` (which
    fires one extra SELECT per listing to load source rows; with 26k
    listings that's 26k extra queries = 2-5s of SQLite overhead).

    Each canonical listing typically has exactly one source row in V1
    (fingerprint = (source_name, source_id)). When multiple exist we
    keep the first by source_name alphabetical order.
    """
    clause, params = _build_filter_clause(
        gemeente=gemeente,
        postcode=postcode,
        prijs_min=prijs_min,
        prijs_max=prijs_max,
        slaapkamers_min=slaapkamers_min,
        opp_min=opp_min,
        epc_label_in=epc_label_in,
        type_in=type_in,
        staat_in=staat_in,
        user_status_in=user_status_in,
        exclude_user_status=exclude_user_status,
        source_in=source_in,
        text=text,
    )
    sql = (
        """
        SELECT
            l.id,
            l.lat, l.lng,
            l.postcode, l.gemeente, l.straat,
            l.titel, l.prijs_eur, l.epc_label, l.type, l.user_status,
            l.hoofd_foto_url AS foto,
            ls.source_url, ls.source_name
        FROM listing l
        LEFT JOIN listing_source ls ON ls.listing_id = l.id
        """
        + clause
        + " ORDER BY l.id, ls.source_name"
    )
    if limit is not None:
        sql += f" LIMIT {int(limit)}"

    seen: set[str] = set()
    out: list[dict[str, object]] = []
    for row in conn.execute(sql, params):
        lid = row["id"]
        if lid in seen:
            continue  # LEFT JOIN dupes when multiple source rows exist
        seen.add(lid)
        out.append(
            {
                "id": lid,
                "lat": row["lat"],
                "lng": row["lng"],
                "postcode": row["postcode"],
                "gemeente": row["gemeente"],
                "straat": row["straat"],
                "titel": row["titel"],
                "prijs_eur": row["prijs_eur"],
                "epc_label": row["epc_label"],
                "type": row["type"],
                "user_status": row["user_status"],
                "foto": row["foto"],
                "source_url": row["source_url"] or "",
                "source_name": row["source_name"] or "",
            }
        )
    return out


def update_user_status(conn: sqlite3.Connection, listing_id: str, status: str) -> None:
    """Set user_status on a listing (UI marker, not bron-afhankelijk)."""
    conn.execute(
        "UPDATE listing SET user_status = ?, updated_at = ? WHERE id = ?",
        (status, _now_iso(), listing_id),
    )


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM app_meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row is not None else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO app_meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# Internal: row → Listing rehydration
# ---------------------------------------------------------------------------
def _row_to_listing(conn: sqlite3.Connection, row: sqlite3.Row) -> Listing:
    src_rows = conn.execute(
        "SELECT * FROM listing_source WHERE listing_id = ?", (row["id"],)
    ).fetchall()
    sources = [
        ListingSource.model_validate(
            {
                "source_name": s["source_name"],
                "source_id": s["source_id"],
                "source_url": s["source_url"],
                "first_seen": s["first_seen"],
                "last_seen": s["last_seen"],
                "status": s["status"],
            }
        )
        for s in src_rows
    ]
    return Listing.model_validate({**dict(row), "sources": sources})
