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
import unicodedata

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
