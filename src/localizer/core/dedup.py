"""Canonical-listing identity + cross-source merge.

V1 design (the simple, correct one):
  - The `fingerprint` is `blake2b(source_name + source_id)`. Two listings
    map to the same fingerprint *only* when they come from the same
    source with the same source-assigned ID. Re-running the same
    connector against the same property therefore stays idempotent
    (same fingerprint → merge), but two different properties never
    collide.
  - Cross-source duplicates ("the same house listed on Zimmo and
    Immoscoop") become **two separate canonical rows in V1**. Detecting
    them automatically would need richer signals than what JSON-LD
    consistently gives us — full street normalisation, photo hashing,
    fuzzy matching. That is V1.1 work; the architecture (Listing.sources
    is already a list, `merge_or_insert` is already in place) supports
    plugging that in later without a schema change.

The earlier attempt used `straat + postcode + opp + bedrooms`, which
collapsed to "just postcode" whenever Immovlan's JSON-LD omitted
streetAddress — and produced false-positive merges of clearly different
properties in the same town. See git log for the post-mortem.

`normalise_address_part` is kept intact: V1.1 cross-source matching
will use it.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import unicodedata

from localizer.core import db
from localizer.core.models import Listing, ListingSource, SourceName, utc_now

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


def compute_fingerprint(*, source_name: SourceName | str, source_id: str) -> str:
    """Per-(source, source_id) canonical fingerprint.

    Same source + same id ⇒ same fingerprint (idempotent re-run).
    Different ids or different sources ⇒ different fingerprints (no false
    merges).
    """
    name = source_name.value if isinstance(source_name, SourceName) else source_name
    raw = f"{name}:{source_id}".encode()
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
