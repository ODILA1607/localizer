"""Dump every canonical listing in the local DB with all its source-references.

Looks for false-positive merges: two clearly different properties stored
under one canonical listing because their fingerprints collapsed.

Run:  python inspect_db.py
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path


def db_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    return base / "Localizer" / "localizer.db"


def main() -> None:
    path = db_path()
    if not path.exists():
        print(f"No database at {path}. Run `localizer refresh` first.")
        return

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row

    listings = conn.execute(
        """
        SELECT
            l.id, l.fingerprint, l.postcode, l.gemeente, l.straat,
            l.oppervlakte_bewoonbaar_m2 AS opp,
            l.slaapkamers AS bed,
            l.prijs_eur AS prijs,
            l.titel
        FROM listing l
        ORDER BY l.fingerprint
        """
    ).fetchall()

    print(f"Database: {path}")
    print(f"Total canonical listings: {len(listings)}\n")
    print("=" * 100)

    for row in listings:
        srcs = conn.execute(
            "SELECT source_name, source_id, source_url FROM listing_source "
            "WHERE listing_id = ? ORDER BY source_name",
            (row["id"],),
        ).fetchall()

        n = len(srcs)
        marker = "★" if n > 1 else " "
        print(
            f"\n{marker} fp={row['fingerprint'][:8]}…  "
            f"{row['postcode']} {row['gemeente'] or '?':<18}  "
            f"straat={row['straat']!r:<28}  "
            f"opp={row['opp']}  bed={row['bed']}  prijs={row['prijs']}"
        )
        if row["titel"]:
            print(f"   titel: {row['titel'][:80]}")
        for src in srcs:
            print(f"   - {src['source_name']:<10} {src['source_url']}")

    print("\n" + "=" * 100)
    multi = [
        r
        for r in listings
        if conn.execute("SELECT 1 FROM listing_source WHERE listing_id = ? LIMIT 2", (r["id"],))
        .fetchall()
        .__len__()
        > 1
    ]
    print(f"Listings with >1 source (★): {len(multi)} of {len(listings)}")


if __name__ == "__main__":
    main()
