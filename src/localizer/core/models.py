"""Canonical Listing schema.

Filled in M1. Only the field list (as a comment) is enumerated here so
the structure is visible from day 1.
"""

from __future__ import annotations

# Planned canonical schema (see brein concept `pand-aggregator-skelet`):
#
#   id              : uuid
#   fingerprint     : hash(adres + opp + slaapkamers)  → dedup key
#   sources         : list[ListingSource]
#                     - source_name      ("immoweb" | "zimmo" | "immoscoop")
#                     - source_id
#                     - source_url       ◀── primary "open original" link
#                     - first_seen
#                     - last_seen
#                     - status           ("active" | "gone")
#   postcode        : int
#   gemeente        : str
#   straat          : str | None
#   prijs_eur       : int | None
#   type            : "huis" | "appartement" | "grond" | "other"
#   oppervlakte_bewoonbaar_m2 : int | None
#   slaapkamers     : int | None
#   epc_label       : "A".."F" | None
#   epc_kwh         : int | None
#   staat           : "instapklaar" | "te-renoveren" | "nieuwbouw" | None
#   titel           : str
#   korte_beschrijving : str
#   hoofd_foto_url  : str | None  (hot-link, never cached locally in V1)
#   user_status     : "nieuw" | "bekeken" | "interessant" | "afgewezen"
#   created_at      : datetime
#   updated_at      : datetime
