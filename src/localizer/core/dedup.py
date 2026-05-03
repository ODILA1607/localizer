"""Cross-source duplicate detection.

A property advertised on Zimmo, Immoscoop and Immoweb is *one* canonical
listing with three `ListingSource` rows — not three separate listings.

Strategy:
  1. Compute a stable `fingerprint` from features that are bron-onafhankelijk
     and rarely vary across listings of the same property:
        normalised(straat + huisnummer) + postcode + opp_bewoonbaar + slaapkamers
  2. If two listings produce the same fingerprint they are merged: a single
     `Listing` keeps both `ListingSource` entries.
  3. When street or surface info is missing (cheaper bronnen often omit
     huisnummer) the fingerprint degrades gracefully — it still hashes
     deterministically, but collisions are more likely. That is acceptable:
     a false-positive merge is recoverable, a false-negative duplicate is
     visible noise to the user.

Photo-hash matching as a tiebreaker is planned for a later milestone.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import unicodedata

from localizer.core import db
from localizer.core.models import Listing, ListingSource, utc_now

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalise_address_part(value: str | None) -> str:
    """Lowercase, strip diacritics, collapse to alphanumerics.

    Examples:
        normalise_address_part("Kerkstraat 12B") -> "kerkstraat12b"
        normalise_address_part("Sint-Niklaasstraat") -> "sintniklaasstraat"
        normalise_address_part(None) -> ""
    """
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")
    return _NON_ALNUM.sub("", ascii_only.lower())


def compute_fingerprint(
    *,
    straat: str | None,
    postcode: int,
    oppervlakte_bewoonbaar_m2: int | None,
    slaapkamers: int | None,
) -> str:
    """Return a 32-char hex fingerprint usable as the canonical dedup key.

    Collisions are technically possible but extremely unlikely for distinct
    real-world properties given the input-space.
    """
    parts = (
        normalise_address_part(straat),
        str(postcode),
        str(oppervlakte_bewoonbaar_m2 or 0),
        str(slaapkamers or 0),
    )
    raw = "|".join(parts).encode("utf-8")
    return hashlib.blake2b(raw, digest_size=16).hexdigest()


# ---------------------------------------------------------------------------
# Cross-source merge on the persistence boundary
# ---------------------------------------------------------------------------
def merge_or_insert(conn: sqlite3.Connection, candidate: Listing) -> tuple[Listing, bool]:
    """Insert `candidate` or merge it into the existing listing with the
    same fingerprint.

    Returns `(stored_listing, inserted)` where `inserted` is True only when
    the listing was newly created. On merge:
      - The existing UUID is preserved (so the UI keeps a stable handle).
      - Sources from `candidate` are appended unless `(source_name,
        source_id)` already exists, in which case the existing source row
        is kept (we do not yet take "latest fields win" — see V1.1).
      - `updated_at` bumps to now.
      - Mutable canonical fields (price, surface, EPC, …) prefer non-None
        values from the candidate over None values on the existing row.
        This way a richer Immoscoop row can fill gaps the Zimmo row left.
    """
    existing = db.find_listing_by_fingerprint(conn, candidate.fingerprint)
    if existing is None:
        db.upsert_listing(conn, candidate)
        return candidate, True

    merged_sources = _merge_source_lists(existing.sources, candidate.sources)
    merged_fields = _merge_field_dicts(existing, candidate)
    merged = existing.model_copy(
        update={**merged_fields, "sources": merged_sources, "updated_at": utc_now()}
    )
    db.upsert_listing(conn, merged)
    return merged, False


def _merge_source_lists(
    existing: list[ListingSource], incoming: list[ListingSource]
) -> list[ListingSource]:
    """Union by `(source_name, source_id)`. Existing entries win on conflict."""
    keyed: dict[tuple[str, str], ListingSource] = {
        (s.source_name.value, s.source_id): s for s in existing
    }
    for s in incoming:
        keyed.setdefault((s.source_name.value, s.source_id), s)
    return list(keyed.values())


_PREFER_NEW_IF_EXISTING_NULL = (
    "straat",
    "prijs_eur",
    "oppervlakte_bewoonbaar_m2",
    "slaapkamers",
    "epc_label",
    "epc_kwh",
    "staat",
    "hoofd_foto_url",
    "korte_beschrijving",
    "titel",
)


def _merge_field_dicts(existing: Listing, candidate: Listing) -> dict[str, object]:
    """Choose the candidate's value when the existing one is empty/None."""
    update: dict[str, object] = {}
    for field_name in _PREFER_NEW_IF_EXISTING_NULL:
        existing_value = getattr(existing, field_name)
        candidate_value = getattr(candidate, field_name)
        if (existing_value in (None, "")) and candidate_value not in (None, ""):
            update[field_name] = candidate_value
    return update
