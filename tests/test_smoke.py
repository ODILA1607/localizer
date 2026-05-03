"""Smoke tests — guarantee the package is importable and the M0 wiring holds.

These tests are intentionally trivial. Their job is to fail loudly if the
project layout, packaging or entry point breaks during refactoring.
"""

from __future__ import annotations

import localizer
from localizer import config, main


def test_version_is_set() -> None:
    assert isinstance(localizer.__version__, str)
    assert localizer.__version__.count(".") == 2  # semver M.m.p


def test_cli_returns_zero(capsys) -> None:
    rc = main.cli()
    assert rc == 0
    captured = capsys.readouterr()
    assert "Localizer" in captured.out


def test_postcode_scope_west_oost_vlaanderen() -> None:
    assert config.is_in_scope(8000) is True  # Brugge
    assert config.is_in_scope(8500) is True  # Kortrijk
    assert config.is_in_scope(9000) is True  # Gent
    assert config.is_in_scope(9999) is True  # edge of OVL range
    assert config.is_in_scope(2000) is False  # Antwerpen — out of scope V1
    assert config.is_in_scope(1000) is False  # Brussel — out of scope V1
    assert config.is_in_scope(7000) is False  # Bergen — out of scope V1
