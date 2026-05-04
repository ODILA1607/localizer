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


def test_cli_with_no_args_prints_version(capsys) -> None:
    rc = main.cli([])
    assert rc == 0
    captured = capsys.readouterr()
    assert "Localizer" in captured.out


def test_cli_help_lists_refresh_subcommand(capsys) -> None:
    import pytest

    with pytest.raises(SystemExit) as exit_info:
        main.cli(["--help"])
    assert exit_info.value.code == 0
    captured = capsys.readouterr()
    assert "refresh" in captured.out


def test_cli_verbose_works_before_or_after_subcommand() -> None:
    """`-v` must work in both positions (argparse subparser-default trap)."""
    parser = main._build_parser()
    assert parser.parse_args(["-v", "refresh"]).verbose == 1
    assert parser.parse_args(["refresh", "-v"]).verbose == 1
    assert parser.parse_args(["-vv", "refresh"]).verbose == 2
    assert parser.parse_args(["refresh", "-vv"]).verbose == 2
    assert parser.parse_args(["refresh"]).verbose == 0


def test_cli_version_flag(capsys) -> None:
    import pytest

    with pytest.raises(SystemExit) as exit_info:
        main.cli(["--version"])
    assert exit_info.value.code == 0
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
