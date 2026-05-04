"""Canonical Listing schema.

One canonical listing per real-world property, with N source-references
(same property typically appears on multiple sites). All connectors map
their raw data into this shape.

Design rules:
- Fields the source does not provide stay None. No invented data.
- Pydantic validates on construction; bad data fails fast at the seam
  between connector and core.
- All datetimes are stored as timezone-aware UTC.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------
class SourceName(enum.StrEnum):
    IMMOWEB = "immoweb"
    ZIMMO = "zimmo"
    IMMOSCOOP = "immoscoop"
    IMMOVLAN = "immovlan"


class ListingStatus(enum.StrEnum):
    """Tracks whether the source still advertises this listing.

    Soft delete: when a listing disappears from the source we mark it `gone`
    rather than dropping the row, so price-history and time-on-market stay
    queryable.
    """

    ACTIVE = "active"
    GONE = "gone"


class PropertyType(enum.StrEnum):
    HUIS = "huis"
    APPARTEMENT = "appartement"
    GROND = "grond"
    OTHER = "other"


class Condition(enum.StrEnum):
    INSTAPKLAAR = "instapklaar"
    TE_RENOVEREN = "te-renoveren"
    NIEUWBOUW = "nieuwbouw"


class EpcLabel(enum.StrEnum):
    """Vlaams EPC-label. F is the worst usual label; A+ exists but rare."""

    A_PLUS = "A+"
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    E = "E"
    F = "F"


class UserStatus(enum.StrEnum):
    """Local-only marking by Thomas. Not bron-afhankelijk."""

    NIEUW = "nieuw"
    BEKEKEN = "bekeken"
    INTERESSANT = "interessant"
    AFGEWEZEN = "afgewezen"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def utc_now() -> datetime:
    """Return tz-aware UTC now. Centralised to keep models pure."""
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class ListingSource(BaseModel):
    """One reference: a single source's view of a listing.

    A canonical Listing has one or more of these (one per source that
    advertised the same property).
    """

    model_config = ConfigDict(frozen=False, str_strip_whitespace=True)

    source_name: SourceName
    source_id: str = Field(min_length=1, description="Stable ID assigned by the source.")
    source_url: HttpUrl
    first_seen: datetime = Field(default_factory=utc_now)
    last_seen: datetime = Field(default_factory=utc_now)
    status: ListingStatus = ListingStatus.ACTIVE


PostcodeBE = Annotated[int, Field(ge=1000, le=9999)]


class Listing(BaseModel):
    """Canonical listing — one per real-world property."""

    model_config = ConfigDict(str_strip_whitespace=True)

    id: UUID = Field(default_factory=uuid4)
    fingerprint: str = Field(
        min_length=16,
        description="Hash of normalised(adres) + opp + slaapkamers; dedup key.",
    )

    sources: list[ListingSource] = Field(min_length=1)

    # Locatie
    postcode: PostcodeBE
    gemeente: str = Field(min_length=1)
    straat: str | None = None

    # Eigenschappen
    prijs_eur: int | None = Field(default=None, ge=0)
    type: PropertyType = PropertyType.OTHER
    oppervlakte_bewoonbaar_m2: int | None = Field(default=None, ge=0)
    slaapkamers: int | None = Field(default=None, ge=0)
    epc_label: EpcLabel | None = None
    epc_kwh: int | None = Field(default=None, ge=0)
    staat: Condition | None = None

    # Tekst + media
    titel: str = ""
    korte_beschrijving: str = ""
    hoofd_foto_url: HttpUrl | None = None

    # Lokale gebruiker (Thomas)
    user_status: UserStatus = UserStatus.NIEUW

    # Audit
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
