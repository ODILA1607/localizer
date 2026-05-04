"""Tests for the dedup fingerprint and address-normalisation helpers."""

from __future__ import annotations

from localizer.core.dedup import compute_fingerprint, normalise_address_part
from localizer.core.models import SourceName


# ---------------------------------------------------------------------------
# Address normalisation (kept around for V1.1 cross-source matching)
# ---------------------------------------------------------------------------
def test_normalise_strips_diacritics_and_punctuation() -> None:
    assert normalise_address_part("Sint-Niklaasstraat") == "sintniklaasstraat"
    assert normalise_address_part("Émile Vanderveldelaan 23A") == "emilevanderveldelaan23a"
    assert normalise_address_part("  Kerkstraat   12  ") == "kerkstraat12"


def test_normalise_handles_none_and_empty() -> None:
    assert normalise_address_part(None) == ""
    assert normalise_address_part("") == ""


# ---------------------------------------------------------------------------
# Fingerprint — V1: per (source, source_id)
# ---------------------------------------------------------------------------
def test_fingerprint_is_deterministic() -> None:
    a = compute_fingerprint(source_name=SourceName.ZIMMO, source_id="123")
    b = compute_fingerprint(source_name=SourceName.ZIMMO, source_id="123")
    assert a == b
    assert len(a) == 32  # blake2b digest_size=16 -> 32 hex chars


def test_fingerprint_accepts_string_source_name() -> None:
    """SourceName enum and its string value must produce the same hash."""
    a = compute_fingerprint(source_name=SourceName.ZIMMO, source_id="123")
    b = compute_fingerprint(source_name="zimmo", source_id="123")
    assert a == b


def test_fingerprint_differs_on_different_id() -> None:
    a = compute_fingerprint(source_name=SourceName.ZIMMO, source_id="123")
    b = compute_fingerprint(source_name=SourceName.ZIMMO, source_id="124")
    assert a != b


def test_fingerprint_differs_on_different_source() -> None:
    """Same id from different sources must NOT collide.

    This is the property that broke V0: when geographical fields were
    null, fingerprints collapsed and unrelated listings merged.
    """
    a = compute_fingerprint(source_name=SourceName.ZIMMO, source_id="123")
    b = compute_fingerprint(source_name=SourceName.IMMOSCOOP, source_id="123")
    assert a != b


def test_fingerprint_handles_alphanumeric_id() -> None:
    """Immovlan uses ids like 'vbe15512'."""
    a = compute_fingerprint(source_name=SourceName.IMMOVLAN, source_id="vbe15512")
    b = compute_fingerprint(source_name=SourceName.IMMOVLAN, source_id="rbv78631")
    assert a != b
    assert len(a) == 32
