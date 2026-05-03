"""Tests for the dedup fingerprint."""

from __future__ import annotations

from localizer.core.dedup import compute_fingerprint, normalise_address_part


def test_normalise_strips_diacritics_and_punctuation() -> None:
    assert normalise_address_part("Sint-Niklaasstraat") == "sintniklaasstraat"
    assert normalise_address_part("Émile Vanderveldelaan 23A") == "emilevanderveldelaan23a"
    assert normalise_address_part("  Kerkstraat   12  ") == "kerkstraat12"


def test_normalise_handles_none_and_empty() -> None:
    assert normalise_address_part(None) == ""
    assert normalise_address_part("") == ""


def test_fingerprint_is_deterministic() -> None:
    fp1 = compute_fingerprint(
        straat="Kerkstraat 12", postcode=8500, oppervlakte_bewoonbaar_m2=120, slaapkamers=3
    )
    fp2 = compute_fingerprint(
        straat="Kerkstraat 12", postcode=8500, oppervlakte_bewoonbaar_m2=120, slaapkamers=3
    )
    assert fp1 == fp2
    assert len(fp1) == 32  # blake2b digest_size=16 → 32 hex chars


def test_fingerprint_ignores_capitalisation_and_spacing() -> None:
    fp1 = compute_fingerprint(
        straat="kerkstraat 12", postcode=8500, oppervlakte_bewoonbaar_m2=120, slaapkamers=3
    )
    fp2 = compute_fingerprint(
        straat="  KERKSTRAAT  12  ",
        postcode=8500,
        oppervlakte_bewoonbaar_m2=120,
        slaapkamers=3,
    )
    assert fp1 == fp2


def test_fingerprint_differs_on_different_property() -> None:
    fp_a = compute_fingerprint(
        straat="Kerkstraat 12", postcode=8500, oppervlakte_bewoonbaar_m2=120, slaapkamers=3
    )
    fp_b = compute_fingerprint(
        straat="Kerkstraat 14", postcode=8500, oppervlakte_bewoonbaar_m2=120, slaapkamers=3
    )
    assert fp_a != fp_b


def test_fingerprint_differs_on_different_postcode() -> None:
    fp_a = compute_fingerprint(
        straat="Kerkstraat 12", postcode=8500, oppervlakte_bewoonbaar_m2=120, slaapkamers=3
    )
    fp_b = compute_fingerprint(
        straat="Kerkstraat 12", postcode=9000, oppervlakte_bewoonbaar_m2=120, slaapkamers=3
    )
    assert fp_a != fp_b


def test_fingerprint_handles_missing_data() -> None:
    fp = compute_fingerprint(
        straat=None, postcode=8500, oppervlakte_bewoonbaar_m2=None, slaapkamers=None
    )
    assert len(fp) == 32  # never crashes; degraded but stable
